from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.deps import get_db, get_settings
from app.errors import APIError, NotFoundError, UnsupportedFeature
from app.models import Package, Repository, RepositoryBranch, RepositoryTag, SyncJob
from app.schemas import PackageCreate, RepositoryCreate, RefreshRequest
from app.security import is_authenticated
from app.serializers import (
    branch_data,
    freshness,
    job_data,
    package_data,
    project_detail,
    repository_detail,
    repository_summary,
    tag_data,
)
from app.services import queries
from app.services.sync import (
    enqueue_job,
    normalize_tag,
    register_repository_url,
    sync_branches,
    sync_packages,
    sync_tags,
)
from app.providers.registry import REGISTRIES, RegistryProvider
from app.utils import parse_dt, utcnow

router = APIRouter(tags=["repositories"])


def _repo(session: Session, identifier: str) -> Repository:
    repo = queries.get_repository(session, identifier)
    if repo is None:
        raise NotFoundError("repository")
    return repo


def _can_see_private(request: Request) -> bool:
    return not request.app.state.settings.auth_required or is_authenticated(request)


@router.get("/repositories")
def list_repositories(
    request: Request,
    owner: str | None = None,
    provider: str | None = None,
    language: str | None = None,
    topic: str | None = None,
    visibility: str | None = None,
    archived: bool | None = None,
    lifecycle: str | None = None,
    full_name: str | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    session: Session = Depends(get_db),
):
    if full_name:
        repo = queries.get_repository(session, full_name)
        if repo is None:
            raise NotFoundError("repository")
        return {"items": [repository_summary(repo)], "pagination": {"page": 1, "per_page": 1, "total": 1, "pages": 1, "has_next": False, "has_previous": False, "next_page": None}}
    return queries.list_repositories(
        session,
        page=page,
        per_page=per_page,
        owner=owner,
        provider=provider,
        language=language,
        topic=topic,
        visibility=visibility,
        archived=archived,
        lifecycle=lifecycle,
        include_private=_can_see_private(request),
    )


