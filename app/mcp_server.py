from __future__ import annotations

from datetime import datetime
from typing import Any

from mcp.server.mcpserver import MCPServer

from app.api import ci, collaboration, history, releases, repositories
from app.api import refresh as refresh_api
from app.config import get_settings
from app.db import Database
from app.errors import APIError
from app.schemas import RefreshRequest
from app.serializers import project_detail, repository_detail, repository_summary
from app.services import queries

settings = get_settings()
db = Database(settings.database_url)
server = MCPServer(
    name="panopticon",
    version="1.0.0",
    instructions=(
        "Read-only project index. Use bounded calls; deep history, logs and diffs "
        "are fetched on demand and never include source file contents."
    ),
)


def _open():
    return db.session()


def _error(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, APIError):
        return {"error": exc.code, "message": exc.message, "details": exc.details}
    return {"error": "provider_unavailable", "message": str(exc)}


@server.tool()
def list_projects(
    page: int = 1, per_page: int = 20, owner: str | None = None, language: str | None = None
) -> dict[str, Any]:
    """List indexed projects with pagination."""
    with _open() as session:
        result = queries.list_projects(
            session, page=page, per_page=min(per_page, 100), owner=owner, language=language
        )
        session.commit()
        return result


@server.tool()
def list_repositories(
    page: int = 1,
    per_page: int = 20,
    owner: str | None = None,
    provider: str | None = None,
    language: str | None = None,
    topic: str | None = None,
    visibility: str | None = None,
    archived: bool | None = None,
    lifecycle: str | None = None,
) -> dict[str, Any]:
    """List indexed repositories across all projects with filters."""
    with _open() as session:
        result = queries.list_repositories(
            session,
            page=page,
            per_page=min(per_page, 100),
            owner=owner,
            provider=provider,
            language=language,
            topic=topic,
            visibility=visibility,
            archived=archived,
            lifecycle=lifecycle,
        )
        session.commit()
        return result


@server.tool()
def get_project(identifier: str) -> dict[str, Any]:
    """Get one project and its repositories by id or slug."""
    with _open() as session:
        project = queries.get_project(session, identifier)
        if project is None:
            return {"error": "not_found", "identifier": identifier}
        result = project_detail(project)
        session.commit()
        return result


@server.tool()
def get_repository(identifier: str) -> dict[str, Any]:
    """Get repository metadata by internal id, owner/name, or provider:owner/name."""
    with _open() as session:
        repo = queries.get_repository(session, identifier)
        if repo is None:
            return {"error": "not_found", "identifier": identifier}
        result = repository_detail(repo)
        session.commit()
        return result


@server.tool()
def get_repository_stats(identifier: str) -> dict[str, Any]:
    """Current provider-reported values plus locally derived index counts."""
    with _open() as session:
        repo = queries.get_repository(session, identifier)
        if repo is None:
            return {"error": "not_found", "identifier": identifier}
        result = queries.repository_stats(session, repo)
        session.commit()
        return result


@server.tool()
def get_latest_commits(identifier: str, limit: int = 20) -> dict[str, Any]:
    """Latest commits on the default branch (local cache first)."""
    with _open() as session:
        result = history.list_commits(
            identifier,
            page=1,
            per_page=min(limit, 100),
            session=session,
            settings=settings,
        )
        session.commit()
        return result


@server.tool()
def get_git_log(
    identifier: str,
    page: int = 1,
    per_page: int = 30,
    branch: str | None = None,
    author: str | None = None,
    since: str | None = None,
    until: str | None = None,
    path: str | None = None,
    search: str | None = None,
) -> dict[str, Any]:
    """Metadata-only git log with branch, author, date, path and message filters."""
    with _open() as session:
        result = history.list_commits(
            identifier,
            page=page,
            per_page=min(per_page, 100),
            branch=branch,
            author=author,
            since=_dt(since),
            until=_dt(until),
            path=path,
            q=search,
            session=session,
            settings=settings,
        )
        session.commit()
        return result


@server.tool()
def get_commit(identifier: str, sha: str, include_files: bool = False) -> dict[str, Any]:
    """Commit details, parents, verification and change summaries (never patches)."""
    with _open() as session:
        result = history.get_commit(
            identifier, sha, include_files=include_files, session=session, settings=settings
        )
        session.commit()
        return result


