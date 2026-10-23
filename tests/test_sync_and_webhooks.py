from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import respx
from sqlalchemy import select

from app.models import ProviderCache, Repository, SyncJob
from app.services.sync import sync_commit_detail, upsert_repository
from app.utils import auth_scope


def _repo_payload(identifier: int, full_name: str) -> dict:
    owner, name = full_name.split("/", 1)
    return {
        "id": identifier,
        "full_name": full_name,
        "name": name,
        "owner": {"login": owner, "type": "User"},
        "html_url": f"https://github.com/{full_name}",
        "clone_url": f"https://github.com/{full_name}.git",
        "description": "Widget factory",
        "visibility": "public",
        "default_branch": "main",
        "language": "Python",
        "topics": ["widgets"],
        "license": {"name": "MIT License", "spdx_id": "MIT"},
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-02-01T00:00:00Z",
        "archived": False,
        "fork": False,
        "is_template": False,
        "size": 100,
        "stargazers_count": 5,
        "forks_count": 1,
        "subscribers_count": 2,
        "open_issues_count": 0,
        "private": False,
    }


def test_webhook_signature_rejection(client):
    response = client.post(
        "/api/v1/webhooks/github",
        content=b'{"zen":"hi"}',
        headers={
            "x-github-event": "ping",
            "x-github-delivery": "d1",
            "x-hub-signature-256": "sha256=bad",
            "content-type": "application/json",
        },
    )
    assert response.status_code == 401


def test_webhook_deduplicates_and_enqueues(client, database):
    payload = {
        "action": "opened",
        "repository": _repo_payload(9001, "octo/widgets"),
        "pull_request": {"number": 5},
    }
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(b"webhook-secret", body, hashlib.sha256).hexdigest()
    headers = {
        "x-github-event": "pull_request",
        "x-github-delivery": "delivery-1",
        "x-hub-signature-256": signature,
        "content-type": "application/json",
    }
    first = client.post("/api/v1/webhooks/github", content=body, headers=headers)
    assert first.status_code == 202
    assert first.json()["status"] == "accepted"
    second = client.post("/api/v1/webhooks/github", content=body, headers=headers)
    assert second.status_code == 202
    assert second.json()["status"] == "duplicate"

    session = database.session()
    repo = session.scalar(select(Repository).where(Repository.provider_id == "9001"))
    assert repo is not None
    jobs = session.scalars(select(SyncJob)).all()
    assert any(job.scope == "pull_requests" for job in jobs)
    session.close()


def test_rename_updates_same_internal_id(session):
    original = upsert_repository(session, _repo_payload(77, "octo/old-name"), auth_scope(""))
    internal_id = original.id
    renamed = upsert_repository(session, _repo_payload(77, "octo/new-name"), auth_scope(""))
    assert renamed.id == internal_id
    assert renamed.full_name == "octo/new-name"
    assert renamed.lifecycle == "active"
    assert session.scalar(select(Repository).where(Repository.full_name == "octo/old-name")) is None


def test_commit_detail_never_persists_patches(settings, database):
    raw = {
        "sha": "a" * 40,
        "html_url": "https://github.com/octo/widgets/commit/" + "a" * 40,
        "commit": {
            "message": "change",
            "author": {"name": "Dev", "email": "dev@example.com", "date": "2024-01-01T00:00:00Z"},
            "committer": {"name": "Dev", "email": "dev@example.com", "date": "2024-01-01T00:00:00Z"},
            "verification": {"verified": True, "reason": "valid"},
        },
        "parents": [{"sha": "b" * 40}],
        "stats": {"total": 1, "additions": 2, "deletions": 1},
        "files": [
            {
                "filename": "app.py",
                "status": "modified",
                "additions": 2,
                "deletions": 1,
                "changes": 3,
                "patch": "@@ -1 +1 @@\n-secret\n+new",
            }
        ],
    }
    session = database.session()
    repo = upsert_repository(session, _repo_payload(55, "octo/widgets"), auth_scope(""))
    session.commit()
    url = "https://api.github.com/repos/octo/widgets/commits/" + "a" * 40
    with respx.mock(assert_all_called=False) as mock:
        mock.get(url).mock(return_value=httpx.Response(200, json=raw))
        result = sync_commit_detail(session, settings, repo, "a" * 40)
        session.commit()
    assert result["status"] == "ok"
    cache_rows = session.scalars(select(ProviderCache)).all()
    assert cache_rows
    for row in cache_rows:
        text = json.dumps(row.payload)
        assert "patch" not in text
        assert "@@ -1 +1 @@" not in text
    session.close()


def test_upsert_preserves_private_visibility_and_scope(session):
    repo = upsert_repository(
        session,
        {**_repo_payload(11, "octo/private"), "private": True, "visibility": "private"},
        "token-abc",
    )
    assert repo.visibility == "private"
    assert repo.auth_scope == "token-abc"
    assert repo.provider_id == "11"
