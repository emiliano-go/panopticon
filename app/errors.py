from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse


class APIError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


class UnsupportedFeature(APIError):
    def __init__(self, provider: str, capability: str) -> None:
        super().__init__(
            422,
            "unsupported_capability",
            f"{provider} does not support {capability}",
            {"provider": provider, "capability": capability},
        )


class PermissionDenied(APIError):
    def __init__(self, message: str = "Permission denied") -> None:
        super().__init__(403, "permission_denied", message)


class NotFoundError(APIError):
    def __init__(self, resource: str) -> None:
        super().__init__(404, "not_found", f"{resource} not found")


async def api_error_handler(_request: Request, exc: APIError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            }
        },
    )