@server.tool()
def compare_commits(identifier: str, base: str, head: str, include_files: bool = False) -> dict[str, Any]:
    """Compare two refs without downloading source contents."""
    with _open() as session:
        result = history.compare(
            identifier, base, head, include_files=include_files, session=session, settings=settings
        )
        session.commit()
        return result


@server.tool()
def list_branches(identifier: str, live: bool = False) -> dict[str, Any]:
    """List indexed branches."""
    with _open() as session:
        result = repositories.list_branches(identifier, live=live, session=session, settings=settings)
        session.commit()
        return result


@server.tool()
def list_tags(identifier: str, live: bool = False) -> dict[str, Any]:
    """List indexed tags, keeping tags and releases distinct."""
    with _open() as session:
        result = repositories.list_tags(identifier, live=live, session=session, settings=settings)
        session.commit()
        return result


@server.tool()
def get_branch(identifier: str, branch: str) -> dict[str, Any]:
    """Branch head details, with protection metadata when available."""
    with _open() as session:
        try:
            result = repositories.get_branch(
                identifier, branch, session=session, settings=settings
            )
        except Exception as exc:
            return _error(exc)
        session.commit()
        return result


@server.tool()
def get_tag(identifier: str, tag_name: str) -> dict[str, Any]:
    """Tag details, including annotated tag metadata on first read."""
    with _open() as session:
        try:
            result = repositories.get_tag(
                identifier, tag_name, session=session, settings=settings
            )
        except Exception as exc:
            return _error(exc)
        session.commit()
        return result


@server.tool()
def get_branch_commits(
    identifier: str,
    branch: str,
    page: int = 1,
    per_page: int = 30,
    author: str | None = None,
    since: str | None = None,
    until: str | None = None,
    path: str | None = None,
) -> dict[str, Any]:
    """Commit history for one branch, with author, date and path filters."""
    with _open() as session:
        try:
            result = history.branch_commits(
                identifier,
                branch,
                page=page,
                per_page=min(per_page, 100),
                author=author,
                since=_dt(since),
                until=_dt(until),
                path=path,
                session=session,
                settings=settings,
            )
        except Exception as exc:
            return _error(exc)
        session.commit()
        return result


@server.tool()
def list_pull_requests(
    identifier: str, state: str | None = None, page: int = 1, per_page: int = 20
) -> dict[str, Any]:
    """List indexed pull requests with bounded pagination."""
    with _open() as session:
        result = collaboration.list_pull_requests(
            identifier,
            state=state,
            page=page,
            per_page=min(per_page, 100),
            session=session,
            settings=settings,
        )
        session.commit()
        return result


@server.tool()
def get_pull_request(identifier: str, number: int, include_files: bool = False) -> dict[str, Any]:
    """Pull request metadata, optionally changed-file summaries."""
    with _open() as session:
        result = collaboration.get_pull_request(
            identifier,
            number,
            include_files=include_files,
            session=session,
            settings=settings,
        )
        session.commit()
        return result


@server.tool()
def list_issues(
    identifier: str, state: str | None = None, page: int = 1, per_page: int = 20
) -> dict[str, Any]:
    """List indexed issues."""
    with _open() as session:
        result = collaboration.list_issues(
            identifier,
            state=state,
            page=page,
            per_page=min(per_page, 100),
            session=session,
            settings=settings,
        )
        session.commit()
        return result


@server.tool()
def get_issue(
    identifier: str, number: int, include_comments: bool = False
) -> dict[str, Any]:
    """Issue details, optionally with comments."""
    with _open() as session:
        try:
            result = collaboration.get_issue(
                identifier,
                number,
                include_comments=include_comments,
                session=session,
                settings=settings,
            )
        except Exception as exc:
            return _error(exc)
        session.commit()
        return result


@server.tool()
def list_releases(identifier: str, page: int = 1, per_page: int = 20) -> dict[str, Any]:
    """List indexed releases without notes by default."""
    with _open() as session:
        result = releases.list_releases(
            identifier,
            page=page,
            per_page=min(per_page, 100),
            session=session,
            settings=settings,
        )
        session.commit()
        return result


