from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.deps import get_db, get_settings
from app.errors import APIError, NotFoundError
from app.models import Package, PackageVersion, Release, Repository
from app.providers.github import GitHubProvider
from app.providers.registry import REGISTRIES
from app.schemas import PackageCreate
from app.serializers import (
    package_data,
    package_version_data,
    release_data,
    version_alignment,
)
from app.services import queries
from app.services.sync import _apply_package_data, enqueue_job, normalize_release, sync_releases
from app.utils import utcnow

router = APIRouter(tags=["releases", "packages"])


def _repo(session: Session, identifier: str) -> Repository:
    repo = queries.get_repository(session, identifier)
    if repo is None:
        raise NotFoundError("repository")
    return repo


@router.get("/repositories/{identifier}/releases/latest")
def latest_release(identifier: str, session: Session = Depends(get_db)):
    repo = _repo(session, identifier)
    release = session.scalar(
        select(Release)
        .where(
            Release.repository_id == repo.id,
            Release.draft.is_(False),
            Release.prerelease.is_(False),
        )
        .order_by(Release.published_at.desc())
        .limit(1)
    )
    if release is None:
        release = session.scalar(
            select(Release)
            .where(Release.repository_id == repo.id, Release.draft.is_(False))
            .order_by(Release.published_at.desc())
            .limit(1)
        )
    if release is None:
        raise NotFoundError("release")
    return release_data(release)


@router.get("/repositories/{identifier}/releases")
def list_releases(
    identifier: str,
    include_prereleases: bool = True,
    include_drafts: bool = False,
    live: bool = False,
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    repo = _repo(session, identifier)
    if live:
        try:
            sync_releases(session, settings, repo, force=True)
        except Exception as exc:
            raise APIError(503, "provider_unavailable", str(exc)) from exc
    stmt = select(Release).where(Release.repository_id == repo.id)
    if not include_prereleases:
        stmt = stmt.where(Release.prerelease.is_(False))
    if not include_drafts:
        stmt = stmt.where(Release.draft.is_(False))
    stmt = stmt.order_by(Release.published_at.desc())
    total = session.scalar(select(__import__("sqlalchemy").func.count()).select_from(stmt.subquery())) or 0
    rows = session.scalars(stmt.offset((page - 1) * per_page).limit(per_page)).all()
    pages = (total + per_page - 1) // per_page if total else 0
    return {
        "items": [release_data(release) for release in rows],
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


@router.get("/repositories/{identifier}/releases/{release_id}")
def get_release(
    identifier: str,
    release_id: str,
    include_notes: bool = False,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    repo = _repo(session, identifier)
    release = None
    if release_id.isdigit():
        release = session.get(Release, int(release_id))
        if release and release.repository_id != repo.id:
            release = None
    if release is None:
        release = session.scalar(
            select(Release).where(
                Release.repository_id == repo.id, Release.provider_id == release_id
            )
        )
    if release is None:
        raise NotFoundError("release")
    if include_notes and release.notes is None:
        provider = GitHubProvider(session, settings, repo.auth_scope)
        try:
            result = provider.release(repo.full_name, int(release.provider_id), force=True)
            data = normalize_release(result.payload or {}, include_notes=True)
            release.notes = data.get("notes")
            release.fetched_at = utcnow()
            release.last_successful_fetch_at = release.fetched_at
            release.refresh_status = "current"
            session.flush()
        except Exception as exc:
            raise APIError(503, "provider_unavailable", str(exc)) from exc
        finally:
            provider.close()
    return release_data(release, include_notes=include_notes)


@router.get("/packages")
def list_packages(
    repository_id: int | None = None,
    project_id: int | None = None,
    registry: str | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    session: Session = Depends(get_db),
):
    return queries.list_packages(
        session,
        page=page,
        per_page=per_page,
        repository_id=repository_id,
        project_id=project_id,
        registry=registry,
    )


@router.post("/packages", status_code=201)
def create_package(
    body: PackageCreate,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    if body.registry not in REGISTRIES:
        raise APIError(422, "unsupported_registry", f"Unsupported registry {body.registry!r}")
    existing = session.scalar(
        select(Package).where(Package.registry == body.registry, Package.name == body.name)
    )
    if existing:
        raise APIError(409, "conflict", "Package is already registered")
    package = Package(
        repository_id=body.repository_id,
        project_id=body.project_id,
        registry=body.registry,
        ecosystem=REGISTRIES[body.registry]["ecosystem"],
        name=body.name,
    )
    session.add(package)
    session.flush()
    from app.providers.registry import RegistryProvider

    try:
        provider = RegistryProvider(session, settings, body.registry)
        try:
            data = provider.fetch(body.name)
        finally:
            provider.close()
        _apply_package_data(session, package, data)
    except Exception as exc:
        package.refresh_status = "refresh_failed"
        package.last_error = str(exc)
    session.flush()
    if package.repository_id:
        enqueue_job(session, package.repository_id, "packages", priority=60)
    return package_data(package)


@router.get("/packages/{package_id}")
def get_package(package_id: int, session: Session = Depends(get_db)):
    package = session.get(Package, package_id)
    if package is None:
        raise NotFoundError("package")
    repo = session.get(Repository, package.repository_id) if package.repository_id else None
    return package_data(package, repository=repo)


@router.get("/packages/{package_id}/versions")
def package_versions(
    package_id: int,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    session: Session = Depends(get_db),
):
    package = session.get(Package, package_id)
    if package is None:
        raise NotFoundError("package")
    stmt = (
        select(PackageVersion)
        .where(PackageVersion.package_id == package_id)
        .order_by(PackageVersion.published_at.desc())
    )
    total = session.scalar(select(__import__("sqlalchemy").func.count()).select_from(stmt.subquery())) or 0
    rows = session.scalars(stmt.offset((page - 1) * per_page).limit(per_page)).all()
    pages = (total + per_page - 1) // per_page if total else 0
    return {
        "package": {"id": package.id, "name": package.name, "registry": package.registry},
        "items": [package_version_data(version) for version in rows],
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
