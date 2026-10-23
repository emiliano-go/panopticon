from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


class ProviderError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class UnsupportedCapability(ProviderError):
    def __init__(self, provider: str, capability: str) -> None:
        super().__init__(f"{provider} does not support capability {capability!r}")
        self.provider = provider
        self.capability = capability


class RateLimitError(ProviderError):
    def __init__(self, message: str, reset_at: datetime | None = None) -> None:
        super().__init__(message, status=429)
        self.reset_at = reset_at


class NotFound(ProviderError):
    def __init__(self, message: str = "Resource not found") -> None:
        super().__init__(message, status=404)


@dataclass
class Page:
    items: list[dict[str, Any]] = field(default_factory=list)
    next_page: int | None = None
    next_url: str | None = None
    etag: str | None = None


CAPABILITIES = {
    "repository_discovery",
    "repository_metadata",
    "branches",
    "tags",
    "commits",
    "commit_detail",
    "compare",
    "pull_requests",
    "issues",
    "releases",
    "workflows",
    "security",
    "dependencies",
}


class ProviderAdapter:
    name = "base"
    capabilities: set[str] = set()

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities

    def require(self, capability: str) -> None:
        if not self.supports(capability):
            raise UnsupportedCapability(self.name, capability)