@server.tool()
def get_release(
    identifier: str, release_id: str, include_notes: bool = False
) -> dict[str, Any]:
    """One release by internal id or provider id; notes fetched on demand."""
    with _open() as session:
        try:
            result = releases.get_release(
                identifier,
                release_id,
                include_notes=include_notes,
                session=session,
                settings=settings,
            )
        except Exception as exc:
            return _error(exc)
        session.commit()
        return result


@server.tool()
def get_latest_release(identifier: str) -> dict[str, Any]:
    """Latest stable release, falling back to the latest non-draft release."""
    with _open() as session:
        result = releases.latest_release(identifier, session=session)
        session.commit()
        return result


@server.tool()
def list_packages(
    repository_id: int | None = None, project_id: int | None = None, page: int = 1, per_page: int = 20
) -> dict[str, Any]:
    """List packages across registries."""
    with _open() as session:
        result = queries.list_packages(
            session,
            page=page,
            per_page=min(per_page, 100),
            repository_id=repository_id,
            project_id=project_id,
        )
        session.commit()
        return result


@server.tool()
def list_repository_packages(identifier: str) -> dict[str, Any]:
    """Packages registered against one repository."""
    with _open() as session:
        try:
            result = repositories.list_repository_packages(identifier, session=session)
        except Exception as exc:
            return _error(exc)
        session.commit()
        return result


@server.tool()
def get_package(package_id: int) -> dict[str, Any]:
    """Package details across a registry, including its repository."""
    with _open() as session:
        try:
            result = releases.get_package(package_id, session=session)
        except Exception as exc:
            return _error(exc)
        session.commit()
        return result


@server.tool()
def get_package_versions(package_id: int, page: int = 1, per_page: int = 50) -> dict[str, Any]:
    """Paginated package release history."""
    with _open() as session:
        result = releases.package_versions(
            package_id, page=page, per_page=min(per_page, 200), session=session
        )
        session.commit()
        return result


@server.tool()
def get_workflow_status(identifier: str) -> dict[str, Any]:
    """CI state, latest run, recent failures and default-branch success alignment."""
    with _open() as session:
        repo = queries.get_repository(session, identifier)
        if repo is None:
            return {"error": "not_found", "identifier": identifier}
        result = queries.workflow_status(session, repo)
        session.commit()
        return result


@server.tool()
def get_workflows(identifier: str, live: bool = False) -> dict[str, Any]:
    """Workflow names with latest run, run counts and failures."""
    with _open() as session:
        try:
            result = ci.list_workflows(identifier, live=live, session=session, settings=settings)
        except Exception as exc:
            return _error(exc)
        session.commit()
        return result


@server.tool()
def get_workflow_runs(
    identifier: str,
    branch: str | None = None,
    status: str | None = None,
    conclusion: str | None = None,
    page: int = 1,
    per_page: int = 30,
    live: bool = False,
) -> dict[str, Any]:
    """Paginated CI runs with branch/status/conclusion filters and a summary."""
    with _open() as session:
        try:
            result = ci.list_workflow_runs(
                identifier,
                branch=branch,
                status=status,
                conclusion=conclusion,
                page=page,
                per_page=min(per_page, 100),
                live=live,
                session=session,
                settings=settings,
            )
        except Exception as exc:
            return _error(exc)
        session.commit()
        return result


@server.tool()
def get_workflow_run_logs(
    identifier: str, run_id: int, max_bytes: int = 200_000
) -> dict[str, Any]:
    """Bounded CI run log text; source files are never included."""
    with _open() as session:
        try:
            result = ci.workflow_run_logs(
                identifier,
                run_id,
                max_bytes=min(max(max_bytes, 1_000), 1_000_000),
                session=session,
                settings=settings,
            )
        except Exception as exc:
            return _error(exc)
        session.commit()
        return result


@server.tool()
def get_security(identifier: str) -> dict[str, Any]:
    """Dependabot alerts, advisories and dependency summary for a repository."""
    with _open() as session:
        try:
            result = ci.security(identifier, session=session, settings=settings)
        except Exception as exc:
            return _error(exc)
        session.commit()
        return result


