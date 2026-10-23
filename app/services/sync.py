from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import (
    Commit,
    Issue,
    Observation,
    Package,
    PackageVersion,
    Project,
    PullRequest,
    Release,
    Repository,
    RepositoryBranch,
    RepositoryTag,
    SyncJob,
    WorkflowRun,
)
from app.providers.base import NotFound, ProviderError, RateLimitError, UnsupportedCapability
from app.providers.github import GitHubProvider
from app.providers.registry import RegistryProvider
from app.utils import (
    auth_scope,
    is_prerelease,
    is_semver,
    parse_dt,
    slugify,
    strip_patches,
    utcnow,
)

log = logging.getLogger(__name__)

SCOPES = {
    "metadata",
    "branches",
    "tags",
    "commits",
    "releases",
    "pull_requests",
    "issues",
    "workflows",
    "security",
    "packages",
    "activity",
    "complete",
}

SCOPE_PRIORITY = {"metadata": 80, "complete": 70, "branches": 60, "pulls": 60, "releases": 55}


def _login(value: Any) -> str | None:
    if isinstance(value, dict):
        return value.get("login") or value.get("name")
    return None


def _names(values: Iterable[Any]) -> list[str]:
    return [name for name in (_login(value) for value in values or []) if name]


def normalize_repository(raw: dict[str, Any], scope: str) -> dict[str, Any]:
    license_info = raw.get("license") or {}
    owner = raw.get("owner") or {}
    parent = raw.get("parent") or {}
    features = {
        key: raw.get(key)
        for key in (
            "has_issues",
            "has_projects",
            "has_wiki",
            "has_pages",
            "has_discussions",
            "allow_forking",
            "web_commit_signoff_required",
        )
    }
    safe_raw = strip_patches(
        {
            key: raw.get(key)
            for key in (
                "id",
                "name",
                "full_name",
                "private",
                "visibility",
                "fork",
                "archived",
                "is_template",
                "default_branch",
                "created_at",
                "updated_at",
                "pushed_at",
                "size",
                "stargazers_count",
                "forks_count",
                "subscribers_count",
                "open_issues_count",
                "topics",
                "license",
                "owner",
                "parent",
                "source",
                "node_id",
            )
        }
    )
    return {
        "provider": "github",
        "provider_id": str(raw.get("id")),
        "auth_scope": scope,
        "full_name": raw.get("full_name") or raw.get("name"),
        "owner": owner.get("login") or (raw.get("full_name") or "/").split("/")[0],
        "owner_type": owner.get("type"),
        "name": raw.get("name"),
        "description": raw.get("description"),
        "url": raw.get("html_url"),
        "clone_url": raw.get("clone_url"),
        "homepage": raw.get("homepage"),
        "visibility": raw.get("visibility") or ("private" if raw.get("private") else "public"),
        "default_branch": raw.get("default_branch"),
        "language": raw.get("language"),
        "topics": raw.get("topics") or [],
        "license": license_info.get("name"),
        "license_spdx": license_info.get("spdx_id"),
        "provider_created_at": parse_dt(raw.get("created_at")),
        "provider_updated_at": parse_dt(raw.get("updated_at") or raw.get("pushed_at")),
        "archived": bool(raw.get("archived")),
        "fork": bool(raw.get("fork")),
        "is_template": bool(raw.get("is_template")),
        "upstream_full_name": parent.get("full_name") or (raw.get("source") or {}).get("full_name"),
        "size_kb": raw.get("size"),
        "stars": raw.get("stargazers_count") or 0,
        "forks": raw.get("forks_count") or 0,
        "watchers": raw.get("subscribers_count") or raw.get("watchers_count") or 0,
        "open_issues": raw.get("open_issues_count") or 0,
        "features": features,
        "raw_provider": safe_raw,
        "lifecycle": "active",
        "last_seen_at": utcnow(),
    }


def normalize_commit(raw: dict[str, Any]) -> dict[str, Any]:
    meta = raw.get("commit") or {}
    author = meta.get("author") or {}
    committer = meta.get("committer") or {}
    verification = meta.get("verification") or {}
    stats = raw.get("stats") or {}
    pull_numbers = []
    for ref in raw.get("pull_requests") or []:
        number = ref.get("number")
        if isinstance(number, int):
            pull_numbers.append(number)
    message = meta.get("message") or ""
    return {
        "sha": raw.get("sha"),
        "abbreviated_sha": (raw.get("sha") or "")[:7],
        "message": message,
        "subject": message.splitlines()[0] if message else None,
        "author_name": author.get("name"),
        "author_email": author.get("email"),
        "authored_at": parse_dt(author.get("date")),
        "committer_name": committer.get("name"),
        "committer_email": committer.get("email"),
        "committed_at": parse_dt(committer.get("date")),
        "parents": [p.get("sha") for p in raw.get("parents") or [] if p.get("sha")],
        "html_url": raw.get("html_url"),
        "verified": verification.get("verified"),
        "verification": verification,
        "changed_files": stats.get("total") or len(raw.get("files") or []) or None,
        "additions": stats.get("additions"),
        "deletions": stats.get("deletions"),
        "total_changes": stats.get("total"),
        "pull_request_numbers": pull_numbers,
        "source_updated_at": parse_dt(committer.get("date")),
    }


