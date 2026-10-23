from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    name: str
    slug: str | None = None
    description: str | None = None
    owner: str | None = None
    homepage: str | None = None
    visibility: str = "public"
    aliases: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    is_group: bool = False
    repository_ids: list[int] = Field(default_factory=list)
    repository_urls: list[str] = Field(default_factory=list)


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    owner: str | None = None
    homepage: str | None = None
    visibility: str | None = None
    aliases: list[str] | None = None
    tags: list[str] | None = None
    categories: list[str] | None = None
    metadata: dict[str, Any] | None = None
    is_group: bool | None = None
    repository_ids: list[int] | None = None


class RepositoryCreate(BaseModel):
    url: str


class PackageCreate(BaseModel):
    registry: str
    name: str
    repository_id: int | None = None
    project_id: int | None = None


class RefreshRequest(BaseModel):
    scope: str = "complete"
    force: bool = False
    page: int | None = None
    per_page: int | None = None
    sha: str | None = None
