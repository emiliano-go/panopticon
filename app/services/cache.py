from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import ProviderCache, ProviderState
from app.providers.base import NotFound, ProviderError, RateLimitError
from app.utils import (
    auth_scope,
    iso,
    link_header_next,
    parse_dt,
    scrub_secrets,
    stable_hash,
    strip_patches,
    utcnow,
)

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()
_FAILURES: dict[str, list[float]] = {}
_CIRCUIT_OPEN: dict[str, float] = {}
_CIRCUIT_THRESHOLD = 5
_CIRCUIT_COOLDOWN = 60.0


@dataclass
class CacheResult:
    payload: Any
    status: str
    fetched_at: datetime | None
    last_successful_fetch_at: datetime | None
    is_stale: bool
    from_cache: bool
    next_url: str | None = None


def _lock_for(key: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class CachedHTTPClient:
    """Conditional, cache-first HTTP client with rate-limit and outage handling."""

    def __init__(
        self,
        session: Session,
        settings: Settings,
        provider: str,
        scope: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.provider = provider
        self.scope = scope or auth_scope(settings.github_token if provider == "github" else None)
        self.client = httpx.Client(
            timeout=settings.github_timeout,
            transport=transport,
            headers={"User-Agent": settings.user_agent, "Accept": "application/vnd.github+json"},
            follow_redirects=True,
        )
        self._request_count = 0

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> CachedHTTPClient:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = dict(extra or {})
        if self.provider == "github" and self.settings.github_token:
            headers["Authorization"] = f"Bearer {self.settings.github_token}"
            headers["X-GitHub-Api-Version"] = "2022-11-28"
        return headers

    def _cache_key(self, resource: str, url: str, params: dict[str, Any]) -> str:
        return stable_hash(self.provider, self.scope, resource, url, params)

    def _precheck_rate_limit(self) -> None:
        state = self.session.scalar(
            select(ProviderState).where(
                ProviderState.provider == self.provider, ProviderState.family == "rest"
            )
        )
        if state and state.remaining is not None and state.remaining <= 0:
            reset = _aware(state.reset_at)
            if reset and reset > utcnow():
                raise RateLimitError(
                    f"{self.provider} rate limit exhausted until {iso(reset)}", reset_at=reset
                )

    def _update_rate_state(self, response: httpx.Response) -> None:
        remaining_raw = response.headers.get("x-ratelimit-remaining")
        limit_raw = response.headers.get("x-ratelimit-limit")
        reset_raw = response.headers.get("x-ratelimit-reset")
        if remaining_raw is None and limit_raw is None:
            return
        family = response.headers.get("x-ratelimit-resource", "rest")
        state = self.session.scalar(
            select(ProviderState).where(
                ProviderState.provider == self.provider, ProviderState.family == family
            )
        )
        if state is None:
            state = ProviderState(provider=self.provider, family=family)
            self.session.add(state)
        state.limit = int(limit_raw) if limit_raw and limit_raw.isdigit() else state.limit
        state.remaining = (
            int(remaining_raw) if remaining_raw and remaining_raw.isdigit() else state.remaining
        )
        if reset_raw and reset_raw.isdigit():
            state.reset_at = datetime.fromtimestamp(int(reset_raw), tz=timezone.utc)
        state.last_error = None if response.status_code < 400 else response.text[:500]
        self.session.flush()

    def _circuit_key(self, url: str) -> str:
        # Group by provider and endpoint shape rather than concrete resource ids.
        parts = url.split("/")
        shape = "/".join(parts[:8])
        return f"{self.provider}:{shape}"

    def _check_circuit(self, key: str) -> None:
        opened = _CIRCUIT_OPEN.get(key)
        if opened and time.time() < opened:
            raise ProviderError(f"Circuit open for {key}; retry later")
        if opened:
            _CIRCUIT_OPEN.pop(key, None)
            _FAILURES.pop(key, None)

    def _record_failure(self, key: str) -> None:
        now = time.time()
        failures = [stamp for stamp in _FAILURES.get(key, []) if now - stamp < 120]
        failures.append(now)
        _FAILURES[key] = failures
        if len(failures) >= _CIRCUIT_THRESHOLD:
            _CIRCUIT_OPEN[key] = now + _CIRCUIT_COOLDOWN

    def get_json(
        self,
        url: str,
        *,
        resource: str,
        params: dict[str, Any] | None = None,
        ttl: int | None = None,
        headers: dict[str, str] | None = None,
        allow_stale: bool = True,
        force: bool = False,
    ) -> CacheResult:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        ttl = ttl if ttl is not None else self.settings.cache_default_ttl
        key = self._cache_key(resource, url, params)
        row = self.session.scalar(select(ProviderCache).where(ProviderCache.cache_key == key))
        now = utcnow()

        if row and not force and row.payload is not None and row.next_refresh_at:
            next_refresh = _aware(row.next_refresh_at)
            if next_refresh and next_refresh > now and row.status in {"current", "stale"}:
                row.hits += 1
                self.session.flush()
                return CacheResult(
                    payload=row.payload,
                    status="current",
                    fetched_at=row.fetched_at,
                    last_successful_fetch_at=row.last_successful_fetch_at,
                    is_stale=False,
                    from_cache=True,
                )

        lock = _lock_for(key)
        with lock:
            if row is None:
                row = ProviderCache(
                    provider=self.provider,
                    resource=resource,
                    scope=self.scope,
                    cache_key=key,
                    status="never_fetched",
                )
                self.session.add(row)
                self.session.flush()
            elif not force and row.payload is not None and row.next_refresh_at:
                next_refresh = _aware(row.next_refresh_at)
                if next_refresh and next_refresh > utcnow():
                    row.hits += 1
                    self.session.flush()
                    return CacheResult(
                        payload=row.payload,
                        status="current",
                        fetched_at=row.fetched_at,
                        last_successful_fetch_at=row.last_successful_fetch_at,
                        is_stale=False,
                        from_cache=True,
                    )

            circuit_key = self._circuit_key(url)
            try:
                self._precheck_rate_limit()
                self._check_circuit(circuit_key)
            except ProviderError as exc:
                if row.payload is not None and allow_stale:
                    row.status = "stale"
                    row.error = str(exc)
                    row.hits += 1
                    self.session.flush()
                    return CacheResult(
                        row.payload, "stale", row.fetched_at, row.last_successful_fetch_at, True, True
                    )
                raise

            request_headers = self._headers(headers)
            if row.etag:
                request_headers["If-None-Match"] = row.etag
            if row.last_modified:
                request_headers["If-Modified-Since"] = row.last_modified

            row.last_attempt_at = utcnow()
            row.misses += 1
            try:
                response = self.client.get(url, params=params or None, headers=request_headers)
                self._request_count += 1
            except httpx.HTTPError as exc:
                self._record_failure(circuit_key)
                row.status = "refresh_failed"
                row.error = str(exc)
                if row.payload is not None and allow_stale:
                    row.hits += 1
                    row.next_refresh_at = utcnow() + timedelta(seconds=min(ttl, 120))
                    self.session.flush()
                    return CacheResult(
                        row.payload, "stale", row.fetched_at, row.last_successful_fetch_at, True, True
                    )
                self.session.flush()
                raise ProviderError(f"Provider request failed: {exc}") from exc

            self._update_rate_state(response)

            if response.status_code == 304:
                row.status = "current"
                row.error = None
                row.fetched_at = utcnow()
                row.last_successful_fetch_at = utcnow()
                row.next_refresh_at = utcnow() + timedelta(seconds=ttl)
                self.session.flush()
                return CacheResult(
                    row.payload, "current", row.fetched_at, row.last_successful_fetch_at, False, True
                )

            if response.status_code == 404:
                _FAILURES.pop(circuit_key, None)
                row.status = "missing"
                row.error = "Not found at source"
                row.fetched_at = utcnow()
                row.next_refresh_at = utcnow() + timedelta(seconds=min(ttl, 300))
                self.session.flush()
                if row.payload is not None and allow_stale:
                    return CacheResult(
                        row.payload, "stale", row.fetched_at, row.last_successful_fetch_at, True, True
                    )
                raise NotFound(f"{resource} not found at provider")

            if response.status_code in {401, 403, 429}:
                reset = self._reset_from_response(response)
                retry_after = response.headers.get("retry-after")
                if retry_after and retry_after.isdigit() and reset is None:
                    reset = utcnow() + timedelta(seconds=int(retry_after))
                message = f"{self.provider} returned {response.status_code}"
                if response.status_code == 403 and "rate limit" not in response.text.lower():
                    row.status = "unavailable"
                    row.error = message
                    self.session.flush()
                    if row.payload is not None and allow_stale:
                        return CacheResult(
                            row.payload, "stale", row.fetched_at, row.last_successful_fetch_at, True, True
                        )
                    raise ProviderError(f"{message}: permission denied", status=403)
                row.status = "refresh_failed"
                row.error = message
                row.next_refresh_at = reset
                self.session.flush()
                if row.payload is not None and allow_stale:
                    return CacheResult(
                        row.payload, "stale", row.fetched_at, row.last_successful_fetch_at, True, True
                    )
                raise RateLimitError(message, reset_at=reset)

            if response.status_code >= 500:
                self._record_failure(circuit_key)
                row.status = "refresh_failed"
                row.error = f"Provider {response.status_code}"
                row.next_refresh_at = utcnow() + timedelta(
                    seconds=min(2 ** min(row.misses, 8) + random.random(), 300)
                )
                self.session.flush()
                if row.payload is not None and allow_stale:
                    return CacheResult(
                        row.payload, "stale", row.fetched_at, row.last_successful_fetch_at, True, True
                    )
                raise ProviderError(f"Provider returned {response.status_code}")

            if response.status_code >= 400:
                row.status = "refresh_failed"
                row.error = f"Provider {response.status_code}: {response.text[:300]}"
                self.session.flush()
                raise ProviderError(f"Provider returned {response.status_code}")

            try:
                payload = strip_patches(scrub_secrets(response.json()))
            except ValueError as exc:
                row.status = "refresh_failed"
                row.error = "Provider returned invalid JSON"
                self.session.flush()
                raise ProviderError("Provider returned invalid JSON") from exc

            now = utcnow()
            row.payload = payload
            row.etag = response.headers.get("etag")
            row.last_modified = response.headers.get("last-modified")
            row.status = "current"
            row.error = None
            row.fetched_at = now
            row.last_successful_fetch_at = now
            row.next_refresh_at = now + timedelta(seconds=ttl)
            row.source_updated_at = parse_dt(payload.get("updated_at")) if isinstance(payload, dict) else None
            self.session.flush()
            _FAILURES.pop(circuit_key, None)
            return CacheResult(
                payload,
                "current",
                now,
                now,
                False,
                False,
                next_url=link_header_next(response.headers.get("link")),
            )

    def _reset_from_response(self, response: httpx.Response) -> datetime | None:
        reset = response.headers.get("x-ratelimit-reset")
        if reset and reset.isdigit():
            return datetime.fromtimestamp(int(reset), tz=timezone.utc)
        return None

    def get_pages(
        self,
        url: str,
        *,
        resource: str,
        params: dict[str, Any] | None = None,
        ttl: int | None = None,
        max_pages: int = 20,
        allow_stale: bool = True,
    ) -> list[dict[str, Any]]:
        params = dict(params or {})
        params.setdefault("per_page", 100)
        items: list[dict[str, Any]] = []
        page = 1
        next_url: str | None = url
        while next_url and page <= max_pages:
            result = self.get_json(
                next_url,
                resource=f"{resource}:page={page}",
                params=params if next_url == url else None,
                ttl=ttl,
                allow_stale=allow_stale,
            )
            if isinstance(result.payload, list):
                items.extend(result.payload)
            elif isinstance(result.payload, dict) and isinstance(result.payload.get("items"), list):
                items.extend(result.payload["items"])
            next_url = result.next_url
            params = None
            page += 1
            if next_url is None:
                break
        return items


def cache_stats(session: Session) -> dict[str, Any]:
    total = session.scalar(select(func.count(ProviderCache.id))) or 0
    hits = session.scalar(select(func.coalesce(func.sum(ProviderCache.hits), 0))) or 0
    misses = session.scalar(select(func.coalesce(func.sum(ProviderCache.misses), 0))) or 0
    by_status = dict(
        session.execute(
            select(ProviderCache.status, func.count()).group_by(ProviderCache.status)
        ).all()
    )
    ratio = (hits / (hits + misses)) if (hits + misses) else 0.0
    return {
        "entries": total,
        "hits": hits,
        "misses": misses,
        "hit_ratio": round(ratio, 4),
        "by_status": by_status,
    }
