from __future__ import annotations

from datetime import timedelta
from typing import Any

from app.models import (
    Commit,
    Issue,
    Observation,
    Package,
    PackageVersion,
    Project,
    ProviderCache,
    PullRequest,
    Release,
    Repository,
    RepositoryBranch,
    RepositoryTag,
    SyncJob,
    WebhookDelivery,
    WorkflowRun,
)
from app.utils import compare_versions, is_semver, iso, utcnow

DEFAULT_TTLS = {
    "repository": 6 * 3600,
    "branches": 15 * 60,
    "tags": 3600,
    "commits": 5 * 60,
    "pulls": 10 * 60,
    "issues": 10 * 60,
    "releases": 3600,
    "packages": 6 * 3600,
    "workflows": 10 * 60,
}


def freshness(
    obj: Any, *, resource: str = "repository", stale_after: int | None = None
) -> dict[str, Any]:
    fetched = getattr(obj, "fetched_at", None) or getattr(obj, "last_successful_fetch_at", None)
    ttl = stale_after or DEFAULT_TTLS.get(resource, 3600)
    is_stale = True
    if fetched:
        if fetched.tzinfo is None:
            from datetime import timezone

            fetched = fetched.replace(tzinfo=timezone.utc)
        is_stale = fetched + timedelta(seconds=ttl) < utcnow()
    return {
        "fetched_at": iso(getattr(obj, "fetched_at", None)),
        "last_successful_fetch_at": iso(getattr(obj, "last_successful_fetch_at", None)),
        "source_updated_at": iso(getattr(obj, "source_updated_at", None)),
        "is_stale": is_stale,
        "refresh_status": getattr(obj, "refresh_status", "never_fetched"),
        "last_error": getattr(obj, "last_error", None),
    }


def project_summary(project: Project) -> dict[str, Any]:
    return {
        "id": project.id,
        "name": project.name,
        "slug": project.slug,
        "description": project.description,
        "owner": project.owner,
        "homepage": project.homepage,
        "visibility": project.visibility,
        "aliases": project.aliases or [],
        "tags": project.tags or [],
        "categories": project.categories or [],
        "is_group": project.is_group,
        "repository_count": len(project.repositories),
        "created_at": iso(project.created_at),
        "updated_at": iso(project.updated_at),
    }


def project_detail(project: Project) -> dict[str, Any]:
    data = project_summary(project)
    data["metadata"] = project.metadata_ or {}
    data["repositories"] = [repository_summary(repo) for repo in project.repositories]
    return data


def repository_summary(repo: Repository) -> dict[str, Any]:
    return {
        "id": repo.id,
        "provider": repo.provider,
        "provider_id": repo.provider_id,
        "full_name": repo.full_name,
        "owner": repo.owner,
        "name": repo.name,
        "description": repo.description,
        "url": repo.url,
        "homepage": repo.homepage,
        "visibility": repo.visibility,
        "default_branch": repo.default_branch,
        "language": repo.language,
        "topics": repo.topics or [],
        "license": repo.license,
        "archived": repo.archived,
        "fork": repo.fork,
        "is_template": repo.is_template,
        "lifecycle": repo.lifecycle,
        "stars": repo.stars,
        "forks": repo.forks,
        "watchers": repo.watchers,
        "open_issues": repo.open_issues,
        "provider_updated_at": iso(repo.provider_updated_at),
        "projects": [{"id": p.id, "name": p.name, "slug": p.slug} for p in repo.projects],
        "freshness": freshness(repo),
    }


def repository_detail(repo: Repository) -> dict[str, Any]:
    data = repository_summary(repo)
    data.update(
        {
            "owner_type": repo.owner_type,
            "clone_url": repo.clone_url,
            "languages": repo.languages or {},
            "license_spdx": repo.license_spdx,
            "provider_created_at": iso(repo.provider_created_at),
            "upstream_full_name": repo.upstream_full_name,
            "size_kb": repo.size_kb,
            "contributors_count": repo.contributors_count,
            "features": repo.features or {},
            "last_seen_at": iso(repo.last_seen_at),
            "raw_metadata": repo.raw_provider or {},
            "links": {
                "repository": repo.url,
                "commits": f"{repo.url}/commits" if repo.url else None,
                "pull_requests": f"{repo.url}/pulls" if repo.url else None,
                "issues": f"{repo.url}/issues" if repo.url else None,
                "releases": f"{repo.url}/releases" if repo.url else None,
                "actions": f"{repo.url}/actions" if repo.url else None,
            },
        }
    )
    return data


