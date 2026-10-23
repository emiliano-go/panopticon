from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.deps import get_db, get_settings
from app.errors import APIError, NotFoundError, UnsupportedFeature
from app.models import Repository, WorkflowRun
from app.providers.github import GitHubProvider
from app.serializers import workflow_run_data
from app.services import queries
from app.services.sync import sync_security, sync_workflows

router = APIRouter(tags=["ci", "security"])


def _repo(session: Session, identifier: str) -> Repository:
    repo = queries.get_repository(session, identifier)
    if repo is None:
        raise NotFoundError("repository")
    return repo


@router.get("/repositories/{identifier}/workflows")
def list_workflows(
    identifier: str,
    live: bool = False,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    repo = _repo(session, identifier)
    if live:
        try:
            sync_workflows(session, settings, repo, force=True)
        except Exception as exc:
            raise APIError(503, "provider_unavailable", str(exc)) from exc
    runs = session.scalars(
        select(WorkflowRun)
        .where(WorkflowRun.repository_id == repo.id)
        .order_by(WorkflowRun.started_at.desc())
        .limit(500)
    ).all()
    grouped: dict[str, dict] = {}
    for run in runs:
        key = run.workflow_name or run.workflow_id or "unknown"
        entry = grouped.setdefault(
            key,
            {
                "workflow_name": run.workflow_name,
                "workflow_id": run.workflow_id,
                "latest_run": workflow_run_data(run),
                "runs": 0,
                "failures": 0,
            },
        )
        entry["runs"] += 1
        if run.conclusion == "failure":
            entry["failures"] += 1
    return {"items": list(grouped.values()), "count": len(grouped)}


@router.get("/repositories/{identifier}/workflow-runs")
def list_workflow_runs(
    identifier: str,
    branch: str | None = None,
    status: str | None = None,
    conclusion: str | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
    live: bool = False,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    repo = _repo(session, identifier)
    if live:
        try:
            sync_workflows(session, settings, repo, force=True)
        except Exception as exc:
            raise APIError(503, "provider_unavailable", str(exc)) from exc
    stmt = select(WorkflowRun).where(WorkflowRun.repository_id == repo.id)
    if branch:
        stmt = stmt.where(WorkflowRun.branch == branch)
    if status:
        stmt = stmt.where(WorkflowRun.status == status)
    if conclusion:
        stmt = stmt.where(WorkflowRun.conclusion == conclusion)
    stmt = stmt.order_by(WorkflowRun.started_at.desc())
    total = session.scalar(select(__import__("sqlalchemy").func.count()).select_from(stmt.subquery())) or 0
    rows = session.scalars(stmt.offset((page - 1) * per_page).limit(per_page)).all()
    pages = (total + per_page - 1) // per_page if total else 0
    return {
        "items": [workflow_run_data(run) for run in rows],
        "summary": queries.workflow_status(session, repo),
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


@router.get("/repositories/{identifier}/workflow-runs/{run_id}/logs")
def workflow_run_logs(
    identifier: str,
    run_id: int,
    max_bytes: int = Query(200_000, ge=1_000, le=1_000_000),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    repo = _repo(session, identifier)
    provider = GitHubProvider(session, settings, repo.auth_scope)
    try:
        return provider.workflow_run_logs(repo.full_name, run_id, max_bytes=max_bytes)
    except Exception as exc:
        raise APIError(503, "provider_unavailable", str(exc)) from exc
    finally:
        provider.close()


@router.get("/repositories/{identifier}/security")
def security(
    identifier: str,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    repo = _repo(session, identifier)
    result = sync_security(session, settings, repo)
    return {
        "repository_id": repo.id,
        "capability": result.get("capability"),
        "dependabot_alerts": result.get("dependabot_alerts", []),
        "advisories": result.get("advisories", []),
        "dependency_summary": result.get("dependency_summary"),
        "errors": {
            key: value
            for key, value in result.items()
            if key.endswith("_error")
        },
        "note": "Security data is retrieved live through provider APIs and respects the configured credentials.",
    }


@router.get("/repositories/{identifier}/dependencies")
def dependencies(
    identifier: str,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    repo = _repo(session, identifier)
    result = sync_security(session, settings, repo)
    if result.get("dependency_summary") is None and result.get("dependencies_error"):
        raise APIError(
            422,
            "unsupported_capability",
            "Dependency graph metadata is unavailable for this repository or credentials",
            {"reason": result.get("dependencies_error")},
        )
    return {
        "repository_id": repo.id,
        "capability": "available" if result.get("dependency_summary") else "unavailable",
        "summary": result.get("dependency_summary"),
        "note": "No manifest files are downloaded or parsed; only provider metadata is used.",
    }
