from __future__ import annotations

from datetime import timedelta
from typing import Any, Callable, TypeVar

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import (
    Commit,
    Issue,
    Observation,
    Package,
    Project,
    PullRequest,
    Release,
    Repository,
    RepositoryBranch,
    RepositoryTag,
    WorkflowRun,
    project_repositories,
)
from app.serializers import (
    branch_data,
    commit_data,
    freshness,
    issue_data,
    observation_data,
    package_data,
    package_version_data,
    project_detail,
    project_summary,
    pull_request_data,
    release_data,
    repository_detail,
    repository_summary,
    tag_data,
    workflow_run_data,
)
from app.utils import compare_versions, iso, utcnow, version_key

T = TypeVar("T")


def paginate(
    session: Session,
    stmt,
    *,
    page: int,
    per_page: int,
    mapper: Callable[[Any], dict[str, Any]],
    max_per_page: int = 100,
) -> dict[str, Any]:
    page = max(1, page)
    per_page = max(1, min(per_page, max_per_page))
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = session.scalars(stmt.offset((page - 1) * per_page).limit(per_page)).all()
    pages = (total + per_page - 1) // per_page if total else 0
    return {
        "items": [mapper(row) for row in rows],
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total,
            "pages": pages,
            "has_next": page < pages,
            "has_previous": page > 1,
            "next_page": page + 1 if page < pages else None,
        },
    }


def get_project(session: Session, identifier: str | int) -> Project | None:
    if isinstance(identifier, int) or str(identifier).isdigit():
        return session.get(Project, int(identifier))
    return session.scalar(select(Project).where(Project.slug == str(identifier)))


def get_repository(session: Session, identifier: str | int) -> Repository | None:
    text = str(identifier)
    if text.isdigit():
        return session.get(Repository, int(text))
    if ":" in text:
        provider, name = text.split(":", 1)
        return session.scalar(
            select(Repository).where(Repository.provider == provider, Repository.full_name == name)
        )
    return session.scalar(select(Repository).where(Repository.full_name == text))


def list_projects(
    session: Session,
    *,
    page: int = 1,
    per_page: int = 50,
    owner: str | None = None,
    provider: str | None = None,
    language: str | None = None,
    topic: str | None = None,
    visibility: str | None = None,
    archived: bool | None = None,
    group: bool | None = None,
) -> dict[str, Any]:
    stmt = select(Project)
    if owner:
        stmt = stmt.where(Project.owner == owner)
    if visibility:
        stmt = stmt.where(Project.visibility == visibility)
    if group is not None:
        stmt = stmt.where(Project.is_group == group)
    if provider or language or topic or archived is not None:
        repo_filters = []
        if provider:
            repo_filters.append(Repository.provider == provider)
        if language:
            repo_filters.append(Repository.language == language)
        if topic:
            repo_filters.append(Repository.topics.contains(topic))
        if archived is not None:
            repo_filters.append(Repository.archived == archived)
        project_ids = select(project_repositories.c.project_id).join(
            Repository, Repository.id == project_repositories.c.repository_id
        )
        if repo_filters:
            project_ids = project_ids.where(*repo_filters)
        stmt = stmt.where(Project.id.in_(project_ids))
    stmt = stmt.order_by(Project.name)
    return paginate(session, stmt, page=page, per_page=per_page, mapper=project_summary)


def list_repositories(
    session: Session,
    *,
    page: int = 1,
    per_page: int = 50,
    owner: str | None = None,
    provider: str | None = None,
    language: str | None = None,
    topic: str | None = None,
    visibility: str | None = None,
    archived: bool | None = None,
    lifecycle: str | None = None,
    include_private: bool = True,
) -> dict[str, Any]:
    stmt = select(Repository)
    if owner:
        stmt = stmt.where(Repository.owner == owner)
    if provider:
        stmt = stmt.where(Repository.provider == provider)
    if language:
        stmt = stmt.where(Repository.language == language)
    if topic:
        stmt = stmt.where(Repository.topics.contains(topic))
    if visibility:
        stmt = stmt.where(Repository.visibility == visibility)
    if archived is not None:
        stmt = stmt.where(Repository.archived == archived)
    if lifecycle:
        stmt = stmt.where(Repository.lifecycle == lifecycle)
    if not include_private:
        stmt = stmt.where(Repository.visibility != "private")
    stmt = stmt.order_by(Repository.full_name)
    return paginate(session, stmt, page=page, per_page=per_page, mapper=repository_summary)


