from __future__ import annotations

from datetime import timedelta

from conftest import AUTH, make_repository
from sqlalchemy import select

from app.models import Commit, Package, Project, PullRequest, Release, Repository
from app.utils import utcnow


def test_health_is_public(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_projects_and_repositories_require_auth(client):
    assert client.get("/api/v1/projects").status_code == 401
    assert client.get("/api/v1/projects", headers=AUTH).status_code == 200


def test_project_and_repository_flow(client, session):
    repo = make_repository(session)
    session.commit()
    project = Project(name="Widgets", slug="widgets", description="A project")
    project.repositories.append(repo)
    session.add(project)
    session.commit()

    projects = client.get("/api/v1/projects", headers=AUTH).json()
    assert projects["pagination"]["total"] == 1
    detail = client.get(f"/api/v1/projects/{project.id}", headers=AUTH).json()
    assert detail["repositories"][0]["full_name"] == "octo/widgets"

    repos = client.get("/api/v1/repositories", headers=AUTH).json()
    assert repos["items"][0]["stars"] == 42
    assert repos["items"][0]["freshness"]["refresh_status"] == "current"
    repo_detail = client.get(f"/api/v1/repositories/{repo.id}", headers=AUTH).json()
    assert repo_detail["links"]["repository"] == "https://github.com/octo/widgets"
    by_name = client.get("/api/v1/repositories?full_name=octo/widgets", headers=AUTH).json()
    assert by_name["items"][0]["id"] == repo.id


def test_private_repository_hidden_without_auth(client, session):
    repo = make_repository(
        session, provider_id="private-1", full_name="octo/secret", name="secret", visibility="private"
    )
    session.commit()
    assert client.get("/api/v1/repositories").status_code == 401
    visible = client.get("/api/v1/repositories", headers=AUTH).json()
    assert visible["items"][0]["full_name"] == "octo/secret"


def test_commit_pagination_and_freshness(client, session):
    repo = make_repository(session)
    now = utcnow()
    for index in range(45):
        session.add(
            Commit(
                repository_id=repo.id,
                sha=f"{index:040x}",
                abbreviated_sha=f"{index:07x}",
                message=f"commit {index}",
                subject=f"commit {index}",
                author_name="Dev",
                authored_at=now - timedelta(minutes=index),
                committed_at=now - timedelta(minutes=index),
                parents=[],
                refresh_status="current",
                fetched_at=now,
            )
        )
    session.commit()
    page = client.get(f"/api/v1/repositories/{repo.id}/commits?page=2&per_page=20", headers=AUTH).json()
    assert page["pagination"]["total"] == 45
    assert page["pagination"]["has_next"] is True
    assert len(page["items"]) == 20
    assert page["items"][0]["freshness"]["refresh_status"] == "current"


def test_stats_releases_and_version_alignment(client, session):
    repo = make_repository(session)
    session.add_all(
        [
            Release(
                repository_id=repo.id,
                provider_id="rel-1",
                tag_name="v1.2.0",
                title="v1.2.0",
                published_at=utcnow(),
                prerelease=False,
                draft=False,
                refresh_status="current",
                fetched_at=utcnow(),
            ),
            Package(
                repository_id=repo.id,
                registry="pypi",
                ecosystem="python",
                name="widgets",
                latest_version="1.2.0",
                latest_stable_version="1.2.0",
                refresh_status="current",
                fetched_at=utcnow(),
            ),
        ]
    )
    session.commit()
    stats = client.get(f"/api/v1/repositories/{repo.id}/stats", headers=AUTH).json()
    assert stats["provider_reported"]["stars"] == 42
    assert stats["locally_indexed"]["releases"] == 1
    assert stats["version_alignment"]["values"]["latest_package_version"] == "1.2.0"

    releases = client.get(f"/api/v1/repositories/{repo.id}/releases", headers=AUTH).json()
    assert releases["items"][0]["tag_name"] == "v1.2.0"
    latest = client.get(f"/api/v1/repositories/{repo.id}/releases/latest", headers=AUTH).json()
    assert latest["tag_name"] == "v1.2.0"


def test_portfolio_summary_uses_local_index(client, session):
    repo = make_repository(session)
    session.add(
        PullRequest(
            repository_id=repo.id,
            number=1,
            title="Add feature",
            state="open",
            updated_at=utcnow(),
            labels=["enhancement"],
        )
    )
    session.commit()
    summary = client.get("/api/v1/summary", headers=AUTH).json()
    assert summary["counts"]["repositories"] == 1
    entry = summary["repositories"][0]
    assert entry["open_pull_requests"] == 1
    assert entry["health"] in {"healthy", "stale", "quiet", "failing", "archived", "unavailable"}


def test_refresh_enqueues_job_and_coalesces(client, session):
    repo = make_repository(session)
    session.commit()
    first = client.post(
        f"/api/v1/repositories/{repo.id}/refresh",
        headers=AUTH,
        json={"scope": "metadata"},
    )
    assert first.status_code == 202
    job_id = first.json()["job"]["id"]
    second = client.post(
        f"/api/v1/repositories/{repo.id}/refresh",
        headers=AUTH,
        json={"scope": "metadata"},
    )
    assert second.json()["job"]["id"] == job_id
    status = client.get(f"/api/v1/jobs/{job_id}", headers=AUTH).json()
    assert status["status"] == "queued"


def test_provider_status_shape(client):
    body = client.get("/api/v1/system/provider-status", headers=AUTH).json()
    for key in ("providers", "cache", "jobs", "webhook", "last_synchronization_at"):
        assert key in body
    assert "hit_ratio" in body["cache"]