def normalize_branch(raw: dict[str, Any]) -> dict[str, Any]:
    commit = raw.get("commit") or {}
    commit_meta = commit.get("commit") or {}
    return {
        "name": raw.get("name"),
        "head_sha": commit.get("sha"),
        "head_committed_at": parse_dt(
            (commit_meta.get("committer") or {}).get("date")
            or (commit_meta.get("author") or {}).get("date")
        ),
        "latest_commit_message": commit_meta.get("message"),
        "protected": raw.get("protected"),
    }


def normalize_tag(raw: dict[str, Any]) -> dict[str, Any]:
    name = raw.get("name") or ""
    return {
        "name": name,
        "target_sha": (raw.get("commit") or {}).get("sha"),
        "tag_type": raw.get("tag_type"),
        "tagger_name": raw.get("tagger_name"),
        "tagger_email": raw.get("tagger_email"),
        "tagger_date": parse_dt(raw.get("tagger_date")),
        "is_semver": is_semver(name),
    }


def normalize_release(raw: dict[str, Any], *, include_notes: bool = False) -> dict[str, Any]:
    assets = [
        {
            "id": asset.get("id"),
            "name": asset.get("name"),
            "size": asset.get("size"),
            "content_type": asset.get("content_type"),
            "download_count": asset.get("download_count"),
            "download_url": asset.get("browser_download_url"),
        }
        for asset in raw.get("assets") or []
    ]
    return {
        "provider_id": str(raw.get("id")),
        "tag_name": raw.get("tag_name"),
        "title": raw.get("name"),
        "url": raw.get("html_url"),
        "published_at": parse_dt(raw.get("published_at")),
        "created_at": parse_dt(raw.get("created_at")),
        "draft": bool(raw.get("draft")),
        "prerelease": bool(raw.get("prerelease")),
        "notes": raw.get("body") if include_notes else None,
        "target_sha": raw.get("target_commitish") if _looks_like_sha(raw.get("target_commitish")) else None,
        "assets": assets,
        "source_updated_at": parse_dt(raw.get("published_at") or raw.get("created_at")),
    }


def _looks_like_sha(value: Any) -> bool:
    return isinstance(value, str) and len(value) in {40, 64} and all(c in "0123456789abcdef" for c in value.lower())


def normalize_pull_request(raw: dict[str, Any]) -> dict[str, Any]:
    merged = bool(raw.get("merged_at"))
    return {
        "number": raw.get("number"),
        "title": raw.get("title") or "",
        "url": raw.get("html_url"),
        "author": _login(raw.get("user")),
        "state": "merged" if merged else (raw.get("state") or "open"),
        "is_draft": bool(raw.get("draft")),
        "created_at": parse_dt(raw.get("created_at")),
        "updated_at": parse_dt(raw.get("updated_at")),
        "merged_at": parse_dt(raw.get("merged_at")),
        "closed_at": parse_dt(raw.get("closed_at")),
        "merge_commit_sha": raw.get("merge_commit_sha"),
        "base_branch": (raw.get("base") or {}).get("ref"),
        "head_branch": (raw.get("head") or {}).get("ref"),
        "head_sha": (raw.get("head") or {}).get("sha"),
        "review_status": None,
        "requested_reviewers": _names(raw.get("requested_reviewers")),
        "labels": [label.get("name") for label in raw.get("labels") or [] if label.get("name")],
        "linked_issues": [],
        "changed_files": raw.get("changed_files"),
        "additions": raw.get("additions"),
        "deletions": raw.get("deletions"),
        "milestone": (raw.get("milestone") or {}).get("title"),
        "assignees": _names(raw.get("assignees")),
        "source_updated_at": parse_dt(raw.get("updated_at")),
    }


def normalize_issue(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "number": raw.get("number"),
        "title": raw.get("title") or "",
        "url": raw.get("html_url"),
        "author": _login(raw.get("user")),
        "state": raw.get("state") or "open",
        "labels": [label.get("name") for label in raw.get("labels") or [] if label.get("name")],
        "assignees": _names(raw.get("assignees")),
        "created_at": parse_dt(raw.get("created_at")),
        "updated_at": parse_dt(raw.get("updated_at")),
        "closed_at": parse_dt(raw.get("closed_at")),
        "milestone": (raw.get("milestone") or {}).get("title"),
        "comments_count": raw.get("comments") or 0,
        "linked_pull_requests": [],
        "source_updated_at": parse_dt(raw.get("updated_at")),
    }