def repository_stats(session: Session, repo: Repository) -> dict[str, Any]:
    counts = {
        "branches": session.scalar(
            select(func.count(RepositoryBranch.id)).where(RepositoryBranch.repository_id == repo.id)
        )
        or 0,
        "tags": session.scalar(
            select(func.count(RepositoryTag.id)).where(RepositoryTag.repository_id == repo.id)
        )
        or 0,
        "commits_indexed": session.scalar(
            select(func.count(Commit.id)).where(Commit.repository_id == repo.id)
        )
        or 0,
        "pull_requests": session.scalar(
            select(func.count(PullRequest.id)).where(PullRequest.repository_id == repo.id)
        )
        or 0,
        "open_pull_requests": session.scalar(
            select(func.count(PullRequest.id)).where(
                PullRequest.repository_id == repo.id, PullRequest.state == "open"
            )
        )
        or 0,
        "issues": session.scalar(
            select(func.count(Issue.id)).where(Issue.repository_id == repo.id)
        )
        or 0,
        "open_issues": session.scalar(
            select(func.count(Issue.id)).where(Issue.repository_id == repo.id, Issue.state == "open")
        )
        or 0,
        "releases": session.scalar(
            select(func.count(Release.id)).where(Release.repository_id == repo.id)
        )
        or 0,
        "packages": session.scalar(
            select(func.count(Package.id)).where(Package.repository_id == repo.id)
        )
        or 0,
        "workflow_runs": session.scalar(
            select(func.count(WorkflowRun.id)).where(WorkflowRun.repository_id == repo.id)
        )
        or 0,
    }
    latest_commit = session.scalar(
        select(Commit)
        .where(Commit.repository_id == repo.id)
        .order_by(Commit.committed_at.desc())
        .limit(1)
    )
    latest_release = session.scalar(
        select(Release)
        .where(Release.repository_id == repo.id, Release.draft.is_(False))
        .order_by(Release.published_at.desc())
        .limit(1)
    )
    return {
        "repository_id": repo.id,
        "provider": repo.provider,
        "provider_reported": {
            "stars": repo.stars,
            "forks": repo.forks,
            "watchers": repo.watchers,
            "open_issues": repo.open_issues,
            "contributors": repo.contributors_count,
            "size_kb": repo.size_kb,
        },
        "locally_indexed": counts,
        "latest_commit": {
            "sha": latest_commit.sha,
            "committed_at": iso(latest_commit.committed_at),
            "subject": latest_commit.subject,
        }
        if latest_commit
        else None,
        "latest_release": {
            "tag_name": latest_release.tag_name,
            "published_at": iso(latest_release.published_at),
            "prerelease": latest_release.prerelease,
        }
        if latest_release
        else None,
        "version_alignment": version_summary(session, repo),
        "freshness": freshness(repo),
    }


def repository_activity(
    session: Session, repo: Repository, *, days: int = 90
) -> dict[str, Any]:
    since = utcnow() - timedelta(days=days)
    latest_pr = session.scalar(
        select(PullRequest)
        .where(PullRequest.repository_id == repo.id)
        .order_by(PullRequest.updated_at.desc())
        .limit(1)
    )
    latest_issue = session.scalar(
        select(Issue)
        .where(Issue.repository_id == repo.id)
        .order_by(Issue.updated_at.desc())
        .limit(1)
    )
    latest_release = session.scalar(
        select(Release)
        .where(Release.repository_id == repo.id)
        .order_by(Release.published_at.desc())
        .limit(1)
    )
    latest_commit = session.scalar(
        select(Commit)
        .where(Commit.repository_id == repo.id)
        .order_by(Commit.committed_at.desc())
        .limit(1)
    )
    commit_count = session.scalar(
        select(func.count(Commit.id)).where(
            Commit.repository_id == repo.id, Commit.committed_at >= since
        )
    ) or 0
    release_count = session.scalar(
        select(func.count(Release.id)).where(
            Release.repository_id == repo.id, Release.published_at >= since
        )
    ) or 0
    merged_pr_count = session.scalar(
        select(func.count(PullRequest.id)).where(
            PullRequest.repository_id == repo.id, PullRequest.merged_at >= since
        )
    ) or 0
    observations = session.scalars(
        select(Observation)
        .where(Observation.repository_id == repo.id)
        .order_by(Observation.observed_at.asc())
        .limit(500)
    ).all()
    return {
        "repository_id": repo.id,
        "period_days": days,
        "latest_commit": {
            "sha": latest_commit.sha,
            "committed_at": iso(latest_commit.committed_at),
            "subject": latest_commit.subject,
        }
        if latest_commit
        else None,
        "latest_release": {
            "tag_name": latest_release.tag_name,
            "published_at": iso(latest_release.published_at),
            "prerelease": latest_release.prerelease,
        }
        if latest_release
        else None,
        "latest_pull_request": {
            "number": latest_pr.number,
            "updated_at": iso(latest_pr.updated_at),
            "state": latest_pr.state,
        }
        if latest_pr
        else None,
        "latest_issue": {
            "number": latest_issue.number,
            "updated_at": iso(latest_issue.updated_at),
            "state": latest_issue.state,
        }
        if latest_issue
        else None,
        "last_metadata_update": iso(repo.provider_updated_at),
        "derived": {
            "commits_in_period": commit_count,
            "releases_in_period": release_count,
            "merged_pull_requests_in_period": merged_pr_count,
            "note": "Derived from the local index, not provider totals.",
        },
        "observations": [observation_data(obs) for obs in observations],
        "freshness": freshness(repo),
    }


