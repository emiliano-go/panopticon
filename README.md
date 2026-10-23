# Panopticon

A self-hosted, read-only project index for repositories, Git history, pull requests, issues, releases, packages and CI metadata. It never clones a repository and never stores source file contents or patches.

Three interfaces share one backend and one cache:

- **REST API** at `/api/v1`
- **MCP server** (`python -m app.cli mcp`)
- **Web dashboard** at `/`

## Architecture

```
GitHub REST/GraphQL ─┐
PyPI / npm / crates ─┼─> provider adapters ─> SQLite index ─> REST / MCP / dashboard
GitHub webhooks ─────┘         │
                               └─> SQLite-backed refresh queue ─> worker
```

- FastAPI + SQLAlchemy 2.0 modular monolith.
- SQLite by default (WAL, busy timeout) with a single persistent volume.
- **dbwarden** compiles schema migrations from `app/models.py`. There is no Alembic.
- Persistent `provider_cache` stores ETag/Last-Modified, payloads and freshness; `304 Not Modified` keeps the payload.
- `sync_jobs` is the refresh queue. Webhooks enqueue incremental jobs; scheduled reconciliation covers missed deliveries.
- Rate limits are read from live response headers; the client backs off, respects reset times, coalesces in-flight requests and breaks failing endpoints out of rotation.

## Quick start (local)

Requirements: Python 3.12+.

```bash
uv venv
uv pip install -e ".[dev]"
cp .env.example .env           # set API_AUTH_TOKEN and GITHUB_TOKEN
dbwarden migrate               # apply the committed SQL migration
python -m app.cli discover     # discover authorized GitHub repositories
python -m app.cli worker       # terminal 2
python -m app.cli serve        # terminal 3, http://localhost:8000
```

The dashboard asks for `API_AUTH_TOKEN` and keeps it in `localStorage`. The REST API accepts `Authorization: Bearer <token>` or `X-API-Key`.

Register a single repository:

```bash
python -m app.cli register https://github.com/owner/repo
```

## Docker

```bash
cp .env.example .env
# edit API_AUTH_TOKEN, GITHUB_TOKEN, GITHUB_WEBHOOK_SECRET
docker compose up --build -d
```

- API: `http://localhost:8000`
- SQLite lives in the `panopticon-data` volume.
- The API container runs `dbwarden migrate` at startup; the worker waits for the API health check.
- `docker compose logs -f api worker` for sync progress.

Build the image directly:

```bash
docker build -t panopticon .
docker run --rm -p 8000:8000 -v panopticon-data:/data \
  -e API_AUTH_TOKEN=change-me -e GITHUB_TOKEN=ghp_xxx panopticon
```

### Published container

CI (`.github/workflows/container.yml`) tests every change and publishes to GHCR on every push to `main`, on every `v*` tag, and when a GitHub release is published:

```
ghcr.io/<owner>/panopticon:latest
ghcr.io/<owner>/panopticon:sha-<commit>
ghcr.io/<owner>/panopticon:<version>
```

Pull requests build the image but do not push it.

## Schema changes with dbwarden

`app/models.py` is the schema contract. To change the schema:

```bash
dbwarden make-migrations "describe the change"
dbwarden --dev migrate        # optional: run against the dev SQLite database
dbwarden migrate              # apply to the configured database
dbwarden status
```

Commit the generated `migrations/primary/*.sql` and `.dbwarden/` snapshots. Never hand-edit a migration. `dbwarden.py` points at `app/models.py` and uses SQLite; `DATABASE_URL` selects the database file (default `sqlite:///./data/personal_api.db`, `/data/personal_api.db` in Docker).

## Configuration

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | SQLite URL. Docker uses `sqlite:////data/personal_api.db`. |
| `API_AUTH_TOKEN` | Bearer token for REST/dashboard. Set this in production. |
| `GITHUB_TOKEN` | Fine-grained read-only PAT. Never committed. |
| `GITHUB_USERNAME` | Public account discovery when no token is configured. |
| `GITHUB_ORGS` | Comma-separated organizations to include. |
| `GITHUB_WEBHOOK_SECRET` | HMAC secret for `/api/v1/webhooks/github`. |
| `AUTO_CREATE_TABLES` | Local convenience; Docker uses `dbwarden migrate` instead. |
| `ALLOW_ARBITRARY_REMOTES` | Allow manual registration beyond known Git hosts (SSRF checks still apply). |

