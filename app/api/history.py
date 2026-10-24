from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.deps import get_db, get_settings
from app.errors import APIError, NotFoundError
from app.models import Commit, Repository
from app.providers.github import GitHubProvider
from app.serializers import commit_data
from app.services import queries
from app.services.sync import _upsert_commit, sync_commit_detail
from app.utils import iso, parse_dt, utcnow

router = APIRouter(tags=["history"])


def _repo(session: Session, identifier: str) -> Repository:
    repo = queries.get_repository(session, identifier)
    if repo is None:
        raise NotFoundError("repository")
    return repo


def _pagination(page: int, per_page: int, total: int, has_next: bool) -> dict[str, Any]:
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


def _provider_commits(
    session: Session,
    settings: Settings,
    repo: Repository,
    *,
    page: int,
    per_page: int,
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
            sha=sha,
            author=author,
            since=since,
            until=until,
            path=path,
            page=page,
            per_page=per_page,
            force=force,
        )
        items = []
        raw_items = result.payload if isinstance(result.payload, list) else []
        for raw in raw_items:
            commit = _upsert_commit(session, repo, raw)
            if commit:
                items.append(commit_data(commit))
        session.flush()
        rows = [
            {
                "sha": (raw.get("sha") or "")[:7],
                "subject": ((raw.get("commit") or {}).get("message") or "").splitlines()[0]
                if (raw.get("commit") or {}).get("message")
                else None,
                "author": {
                    "name": ((raw.get("commit") or {}).get("author") or {}).get("name"),
                    "email": ((raw.get("commit") or {}).get("author") or {}).get("email"),
                    "timestamp": ((raw.get("commit") or {}).get("author") or {}).get("date"),
                },
                "committer": {
                    "name": ((raw.get("commit") or {}).get("committer") or {}).get("name"),
                    "email": ((raw.get("commit") or {}).get("committer") or {}).get("email"),
                    "timestamp": ((raw.get("commit") or {}).get("committer") or {}).get("date"),
                },
                "parents": [p.get("sha") for p in raw.get("parents") or []],
                "url": raw.get("html_url"),
                "freshness": {"is_stale": False, "refresh_status": "current", "fetched_at": iso(utcnow())},
            }
            for raw in raw_items
        ]
        return {
            "items": rows,
            "pagination": _pagination(page, per_page, len(rows), bool(result.next_url)),
            "source": "provider",
        }
    finally:
        provider.close()


