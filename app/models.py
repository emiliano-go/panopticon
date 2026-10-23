from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class FreshnessMixin:
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_successful_fetch_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    refresh_status: Mapped[str] = mapped_column(String(32), default="never_fetched")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Project(TimestampMixin, Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    homepage: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    visibility: Mapped[str] = mapped_column(String(32), default="public", index=True)
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    categories: Mapped[list[str]] = mapped_column(JSON, default=list)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    is_group: Mapped[bool] = mapped_column(Boolean, default=False)

    repositories: Mapped[list[Repository]] = relationship(
        secondary="project_repositories", back_populates="projects"
    )


class Repository(TimestampMixin, FreshnessMixin, Base):
    __tablename__ = "repositories"
    __table_args__ = (
        UniqueConstraint("provider", "provider_id", name="uq_repository_provider_id"),
        UniqueConstraint("provider", "full_name", name="uq_repository_full_name"),
        Index("ix_repositories_owner", "owner"),
        Index("ix_repositories_language", "language"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), default="github", index=True)
    provider_id: Mapped[str] = mapped_column(String(128), index=True)
    auth_scope: Mapped[str] = mapped_column(String(64), default="default")
    full_name: Mapped[str] = mapped_column(String(512))
    owner: Mapped[str] = mapped_column(String(255))
    owner_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    clone_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    homepage: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    visibility: Mapped[str] = mapped_column(String(32), default="public", index=True)
    default_branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    language: Mapped[str | None] = mapped_column(String(64), nullable=True)
    languages: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    topics: Mapped[list[str]] = mapped_column(JSON, default=list)
    license: Mapped[str | None] = mapped_column(String(255), nullable=True)
    license_spdx: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    provider_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    archived: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    fork: Mapped[bool] = mapped_column(Boolean, default=False)
    is_template: Mapped[bool] = mapped_column(Boolean, default=False)
    upstream_full_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    size_kb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stars: Mapped[int] = mapped_column(Integer, default=0)
    forks: Mapped[int] = mapped_column(Integer, default=0)
    watchers: Mapped[int] = mapped_column(Integer, default=0)
    open_issues: Mapped[int] = mapped_column(Integer, default=0)
    contributors_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    features: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    raw_provider: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    lifecycle: Mapped[str] = mapped_column(String(32), default="active", index=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    projects: Mapped[list[Project]] = relationship(
        secondary="project_repositories", back_populates="repositories"
    )
    branches: Mapped[list[RepositoryBranch]] = relationship(
        back_populates="repository", cascade="all, delete-orphan"
    )
    tags: Mapped[list[RepositoryTag]] = relationship(
        back_populates="repository", cascade="all, delete-orphan"
    )
    packages: Mapped[list[Package]] = relationship(back_populates="repository")


class RepositoryBranch(FreshnessMixin, Base):
    __tablename__ = "repository_branches"
    __table_args__ = (UniqueConstraint("repository_id", "name", name="uq_branch_repo_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    head_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    head_committed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    latest_commit_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    ahead: Mapped[int | None] = mapped_column(Integer, nullable=True)
    behind: Mapped[int | None] = mapped_column(Integer, nullable=True)
    protected: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    required_checks: Mapped[list[str]] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    repository: Mapped[Repository] = relationship(back_populates="branches")


class RepositoryTag(FreshnessMixin, Base):
    __tablename__ = "repository_tags"
    __table_args__ = (UniqueConstraint("repository_id", "name", name="uq_tag_repo_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    target_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tag_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tagger_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tagger_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tagger_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    release_id: Mapped[int | None] = mapped_column(
        ForeignKey("releases.id", ondelete="SET NULL"), nullable=True
    )
    is_semver: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    repository: Mapped[Repository] = relationship(back_populates="tags")


class Commit(FreshnessMixin, Base):
    __tablename__ = "commits"
    __table_args__ = (
        UniqueConstraint("repository_id", "sha", name="uq_commit_repo_sha"),
        Index("ix_commits_repo_date", "repository_id", "committed_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    sha: Mapped[str] = mapped_column(String(64), index=True)
    abbreviated_sha: Mapped[str | None] = mapped_column(String(16), nullable=True)
    message: Mapped[str] = mapped_column(Text)
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    author_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    author_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    authored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    committer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    committer_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    parents: Mapped[list[str]] = mapped_column(JSON, default=list)
    html_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    verified: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    verification: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    changed_files: Mapped[int | None] = mapped_column(Integer, nullable=True)
    additions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deletions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_changes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pull_request_numbers: Mapped[list[int]] = mapped_column(JSON, default=list)


class PullRequest(FreshnessMixin, Base):
    __tablename__ = "pull_requests"
    __table_args__ = (UniqueConstraint("repository_id", "number", name="uq_pr_repo_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    author: Mapped[str | None] = mapped_column(String(255), nullable=True)
    state: Mapped[str] = mapped_column(String(32), default="open", index=True)
    is_draft: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    merged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    merge_commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    base_branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    head_branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    head_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    review_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    requested_reviewers: Mapped[list[str]] = mapped_column(JSON, default=list)
    labels: Mapped[list[str]] = mapped_column(JSON, default=list)
    linked_issues: Mapped[list[int]] = mapped_column(JSON, default=list)
    changed_files: Mapped[int | None] = mapped_column(Integer, nullable=True)
    additions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deletions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    checks_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    milestone: Mapped[str | None] = mapped_column(String(255), nullable=True)
    assignees: Mapped[list[str]] = mapped_column(JSON, default=list)


class Issue(FreshnessMixin, Base):
    __tablename__ = "issues"
    __table_args__ = (UniqueConstraint("repository_id", "number", name="uq_issue_repo_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    author: Mapped[str | None] = mapped_column(String(255), nullable=True)
    state: Mapped[str] = mapped_column(String(32), default="open", index=True)
    labels: Mapped[list[str]] = mapped_column(JSON, default=list)
    assignees: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    milestone: Mapped[str | None] = mapped_column(String(255), nullable=True)
    comments_count: Mapped[int] = mapped_column(Integer, default=0)
    linked_pull_requests: Mapped[list[int]] = mapped_column(JSON, default=list)


class Release(FreshnessMixin, Base):
    __tablename__ = "releases"
    __table_args__ = (
        UniqueConstraint("repository_id", "provider_id", name="uq_release_repo_provider"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    provider_id: Mapped[str] = mapped_column(String(128))
    tag_name: Mapped[str] = mapped_column(String(255), index=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    draft: Mapped[bool] = mapped_column(Boolean, default=False)
    prerelease: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    assets: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)


class Package(TimestampMixin, FreshnessMixin, Base):
    __tablename__ = "packages"
    __table_args__ = (UniqueConstraint("registry", "name", name="uq_package_registry_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int | None] = mapped_column(
        ForeignKey("repositories.id", ondelete="SET NULL"), nullable=True, index=True
    )
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    registry: Mapped[str] = mapped_column(String(32), index=True)
    ecosystem: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(512), index=True)
    url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    latest_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    latest_stable_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    license: Mapped[str | None] = mapped_column(String(255), nullable=True)
    maintainers: Mapped[list[str]] = mapped_column(JSON, default=list)
    downloads: Mapped[int | None] = mapped_column(Integer, nullable=True)
    downloads_period: Mapped[str | None] = mapped_column(String(32), nullable=True)
    deprecated: Mapped[bool] = mapped_column(Boolean, default=False)
    yanked: Mapped[bool] = mapped_column(Boolean, default=False)
    package_type: Mapped[str | None] = mapped_column(String(32), nullable=True)

    repository: Mapped[Repository | None] = relationship(back_populates="packages")
    versions: Mapped[list[PackageVersion]] = relationship(
        back_populates="package", cascade="all, delete-orphan"
    )


class PackageVersion(Base):
    __tablename__ = "package_versions"
    __table_args__ = (UniqueConstraint("package_id", "version", name="uq_version_package_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    package_id: Mapped[int] = mapped_column(
        ForeignKey("packages.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[str] = mapped_column(String(128))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    yanked: Mapped[bool] = mapped_column(Boolean, default=False)
    deprecated: Mapped[bool] = mapped_column(Boolean, default=False)
    is_prerelease: Mapped[bool] = mapped_column(Boolean, default=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    package: Mapped[Package] = relationship(back_populates="versions")


class WorkflowRun(FreshnessMixin, Base):
    __tablename__ = "workflow_runs"
    __table_args__ = (
        UniqueConstraint("repository_id", "provider_id", name="uq_workflow_repo_provider"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    provider_id: Mapped[str] = mapped_column(String(128))
    workflow_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    workflow_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    run_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    conclusion: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    head_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    html_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    failed_jobs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)


class Observation(Base):
    __tablename__ = "observations"
    __table_args__ = (Index("ix_observation_repo_kind_time", "repository_id", "kind", "observed_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int | None] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), nullable=True
    )
    package_id: Mapped[int | None] = mapped_column(
        ForeignKey("packages.id", ondelete="CASCADE"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(64), index=True)
    value: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(32), default="provider")
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)


class ProviderCache(Base):
    __tablename__ = "provider_cache"
    __table_args__ = (UniqueConstraint("cache_key", name="uq_provider_cache_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)
    resource: Mapped[str] = mapped_column(String(255), index=True)
    scope: Mapped[str] = mapped_column(String(64), default="default")
    cache_key: Mapped[str] = mapped_column(String(512))
    etag: Mapped[str | None] = mapped_column(String(512), nullable=True)
    last_modified: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payload: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="never_fetched")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_successful_fetch_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_refresh_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    hits: Mapped[int] = mapped_column(Integer, default=0)
    misses: Mapped[int] = mapped_column(Integer, default=0)


class SyncJob(TimestampMixin, Base):
    __tablename__ = "sync_jobs"
    __table_args__ = (Index("ix_sync_jobs_status_priority", "status", "priority"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int | None] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), nullable=True, index=True
    )
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )
    scope: Mapped[str] = mapped_column(String(64))
    priority: Mapped[int] = mapped_column(Integer, default=50)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"

    id: Mapped[int] = mapped_column(primary_key=True)
    delivery_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    event: Mapped[str] = mapped_column(String(64), index=True)
    action: Mapped[str | None] = mapped_column(String(64), nullable=True)
    repository_id: Mapped[int | None] = mapped_column(
        ForeignKey("repositories.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), default="received")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Integration(TimestampMixin, Base):
    __tablename__ = "integrations"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    credential_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)


class ProviderState(Base):
    __tablename__ = "provider_state"
    __table_args__ = (UniqueConstraint("provider", "family", name="uq_provider_state_family"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(32))
    family: Mapped[str] = mapped_column(String(32))
    limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    remaining: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class ProjectRepository(Base):
    __tablename__ = "project_repositories"

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), primary_key=True
    )


project_repositories = ProjectRepository.__table__
