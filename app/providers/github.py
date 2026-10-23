from __future__ import annotations

import io
import zipfile
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.config import Settings
from app.providers.base import ProviderAdapter, ProviderError, RateLimitError
from app.services.cache import CachedHTTPClient
from app.utils import utcnow

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

TTL = {
    "metadata": 6 * 3600,
    "branches": 15 * 60,
    "tags": 60 * 60,
    "commits": 5 * 60,
    "pulls": 10 * 60,
    "issues": 10 * 60,
    "releases": 60 * 60,
    "workflows": 10 * 60,
    "security": 6 * 3600,
    "languages": 24 * 3600,
}


class GitHubProvider(ProviderAdapter):
    name = "github"
    capabilities = CAPABILITIES

    def __init__(
        self,
        session: Session,
        settings: Settings,
        scope: str | None = None,
        client: CachedHTTPClient | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self._client = client or CachedHTTPClient(session, settings, "github", scope)

    @property
    def client(self) -> CachedHTTPClient:
        return self._client

    def close(self) -> None:
        self._client.close()

    def _url(self, path: str) -> str:
        return f"{self.settings.github_api_url.rstrip('/')}/{path.lstrip('/')}"

    def _repo_path(self, full_name: str, suffix: str = "") -> str:
        return f"repos/{full_name}{suffix}"

    def repository(self, full_name: str, *, force: bool = False):
        return self.client.get_json(
            self._url(self._repo_path(full_name)),
            resource=f"repository:{full_name}",
            ttl=TTL["metadata"],
            force=force,
        )

    def languages(self, full_name: str):
        return self.client.get_json(
            self._url(self._repo_path(full_name, "/languages")),
            resource=f"languages:{full_name}",
            ttl=TTL["languages"],
        )

    def contributors(self, full_name: str):
        return self.client.get_json(
            self._url(self._repo_path(full_name, "/contributors")),
            resource=f"contributors:{full_name}",
            params={"per_page": 100, "anon": "true"},
            ttl=TTL["metadata"],
        )

    def authenticated_repositories(self) -> list[dict[str, Any]]:
        if self.settings.github_token:
            url = self._url("user/repos")
            params = {
                "affiliation": "owner,collaborator,organization_member",
                "sort": "updated",
                "per_page": 100,
            }
            repos = self.client.get_pages(
                url, resource="discovery:user", params=params, ttl=3600, max_pages=30
            )
        elif self.settings.github_username:
            url = self._url(f"users/{self.settings.github_username}/repos")
            repos = self.client.get_pages(
                url,
                resource="discovery:user",
                params={"per_page": 100, "sort": "updated"},
                ttl=3600,
                max_pages=30,
            )
        else:
            repos = []
        seen = {item.get("id") for item in repos}
        for org in self.settings.github_orgs_list:
            if self.settings.github_token:
                url = self._url(f"orgs/{org}/repos")
                params = {"per_page": 100}
            else:
                url = self._url(f"users/{org}/repos")
                params = {"per_page": 100}
            for item in self.client.get_pages(
                url, resource=f"discovery:org:{org}", params=params, ttl=3600, max_pages=30
            ):
                if item.get("id") not in seen:
                    repos.append(item)
                    seen.add(item.get("id"))
        return repos

    def organization_repositories(self, org: str) -> list[dict[str, Any]]:
        url = self._url(f"orgs/{org}/repos")
        return self.client.get_pages(
            url, resource=f"discovery:org:{org}", params={"per_page": 100}, ttl=3600
        )

    def branches(self, full_name: str, *, force: bool = False):
        return self.client.get_pages(
            self._url(self._repo_path(full_name, "/branches")),
            resource=f"branches:{full_name}",
            params={"per_page": 100},
            ttl=TTL["branches"],
            allow_stale=not force,
        )

    def branch(self, full_name: str, branch: str, *, force: bool = False):
        return self.client.get_json(
            self._url(self._repo_path(full_name, f"/branches/{branch}")),
            resource=f"branch:{full_name}:{branch}",
            ttl=TTL["branches"],
            force=force,
        )

    def branch_protection(self, full_name: str, branch: str):
        return self.client.get_json(
            self._url(self._repo_path(full_name, f"/branches/{branch}/protection")),
            resource=f"branch_protection:{full_name}:{branch}",
            ttl=TTL["branches"],
        )

    def tags(self, full_name: str, *, force: bool = False):
        return self.client.get_pages(
            self._url(self._repo_path(full_name, "/tags")),
            resource=f"tags:{full_name}",
            params={"per_page": 100},
            ttl=TTL["tags"],
            allow_stale=not force,
        )

    def ref(self, full_name: str, ref: str):
        return self.client.get_json(
            self._url(self._repo_path(full_name, f"/git/ref/{ref}")),
            resource=f"ref:{full_name}:{ref}",
            ttl=TTL["tags"],
        )

    def annotated_tag(self, full_name: str, tag: str):
        return self.client.get_json(
            self._url(self._repo_path(full_name, f"/git/tags/{tag}")),
            resource=f"annotated_tag:{full_name}:{tag}",
            ttl=TTL["tags"],
        )

    def commits_page(
        self,
        full_name: str,
        *,
        sha: str | None = None,
        author: str | None = None,
        since: str | None = None,
        until: str | None = None,
        path: str | None = None,
        page: int = 1,
        per_page: int = 30,
        force: bool = False,
    ):
        return self.client.get_json(
            self._url(self._repo_path(full_name, "/commits")),
            resource=f"commits:{full_name}",
            params={
                "sha": sha,
                "author": author,
                "since": since,
                "until": until,
                "path": path,
                "page": page,
                "per_page": per_page,
            },
            ttl=TTL["commits"],
            force=force,
        )

    def commit(self, full_name: str, sha: str, *, force: bool = False):
        return self.client.get_json(
            self._url(self._repo_path(full_name, f"/commits/{sha}")),
            resource=f"commit:{full_name}:{sha}",
            ttl=TTL["commits"],
            force=force,
        )

    def check_runs(self, full_name: str, ref: str):
        return self.client.get_json(
            self._url(self._repo_path(full_name, f"/commits/{ref}/check-runs")),
            resource=f"check_runs:{full_name}:{ref}",
            params={"per_page": 100},
            ttl=TTL["pulls"],
        )

    def commit_pulls(self, full_name: str, sha: str):
        return self.client.get_pages(
            self._url(self._repo_path(full_name, f"/commits/{sha}/pulls")),
            resource=f"commit_pulls:{full_name}:{sha}",
            params={"per_page": 100},
            ttl=TTL["pulls"],
        )

    def compare(self, full_name: str, base: str, head: str, *, force: bool = False):
        return self.client.get_json(
            self._url(self._repo_path(full_name, f"/compare/{base}...{head}")),
            resource=f"compare:{full_name}:{base}...{head}",
            ttl=TTL["commits"],
            force=force,
        )

    def pull_requests(
        self,
        full_name: str,
        *,
        state: str = "open",
        sort: str = "updated",
        direction: str = "desc",
        page: int = 1,
        per_page: int = 30,
        force: bool = False,
    ):
        return self.client.get_json(
            self._url(self._repo_path(full_name, "/pulls")),
            resource=f"pulls:{full_name}",
            params={
                "state": state,
                "sort": sort,
                "direction": direction,
                "page": page,
                "per_page": per_page,
            },
            ttl=TTL["pulls"],
            force=force,
        )

    def pull_request(self, full_name: str, number: int, *, force: bool = False):
        return self.client.get_json(
            self._url(self._repo_path(full_name, f"/pulls/{number}")),
            resource=f"pull:{full_name}:{number}",
            ttl=TTL["pulls"],
            force=force,
        )

    def pull_request_files(self, full_name: str, number: int):
        return self.client.get_pages(
            self._url(self._repo_path(full_name, f"/pulls/{number}/files")),
            resource=f"pull_files:{full_name}:{number}",
            params={"per_page": 100},
            ttl=TTL["pulls"],
            max_pages=3,
        )

    def pull_request_reviews(self, full_name: str, number: int):
        return self.client.get_pages(
            self._url(self._repo_path(full_name, f"/pulls/{number}/reviews")),
            resource=f"pull_reviews:{full_name}:{number}",
            params={"per_page": 100},
            ttl=TTL["pulls"],
        )

    def issues(
        self,
        full_name: str,
        *,
        state: str = "open",
        since: str | None = None,
        sort: str = "updated",
        direction: str = "desc",
        page: int = 1,
        per_page: int = 30,
        force: bool = False,
    ):
        return self.client.get_json(
            self._url(self._repo_path(full_name, "/issues")),
            resource=f"issues:{full_name}",
            params={
                "state": state,
                "since": since,
                "sort": sort,
                "direction": direction,
                "page": page,
                "per_page": per_page,
            },
            ttl=TTL["issues"],
            force=force,
        )

    def issue(self, full_name: str, number: int, *, force: bool = False):
        return self.client.get_json(
            self._url(self._repo_path(full_name, f"/issues/{number}")),
            resource=f"issue:{full_name}:{number}",
            ttl=TTL["issues"],
            force=force,
        )

    def issue_comments(self, full_name: str, number: int):
        return self.client.get_pages(
            self._url(self._repo_path(full_name, f"/issues/{number}/comments")),
            resource=f"issue_comments:{full_name}:{number}",
            params={"per_page": 100},
            ttl=TTL["issues"],
            max_pages=3,
        )

    def releases(self, full_name: str, *, force: bool = False):
        return self.client.get_pages(
            self._url(self._repo_path(full_name, "/releases")),
            resource=f"releases:{full_name}",
            params={"per_page": 100},
            ttl=TTL["releases"],
            allow_stale=not force,
        )

    def latest_release(self, full_name: str, *, force: bool = False):
        return self.client.get_json(
            self._url(self._repo_path(full_name, "/releases/latest")),
            resource=f"latest_release:{full_name}",
            ttl=TTL["releases"],
            force=force,
        )

    def release(self, full_name: str, release_id: int, *, force: bool = False):
        return self.client.get_json(
            self._url(self._repo_path(full_name, f"/releases/{release_id}")),
            resource=f"release:{full_name}:{release_id}",
            ttl=TTL["releases"],
            force=force,
        )

    def workflows(self, full_name: str):
        return self.client.get_pages(
            self._url(self._repo_path(full_name, "/actions/workflows")),
            resource=f"workflows:{full_name}",
            params={"per_page": 100},
            ttl=TTL["workflows"],
        )

    def workflow_runs(
        self,
        full_name: str,
        *,
        branch: str | None = None,
        per_page: int = 30,
        page: int = 1,
        force: bool = False,
    ):
        return self.client.get_json(
            self._url(self._repo_path(full_name, "/actions/runs")),
            resource=f"workflow_runs:{full_name}",
            params={"branch": branch, "per_page": per_page, "page": page},
            ttl=TTL["workflows"],
            force=force,
        )

    def workflow_run_jobs(self, full_name: str, run_id: int):
        return self.client.get_pages(
            self._url(self._repo_path(full_name, f"/actions/runs/{run_id}/jobs")),
            resource=f"workflow_jobs:{full_name}:{run_id}",
            params={"per_page": 100},
            ttl=TTL["workflows"],
            max_pages=3,
        )

    def workflow_run_logs(self, full_name: str, run_id: int, *, max_bytes: int = 500_000) -> dict:
        """On-demand log download, capped in memory; only a bounded excerpt is cached."""
        headers = self.client._headers({"Accept": "application/vnd.github+json"})
        url = self._url(self._repo_path(full_name, f"/actions/runs/{run_id}/logs"))
        try:
            response = self.client.client.get(url, headers=headers)
            self.client._update_rate_state(response)
            self.client._request_count += 1
        except Exception as exc:
            raise ProviderError(f"Unable to download workflow logs: {exc}") from exc
        if response.status_code in {403, 429}:
            raise RateLimitError("Workflow log download was rate limited")
        if response.status_code >= 400:
            raise ProviderError(f"Workflow log download returned {response.status_code}")
        content = response.content[:max_bytes]
        text: str | None = None
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                chunks: list[str] = []
                remaining = max_bytes
                for name in archive.namelist()[:20]:
                    if remaining <= 0:
                        break
                    with archive.open(name) as handle:
                        data = handle.read(remaining)
                        chunks.append(f"--- {name} ---\n" + data.decode("utf-8", "replace"))
                        remaining -= len(data)
                text = "\n".join(chunks)[:max_bytes]
        except zipfile.BadZipFile:
            text = content.decode("utf-8", "replace")
        return {
            "run_id": run_id,
            "truncated": len(response.content) > max_bytes,
            "bytes": len(response.content),
            "text": text,
            "fetched_at": utcnow().isoformat(),
        }

    def dependabot_alerts(self, full_name: str):
        return self.client.get_pages(
            self._url(self._repo_path(full_name, "/dependabot/alerts")),
            resource=f"dependabot:{full_name}",
            params={"state": "open", "per_page": 100},
            ttl=TTL["security"],
            max_pages=3,
        )

    def security_advisories(self, full_name: str):
        return self.client.get_pages(
            self._url(self._repo_path(full_name, "/security-advisories")),
            resource=f"advisories:{full_name}",
            params={"per_page": 100},
            ttl=TTL["security"],
            max_pages=2,
        )

    def dependency_sbom(self, full_name: str):
        return self.client.get_json(
            self._url(self._repo_path(full_name, "/dependency-graph/sbom")),
            resource=f"sbom:{full_name}",
            ttl=TTL["security"],
        )

    def rate_limit(self):
        return self.client.get_json(
            self._url("rate_limit"), resource="rate_limit", ttl=60
        )

    def graphql(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
        *,
        timeout: float = 20.0,
    ) -> dict[str, Any]:
        """Bounded POST GraphQL helper. Never used for bulk enumeration."""
        if not self.settings.github_token:
            raise ProviderError("GitHub GraphQL requires a token")
        headers = self.client._headers({"Content-Type": "application/json"})
        try:
            response = self.client.client.post(
                self.settings.github_graphql_url,
                json={"query": query, "variables": variables or {}},
                headers=headers,
                timeout=timeout,
            )
        except Exception as exc:
            raise ProviderError(f"GraphQL request failed: {exc}") from exc
        self.client._request_count += 1
        if response.status_code in {401, 403, 429}:
            raise RateLimitError(f"GraphQL rate limited ({response.status_code})")
        if response.status_code >= 400:
            raise ProviderError(f"GraphQL returned {response.status_code}")
        body = response.json()
        if body.get("errors"):
            raise ProviderError(f"GraphQL errors: {body['errors'][:2]}")
        return body.get("data") or {}

    def repository_overview_graphql(self, owner: str, name: str) -> dict[str, Any]:
        query = """
        query($owner: String!, $name: String!) {
          repository(owner: $owner, name: $name) {
            id nameWithOwner description homepageUrl isPrivate isArchived isFork isTemplate
            stargazerCount forkCount watchers { totalCount }
            issues(states: OPEN) { totalCount }
            defaultBranchRef { name target { ... on Commit { oid committedDate } } }
            primaryLanguage { name }
            licenseInfo { spdxId name }
            repositoryTopics(first: 20) { nodes { topic { name } } }
            releases(first: 1, orderBy: {field: CREATED_AT, direction: DESC}) {
              nodes { tagName publishedAt isPrerelease isDraft }
            }
          }
        }
        """
        return self.graphql(query, {"owner": owner, "name": name})
