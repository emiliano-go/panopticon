from __future__ import annotations

from datetime import timezone
from typing import Any
from urllib.parse import quote

from sqlalchemy.orm import Session

from app.config import Settings
from app.providers.base import ProviderError, UnsupportedCapability
from app.services.cache import CachedHTTPClient
from app.utils import is_prerelease, version_key

REGISTRIES: dict[str, dict[str, str]] = {
    "pypi": {"ecosystem": "python", "base": "https://pypi.org"},
    "npm": {"ecosystem": "javascript", "base": "https://registry.npmjs.org"},
    "crates": {"ecosystem": "rust", "base": "https://crates.io"},
    "go": {"ecosystem": "go", "base": "https://proxy.golang.org"},
}


class RegistryProvider:
    """Public registry adapter. Supports independent ecosystems behind one shape."""

    name = "registry"

    def __init__(
        self, session: Session, settings: Settings, registry: str, client: CachedHTTPClient | None = None
    ) -> None:
        if registry not in REGISTRIES:
            if registry in {"github_packages"}:
                raise UnsupportedCapability("github_packages", "package_registry")
            raise ProviderError(f"Unsupported registry {registry!r}")
        self.registry = registry
        self.session = session
        self.settings = settings
        self._client = client or CachedHTTPClient(session, settings, registry)

    def close(self) -> None:
        self._client.close()

    def fetch(self, name: str, *, force: bool = False) -> dict[str, Any]:
        if self.registry == "pypi":
            return self._pypi(name, force)
        if self.registry == "npm":
            return self._npm(name, force)
        if self.registry == "crates":
            return self._crates(name, force)
        if self.registry == "go":
            return self._go(name, force)
        raise UnsupportedCapability(self.registry, "package_registry")

    def _stable(self, versions: list[dict[str, Any]], latest: str | None) -> str | None:
        stable = [
            v["version"]
            for v in versions
            if not v.get("prerelease") and not v.get("yanked") and version_key(v["version"])
        ]
        if not stable:
            return latest
        stable.sort(key=lambda v: version_key(v), reverse=True)  # type: ignore[arg-type]
        return stable[0]

    def _pypi(self, name: str, force: bool) -> dict[str, Any]:
        result = self._client.get_json(
            f"{REGISTRIES['pypi']['base']}/pypi/{quote(name, safe='')}/json",
            resource=f"pypi:{name}",
            ttl=6 * 3600,
            force=force,
        )
        data = result.payload or {}
        info = data.get("info") or {}
        versions: list[dict[str, Any]] = []
        for version, files in (data.get("releases") or {}).items():
            first = files[0] if files else {}
            published = first.get("upload_time_iso_8601") or first.get("upload_time")
            versions.append(
                {
                    "version": version,
                    "published_at": published,
                    "yanked": any(f.get("yanked") for f in files) if files else False,
                    "deprecated": False,
                    "prerelease": is_prerelease(version),
                    "metadata": {"file_count": len(files)},
                }
            )
        versions.sort(key=lambda v: v.get("published_at") or "", reverse=True)
        versions = versions[:300]
        latest = info.get("version")
        maintainers = [
            value
            for value in [info.get("author"), info.get("maintainer")]
            if value
        ]
        return {
            "registry": "pypi",
            "ecosystem": "python",
            "name": info.get("name") or name,
            "url": info.get("package_url") or f"https://pypi.org/project/{name}/",
            "latest_version": latest,
            "latest_stable_version": self._stable(versions, latest),
            "published_at": versions[0]["published_at"] if versions else None,
            "description": info.get("summary"),
            "license": info.get("license"),
            "maintainers": maintainers,
            "downloads": None,
            "downloads_period": None,
            "deprecated": False,
            "yanked": any(v["yanked"] for v in versions if v["version"] == latest),
            "versions": versions,
        }

    def _npm(self, name: str, force: bool) -> dict[str, Any]:
        encoded = quote(name, safe="@")
        result = self._client.get_json(
            f"{REGISTRIES['npm']['base']}/{encoded}",
            resource=f"npm:{name}",
            ttl=6 * 3600,
            force=force,
        )
        data = result.payload or {}
        tags = data.get("dist-tags") or {}
        times = data.get("time") or {}
        versions: list[dict[str, Any]] = []
        for version, meta in (data.get("versions") or {}).items():
            versions.append(
                {
                    "version": version,
                    "published_at": times.get(version),
                    "yanked": False,
                    "deprecated": bool(meta.get("deprecated")),
                    "prerelease": is_prerelease(version),
                    "metadata": {
                        "license": meta.get("license"),
                        "deprecated_message": meta.get("deprecated"),
                    },
                }
            )
        versions.sort(key=lambda v: v.get("published_at") or "", reverse=True)
        versions = versions[:500]
        latest = tags.get("latest")
        latest_meta = (data.get("versions") or {}).get(latest or "", {})
        downloads = None
        try:
            dl = self._client.get_json(
                f"https://api.npmjs.org/downloads/point/last-month/{encoded}",
                resource=f"npm_downloads:{name}",
                ttl=24 * 3600,
            )
            if isinstance(dl.payload, dict):
                downloads = dl.payload.get("downloads")
        except ProviderError:
            downloads = None
        return {
            "registry": "npm",
            "ecosystem": "javascript",
            "name": data.get("name") or name,
            "url": f"https://www.npmjs.com/package/{name}",
            "latest_version": latest,
            "latest_stable_version": tags.get("latest") if not is_prerelease(tags.get("latest")) else self._stable(versions, latest),
            "published_at": times.get(latest or "") or (versions[0]["published_at"] if versions else None),
            "description": data.get("description") or latest_meta.get("description"),
            "license": latest_meta.get("license") or data.get("license"),
            "maintainers": [m.get("name") for m in data.get("maintainers") or [] if m.get("name")],
            "downloads": downloads,
            "downloads_period": "last-month" if downloads is not None else None,
            "deprecated": bool(latest_meta.get("deprecated")),
            "yanked": False,
            "versions": versions,
        }

    def _crates(self, name: str, force: bool) -> dict[str, Any]:
        result = self._client.get_json(
            f"{REGISTRIES['crates']['base']}/api/v1/crates/{quote(name, safe='')}",
            resource=f"crates:{name}",
            ttl=6 * 3600,
            force=force,
            headers={"User-Agent": self.settings.user_agent},
        )
        data = result.payload or {}
        crate = data.get("crate") or {}
        versions: list[dict[str, Any]] = []
        for item in data.get("versions") or []:
            versions.append(
                {
                    "version": item.get("num"),
                    "published_at": item.get("created_at"),
                    "yanked": bool(item.get("yanked")),
                    "deprecated": False,
                    "prerelease": is_prerelease(item.get("num")),
                    "metadata": {"license": item.get("license"), "downloads": item.get("downloads")},
                }
            )
        versions.sort(key=lambda v: v.get("published_at") or "", reverse=True)
        versions = versions[:300]
        latest = crate.get("max_version") or crate.get("newest_version")
        return {
            "registry": "crates",
            "ecosystem": "rust",
            "name": crate.get("id") or name,
            "url": f"https://crates.io/crates/{name}",
            "latest_version": latest,
            "latest_stable_version": crate.get("max_stable_version") or self._stable(versions, latest),
            "published_at": versions[0]["published_at"] if versions else None,
            "description": crate.get("description"),
            "license": crate.get("license"),
            "maintainers": [],
            "downloads": crate.get("downloads"),
            "downloads_period": "all-time",
            "deprecated": False,
            "yanked": False,
            "versions": versions,
        }

    def _go(self, name: str, force: bool) -> dict[str, Any]:
        base = f"{REGISTRIES['go']['base']}/{quote(name, safe='/')}"
        listing = self._client.get_json(
            f"{base}/@v/list",
            resource=f"go_list:{name}",
            ttl=12 * 3600,
            force=force,
        )
        raw = listing.payload
        if isinstance(raw, str):
            versions_text = raw.splitlines()
        elif isinstance(raw, list):
            versions_text = [str(item) for item in raw]
        else:
            versions_text = []
        versions = [
            {
                "version": version,
                "published_at": None,
                "yanked": False,
                "deprecated": False,
                "prerelease": is_prerelease(version),
                "metadata": {},
            }
            for version in versions_text
            if version.strip()
        ]
        versions.sort(key=lambda v: version_key(v["version"]) or version_key("0.0.0"), reverse=True)  # type: ignore[arg-type]
        latest = versions[0]["version"] if versions else None
        if latest:
            try:
                info = self._client.get_json(
                    f"{base}/@v/{quote(latest, safe='')}.info",
                    resource=f"go_info:{name}:{latest}",
                    ttl=12 * 3600,
                )
                if isinstance(info.payload, dict):
                    versions[0]["published_at"] = info.payload.get("Time")
                    versions[0]["metadata"] = {"commit_time": info.payload.get("Time")}
            except ProviderError:
                pass
        return {
            "registry": "go",
            "ecosystem": "go",
            "name": name,
            "url": f"https://pkg.go.dev/{name}",
            "latest_version": latest,
            "latest_stable_version": self._stable(versions, latest),
            "published_at": versions[0]["published_at"] if versions else None,
            "description": None,
            "license": None,
            "maintainers": [],
            "downloads": None,
            "downloads_period": None,
            "deprecated": False,
            "yanked": False,
            "versions": versions[:300],
        }


def registry_provider(
    session: Session, settings: Settings, registry: str
) -> RegistryProvider:
    return RegistryProvider(session, settings, registry)
