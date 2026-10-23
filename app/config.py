from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Personal Project Index"
    environment: str = "development"
    database_url: str = f"sqlite:///{BASE_DIR / 'data' / 'personal_api.db'}"
    auto_create_tables: bool = True

    api_auth_token: str = ""

    github_token: str = ""
    github_api_url: str = "https://api.github.com"
    github_graphql_url: str = "https://api.github.com/graphql"
    github_orgs: list[str] = []
    github_username: str = ""
    github_webhook_secret: str = ""
    github_timeout: float = 20.0

    allow_arbitrary_remotes: bool = False

    cache_default_ttl: int = 3600
    cache_stale_ttl: int = 60 * 60 * 24 * 7
    user_agent: str = "personal-project-index/1.0"

    worker_poll_seconds: float = 2.0
    worker_max_attempts: int = 5

    frontend_dir: Path = BASE_DIR / "web"

    @field_validator("github_orgs", mode="before")
    @classmethod
    def split_orgs(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def auth_required(self) -> bool:
        return bool(self.api_auth_token)

    @property
    def github_enabled(self) -> bool:
        return bool(self.github_token or self.github_username)


@lru_cache
def get_settings() -> Settings:
    return Settings()
