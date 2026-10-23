from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.deps import get_db, get_settings
from app.errors import APIError, NotFoundError
from app.models import Project, Repository
from app.schemas import ProjectCreate, ProjectUpdate
from app.serializers import project_detail, project_summary
from app.services import queries
from app.services.sync import register_repository_url
from app.utils import slugify

router = APIRouter(tags=["projects"])


@router.get("/projects")
def list_projects(
    owner: str | None = None,
    provider: str | None = None,
    language: str | None = None,
    topic: str | None = None,
    visibility: str | None = None,
    archived: bool | None = None,
    group: bool | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    session: Session = Depends(get_db),
):
    return queries.list_projects(
        session,
        page=page,
        per_page=per_page,
        owner=owner,
        provider=provider,
        language=language,
        topic=topic,
        visibility=visibility,
        archived=archived,
        group=group,
    )


@router.post("/projects", status_code=201)
def create_project(
    body: ProjectCreate,
    request: Request,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    slug = slugify(body.slug or body.name)
    if session.scalar(select(Project).where(Project.slug == slug)):
        raise APIError(409, "conflict", f"Project slug {slug!r} already exists")
    project = Project(
        name=body.name,
        slug=slug,
        description=body.description,
        owner=body.owner,
        homepage=body.homepage,
        visibility=body.visibility,
        aliases=body.aliases,
        tags=body.tags,
        categories=body.categories,
        metadata_=body.metadata,
        is_group=body.is_group,
    )
    session.add(project)
    session.flush()
    for repo_id in body.repository_ids:
        repo = session.get(Repository, repo_id)
        if repo is None:
            raise NotFoundError(f"repository {repo_id}")
        project.repositories.append(repo)
    for url in body.repository_urls:
        try:
            repo = register_repository_url(session, settings, url)
        except Exception as exc:
            raise APIError(422, "invalid_repository", str(exc)) from exc
        if repo not in project.repositories:
            project.repositories.append(repo)
    session.flush()
    return project_detail(project)


@router.get("/projects/{identifier}")
def get_project(identifier: str, session: Session = Depends(get_db)):
    project = queries.get_project(session, identifier)
    if project is None:
        raise NotFoundError("project")
    return project_detail(project)


@router.patch("/projects/{identifier}")
def update_project(
    identifier: str, body: ProjectUpdate, session: Session = Depends(get_db)
):
    project = queries.get_project(session, identifier)
    if project is None:
        raise NotFoundError("project")
    data = body.model_dump(exclude_unset=True)
    repository_ids = data.pop("repository_ids", None)
    metadata = data.pop("metadata", None)
    for key, value in data.items():
        if value is not None:
            setattr(project, key, value)
    if metadata is not None:
        project.metadata_ = metadata
    if repository_ids is not None:
        project.repositories.clear()
        for repo_id in repository_ids:
            repo = session.get(Repository, repo_id)
            if repo is None:
                raise NotFoundError(f"repository {repo_id}")
            project.repositories.append(repo)
    session.flush()
    return project_detail(project)


@router.delete("/projects/{identifier}", status_code=204)
def delete_project(identifier: str, session: Session = Depends(get_db)):
    project = queries.get_project(session, identifier)
    if project is None:
        raise NotFoundError("project")
    session.delete(project)
    session.flush()
    return None