def normalize_workflow_run(raw: dict[str, Any], failed_jobs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    started = parse_dt(raw.get("run_started_at") or raw.get("created_at"))
    completed = parse_dt(raw.get("updated_at"))
    duration = int((completed - started).total_seconds()) if started and completed and completed >= started else None
    return {
        "provider_id": str(raw.get("id")),
        "workflow_name": raw.get("name"),
        "workflow_id": str(raw.get("workflow_id")) if raw.get("workflow_id") is not None else None,
        "run_number": raw.get("run_number"),
        "status": raw.get("status"),
        "conclusion": raw.get("conclusion"),
        "branch": raw.get("head_branch"),
        "head_sha": raw.get("head_sha"),
        "event": raw.get("event"),
        "started_at": started,
        "completed_at": completed,
        "duration_seconds": duration,
        "html_url": raw.get("html_url"),
        "failed_jobs": failed_jobs or [],
        "source_updated_at": completed,
    }


def upsert_repository(
    session: Session, raw: dict[str, Any], scope: str, *, lifecycle: str | None = None
) -> Repository:
    data = normalize_repository(raw, scope)
    repo = session.scalar(
        select(Repository).where(
            Repository.provider == "github", Repository.provider_id == data["provider_id"]
        )
    )
    if repo is None and data["full_name"]:
        repo = session.scalar(
            select(Repository).where(
                Repository.provider == "github", Repository.full_name == data["full_name"]
            )
        )
    if repo is None:
        repo = Repository(provider="github", provider_id=data["provider_id"], full_name=data["full_name"], owner=data["owner"], name=data["name"])
        session.add(repo)
    for key, value in data.items():
        setattr(repo, key, value)
    if lifecycle:
        repo.lifecycle = lifecycle
    now = utcnow()
    repo.fetched_at = now
    repo.last_successful_fetch_at = now
    repo.refresh_status = "current"
    repo.last_error = None
    if repo.provider_updated_at:
        repo.source_updated_at = repo.provider_updated_at
    session.flush()
    return repo


def record_observation(session: Session, repo: Repository, kind: str, value: int | None) -> None:
    if value is None:
        return
    last = session.scalar(
        select(Observation)
        .where(Observation.repository_id == repo.id, Observation.kind == kind)
        .order_by(Observation.observed_at.desc())
    )
    if last and last.value == value:
        return
    session.add(
        Observation(
            repository_id=repo.id,
            kind=kind,
            value=value,
            source="provider",
            observed_at=utcnow(),
            metadata_={},
        )
    )


def sync_metadata(session: Session, settings: Settings, repo: Repository, *, force: bool = False) -> dict[str, Any]:
    provider = GitHubProvider(session, settings, repo.auth_scope)
    try:
        result = provider.repository(repo.full_name, force=force)
        fresh = upsert_repository(session, result.payload, repo.auth_scope)
        languages = provider.languages(repo.full_name)
        if isinstance(languages.payload, dict):
            fresh.languages = languages.payload
        try:
            contributors = provider.contributors(repo.full_name)
            if isinstance(contributors.payload, list):
                fresh.contributors_count = len(contributors.payload)
        except ProviderError:
            pass
        record_observation(session, fresh, "stars", fresh.stars)
        record_observation(session, fresh, "forks", fresh.forks)
        session.flush()
        return {"repository": fresh.full_name, "status": "ok"}
    finally:
        provider.close()


def sync_branches(session: Session, settings: Settings, repo: Repository, *, force: bool = False) -> dict[str, Any]:
    provider = GitHubProvider(session, settings, repo.auth_scope)
    try:
        raw_branches = provider.branches(repo.full_name, force=force)
        seen: set[str] = set()
        for raw in raw_branches:
            data = normalize_branch(raw)
            if not data["name"]:
                continue
            seen.add(data["name"])
            branch = session.scalar(
                select(RepositoryBranch).where(
                    RepositoryBranch.repository_id == repo.id, RepositoryBranch.name == data["name"]
                )
            )
            if branch is None:
                branch = RepositoryBranch(repository_id=repo.id, name=data["name"])
                session.add(branch)
            for key, value in data.items():
                setattr(branch, key, value)
            branch.is_default = data["name"] == repo.default_branch
            branch.fetched_at = utcnow()
            branch.last_successful_fetch_at = branch.fetched_at
            branch.refresh_status = "current"
            branch.last_error = None
            if branch.is_default and not branch.protected:
                try:
                    protection = provider.branch_protection(repo.full_name, branch.name)
                    branch.protected = True
                    checks = (protection.payload or {}).get("required_status_checks") or {}
                    branch.required_checks = [c.get("context") for c in checks.get("contexts") or []]
                except ProviderError:
                    branch.protected = bool(branch.protected)
        for stale in session.scalars(
            select(RepositoryBranch).where(RepositoryBranch.repository_id == repo.id)
        ).all():
            if stale.name not in seen:
                stale.refresh_status = "stale"
        session.flush()
        return {"branches": len(seen)}
    finally:
        provider.close()


def sync_tags(session: Session, settings: Settings, repo: Repository, *, force: bool = False) -> dict[str, Any]:
    provider = GitHubProvider(session, settings, repo.auth_scope)
    try:
        raw_tags = provider.tags(repo.full_name, force=force)
        seen: set[str] = set()
        for raw in raw_tags:
            data = normalize_tag(raw)
            if not data["name"]:
                continue
            seen.add(data["name"])
            tag = session.scalar(
                select(RepositoryTag).where(
                    RepositoryTag.repository_id == repo.id, RepositoryTag.name == data["name"]
                )
            )
            if tag is None:
                tag = RepositoryTag(repository_id=repo.id, name=data["name"])
                session.add(tag)
            for key, value in data.items():
                setattr(tag, key, value)
            tag.fetched_at = utcnow()
            tag.last_successful_fetch_at = tag.fetched_at
            tag.refresh_status = "current"
            tag.last_error = None
        for stale in session.scalars(
            select(RepositoryTag).where(RepositoryTag.repository_id == repo.id)
        ).all():
            if stale.name not in seen:
                stale.refresh_status = "stale"
        session.flush()
        return {"tags": len(seen)}
    finally:
        provider.close()


def _upsert_commit(session: Session, repo: Repository, raw: dict[str, Any]) -> Commit | None:
    data = normalize_commit(raw)
    if not data.get("sha"):
        return None
    commit = session.scalar(
        select(Commit).where(Commit.repository_id == repo.id, Commit.sha == data["sha"])
    )
    if commit is None:
        commit = Commit(repository_id=repo.id, sha=data["sha"], message=data.get("message") or "")
        session.add(commit)
    for key, value in data.items():
        if key in {"changed_files", "additions", "deletions", "total_changes"} and value is None:
            continue
        setattr(commit, key, value)
    commit.fetched_at = utcnow()
    commit.last_successful_fetch_at = commit.fetched_at
    commit.refresh_status = "current"
    commit.last_error = None
    return commit


def sync_commits(
    session: Session,
    settings: Settings,
    repo: Repository,
    *,
    page: int = 1,
    per_page: int = 30,
    sha: str | None = None,
    author: str | None = None,
    since: str | None = None,
    until: str | None = None,
    path: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    provider = GitHubProvider(session, settings, repo.auth_scope)
    try:
        result = provider.commits_page(
            repo.full_name,
            sha=sha or repo.default_branch,
            author=author,
            since=since,
            until=until,
            path=path,
            page=page,
            per_page=per_page,
            force=force,
        )
        raw_items = result.payload if isinstance(result.payload, list) else []
        count = 0
        for raw in raw_items:
            if _upsert_commit(session, repo, raw):
                count += 1
        session.flush()
        return {"commits": count, "page": page, "next_page": bool(result.next_url)}
    finally:
        provider.close()


def sync_commit_detail(session: Session, settings: Settings, repo: Repository, sha: str) -> dict[str, Any]:
    provider = GitHubProvider(session, settings, repo.auth_scope)
    try:
        result = provider.commit(repo.full_name, sha)
        commit = _upsert_commit(session, repo, result.payload)
        session.flush()
        return {"commit": commit.sha if commit else sha, "status": "ok"}
    finally:
        provider.close()


def sync_pull_requests(session: Session, settings: Settings, repo: Repository, *, force: bool = False) -> dict[str, Any]:
    provider = GitHubProvider(session, settings, repo.auth_scope)
    try:
        result = provider.pull_requests(repo.full_name, state="all", per_page=50, force=force)
        raw_items = result.payload if isinstance(result.payload, list) else []
        count = 0
        for raw in raw_items:
            number = raw.get("number")
            if number is None:
                continue
            data = normalize_pull_request(raw)
            pr = session.scalar(
                select(PullRequest).where(
                    PullRequest.repository_id == repo.id, PullRequest.number == number
                )
            )
            if pr is None:
                pr = PullRequest(repository_id=repo.id, number=number, title=data["title"])
                session.add(pr)
            for key, value in data.items():
                setattr(pr, key, value)
            pr.fetched_at = utcnow()
            pr.last_successful_fetch_at = pr.fetched_at
            pr.refresh_status = "current"
            pr.last_error = None
            count += 1
        session.flush()
        return {"pull_requests": count}
    finally:
        provider.close()


def sync_issues(session: Session, settings: Settings, repo: Repository, *, force: bool = False) -> dict[str, Any]:
    provider = GitHubProvider(session, settings, repo.auth_scope)
    try:
        result = provider.issues(repo.full_name, state="all", per_page=50, force=force)
        raw_items = result.payload if isinstance(result.payload, list) else []
        count = 0
        for raw in raw_items:
            if raw.get("pull_request"):
                continue
            number = raw.get("number")
            if number is None:
                continue
            data = normalize_issue(raw)
            issue = session.scalar(
                select(Issue).where(Issue.repository_id == repo.id, Issue.number == number)
            )
            if issue is None:
                issue = Issue(repository_id=repo.id, number=number, title=data["title"])
                session.add(issue)
            for key, value in data.items():
                setattr(issue, key, value)
            issue.fetched_at = utcnow()
            issue.last_successful_fetch_at = issue.fetched_at
            issue.refresh_status = "current"
            issue.last_error = None
            count += 1
        session.flush()
        return {"issues": count}
    finally:
        provider.close()


def sync_releases(session: Session, settings: Settings, repo: Repository, *, force: bool = False) -> dict[str, Any]:
    provider = GitHubProvider(session, settings, repo.auth_scope)
    try:
        raw_items = provider.releases(repo.full_name, force=force)
        count = 0
        for raw in raw_items:
            data = normalize_release(raw, include_notes=False)
            if not data.get("provider_id"):
                continue
            release = session.scalar(
                select(Release).where(
                    Release.repository_id == repo.id, Release.provider_id == data["provider_id"]
                )
            )
            if release is None:
                release = Release(
                    repository_id=repo.id,
                    provider_id=data["provider_id"],
                    tag_name=data["tag_name"] or "",
                )
                session.add(release)
            for key, value in data.items():
                setattr(release, key, value)
            release.fetched_at = utcnow()
            release.last_successful_fetch_at = release.fetched_at
            release.refresh_status = "current"
            release.last_error = None
            count += 1
        session.flush()
        for tag in session.scalars(
            select(RepositoryTag).where(RepositoryTag.repository_id == repo.id)
        ).all():
            release = session.scalar(
                select(Release).where(
                    Release.repository_id == repo.id, Release.tag_name == tag.name
                )
            )
            tag.release_id = release.id if release else None
        session.flush()
        return {"releases": count}
    finally:
        provider.close()


def sync_workflows(session: Session, settings: Settings, repo: Repository, *, force: bool = False) -> dict[str, Any]:
    provider = GitHubProvider(session, settings, repo.auth_scope)
    try:
        result = provider.workflow_runs(repo.full_name, per_page=30, force=force)
        payload = result.payload or {}
        raw_items = payload.get("workflow_runs", []) if isinstance(payload, dict) else []
        count = 0
        for raw in raw_items:
            failed_jobs: list[dict[str, Any]] = []
            if raw.get("conclusion") == "failure" and raw.get("id"):
                try:
                    jobs = provider.workflow_run_jobs(repo.full_name, raw["id"])
                    failed_jobs = [
                        {
                            "name": job.get("name"),
                            "conclusion": job.get("conclusion"),
                            "url": job.get("html_url"),
                            "failed_steps": [
                                step.get("name")
                                for step in job.get("steps") or []
                                if step.get("conclusion") == "failure"
                            ],
                        }
                        for job in jobs
                        if job.get("conclusion") in {"failure", "timed_out"}
                    ]
                except ProviderError:
                    failed_jobs = []
            data = normalize_workflow_run(raw, failed_jobs)
            run = session.scalar(
                select(WorkflowRun).where(
                    WorkflowRun.repository_id == repo.id,
                    WorkflowRun.provider_id == data["provider_id"],
                )
            )
            if run is None:
                run = WorkflowRun(repository_id=repo.id, provider_id=data["provider_id"])
                session.add(run)
            for key, value in data.items():
                setattr(run, key, value)
            run.fetched_at = utcnow()
            run.last_successful_fetch_at = run.fetched_at
            run.refresh_status = "current"
            run.last_error = None
            count += 1
        session.flush()
        return {"workflow_runs": count}
    finally:
        provider.close()


def sync_packages(session: Session, settings: Settings, repo: Repository, *, force: bool = False) -> dict[str, Any]:
    packages = session.scalars(select(Package).where(Package.repository_id == repo.id)).all()
    synced = 0
    errors: list[str] = []
    for package in packages:
        try:
            provider = RegistryProvider(session, settings, package.registry)
            try:
                data = provider.fetch(package.name, force=force)
            finally:
                provider.close()
            _apply_package_data(session, package, data)
            synced += 1
        except (ProviderError, UnsupportedCapability) as exc:
            package.refresh_status = "unavailable"
            package.last_error = str(exc)
            errors.append(f"{package.registry}:{package.name}: {exc}")
    session.flush()
    return {"packages": synced, "errors": errors}


def _apply_package_data(session: Session, package: Package, data: dict[str, Any]) -> None:
    now = utcnow()
    for key in (
        "latest_version",
        "latest_stable_version",
        "description",
        "license",
        "maintainers",
        "downloads",
        "downloads_period",
        "deprecated",
        "yanked",
        "url",
    ):
        if key in data:
            setattr(package, key, data[key])
    package.published_at = parse_dt(data.get("published_at"))
    package.fetched_at = now
    package.last_successful_fetch_at = now
    package.refresh_status = "current"
    package.last_error = None
    versions = data.get("versions") or []
    for item in versions:
        version = session.scalar(
            select(PackageVersion).where(
                PackageVersion.package_id == package.id,
                PackageVersion.version == item["version"],
            )
        )
        if version is None:
            version = PackageVersion(package_id=package.id, version=item["version"])
            session.add(version)
        version.published_at = parse_dt(item.get("published_at"))
        version.yanked = bool(item.get("yanked"))
        version.deprecated = bool(item.get("deprecated"))
        version.is_prerelease = bool(item.get("prerelease")) or is_prerelease(item["version"])
        version.metadata_ = item.get("metadata") or {}
        version.fetched_at = now
    if package.downloads is not None:
        record_observation(session, None, "downloads", None) if False else None
        last = session.scalar(
            select(Observation)
            .where(Observation.package_id == package.id, Observation.kind == "downloads")
            .order_by(Observation.observed_at.desc())
        )
        if not last or last.value != package.downloads:
            session.add(
                Observation(
                    package_id=package.id,
                    kind="downloads",
                    value=package.downloads,
                    source=package.registry,
                    observed_at=now,
                )
            )
    session.flush()


def sync_security(session: Session, settings: Settings, repo: Repository, *, force: bool = False) -> dict[str, Any]:
    provider = GitHubProvider(session, settings, repo.auth_scope)
    result: dict[str, Any] = {"dependabot_alerts": [], "advisories": [], "dependency_summary": None, "capability": "available"}
    try:
        try:
            alerts = provider.dependabot_alerts(repo.full_name)
            result["dependabot_alerts"] = [
                {
                    "number": alert.get("number"),
                    "state": alert.get("state"),
                    "severity": (alert.get("security_advisory") or {}).get("severity"),
                    "summary": (alert.get("security_advisory") or {}).get("summary"),
                    "package": (alert.get("dependency") or {}).get("package", {}).get("name"),
                    "manifest_path": (alert.get("dependency") or {}).get("manifest_path"),
                    "fixed_version": (
                        ((alert.get("security_vulnerability") or {}).get("first_patched_version") or {})
                        .get("identifier")
                    ),
                    "url": alert.get("html_url"),
                }
                for alert in alerts
            ]
        except (ProviderError, RateLimitError) as exc:
            result["dependabot_error"] = str(exc)
        try:
            advisories = provider.security_advisories(repo.full_name)
            result["advisories"] = [
                {
                    "ghsa_id": advisory.get("ghsa_id"),
                    "cve_id": advisory.get("cve_id"),
                    "severity": advisory.get("severity"),
                    "summary": advisory.get("summary"),
                    "published_at": advisory.get("published_at"),
                    "url": advisory.get("html_url"),
                }
                for advisory in advisories
            ]
        except (ProviderError, RateLimitError) as exc:
            result["advisories_error"] = str(exc)
        try:
            sbom = provider.dependency_sbom(repo.full_name)
            payload = sbom.payload if isinstance(sbom.payload, dict) else {}
            packages = (payload.get("sbom") or {}).get("packages") or []
            result["dependency_summary"] = {
                "package_count": len(packages),
                "ecosystems": sorted(
                    {
                        (pkg.get("externalRefs") or [{}])[0].get("referenceLocator", "").split(":")[0]
                        for pkg in packages
                        if pkg.get("externalRefs")
                    }
                    - {""}
                ),
            }
        except (ProviderError, RateLimitError) as exc:
            result["dependency_summary"] = None
            result["dependencies_error"] = str(exc)
        if all(
            key in result
            for key in ("dependabot_error", "advisories_error", "dependencies_error")
        ):
            result["capability"] = "unavailable"
        return result
    finally:
        provider.close()


def run_scope(session: Session, settings: Settings, repo: Repository, scope: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    if scope in {"metadata", "activity", "complete"}:
        output: dict[str, Any] = sync_metadata(session, settings, repo, force=payload.get("force", False))
        if scope == "metadata":
            return output
    else:
        output = {}
    results: dict[str, Any] = {"metadata": output} if output else {}
    scopes = (
        ["branches", "tags", "commits", "releases", "pull_requests", "issues", "workflows", "security", "packages"]
        if scope == "complete"
        else [scope]
    )
    for item in scopes:
        if item in {"metadata", "activity", "complete"}:
            continue
        try:
            if item == "branches":
                results[item] = sync_branches(session, settings, repo, force=payload.get("force", False))
            elif item == "tags":
                results[item] = sync_tags(session, settings, repo, force=payload.get("force", False))
            elif item == "commits":
                results[item] = sync_commits(
                    session,
                    settings,
                    repo,
                    page=int(payload.get("page", 1)),
                    per_page=int(payload.get("per_page", 30)),
                    sha=payload.get("sha"),
                    author=payload.get("author"),
                    since=payload.get("since"),
                    until=payload.get("until"),
                    path=payload.get("path"),
                    force=payload.get("force", False),
                )
            elif item == "releases":
                results[item] = sync_releases(session, settings, repo, force=payload.get("force", False))
            elif item == "pull_requests":
                results[item] = sync_pull_requests(session, settings, repo, force=payload.get("force", False))
            elif item == "issues":
                results[item] = sync_issues(session, settings, repo, force=payload.get("force", False))
            elif item == "workflows":
                results[item] = sync_workflows(session, settings, repo, force=payload.get("force", False))
            elif item == "security":
                results[item] = sync_security(session, settings, repo, force=payload.get("force", False))
            elif item == "packages":
                results[item] = sync_packages(session, settings, repo, force=payload.get("force", False))
        except (ProviderError, RateLimitError, UnsupportedCapability) as exc:
            results[item] = {"error": str(exc)}
            repo.last_error = str(exc)
            session.flush()
    repo.refresh_status = "current" if not any(isinstance(v, dict) and v.get("error") for v in results.values()) else "refresh_failed"
    repo.fetched_at = utcnow()
    repo.last_successful_fetch_at = repo.fetched_at
    session.flush()
    return results


def discover_repositories(
    session: Session, settings: Settings, *, mark_missing: bool = True
) -> dict[str, Any]:
    if not settings.github_enabled:
        return {"discovered": 0, "reason": "GitHub is not configured"}
    scope = auth_scope(settings.github_token)
    provider = GitHubProvider(session, settings, scope)
    try:
        raw_repositories = provider.authenticated_repositories()
        seen_ids: set[str] = set()
        created = 0
        updated = 0
        for raw in raw_repositories:
            provider_id = str(raw.get("id"))
            if not provider_id:
                continue
            existing = session.scalar(
                select(Repository).where(
                    Repository.provider == "github", Repository.provider_id == provider_id
                )
            )
            upsert_repository(session, raw, scope)
            if existing:
                updated += 1
            else:
                created += 1
            seen_ids.add(provider_id)
        if mark_missing:
            for repo in session.scalars(
                select(Repository).where(
                    Repository.provider == "github",
                    Repository.auth_scope == scope,
                    Repository.lifecycle == "active",
                )
            ).all():
                if repo.provider_id not in seen_ids:
                    repo.lifecycle = "inaccessible"
                    repo.refresh_status = "stale"
                    repo.last_error = "Not returned by a successful discovery reconciliation"
        session.flush()
        return {
            "discovered": len(raw_repositories),
            "created": created,
            "updated": updated,
            "scope": scope,
        }
    finally:
        provider.close()


def ensure_standalone_project(session: Session, repo: Repository) -> Project:
    if repo.projects:
        return repo.projects[0]
    slug = slugify(repo.full_name)
    project = session.scalar(select(Project).where(Project.slug == slug))
    if project is None:
        project = Project(
            name=repo.full_name,
            slug=slug,
            description=repo.description,
            owner=repo.owner,
            homepage=repo.homepage,
            visibility=repo.visibility,
        )
        session.add(project)
        session.flush()
    if repo not in project.repositories:
        project.repositories.append(repo)
    session.flush()
    return project


def register_repository_url(session: Session, settings: Settings, url: str) -> Repository:
    from app.utils import validate_remote_url

    validate_remote_url(url, allow_arbitrary=settings.allow_arbitrary_remotes)
    from urllib.parse import urlparse

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host not in {"github.com", "www.github.com"}:
        raise UnsupportedCapability("manual-registration", f"host {host}")
    from app.utils import parse_github_repo_url

    owner, name = parse_github_repo_url(url)
    provider = GitHubProvider(session, settings)
    try:
        result = provider.repository(f"{owner}/{name}")
    finally:
        provider.close()
    repo = upsert_repository(session, result.payload, auth_scope(settings.github_token))
    ensure_standalone_project(session, repo)
    enqueue_job(session, repo.id, "complete", priority=90)
    return repo


def enqueue_job(
    session: Session,
    repository_id: int | None,
    scope: str,
    *,
    priority: int = 50,
    payload: dict[str, Any] | None = None,
    project_id: int | None = None,
) -> SyncJob:
    if scope not in SCOPES:
        raise ValueError(f"Unknown refresh scope {scope!r}")
    existing = session.scalar(
        select(SyncJob).where(
            SyncJob.repository_id == repository_id,
            SyncJob.scope == scope,
            SyncJob.status.in_(["queued", "running"]),
        )
    )
    if existing:
        if priority > existing.priority:
            existing.priority = priority
        return existing
    job = SyncJob(
        repository_id=repository_id,
        project_id=project_id,
        scope=scope,
        priority=priority,
        payload=payload or {},
        next_run_at=utcnow(),
    )
    session.add(job)
    session.flush()
    return job


def claim_job(session: Session) -> SyncJob | None:
    now = utcnow()
    job = session.scalar(
        select(SyncJob)
        .where(
            SyncJob.status == "queued",
            (SyncJob.next_run_at.is_(None)) | (SyncJob.next_run_at <= now),
        )
        .order_by(SyncJob.priority.desc(), SyncJob.id.asc())
        .limit(1)
    )
    if job is None:
        return None
    job.status = "running"
    job.started_at = now
    job.attempts += 1
    job.error = None
    session.flush()
    return job


def execute_job(session: Session, settings: Settings, job: SyncJob) -> dict[str, Any]:
    if job.repository_id is None:
        job.status = "succeeded"
        job.finished_at = utcnow()
        session.flush()
        return {"status": "skipped", "reason": "no repository"}
    repo = session.get(Repository, job.repository_id)
    if repo is None:
        job.status = "succeeded"
        job.finished_at = utcnow()
        session.flush()
        return {"status": "skipped", "reason": "repository removed"}
    try:
        result = run_scope(session, settings, repo, job.scope, job.payload)
        job.status = "succeeded"
        job.finished_at = utcnow()
        job.error = None
        session.flush()
        return {"status": "succeeded", "result": result}
    except RateLimitError as exc:
        job.attempts = max(job.attempts, job.max_attempts)
        job.status = "queued"
        job.next_run_at = exc.reset_at or (utcnow() + timedelta(minutes=5))
        job.error = str(exc)
        job.started_at = None
        session.flush()
        return {"status": "rate_limited", "reset_at": job.next_run_at}
    except (ProviderError, Exception) as exc:
        job.error = str(exc)[:2000]
        if job.attempts >= job.max_attempts:
            job.status = "failed"
            job.finished_at = utcnow()
        else:
            job.status = "queued"
            delay = min(2 ** job.attempts * 10, 900)
            job.next_run_at = utcnow() + timedelta(seconds=delay)
            job.started_at = None
        repo.refresh_status = "refresh_failed"
        repo.last_error = job.error
        session.flush()
        return {"status": job.status, "error": job.error}


def schedule_recurring(session: Session, settings: Settings) -> int:
    now = utcnow()
    scheduled = 0
    policies = {
        "metadata": 6 * 3600,
        "branches": 15 * 60,
        "tags": 3600,
        "releases": 3600,
        "pull_requests": 15 * 60,
        "issues": 15 * 60,
        "workflows": 30 * 60,
        "packages": 12 * 3600,
    }
    for repo in session.scalars(
        select(Repository).where(Repository.lifecycle == "active")
    ).all():
        if repo.refresh_status == "never_fetched" or repo.fetched_at is None:
            enqueue_job(session, repo.id, "metadata", priority=70)
            scheduled += 1
            continue
        fetched = repo.fetched_at
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=now.tzinfo)
        age = (now - fetched).total_seconds()
        if age >= policies["metadata"]:
            enqueue_job(session, repo.id, "metadata", priority=50)
            scheduled += 1
        if age >= policies["branches"]:
            enqueue_job(session, repo.id, "branches", priority=40)
            enqueue_job(session, repo.id, "pull_requests", priority=40)
            enqueue_job(session, repo.id, "issues", priority=40)
            scheduled += 3
        if age >= policies["releases"]:
            enqueue_job(session, repo.id, "releases", priority=30)
            scheduled += 1
        if repo.packages:
            if age >= policies["packages"]:
                enqueue_job(session, repo.id, "packages", priority=20)
                scheduled += 1
    return scheduled