@router.post("/repositories", status_code=201)
def create_repository(
    body: RepositoryCreate,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    try:
        repo = register_repository_url(session, settings, body.url)
    except Exception as exc:
        raise APIError(422, "invalid_repository", str(exc)) from exc
    return repository_detail(repo)


@router.get("/repositories/{identifier}")
def get_repository(identifier: str, session: Session = Depends(get_db)):
    return repository_detail(_repo(session, identifier))


@router.get("/repositories/{identifier}/branches")
def list_branches(
    identifier: str,
    live: bool = False,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    repo = _repo(session, identifier)
    if live:
        try:
            sync_branches(session, settings, repo)
        except Exception as exc:
            raise APIError(503, "provider_unavailable", str(exc)) from exc
    branches = session.scalars(
        select(RepositoryBranch)
        .where(RepositoryBranch.repository_id == repo.id)
        .order_by(RepositoryBranch.is_default.desc(), RepositoryBranch.name)
    ).all()
    return {
        "items": [branch_data(branch) for branch in branches],
        "pagination": {"page": 1, "per_page": len(branches), "total": len(branches), "pages": 1, "has_next": False, "has_previous": False, "next_page": None},
    }


@router.get("/repositories/{identifier}/branches/{branch}")
def get_branch(
    identifier: str,
    branch: str,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    repo = _repo(session, identifier)
    row = session.scalar(
        select(RepositoryBranch).where(
            RepositoryBranch.repository_id == repo.id, RepositoryBranch.name == branch
        )
    )
    if row is None:
        raise NotFoundError("branch")
    data = branch_data(row)
    try:
        from app.providers.github import GitHubProvider

        provider = GitHubProvider(session, settings, repo.auth_scope)
        try:
            protection = provider.branch_protection(repo.full_name, branch)
            data["protection"] = protection.payload
            data["protected"] = True
        finally:
            provider.close()
    except Exception:
        data["protection"] = None
    return data


@router.get("/repositories/{identifier}/tags")
def list_tags(
    identifier: str,
    live: bool = False,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    repo = _repo(session, identifier)
    if live:
        try:
            sync_tags(session, settings, repo)
        except Exception as exc:
            raise APIError(503, "provider_unavailable", str(exc)) from exc
    tags = session.scalars(
        select(RepositoryTag)
        .where(RepositoryTag.repository_id == repo.id)
        .order_by(RepositoryTag.name.desc())
    ).all()
    return {
        "items": [tag_data(tag) for tag in tags],
        "pagination": {"page": 1, "per_page": len(tags), "total": len(tags), "pages": 1, "has_next": False, "has_previous": False, "next_page": None},
    }


@router.get("/repositories/{identifier}/tags/{tag_name}")
def get_tag(
    identifier: str,
    tag_name: str,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    repo = _repo(session, identifier)
    tag = session.scalar(
        select(RepositoryTag).where(
            RepositoryTag.repository_id == repo.id, RepositoryTag.name == tag_name
        )
    )
    if tag is None:
        raise NotFoundError("tag")
    if tag.tag_type is None:
        try:
            from app.providers.github import GitHubProvider

            provider = GitHubProvider(session, settings, repo.auth_scope)
            try:
                ref = provider.ref(repo.full_name, f"tags/{tag_name}")
                obj = (ref.payload or {}).get("object") or {}
                if obj.get("type") == "tag" and obj.get("sha"):
                    annotated = provider.annotated_tag(repo.full_name, obj["sha"])
                    payload = annotated.payload or {}
                    tag.tag_type = "annotated"
                    tag.tagger_name = (payload.get("tagger") or {}).get("name")
                    tag.tagger_email = (payload.get("tagger") or {}).get("email")
                    tag.tagger_date = parse_dt((payload.get("tagger") or {}).get("date"))
                    tag.target_sha = (payload.get("object") or {}).get("sha") or tag.target_sha
                else:
                    tag.tag_type = "lightweight"
                tag.fetched_at = utcnow()
                tag.last_successful_fetch_at = tag.fetched_at
                tag.refresh_status = "current"
            finally:
                provider.close()
        except Exception as exc:
            tag.last_error = str(exc)
    session.flush()
    return tag_data(tag)


@router.get("/repositories/{identifier}/stats")
def get_stats(identifier: str, session: Session = Depends(get_db)):
    return queries.repository_stats(session, _repo(session, identifier))


@router.get("/repositories/{identifier}/activity")
def get_activity(
    identifier: str,
    days: int = Query(90, ge=1, le=3650),
    session: Session = Depends(get_db),
):
    return queries.repository_activity(session, _repo(session, identifier), days=days)


@router.get("/repositories/{identifier}/refresh-status")
def refresh_status(identifier: str, session: Session = Depends(get_db)):
    repo = _repo(session, identifier)
    jobs = session.scalars(
        select(SyncJob)
        .where(SyncJob.repository_id == repo.id)
        .order_by(SyncJob.created_at.desc())
        .limit(20)
    ).all()
    return {
        "repository_id": repo.id,
        "lifecycle": repo.lifecycle,
        "freshness": freshness(repo),
        "last_error": repo.last_error,
        "jobs": [job_data(job) for job in jobs],
    }


@router.get("/repositories/{identifier}/packages")
def list_repository_packages(identifier: str, session: Session = Depends(get_db)):
    repo = _repo(session, identifier)
    packages = session.scalars(
        select(Package).where(Package.repository_id == repo.id).order_by(Package.name)
    ).all()
    return {
        "items": [package_data(package, repository=repo) for package in packages],
        "pagination": {"page": 1, "per_page": len(packages), "total": len(packages), "pages": 1, "has_next": False, "has_previous": False, "next_page": None},
    }


@router.post("/repositories/{identifier}/packages", status_code=201)
def create_package(
    identifier: str,
    body: PackageCreate,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    repo = _repo(session, identifier)
    if body.registry not in REGISTRIES:
        if body.registry == "github_packages":
            raise UnsupportedFeature("github_packages", "package_registry")
        raise APIError(422, "unsupported_registry", f"Unsupported registry {body.registry!r}")
    existing = session.scalar(
        select(Package).where(Package.registry == body.registry, Package.name == body.name)
    )
    if existing:
        raise APIError(409, "conflict", "Package is already registered")
    project_id = body.project_id or (repo.projects[0].id if repo.projects else None)
    package = Package(
        repository_id=repo.id,
        project_id=project_id,
        registry=body.registry,
        ecosystem=REGISTRIES[body.registry]["ecosystem"],
        name=body.name,
    )
    session.add(package)
    session.flush()
    try:
        provider = RegistryProvider(session, settings, body.registry)
        try:
            data = provider.fetch(body.name)
        finally:
            provider.close()
        from app.services.sync import _apply_package_data

        _apply_package_data(session, package, data)
    except Exception as exc:
        package.refresh_status = "refresh_failed"
        package.last_error = str(exc)
    session.flush()
    enqueue_job(session, repo.id, "packages", priority=60)
    return package_data(package, repository=repo)
