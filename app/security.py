from __future__ import annotations

import hashlib
import hmac

from fastapi import Request

from app.config import Settings


def _extract_token(request: Request) -> str | None:
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    api_key = request.headers.get("x-api-key")
    if api_key:
        return api_key.strip()
    return None


def is_authenticated(request: Request) -> bool:
    settings: Settings = request.app.state.settings
    if not settings.auth_required:
        return True
    token = _extract_token(request)
    if not token:
        return False
    return hmac.compare_digest(token, settings.api_auth_token)


def require_auth(request: Request) -> None:
    from app.errors import APIError

    if not is_authenticated(request):
        raise APIError(
            401,
            "unauthorized",
            "A valid bearer token or X-API-Key header is required",
        )


def verify_webhook_signature(secret: str, body: bytes, signature: str | None) -> bool:
    if not secret:
        return False
    if not signature or not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