## REST API

Base path `/api/v1`.

- Projects: list/create/get/patch/delete, with owner/provider/language/topic/visibility/archived/group filters.
- Repositories: list, manual registration, detail, branches, tags, stats, activity, refresh status, packages.
- History: commits, commit detail (changed-file metadata only), branch commits, `compare/{base}...{head}`.
- Collaboration: pull requests, PR detail/files/reviews/checks, issues, issue comments.
- Releases and packages: releases, latest, release notes on demand, package versions and version alignment.
- CI/security: workflows, workflow runs, bounded on-demand logs, Dependabot alerts, advisories, dependency summary.
- Portfolio: `/summary`, `/activity`, `/releases/recent`, `/pull-requests/recent`, `/packages/recent`.
- Refresh: enqueue scoped jobs, poll `/jobs/{id}`, `/system/provider-status`, `/system/discover`, `/system/schedule`, `/system/webhooks`.

All list endpoints paginate. Resources include `freshness` (`fetched_at`, `last_successful_fetch_at`, `source_updated_at`, `is_stale`, `refresh_status`).

Example:

```bash
curl -H "Authorization: Bearer $API_AUTH_TOKEN" \
  "http://localhost:8000/api/v1/repositories?full_name=owner/repo"
```

### Webhooks

Point a GitHub webhook at `https://your-host/api/v1/webhooks/github` with content type `application/json` and the same `GITHUB_WEBHOOK_SECRET`. Signatures are verified, deliveries are deduplicated by `X-GitHub-Delivery`, and accepted events enqueue asynchronous jobs. Push, pull request, issues, release, workflow run, repository, create/delete, public and fork events are handled.

## MCP

```bash
python -m app.cli mcp                 # stdio
python -m app.cli mcp --transport streamable-http --host 127.0.0.1 --port 8001
```

Example client configuration:

```json
{
  "mcpServers": {
    "panopticon": {
      "command": "/path/to/personal-api/.venv/bin/python",
      "args": ["-m", "app.cli", "mcp"],
      "env": {"DATABASE_URL": "sqlite:////absolute/path/to/data/personal_api.db"}
    }
  }
}
```

Tools mirror the REST queries: `list_projects`, `get_project`, `get_repository`, `get_repository_stats`, `get_latest_commits`, `get_git_log`, `get_commit`, `compare_commits`, `list_branches`, `list_tags`, `list_pull_requests`, `get_pull_request`, `list_issues`, `list_releases`, `get_latest_release`, `list_packages`, `get_package_versions`, `get_workflow_status`, `get_repository_activity`, `get_project_summary`, `search_projects`, `request_refresh`, `get_refresh_status`.

Responses are bounded and paginated, and include source links. Remote MCP transports must be placed behind an authenticated, TLS-terminating reverse proxy; stdio is the default.

## Security

- Credentials stay server-side (`GITHUB_TOKEN`, `GITHUB_WEBHOOK_SECRET`), never in the database or dashboard.
- The API and dashboard data are private when `API_AUTH_TOKEN` is set.
- Private repositories are excluded from unauthenticated listings.
- Manual URLs require HTTPS, reject credentials, and resolve to public addresses only (SSRF guard).
- Webhook signatures are validated with HMAC-SHA256.
- Provider caches are keyed by provider, resource, query and authentication scope.
- Logs and cached payloads are scrubbed of token-like keys; patches, diffs and file contents are stripped before storage.
- Refresh operations only target registered repositories and always enqueue read-only jobs.

## Testing

```bash
pytest -q
```

The suite covers normalization, conditional requests and `304`, stale-data survival during outages, rate-limit headers, webhook signature/deduplication, rename/transfer identity, private repository authorization, pagination, MCP tool registration, migration coverage and patch stripping.

## Non-goals and limits

- No cloning, no shallow fetch, no source file downloads.
- No source code browser, CI/CD runner, deployment system or repository writer.
- Git history is limited to what the provider exposes through its API. Unsupported operations return a structured unsupported-capability response instead of silently cloning.
- Historical star/fork trends only exist from observations collected after the service starts.
- GitHub Packages has no public package listing API in this version; it is reported as unsupported.
- SQLite is the supported store. Turso/libSQL is not wired in; the SQLAlchemy models and DB URL abstraction keep that option open.