def branch_data(branch: RepositoryBranch) -> dict[str, Any]:
    return {
        "id": branch.id,
        "name": branch.name,
        "head_sha": branch.head_sha,
        "head_committed_at": iso(branch.head_committed_at),
        "latest_commit_message": branch.latest_commit_message,
        "is_default": branch.is_default,
        "ahead": branch.ahead,
        "behind": branch.behind,
        "protected": branch.protected,
        "required_checks": branch.required_checks or [],
        "freshness": freshness(branch, resource="branches"),
    }


def tag_data(tag: RepositoryTag) -> dict[str, Any]:
    return {
        "id": tag.id,
        "name": tag.name,
        "target_sha": tag.target_sha,
        "tag_type": tag.tag_type,
        "tagger": {
            "name": tag.tagger_name,
            "email": tag.tagger_email,
            "date": iso(tag.tagger_date),
        },
        "release_id": tag.release_id,
        "is_semver": tag.is_semver,
        "freshness": freshness(tag, resource="tags"),
    }


def commit_data(commit: Commit, *, detail: bool = False) -> dict[str, Any]:
    data = {
        "sha": commit.sha,
        "abbreviated_sha": commit.abbreviated_sha,
        "subject": commit.subject,
        "author": {
            "name": commit.author_name,
            "email": commit.author_email,
            "timestamp": iso(commit.authored_at),
        },
        "committer": {
            "name": commit.committer_name,
            "email": commit.committer_email,
            "timestamp": iso(commit.committed_at),
        },
        "parents": commit.parents or [],
        "url": commit.html_url,
        "verified": commit.verified,
        "pull_requests": commit.pull_request_numbers or [],
        "freshness": freshness(commit, resource="commits"),
    }
    if detail:
        data.update(
            {
                "message": commit.message,
                "verification": commit.verification or {},
                "stats": {
                    "changed_files": commit.changed_files,
                    "additions": commit.additions,
                    "deletions": commit.deletions,
                    "total_changes": commit.total_changes,
                },
            }
        )
    return data


def pull_request_data(pr: PullRequest) -> dict[str, Any]:
    return {
        "id": pr.id,
        "number": pr.number,
        "title": pr.title,
        "url": pr.url,
        "author": pr.author,
        "state": pr.state,
        "draft": pr.is_draft,
        "created_at": iso(pr.created_at),
        "updated_at": iso(pr.updated_at),
        "merged_at": iso(pr.merged_at),
        "closed_at": iso(pr.closed_at),
        "merge_commit_sha": pr.merge_commit_sha,
        "base_branch": pr.base_branch,
        "head_branch": pr.head_branch,
        "head_sha": pr.head_sha,
        "review_status": pr.review_status,
        "requested_reviewers": pr.requested_reviewers or [],
        "labels": pr.labels or [],
        "linked_issues": pr.linked_issues or [],
        "changed_files": pr.changed_files,
        "additions": pr.additions,
        "deletions": pr.deletions,
        "checks_status": pr.checks_status,
        "milestone": pr.milestone,
        "assignees": pr.assignees or [],
        "freshness": freshness(pr, resource="pulls"),
    }


def issue_data(issue: Issue) -> dict[str, Any]:
    return {
        "id": issue.id,
        "number": issue.number,
        "title": issue.title,
        "url": issue.url,
        "author": issue.author,
        "state": issue.state,
        "labels": issue.labels or [],
        "assignees": issue.assignees or [],
        "created_at": iso(issue.created_at),
        "updated_at": iso(issue.updated_at),
        "closed_at": iso(issue.closed_at),
        "milestone": issue.milestone,
        "comments_count": issue.comments_count,
        "linked_pull_requests": issue.linked_pull_requests or [],
        "freshness": freshness(issue, resource="issues"),
    }


def release_data(release: Release, *, include_notes: bool = False) -> dict[str, Any]:
    data = {
        "id": release.id,
        "provider_id": release.provider_id,
        "tag_name": release.tag_name,
        "title": release.title,
        "url": release.url,
        "published_at": iso(release.published_at),
        "created_at": iso(release.created_at),
        "draft": release.draft,
        "prerelease": release.prerelease,
        "target_sha": release.target_sha,
        "is_semver": is_semver(release.tag_name),
        "assets": release.assets or [],
        "freshness": freshness(release, resource="releases"),
    }
    if include_notes:
        data["notes"] = release.notes
    return data


