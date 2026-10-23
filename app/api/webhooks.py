from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.deps import get_db, get_settings
from app.errors import APIError
from app.models import Repository, WebhookDelivery
from app.security import verify_webhook_signature
from app.services.sync import enqueue_job, ensure_standalone_project, upsert_repository
from app.utils import auth_scope, stable_hash, strip_patches, utcnow

router = APIRouter(tags=["webhooks"])

EVENT_SCOPES = {
    "push": ["commits", "metadata"],
    "pull_request": ["pull_requests"],
    "issues": ["issues"],
    "release": ["releases"],
    "workflow_run": ["workflows"],
    "repository": ["metadata"],
    "create": ["branches", "tags"],
    "delete": ["branches", "tags"],
    "public": ["metadata"],
    "fork": ["metadata"],
}


@router.post("/webhooks/github", status_code=202)
async def github_webhook(
    request: Request,
    x_github_event: str = Header(default="ping"),
    x_github_delivery: str | None = Header(default=None),
    x_hub_signature_256: str | None = Header(default=None),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    body = await request.body()
    if not settings.github_webhook_secret:
        raise APIError(503, "webhook_not_configured", "GITHUB_WEBHOOK_SECRET is not configured")
    if not verify_webhook_signature(settings.github_webhook_secret, body, x_hub_signature_256):
        raise APIError(401, "invalid_signature", "Webhook signature verification failed")
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise APIError(400, "invalid_payload", "Webhook body is not valid JSON") from exc
    delivery_id = x_github_delivery or stable_hash(body)
    existing = session.scalar(
        select(WebhookDelivery).where(WebhookDelivery.delivery_id == delivery_id)
    )
    if existing:
        return {"status": "duplicate", "delivery_id": delivery_id}

    action = payload.get("action")
    raw_repo = payload.get("repository") or {}
    repo = None
    if raw_repo:
        repo = session.scalar(
            select(Repository).where(
                Repository.provider == "github",
                Repository.provider_id == str(raw_repo.get("id")),
            )
        )
        if repo is None and raw_repo.get("full_name"):
            repo = session.scalar(
                select(Repository).where(
                    Repository.provider == "github",
                    Repository.full_name == raw_repo.get("full_name"),
                )
            )
        if repo is None and raw_repo.get("id") and raw_repo.get("full_name"):
            repo = upsert_repository(session, raw_repo, auth_scope(settings.github_token))
            ensure_standalone_project(session, repo)

    if repo is not None:
        raw_repo = payload.get("repository") or raw_repo
        if raw_repo.get("full_name") and x_github_event == "repository" and action in {
            "renamed",
            "transferred",
            "archived",
            "unarchived",
        }:
            upsert_repository(session, raw_repo, repo.auth_scope)
        if x_github_event == "repository" and action == "deleted":
            repo.lifecycle = "deleted"
            repo.refresh_status = "stale"
        for scope in EVENT_SCOPES.get(x_github_event, ["metadata"]):
            enqueue_job(session, repo.id, scope, priority=95)

    delivery = WebhookDelivery(
        delivery_id=delivery_id,
        event=x_github_event,
        action=action,
        repository_id=repo.id if repo else None,
        status="queued" if repo else "ignored",
        received_at=utcnow(),
        processed_at=utcnow(),
        payload_hash=hashlib.sha256(body).hexdigest(),
        payload=strip_patches(payload),
    )
    session.add(delivery)
    session.flush()
    return {
        "status": "accepted",
        "delivery_id": delivery_id,
        "event": x_github_event,
        "repository_id": repo.id if repo else None,
    }