@router.get("/repositories/{identifier}/commits")
def list_commits(
    identifier: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
    branch: str | None = None,
    sha: str | None = None,
    author: str | None = None,
    committer: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    path: str | None = None,
    q: str | None = None,
    first_parent: bool = False,
    merges: bool | None = None,
    live: bool = False,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    repo = _repo(session, identifier)
    if live or branch or author or since or until or path:
        return _provider_commits(
            session,
            settings,
            repo,
            page=page,
            per_page=per_page,
            sha=branch or sha,
            author=author,
            since=since.isoformat() if since else None,
            until=until.isoformat() if until else None,
            path=path,
            force=live,
        )
    stmt = select(Commit).where(Commit.repository_id == repo.id)
    if sha:
        stmt = stmt.where(Commit.sha.startswith(sha))
    if committer:
        stmt = stmt.where(
            or_(
                Commit.committer_name.ilike(f"%{committer}%"),
                Commit.committer_email.ilike(f"%{committer}%"),
            )
        )
    if q:
        stmt = stmt.where(Commit.message.ilike(f"%{q}%"))
    stmt = stmt.order_by(Commit.committed_at.desc())
    if first_parent or merges is not None:
        rows = session.scalars(stmt.limit(1000)).all()
        if first_parent:
            rows = [row for row in rows if len(row.parents or []) <= 1]
        if merges is True:
            rows = [row for row in rows if len(row.parents or []) > 1]
        elif merges is False:
            rows = [row for row in rows if len(row.parents or []) <= 1]
        total = len(rows)
        start = (page - 1) * per_page
        page_rows = rows[start : start + per_page]
        return {
            "items": [commit_data(commit) for commit in page_rows],
            "pagination": _pagination(page, per_page, total, start + per_page < total),
            "source": "local",
        }
    total = session.scalar(
        select(__import__("sqlalchemy").func.count()).select_from(stmt.subquery())
    ) or 0
    rows = session.scalars(stmt.offset((page - 1) * per_page).limit(per_page)).all()
    return {
        "items": [commit_data(commit) for commit in rows],
        "pagination": _pagination(page, per_page, total, page * per_page < total),
        "source": "local",
    }


@router.get("/repositories/{identifier}/commits/{sha}")
def get_commit(
    identifier: str,
    sha: str,
    include_files: bool = False,
    include_pulls: bool = True,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    repo = _repo(session, identifier)
    provider = GitHubProvider(session, settings, repo.auth_scope)
    try:
        result = provider.commit(repo.full_name, sha)
    except Exception as exc:
        commit = session.scalar(
            select(Commit).where(
                Commit.repository_id == repo.id, Commit.sha.startswith(sha)
            )
        )
        if commit is None:
            raise APIError(503, "provider_unavailable", str(exc)) from exc
        data = commit_data(commit, detail=True)
        data["files"] = []
        return data
    finally:
        provider.close()
    raw = result.payload or {}
    commit = _upsert_commit(session, repo, raw)
    session.flush()
    data = commit_data(commit, detail=True)
    files = []
    for item in raw.get("files") or []:
        files.append(
            {
                "filename": item.get("filename"),
                "previous_filename": item.get("previous_filename"),
                "status": item.get("status"),
                "additions": item.get("additions"),
                "deletions": item.get("deletions"),
                "changes": item.get("changes"),
                "blob_url": item.get("blob_url"),
            }
        )
    if include_files:
        data["files"] = files
    data["files_summary"] = {"count": len(files)}
    data["source"] = "provider"
    if include_pulls:
        try:
            provider = GitHubProvider(session, settings, repo.auth_scope)
            try:
                pulls = provider.commit_pulls(repo.full_name, raw.get("sha") or sha)
                data["pull_requests_detail"] = [
                    {
                        "number": pr.get("number"),
                        "title": pr.get("title"),
                        "state": pr.get("state"),
                        "merged_at": pr.get("merged_at"),
                        "url": pr.get("html_url"),
                    }
                    for pr in pulls
                ]
            finally:
                provider.close()
        except Exception:
            data["pull_requests_detail"] = []
    return data


@router.get("/repositories/{identifier}/branches/{branch}/commits")
def branch_commits(
    identifier: str,
    branch: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
    author: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    path: str | None = None,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    repo = _repo(session, identifier)
    return _provider_commits(
        session,
        settings,
        repo,
        page=page,
        per_page=per_page,
        sha=branch,
        author=author,
        since=since.isoformat() if since else None,
        until=until.isoformat() if until else None,
        path=path,
    )


@router.get("/repositories/{identifier}/compare/{base}...{head}")
def compare(
    identifier: str,
    base: str,
    head: str,
    include_files: bool = True,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    repo = _repo(session, identifier)
    provider = GitHubProvider(session, settings, repo.auth_scope)
    try:
        result = provider.compare(repo.full_name, base, head)
    except Exception as exc:
        raise APIError(503, "provider_unavailable", str(exc)) from exc
    finally:
        provider.close()
    raw = result.payload or {}
    commits = []
    for item in (raw.get("commits") or [])[:250]:
        commit = _upsert_commit(session, repo, item)
        commits.append(commit_data(commit) if commit else {"sha": item.get("sha")})
    session.flush()
    files = []
    for item in raw.get("files") or []:
        files.append(
            {
                "filename": item.get("filename"),
                "previous_filename": item.get("previous_filename"),
                "status": item.get("status"),
                "additions": item.get("additions"),
                "deletions": item.get("deletions"),
                "changes": item.get("changes"),
            }
        )
    data = {
        "base": base,
        "head": head,
        "status": raw.get("status"),
        "ahead_by": raw.get("ahead_by"),
        "behind_by": raw.get("behind_by"),
        "total_commits": raw.get("total_commits"),
        "commits": commits,
        "stats": raw.get("stats") or {},
        "source": "provider",
        "freshness": {
            "fetched_at": result.fetched_at.isoformat() if result.fetched_at else None,
            "is_stale": result.is_stale,
            "refresh_status": result.status,
        },
    }
    if include_files:
        data["files"] = files
        data["files_summary"] = {"count": len(files)}
    return data
