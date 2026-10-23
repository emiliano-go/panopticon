from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.deps import get_db, get_settings
from app.errors import APIError, NotFoundError
from app.models import Issue, PullRequest, Repository
from app.providers.github import GitHubProvider
from app.serializers import issue_data, pull_request_data
from app.services import queries
from app.services.sync import normalize_issue, normalize_pull_request
from app.utils import utcnow

router = APIRouter(tags=["collaboration"])


def _repo(session: Session, identifier: str) -> Repository:
    repo = queries.get_repository(session, identifier)
    if repo is None:
        raise NotFoundError("repository")
    return repo


def _pagination(page: int, per_page: int, total: int, has_next: bool) -> dict:
    pages = (total + per_page - 1) // per_page if total else 0
    return {
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": pages,
        "has_next": has_next or page < pages,
        "has_previous": page > 1,
        "next_page": page + 1 if has_next or page < pages else None,
    }


@router.get("/repositories/{identifier}/pull-requests")
def list_pull_requests(
    identifier: str,
    state: str | None = None,
    branch: str | None = None,
    author: str | None = None,
    label: str | None = None,
    updated_since: datetime | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
    live: bool = False,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    repo = _repo(session, identifier)
    if live:
        from app.services.sync import sync_pull_requests

        try:
            sync_pull_requests(session, settings, repo, force=True)
        except Exception as exc:
            raise APIError(503, "provider_unavailable", str(exc)) from exc
    stmt = select(PullRequest).where(PullRequest.repository_id == repo.id)
    if state:
        stmt = stmt.where(PullRequest.state == state)
    if branch:
        stmt = stmt.where(
            or_(PullRequest.base_branch == branch, PullRequest.head_branch == branch)
        )
    if author:
        stmt = stmt.where(PullRequest.author == author)
    if label:
        stmt = stmt.where(PullRequest.labels.contains(label))
    if updated_since:
        stmt = stmt.where(PullRequest.updated_at >= updated_since)
    stmt = stmt.order_by(PullRequest.updated_at.desc())
    total = session.scalar(select(__import__("sqlalchemy").func.count()).select_from(stmt.subquery())) or 0
    rows = session.scalars(stmt.offset((page - 1) * per_page).limit(per_page)).all()
    return {
        "items": [pull_request_data(pr) for pr in rows],
        "pagination": _pagination(page, per_page, total, page * per_page < total),
    }


@router.get("/repositories/{identifier}/pull-requests/{number}")
def get_pull_request(
    identifier: str,
    number: int,
    include_files: bool = False,
    include_reviews: bool = False,
    include_checks: bool = True,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    repo = _repo(session, identifier)
    pr = session.scalar(
        select(PullRequest).where(
            PullRequest.repository_id == repo.id, PullRequest.number == number
        )
    )
    provider = GitHubProvider(session, settings, repo.auth_scope)
    try:
        result = provider.pull_request(repo.full_name, number)
        data = normalize_pull_request(result.payload or {})
        if pr is None:
            pr = PullRequest(repository_id=repo.id, number=number, title=data["title"])
            session.add(pr)
        for key, value in data.items():
            setattr(pr, key, value)
        pr.fetched_at = utcnow()
        pr.last_successful_fetch_at = pr.fetched_at
        pr.refresh_status = "current"
        pr.last_error = None
        session.flush()
        response = pull_request_data(pr)
        response["body"] = (result.payload or {}).get("body")
        if include_files:
            try:
                files = provider.pull_request_files(repo.full_name, number)
                response["files"] = [
                    {
                        "filename": item.get("filename"),
                        "status": item.get("status"),
                        "additions": item.get("additions"),
                        "deletions": item.get("deletions"),
                        "changes": item.get("changes"),
                    }
                    for item in files
                ]
            except Exception as exc:
                response["files_error"] = str(exc)
        if include_reviews:
            try:
                reviews = provider.pull_request_reviews(repo.full_name, number)
                response["reviews"] = [
                    {
                        "user": (review.get("user") or {}).get("login"),
                        "state": review.get("state"),
                        "submitted_at": review.get("submitted_at"),
                        "url": review.get("html_url"),
                    }
                    for review in reviews
                ]
                states = [review.get("state") for review in reviews]
                if "CHANGES_REQUESTED" in states:
                    response["review_status"] = "changes_requested"
                elif "APPROVED" in states:
                    response["review_status"] = "approved"
                elif "PENDING" in states:
                    response["review_status"] = "pending"
            except Exception as exc:
                response["reviews_error"] = str(exc)
        if include_checks and pr.head_sha:
            try:
                checks = provider.check_runs(repo.full_name, pr.head_sha)
                runs = (checks.payload or {}).get("check_runs") or []
                response["checks"] = [
                    {
                        "name": run.get("name"),
                        "status": run.get("status"),
                        "conclusion": run.get("conclusion"),
                        "url": run.get("html_url"),
                    }
                    for run in runs
                ]
                if any(run.get("conclusion") == "failure" for run in runs):
                    pr.checks_status = "failure"
                elif runs and all(run.get("status") == "completed" for run in runs):
                    pr.checks_status = "success"
                elif runs:
                    pr.checks_status = "pending"
                response["checks_status"] = pr.checks_status
                session.flush()
            except Exception as exc:
                response["checks_error"] = str(exc)
        return response
    finally:
        provider.close()


@router.get("/repositories/{identifier}/issues")
def list_issues(
    identifier: str,
    state: str | None = None,
    author: str | None = None,
    label: str | None = None,
    updated_since: datetime | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
    live: bool = False,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    repo = _repo(session, identifier)
    if live:
        from app.services.sync import sync_issues

        try:
            sync_issues(session, settings, repo, force=True)
        except Exception as exc:
            raise APIError(503, "provider_unavailable", str(exc)) from exc
    stmt = select(Issue).where(Issue.repository_id == repo.id)
    if state:
        stmt = stmt.where(Issue.state == state)
    if author:
        stmt = stmt.where(Issue.author == author)
    if label:
        stmt = stmt.where(Issue.labels.contains(label))
    if updated_since:
        stmt = stmt.where(Issue.updated_at >= updated_since)
    stmt = stmt.order_by(Issue.updated_at.desc())
    total = session.scalar(select(__import__("sqlalchemy").func.count()).select_from(stmt.subquery())) or 0
    rows = session.scalars(stmt.offset((page - 1) * per_page).limit(per_page)).all()
    return {
        "items": [issue_data(issue) for issue in rows],
        "pagination": _pagination(page, per_page, total, page * per_page < total),
    }


@router.get("/repositories/{identifier}/issues/{number}")
def get_issue(
    identifier: str,
    number: int,
    include_comments: bool = False,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    repo = _repo(session, identifier)
    issue = session.scalar(
        select(Issue).where(Issue.repository_id == repo.id, Issue.number == number)
    )
    provider = GitHubProvider(session, settings, repo.auth_scope)
    try:
        result = provider.issue(repo.full_name, number)
        data = normalize_issue(result.payload or {})
        if issue is None:
            issue = Issue(repository_id=repo.id, number=number, title=data["title"])
            session.add(issue)
        for key, value in data.items():
            setattr(issue, key, value)
        issue.fetched_at = utcnow()
        issue.last_successful_fetch_at = issue.fetched_at
        issue.refresh_status = "current"
        issue.last_error = None
        session.flush()
        response = issue_data(issue)
        response["body"] = (result.payload or {}).get("body")
        if include_comments:
            try:
                comments = provider.issue_comments(repo.full_name, number)
                response["comments"] = [
                    {
                        "user": (comment.get("user") or {}).get("login"),
                        "created_at": comment.get("created_at"),
                        "updated_at": comment.get("updated_at"),
                        "body": comment.get("body"),
                        "url": comment.get("html_url"),
                    }
                    for comment in comments
                ]
            except Exception as exc:
                response["comments_error"] = str(exc)
        return response
    finally:
        provider.close()