@server.tool()
def get_dependencies(identifier: str) -> dict[str, Any]:
    """Provider-reported dependency graph summary; no manifests are downloaded."""
    with _open() as session:
        try:
            result = ci.dependencies(identifier, session=session, settings=settings)
        except Exception as exc:
            return _error(exc)
        session.commit()
        return result


@server.tool()
def get_repository_activity(identifier: str, days: int = 90) -> dict[str, Any]:
    """Activity summary and locally recorded observations."""
    with _open() as session:
        repo = queries.get_repository(session, identifier)
        if repo is None:
            return {"error": "not_found", "identifier": identifier}
        result = queries.repository_activity(session, repo, days=min(days, 3650))
        session.commit()
        return result


@server.tool()
def get_project_summary(identifier: str) -> dict[str, Any]:
    """Project plus per-repository stats and version alignment."""
    with _open() as session:
        project = queries.get_project(session, identifier)
        if project is None:
            return {"error": "not_found", "identifier": identifier}
        result = {
            "project": project_detail(project),
            "repositories": [
                {
                    "repository": repository_summary(repo),
                    "stats": queries.repository_stats(session, repo),
                    "activity": queries.repository_activity(session, repo, days=90),
                }
                for repo in project.repositories
            ],
        }
        session.commit()
        return result


@server.tool()
def get_portfolio_summary() -> dict[str, Any]:
    """Cross-project counts, health, latest commits, releases and CI state."""
    with _open() as session:
        result = queries.portfolio_summary(session)
        session.commit()
        return result


@server.tool()
def get_recent_activity(limit: int = 10) -> dict[str, Any]:
    """Recent releases, pull requests and packages across the portfolio."""
    with _open() as session:
        result = {
            "releases": queries.recent_releases(session, limit=min(limit, 50)),
            "pull_requests": queries.recent_pull_requests(session, limit=min(limit, 50)),
            "packages": queries.recent_packages(session, limit=min(limit, 50)),
        }
        session.commit()
        return result


@server.tool()
def search_projects(query: str, limit: int = 20) -> dict[str, Any]:
    """Search projects by name, slug or description."""
    with _open() as session:
        result = queries.search_projects(session, query, limit=min(limit, 50))
        session.commit()
        return {"items": result}


@server.tool()
def request_refresh(identifier: str, scope: str = "metadata", force: bool = False) -> dict[str, Any]:
    """Enqueue a read-only synchronization job and return its id."""
    with _open() as session:
        result = refresh_api.request_refresh(
            identifier, RefreshRequest(scope=scope, force=force), session=session
        )
        session.commit()
        return result


@server.tool()
def get_refresh_status(identifier: str) -> dict[str, Any]:
    """Freshness, lifecycle and recent jobs for a repository."""
    with _open() as session:
        result = repositories.refresh_status(identifier, session=session)
        session.commit()
        return result


@server.tool()
def get_job(job_id: int) -> dict[str, Any]:
    """Status and outcome of one queued or completed refresh job."""
    with _open() as session:
        try:
            result = refresh_api.get_job(job_id, session=session)
        except Exception as exc:
            return _error(exc)
        session.commit()
        return result


@server.tool()
def list_jobs(
    status: str | None = None,
    repository_id: int | None = None,
    page: int = 1,
    per_page: int = 50,
) -> dict[str, Any]:
    """List refresh jobs with status and repository filters."""
    with _open() as session:
        result = refresh_api.list_jobs(
            status=status,
            repository_id=repository_id,
            page=page,
            per_page=min(per_page, 100),
            session=session,
        )
        session.commit()
        return result


@server.tool()
def get_provider_status() -> dict[str, Any]:
    """Provider rate limits, cache, job queue and webhook freshness."""
    with _open() as session:
        result = refresh_api.provider_status(session=session)
        session.commit()
        return result


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def run_mcp(transport: str = "stdio", host: str = "127.0.0.1", port: int = 8001) -> None:
    if transport == "stdio":
        server.run(transport="stdio")
    else:
        server.run(transport=transport, host=host, port=port)


if __name__ == "__main__":
    run_mcp()