def package_data(package: Package, *, repository: Repository | None = None) -> dict[str, Any]:
    data = {
        "id": package.id,
        "registry": package.registry,
        "ecosystem": package.ecosystem,
        "name": package.name,
        "url": package.url,
        "latest_version": package.latest_version,
        "latest_stable_version": package.latest_stable_version,
        "published_at": iso(package.published_at),
        "description": package.description,
        "license": package.license,
        "maintainers": package.maintainers or [],
        "downloads": package.downloads,
        "downloads_period": package.downloads_period,
        "deprecated": package.deprecated,
        "yanked": package.yanked,
        "repository_id": package.repository_id,
        "project_id": package.project_id,
        "freshness": freshness(package, resource="packages"),
    }
    if repository is not None:
        data["version_alignment"] = version_alignment(repository, package)
    return data


def version_alignment(repo: Repository, package: Package) -> dict[str, Any]:
    latest_release = None
    for release in sorted(
        repo.tags or [], key=lambda tag: tag.name, reverse=True
    ):
        if not release.is_semver:
            continue
        latest_release = release.name
        break
    comparisons = {
        "package_latest_vs_tag": compare_versions(package.latest_version, latest_release),
        "package_stable_vs_tag": compare_versions(package.latest_stable_version, latest_release),
    }
    return {
        "latest_tag": latest_release,
        "package_latest": package.latest_version,
        "aligned": comparisons["package_latest_vs_tag"] == "equal",
        "comparisons": comparisons,
        "ordering_known": all(value != "unknown" for value in comparisons.values()),
    }


def package_version_data(version: PackageVersion) -> dict[str, Any]:
    return {
        "version": version.version,
        "published_at": iso(version.published_at),
        "yanked": version.yanked,
        "deprecated": version.deprecated,
        "prerelease": version.is_prerelease,
        "metadata": version.metadata_ or {},
        "fetched_at": iso(version.fetched_at),
    }


def workflow_run_data(run: WorkflowRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "provider_id": run.provider_id,
        "workflow_name": run.workflow_name,
        "workflow_id": run.workflow_id,
        "run_number": run.run_number,
        "status": run.status,
        "conclusion": run.conclusion,
        "branch": run.branch,
        "head_sha": run.head_sha,
        "event": run.event,
        "started_at": iso(run.started_at),
        "completed_at": iso(run.completed_at),
        "duration_seconds": run.duration_seconds,
        "url": run.html_url,
        "failed_jobs": run.failed_jobs or [],
        "freshness": freshness(run, resource="workflows"),
    }


def observation_data(obs: Observation) -> dict[str, Any]:
    return {
        "id": obs.id,
        "kind": obs.kind,
        "value": obs.value,
        "source": obs.source,
        "observed_at": iso(obs.observed_at),
        "metadata": obs.metadata_ or {},
    }


def job_data(job: SyncJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "repository_id": job.repository_id,
        "scope": job.scope,
        "priority": job.priority,
        "status": job.status,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "error": job.error,
        "created_at": iso(job.created_at),
        "started_at": iso(job.started_at),
        "finished_at": iso(job.finished_at),
        "next_run_at": iso(job.next_run_at),
    }


def webhook_data(delivery: WebhookDelivery) -> dict[str, Any]:
    return {
        "id": delivery.id,
        "delivery_id": delivery.delivery_id,
        "event": delivery.event,
        "action": delivery.action,
        "repository_id": delivery.repository_id,
        "status": delivery.status,
        "received_at": iso(delivery.received_at),
        "processed_at": iso(delivery.processed_at),
        "error": delivery.error,
    }


def cache_entry_data(entry: ProviderCache) -> dict[str, Any]:
    return {
        "provider": entry.provider,
        "resource": entry.resource,
        "scope": entry.scope,
        "status": entry.status,
        "error": entry.error,
        "etag": bool(entry.etag),
        "last_modified": bool(entry.last_modified),
        "fetched_at": iso(entry.fetched_at),
        "last_successful_fetch_at": iso(entry.last_successful_fetch_at),
        "next_refresh_at": iso(entry.next_refresh_at),
        "source_updated_at": iso(entry.source_updated_at),
        "hits": entry.hits,
        "misses": entry.misses,
    }
