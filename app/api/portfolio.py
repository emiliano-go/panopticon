from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.deps import get_db
from app.models import Commit, Issue, PullRequest, Release, Repository
from app.security import is_authenticated
from app.serializers import commit_data, issue_data, pull_request_data, release_data
from app.services import queries

router = APIRouter(tags=["portfolio"])


def _include_private(request: Request) -> bool:
    return not request.app.state.settings.auth_required or is_authenticated(request)


@router.get("/summary")
def summary(request: Request, session: Session = Depends(get_db)):
    return queries.portfolio_summary(session, include_private=_include_private(request))


@router.get("/activity")
def activity(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    session: Session = Depends(get_db),
):
    private = _include_private(request)
    repo_stmt = select(Repository)
    if not private:
        repo_stmt = repo_stmt.where(Repository.visibility != "private")
    repos = {repo.id: repo for repo in session.scalars(repo_stmt).all()}
    commits = session.scalars(
        select(Commit)
        .where(Commit.repository_id.in_(list(repos)))
        .order_by(Commit.committed_at.desc())
        .limit(limit)
    ).all()
    releases = session.scalars(
        select(Release)
        .where(Release.repository_id.in_(list(repos)), Release.draft.is_(False))
        .order_by(Release.published_at.desc())
        .limit(limit)
    ).all()
    pull_requests = session.scalars(
        select(PullRequest)
        .where(PullRequest.repository_id.in_(list(repos)))
        .order_by(PullRequest.updated_at.desc())
        .limit(limit)
    ).all()
    issues = session.scalars(
        select(Issue)
        .where(Issue.repository_id.in_(list(repos)))
        .order_by(Issue.updated_at.desc())
        .limit(limit)
    ).all()

    def repo_ref(repo_id: int) -> dict:
        repo = repos[repo_id]
        return {"id": repo.id, "full_name": repo.full_name, "url": repo.url}

    return {
        "commits": [
            {**commit_data(commit), "repository": repo_ref(commit.repository_id)}
            for commit in commits
        ],
        "releases": [
            {**release_data(release), "repository": repo_ref(release.repository_id)}
            for release in releases
        ],
        "pull_requests": [
            {**pull_request_data(pr), "repository": repo_ref(pr.repository_id)}
            for pr in pull_requests
        ],
        "issues": [
            {**issue_data(issue), "repository": repo_ref(issue.repository_id)}
            for issue in issues
        ],
    }


@router.get("/releases/recent")
def recent_releases(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    session: Session = Depends(get_db),
):
    return {
        "items": queries.recent_releases(
            session, limit=limit, include_private=_include_private(request)
        )
    }


@router.get("/pull-requests/recent")
def recent_pull_requests(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    session: Session = Depends(get_db),
):
    return {
        "items": queries.recent_pull_requests(
            session, limit=limit, include_private=_include_private(request)
        )
    }


@router.get("/packages/recent")
def recent_packages(
    limit: int = Query(20, ge=1, le=100), session: Session = Depends(get_db)
):
    return {"items": queries.recent_packages(session, limit=limit)}