def version_summary(session: Session, repo: Repository) -> dict[str, Any]:
    tags = session.scalars(
        select(RepositoryTag).where(RepositoryTag.repository_id == repo.id)
    ).all()
    semver_tags = sorted(
        [tag for tag in tags if tag.is_semver and version_key(tag.name)],
        key=lambda tag: version_key(tag.name),  # type: ignore[arg-type]
        reverse=True,
    )
    releases = session.scalars(
        select(Release).where(Release.repository_id == repo.id, Release.draft.is_(False))
    ).all()
    stable_releases = sorted(
        [r for r in releases if not r.prerelease and version_key(r.tag_name)],
        key=lambda r: version_key(r.tag_name),  # type: ignore[arg-type]
        reverse=True,
    )
    prereleases = sorted(
        [r for r in releases if r.prerelease and version_key(r.tag_name)],
        key=lambda r: version_key(r.tag_name),  # type: ignore[arg-type]
        reverse=True,
    )
    packages = session.scalars(select(Package).where(Package.repository_id == repo.id)).all()
    values = {
        "latest_git_tag": semver_tags[0].name if semver_tags else None,
        "latest_stable_release": stable_releases[0].tag_name if stable_releases else None,
        "latest_prerelease": prereleases[0].tag_name if prereleases else None,
        "latest_package_version": packages[0].latest_version if packages else None,
        "latest_registry_version": packages[0].latest_version if packages else None,
    }
    comparisons: dict[str, str] = {}
    keys = list(values)
    for index, left in enumerate(keys):
        for right in keys[index + 1 :]:
            comparisons[f"{left}__vs__{right}"] = compare_versions(values[left], values[right])
    distinct = {value for value in values.values() if value}
    return {
        "values": values,
        "comparisons": comparisons,
        "ordering_known": all(value != "unknown" for value in comparisons.values()),
        "all_equal": len(distinct) <= 1,
        "packages": [package_data(package, repository=repo) for package in packages],
    }


def workflow_status(session: Session, repo: Repository) -> dict[str, Any]:
    runs = session.scalars(
        select(WorkflowRun)
        .where(WorkflowRun.repository_id == repo.id)
        .order_by(WorkflowRun.started_at.desc())
        .limit(50)
    ).all()
    default_head = session.scalar(
        select(RepositoryBranch).where(
            RepositoryBranch.repository_id == repo.id,
            RepositoryBranch.is_default.is_(True),
        )
    )
    head_sha = default_head.head_sha if default_head else None
    latest = runs[0] if runs else None
    latest_success_on_head = next(
        (
            run
            for run in runs
            if run.conclusion == "success" and run.branch == repo.default_branch and run.head_sha == head_sha
        ),
        None,
    )
    if latest is None:
        ci_state = "no_runs"
    elif latest.status in {"queued", "in_progress", "waiting", "requested", "pending"}:
        ci_state = "running"
    elif latest.conclusion == "success":
        ci_state = "success"
    elif latest.conclusion == "cancelled":
        ci_state = "cancelled"
    elif latest.conclusion == "skipped":
        ci_state = "skipped"
    else:
        ci_state = "failure"
    return {
        "repository_id": repo.id,
        "ci_state": ci_state,
        "latest_run": workflow_run_data(latest) if latest else None,
        "latest_success_on_default_head": workflow_run_data(latest_success_on_head)
        if latest_success_on_head
        else None,
        "default_branch_head_sha": head_sha,
        "head_has_successful_run": bool(latest_success_on_head),
        "recent_failures": [workflow_run_data(run) for run in runs if run.conclusion == "failure"][:10],
        "runs": [workflow_run_data(run) for run in runs],
    }


