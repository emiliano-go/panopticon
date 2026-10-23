from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.deps import get_db, get_settings
from app.errors import APIError, NotFoundError
from app.models import ProviderState, Repository, SyncJob, WebhookDelivery
from app.schemas import RefreshRequest
from app.serializers import job_data, webhook_data
from app.services import queries
from app.services.cache import cache_stats
from app.services.sync import (
    SCOPES,
    discover_repositories,
    enqueue_job,
    schedule_recurring,
)
from app.utils import iso, utcnow

router = APIRouter(tags=["refresh", "system"])


@router.post("/repositories/{identifier}/refresh", status_code=202)
def request_refresh(
    identifier: str,
    body: RefreshRequest,
    session: Session = Depends(get_db),
):
    repo = queries.get_repository(session, identifier)
    if repo is None:
        raise NotFoundError("repository")
    if body.scope not in SCOPES:
        raise APIError(422, "invalid_scope", f"Unknown scope {body.scope!r}")
    payload = {
        key: value
        for key, value in body.model_dump().items()
        if key not in {"scope"} and value is not None
    }
    job = enqueue_job(
        session, repo.id, body.scope, priority=95, payload=payload
    )
    return {"job": job_data(job), "message": "Refresh enqueued"}


@router.get("/jobs/{job_id}")
def get_job(job_id: int, session: Session = Depends(get_db)):
    job = session.get(SyncJob, job_id)
    if job is None:
        raise NotFoundError("job")
    return job_data(job)


@router.get("/jobs")
def list_jobs(
    status: str | None = None,
    repository_id: int | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    session: Session = Depends(get_db),
):
    stmt = select(SyncJob)
    if status:
        stmt = stmt.where(SyncJob.status == status)
    if repository_id:
        stmt = stmt.where(SyncJob.repository_id == repository_id)
    stmt = stmt.order_by(SyncJob.created_at.desc())
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = session.scalars(stmt.offset((page - 1) * per_page).limit(per_page)).all()
    pages = (total + per_page - 1) // per_page if total else 0
    return {
        "items": [job_data(job) for job in rows],
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


@router.get("/system/provider-status")
def provider_status(session: Session = Depends(get_db)):
    states = session.scalars(select(ProviderState)).all()
    queue = dict(
        session.execute(
            select(SyncJob.status, func.count()).group_by(SyncJob.status)
        ).all()
    )
    last_sync = session.scalar(select(func.max(Repository.last_successful_fetch_at)))
    last_webhook = session.scalar(
        select(WebhookDelivery).order_by(WebhookDelivery.received_at.desc()).limit(1)
    )
    pending_webhooks = session.scalar(
        select(func.count(WebhookDelivery.id)).where(WebhookDelivery.status == "received")
    ) or 0
    lag = None
    if last_webhook and last_webhook.received_at:
        received = last_webhook.received_at
        if received.tzinfo is None:
            from datetime import timezone

            received = received.replace(tzinfo=timezone.utc)
        lag = int((utcnow() - received).total_seconds())
    return {
        "providers": [
            {
                "provider": state.provider,
                "family": state.family,
                "limit": state.limit,
                "remaining": state.remaining,
                "reset_at": iso(state.reset_at),
                "updated_at": iso(state.updated_at),
                "last_error": state.last_error,
            }
            for state in states
        ],
        "cache": cache_stats(session),
        "jobs": queue,
        "queued_jobs": queue.get("queued", 0),
        "running_jobs": queue.get("running", 0),
        "failed_jobs": queue.get("failed", 0),
        "last_synchronization_at": iso(last_sync),
        "webhook": {
            "pending": pending_webhooks,
            "last_received_at": iso(last_webhook.received_at) if last_webhook else None,
            "processing_lag_seconds": lag,
        },
        "generated_at": iso(utcnow()),
    }


@router.post("/system/discover")
def discover(
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    try:
        result = discover_repositories(session, settings)
    except Exception as exc:
        raise APIError(503, "provider_unavailable", str(exc)) from exc
    return result


@router.post("/system/schedule")
def schedule(
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    return {"scheduled": schedule_recurring(session, settings)}


@router.get("/system/webhooks")
def webhooks(
    limit: int = Query(50, ge=1, le=200),
    session: Session = Depends(get_db),
):
    rows = session.scalars(
        select(WebhookDelivery)
        .order_by(WebhookDelivery.received_at.desc())
        .limit(limit)
    ).all()
    return {"items": [webhook_data(row) for row in rows]}
