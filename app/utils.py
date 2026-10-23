from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import socket
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from packaging.version import InvalidVersion, Version

SECRET_KEYS = {
    "token",
    "access_token",
    "authorization",
    "password",
    "secret",
    "private_key",
    "client_secret",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def stable_hash(*parts: Any) -> str:
    payload = json.dumps(parts, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def auth_scope(token: str | None) -> str:
    if not token:
        return "anonymous"
    return "token-" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "project"


def is_semver(value: str | None) -> bool:
    if not value:
        return False
    cleaned = value.strip().lstrip("vV=")
    try:
        Version(cleaned)
    except InvalidVersion:
        return False
    return bool(re.match(r"^\d+\.\d+\.\d+", cleaned))


def version_key(value: str | None) -> Version | None:
    if not value:
        return None
    try:
        return Version(value.strip().lstrip("vV="))
    except InvalidVersion:
        return None


def compare_versions(left: str | None, right: str | None) -> str:
    """Return equal, left_newer, right_newer, or unknown."""
    lv, rv = version_key(left), version_key(right)
    if lv is None or rv is None:
        if left == right:
            return "equal"
        return "unknown"
    if lv == rv:
        return "equal"
    return "left_newer" if lv > rv else "right_newer"


def is_prerelease(value: str | None) -> bool:
    version = version_key(value)
    return bool(version and version.is_prerelease)


def scrub_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if key.lower() in SECRET_KEYS:
                cleaned[key] = "[redacted]"
            else:
                cleaned[key] = scrub_secrets(item)
        return cleaned
    if isinstance(value, list):
        return [scrub_secrets(item) for item in value]
    return value


def strip_patches(value: Any) -> Any:
    """Remove source patches everywhere; the index never persists file contents."""
    if isinstance(value, dict):
        return {
            key: strip_patches(item)
            for key, item in value.items()
            if key not in {"patch", "diff", "contents", "content", "body_content"}
        }
    if isinstance(value, list):
        return [strip_patches(item) for item in value]
    return value


ALLOWED_REGISTRY_HOSTS = {
    "pypi.org",
    "registry.npmjs.org",
    "crates.io",
    "proxy.golang.org",
    "github.com",
    "gitlab.com",
    "codeberg.org",
}


def validate_remote_url(url: str, allow_arbitrary: bool = False) -> str:
    """Reject non-https, credentialed, and private-network URLs (SSRF guard)."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Only absolute https URLs are accepted")
    if parsed.username or parsed.password:
        raise ValueError("URLs with embedded credentials are rejected")
    host = parsed.hostname.lower()
    if not allow_arbitrary and not any(
        host == allowed or host.endswith("." + allowed) for allowed in ALLOWED_REGISTRY_HOSTS
    ):
        raise ValueError(f"Host {host!r} is not an allowed provider host")
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("Host cannot be resolved") from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
        ):
            raise ValueError("URL resolves to a private or reserved address")
    return parsed.geturl()


def parse_github_repo_url(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    if parsed.netloc.lower() not in {"github.com", "www.github.com"}:
        raise ValueError("Only GitHub repository URLs are supported for this shortcut")
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) < 2:
        raise ValueError("GitHub URL must include owner and repository")
    name = parts[1][:-4] if parts[1].endswith(".git") else parts[1]
    return parts[0], name


def link_header_next(link_header: str | None) -> str | None:
    if not link_header:
        return None
    for part in link_header.split(","):
        section = part.split(";")
        if len(section) < 2:
            continue
        url = section[0].strip().strip("<>")
        if any('rel="next"' in bit.strip() for bit in section[1:]):
            return url
    return None