def portfolio_summary(session: Session, *, include_private: bool = True) -> dict[str, Any]:
    repo_stmt = select(Repository)
    if not include_private:
        repo_stmt = repo_stmt.where(Repository.visibility != "private")
    repos = session.scalars(repo_stmt.order_by(Repository.full_name)).all()
    projects = session.scalars(select(Project).order_by(Project.name)).all()
    entries = []
    for repo in repos:
        latest_commit = session.scalar(
            select(Commit)
            .where(Commit.repository_id == repo.id)
            .order_by(Commit.committed_at.desc())
            .limit(1)
        )
        latest_release = session.scalar(
            select(Release)
            .where(Release.repository_id == repo.id, Release.draft.is_(False))
            .order_by(Release.published_at.desc())
            .limit(1)
        )
        open_prs = session.scalar(
            select(func.count(PullRequest.id)).where(
                PullRequest.repository_id == repo.id, PullRequest.state == "open"
            )
        ) or 0
        packages = session.scalars(select(Package).where(Package.repository_id == repo.id)).all()
        ci = workflow_status(session, repo)
        entries.append(
            {
                **repository_summary(repo),
                "latest_commit": {
                    "sha": latest_commit.sha,
                    "committed_at": iso(latest_commit.committed_at),
                    "subject": latest_commit.subject,
                    "age_seconds": int((utcnow() - latest_commit.committed_at).total_seconds())
                    if latest_commit and latest_commit.committed_at
                    else None,
                }
                if latest_commit
                else None,
                "latest_release": {
                    "tag_name": latest_release.tag_name,
                    "published_at": iso(latest_release.published_at),
                    "prerelease": latest_release.prerelease,
                }
                if latest_release
                else None,
                "open_pull_requests": open_prs,
                "package_versions": [
                    {"registry": p.registry, "name": p.name, "version": p.latest_version}
                    for p in packages
                ],
                "ci_state": ci["ci_state"],
                "health": _health(repo, latest_commit, ci["ci_state"]),
            }
        )
    return {
        "projects": [project_summary(project) for project in projects],
        "repositories": entries,
        "counts": {
            "projects": len(projects),
            "repositories": len(repos),
            "archived": sum(1 for repo in repos if repo.archived),
            "stale": sum(1 for repo in repos if freshness(repo)["is_stale"]),
            "private": sum(1 for repo in repos if repo.visibility == "private"),
        },
        "generated_at": iso(utcnow()),
    }


def _health(repo: Repository, latest_commit: Commit | None, ci_state: str) -> str:
    if repo.lifecycle in {"inaccessible", "deleted"}:
        return "unavailable"
    if repo.archived:
        return "archived"
    if freshness(repo)["is_stale"]:
        return "stale"
    if ci_state == "failure":
        return "failing"
    if latest_commit and latest_commit.committed_at:
        committed = latest_commit.committed_at
        if committed.tzinfo is None:
            from datetime import timezone

            committed = committed.replace(tzinfo=timezone.utc)
        if (utcnow() - committed).days > 365:
            return "quiet"
    return "healthy"


def recent_releases(session: Session, *, limit: int = 20, include_private: bool = True) -> list[dict[str, Any]]:
    stmt = (
        select(Release, Repository)
        .join(Repository, Repository.id == Release.repository_id)
        .where(Release.draft.is_(False))
        .order_by(Release.published_at.desc())
        .limit(limit)
    )
    if not include_private:
        stmt = stmt.where(Repository.visibility != "private")
    return [
        {
            **release_data(release),
            "repository": {"id": repo.id, "full_name": repo.full_name, "url": repo.url},
        }
        for release, repo in session.execute(stmt).all()
    ]


def recent_pull_requests(session: Session, *, limit: int = 20, include_private: bool = True) -> list[dict[str, Any]]:
    stmt = (
        select(PullRequest, Repository)
        .join(Repository, Repository.id == PullRequest.repository_id)
        .order_by(PullRequest.updated_at.desc())
        .limit(limit)
    )
    if not include_private:
        stmt = stmt.where(Repository.visibility != "private")
    return [
        {
            **pull_request_data(pr),
            "repository": {"id": repo.id, "full_name": repo.full_name, "url": repo.url},
        }
        for pr, repo in session.execute(stmt).all()
    ]


def recent_packages(session: Session, *, limit: int = 20) -> list[dict[str, Any]]:
    packages = session.scalars(
        select(Package).order_by(Package.updated_at.desc()).limit(limit)
    ).all()
    return [package_data(package) for package in packages]


def list_packages(
    session: Session,
    *,
    page: int = 1,
    per_page: int = 50,
    repository_id: int | None = None,
    project_id: int | None = None,
    registry: str | None = None,
) -> dict[str, Any]:
    stmt = select(Package)
    if repository_id:
        stmt = stmt.where(Package.repository_id == repository_id)
    if project_id:
        stmt = stmt.where(Package.project_id == project_id)
    if registry:
        stmt = stmt.where(Package.registry == registry)
    stmt = stmt.order_by(Package.name)
    return paginate(session, stmt, page=page, per_page=per_page, mapper=package_data)


def search_projects(
    session: Session, query: str, *, limit: int = 20
) -> list[dict[str, Any]]:
    pattern = f"%{query}%"
    projects = session.scalars(
        select(Project)
        .where(
            or_(
                Project.name.ilike(pattern),
                Project.slug.ilike(pattern),
                Project.description.ilike(pattern),
            )
        )
        .order_by(Project.name)
        .limit(limit)
    ).all()
    return [project_detail(project) for project in projects]
