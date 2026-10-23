from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import Database
from app.main import create_app
from app.models import Repository

AUTH = {"Authorization": "Bearer test-token"}


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        auto_create_tables=True,
        api_auth_token="test-token",
        github_token="",
        github_username="",
        github_webhook_secret="webhook-secret",
        frontend_dir=tmp_path / "web",
        cache_default_ttl=60,
    )


@pytest.fixture
def database(settings: Settings) -> Database:
    db = Database(settings.database_url)
    db.create_all()
    yield db
    db.dispose()


@pytest.fixture
def app(settings: Settings, database: Database):
    return create_app(settings, database)


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app)


@pytest.fixture
def session(database: Database):
    session = database.session()
    try:
        yield session
        session.commit()
    finally:
        session.close()


def make_repository(session, **overrides) -> Repository:
    values = {
        "provider": "github",
        "provider_id": "12345",
        "full_name": "octo/widgets",
        "owner": "octo",
        "name": "widgets",
        "url": "https://github.com/octo/widgets",
        "description": "Widget factory",
        "visibility": "public",
        "default_branch": "main",
        "language": "Python",
        "topics": ["widgets", "tools"],
        "stars": 42,
        "forks": 7,
        "open_issues": 3,
        "lifecycle": "active",
        "refresh_status": "current",
    }
    values.update(overrides)
    repo = Repository(**values)
    session.add(repo)
    session.flush()
    return repo
