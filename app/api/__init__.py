from fastapi import APIRouter, Depends

from app.api import (
    collaboration,
    ci,
    history,
    portfolio,
    projects,
    refresh,
    releases,
    repositories,
)
from app.security import require_auth

api_router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_auth)])
api_router.include_router(projects.router)
api_router.include_router(repositories.router)
api_router.include_router(history.router)
api_router.include_router(collaboration.router)
api_router.include_router(releases.router)
api_router.include_router(ci.router)
api_router.include_router(portfolio.router)
api_router.include_router(refresh.router)
