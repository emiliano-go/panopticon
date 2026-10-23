from __future__ import annotations

import httpx
import pytest
import respx
from sqlalchemy import select

from app.models import ProviderCache, ProviderState
from app.providers.base import RateLimitError
from app.services.cache import CachedHTTPClient


def test_304_retains_payload_and_verifies_cache(settings, database):
    url = "https://api.github.com/repos/octo/widgets"
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(url).mock(
            return_value=httpx.Response(200, json={"id": 1, "full_name": "octo/widgets"}, headers={"ETag": '"abc"'})
        )
        session = database.session()
        client = CachedHTTPClient(session, settings, "github", transport=None)
        first = client.get_json(url, resource="repo", ttl=60)
        session.commit()
        assert first.status == "current"

        row = session.scalar(select(ProviderCache))
        row.next_refresh_at = row.fetched_at.replace(year=2000)
        session.commit()

        route.mock(
            return_value=httpx.Response(304, headers={"ETag": '"abc"'})
        )
        second = client.get_json(url, resource="repo", ttl=60, force=True)
        session.commit()
        assert second.payload == {"id": 1, "full_name": "octo/widgets"}
        assert second.status == "current"
        assert second.from_cache is True
        session.close()


def test_stale_payload_survives_provider_outage(settings, database):
    url = "https://api.github.com/repos/octo/widgets"
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(url).mock(return_value=httpx.Response(200, json={"id": 1}))
        session = database.session()
        client = CachedHTTPClient(session, settings, "github")
        assert client.get_json(url, resource="repo", ttl=0).payload == {"id": 1}
        session.commit()

        route.mock(side_effect=httpx.ConnectError("boom"))
        result = client.get_json(url, resource="repo", ttl=0, force=True)
        session.commit()
        assert result.payload == {"id": 1}
        assert result.is_stale is True
        assert result.status == "stale"
        session.close()


def test_rate_limit_headers_are_persisted_and_raise(settings, database):
    url = "https://api.github.com/repos/octo/widgets"
    with respx.mock(assert_all_called=False) as mock:
        mock.get(url).mock(
            return_value=httpx.Response(
                429,
                json={"message": "API rate limit exceeded"},
                headers={
                    "x-ratelimit-remaining": "0",
                    "x-ratelimit-limit": "5000",
                    "x-ratelimit-reset": "9999999999",
                    "x-ratelimit-resource": "core",
                },
            )
        )
        session = database.session()
        client = CachedHTTPClient(session, settings, "github")
        with pytest.raises(RateLimitError):
            client.get_json(url, resource="repo", ttl=60, allow_stale=False)
        session.commit()
        state = session.scalar(select(ProviderState))
        assert state is not None
        assert state.remaining == 0
        assert state.reset_at is not None
        session.close()


def test_cache_key_includes_authorization_scope(settings, database):
    settings_a = settings.model_copy(update={"github_token": "token-a"})
    settings_b = settings.model_copy(update={"github_token": "token-b"})
    session = database.session()
    client_a = CachedHTTPClient(session, settings_a, "github")
    client_b = CachedHTTPClient(session, settings_b, "github")
    key_a = client_a._cache_key("repo", "https://x", {})
    key_b = client_b._cache_key("repo", "https://x", {})
    assert key_a != key_b
    session.close()
