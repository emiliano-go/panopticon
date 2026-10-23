# Panopticon

**One read-only index for every repository you own or maintain.** Panopticon aggregates GitHub repository metadata, Git history, pull requests, issues, releases, package registries and CI status into a single self-hosted API, MCP server and dashboard — without cloning a single repository or downloading source files.

## What's Panopticon?

Panopticon answers questions like:

- What did I ship this month, across all my projects?
- Which repository is behind its published package version?
- What changed in this commit, and which pull request introduced it?
- Which projects have failing CI or stale metadata?
- What is the latest release, and does it match the Git tag and PyPI/npm/crates version?
- How do I give an AI coding agent access to all of this without exposing GitHub tokens?

It is a **personal project index**, not a code host, CI system or monitoring platform. It reads from providers, normalizes the metadata once, caches it persistently, and exposes the same data everywhere.

### What it indexes

| Area | Examples |
|---|---|
| Repositories | Name, owner, visibility, topics, license, languages, stars, forks, features, lifecycle |
| Git history | Commits, parents, authors, committers, verification, stats, changed-file metadata, comparisons |
| Branches and tags | Head commits, protection, annotated tag metadata, semantic-version detection |
| Collaboration | Pull requests, reviews, checks, issues, comments (on demand) |
| Releases | Tags, drafts, prereleases, assets, notes fetched on demand |
| Packages | PyPI, npm, crates.io, Go modules, versions, downloads, deprecation |
| CI and security | GitHub Actions runs, failures, Dependabot alerts, advisories, dependency summaries |
| Portfolio | Cross-project activity, recent releases, stale/failing health indicators |

### Three interfaces, one backend

| Interface | Location | Use |
|---|---|---|
| REST API | `/api/v1` | Canonical, scriptable interface |
| MCP server | `python -m app.cli mcp` | AI assistants and coding agents |
| Dashboard | `/` | Browsable portfolio and repository views |

All three read the same SQLite index and enqueue work through the same refresh queue. No interface performs a synchronous full synchronization.

## How it works

```
GitHub REST/GraphQL ─┐
PyPI / npm / crates ─┼─> provider adapters ─> SQLite index ─> REST / MCP / dashboard
GitHub webhooks ─────┘         │
                               └─> refresh queue ─> worker
```

- **FastAPI + SQLAlchemy 2.0** modular monolith, no microservices.
- **SQLite** with WAL mode and a 30s busy timeout, backed by one persistent volume.
- **dbwarden** compiles versioned SQL migrations from `app/models.py`; migrations are reviewable artifacts, not runtime code.
- **`provider_cache`** stores ETag/Last-Modified, normalized payloads, freshness and retry state. A `304 Not Modified` keeps the existing payload and only updates verification timestamps.
- **`sync_jobs`** is a persistent, priority refresh queue. Webhooks enqueue incremental scopes; scheduled reconciliation recovers missed events.
- **Adaptive rate limits** come from live provider headers: shared request queue, per-endpoint circuit breaking, reset-aware backoff, duplicate-request coalescing and stale-cache fallback during outages.
- **No source persistence.** Patches, diffs, file contents and full workflow logs are stripped before they reach the database.

## Quick start with Docker

```bash
git clone https://github.com/emiliano-go/panopticon.git
cd panopticon
cp .env.example .env
# edit .env: API_AUTH_TOKEN, GITHUB_TOKEN, GITHUB_WEBHOOK_SECRET
docker compose up --build -d
```

Open <http://localhost:8000>, click **Unlock**, and paste `API_AUTH_TOKEN`.

What Compose starts:

- `api` — runs `dbwarden migrate` then Uvicorn, with a health check.
- `worker` — processes refresh jobs and periodic reconciliation, waiting for the API to become healthy.
- `panopticon-data` — named volume containing `panopticon.db`.

Useful commands:

```bash
docker compose logs -f api worker
docker compose exec api python -m app.cli discover
docker compose exec api python -m app.cli register https://github.com/owner/repo
docker compose down          # keeps the volume
docker compose down -v       # deletes the index
```

To use the published image instead of building locally, set `PANOPTICON_IMAGE` in `.env` and omit `--build`.

### Published container

CI tests every change and publishes to GHCR on pushes to `main`, on `v*` tags, and when a GitHub release is published:

```
ghcr.io/emiliano-go/panopticon:latest
ghcr.io/emiliano-go/panopticon:main
ghcr.io/emiliano-go/panopticon:sha-<commit>
ghcr.io/emiliano-go/panopticon:<version>
```

Pull requests build the image but do not push it.

## Local development

Requirements: Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv venv
uv pip install -e ".[dev]"
cp .env.example .env
dbwarden migrate               # apply the committed SQL migration
python -m app.cli discover     # import authorized GitHub repositories
python -m app.cli worker       # terminal 2
python -m app.cli serve        # terminal 3
```

The dashboard asks for `API_AUTH_TOKEN` and keeps it in `localStorage`. The API accepts `Authorization: Bearer <token>` or `X-API-Key`.

## Configuration

| Variable | Purpose |
|---|---|
| `API_AUTH_TOKEN` | **Required in production.** Bearer token for REST and dashboard data. |
| `DATABASE_URL` | SQLite URL. Local default `sqlite:///./data/personal_api.db`; Compose uses `sqlite:////data/panopticon.db`. |
| `GITHUB_TOKEN` | Fine-grained read-only PAT. Enables private repositories and higher rate limits. |
| `GITHUB_USERNAME` | Public account to discover when no token is configured. |
| `GITHUB_ORGS` | Comma-separated organizations to include in discovery. |
| `GITHUB_WEBHOOK_SECRET` | HMAC secret for `/api/v1/webhooks/github`. |
| `AUTO_CREATE_TABLES` | Local convenience only; containers run migrations instead. |
| `ALLOW_ARBITRARY_REMOTES` | Permit manual registration beyond known Git hosts (SSRF checks still apply). |
| `PANOPTICON_IMAGE` | Compose image reference. |
| `PANOPTICON_PORT` | Host port for the dashboard/API. |

Never commit `.env`. The repository ignores it, along with the SQLite database and local tooling state.

## REST API

Base path: `/api/v1`. All collection endpoints paginate, and every resource carries freshness metadata (`fetched_at`, `last_successful_fetch_at`, `source_updated_at`, `is_stale`, `refresh_status`).

| Group | Endpoints |
|---|---|
| Projects | `GET/POST /projects`, `GET/PATCH/DELETE /projects/{id}` |
| Repositories | `GET/POST /repositories`, `GET /repositories/{id}`, branches, tags, stats, activity, refresh status, packages |
| History | `/commits`, `/commits/{sha}`, `/branches/{branch}/commits`, `/compare/{base}...{head}` |
| Collaboration | `/pull-requests`, `/pull-requests/{number}`, `/issues`, `/issues/{number}` |
| Releases | `/releases`, `/releases/latest`, `/releases/{id}` (notes on demand) |
| Packages | `GET/POST /packages`, `/packages/{id}`, `/packages/{id}/versions` |
| CI/security | `/workflows`, `/workflow-runs`, `/workflow-runs/{id}/logs`, `/security`, `/dependencies` |
| Portfolio | `/summary`, `/activity`, `/releases/recent`, `/pull-requests/recent`, `/packages/recent` |
| Operations | `/repositories/{id}/refresh`, `/jobs/{id}`, `/system/provider-status`, `/system/discover`, `/system/schedule`, `/system/webhooks` |

```bash
curl -H "Authorization: Bearer $API_AUTH_TOKEN" \
  "http://localhost:8000/api/v1/repositories?full_name=owner/repo"

curl -H "Authorization: Bearer $API_AUTH_TOKEN" \
  "http://localhost:8000/api/v1/repositories/1/commits?per_page=20"

curl -X POST -H "Authorization: Bearer $API_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"scope":"releases"}' \
  "http://localhost:8000/api/v1/repositories/1/refresh"
```

### Webhooks

Create a GitHub webhook pointing at `https://your-host/api/v1/webhooks/github`:

- Content type: `application/json`
- Secret: same value as `GITHUB_WEBHOOK_SECRET`
- Events: pushes, pull requests, issues, releases, workflow runs, repository changes

Deliveries are signature-verified, deduplicated by `X-GitHub-Delivery`, and processed asynchronously.

## MCP server

```bash
python -m app.cli mcp                                  # stdio
python -m app.cli mcp --transport streamable-http \
  --host 127.0.0.1 --port 8001                         # behind an authenticated TLS proxy
```

Tools: `list_projects`, `get_project`, `get_repository`, `get_repository_stats`, `get_latest_commits`, `get_git_log`, `get_commit`, `compare_commits`, `list_branches`, `list_tags`, `list_pull_requests`, `get_pull_request`, `list_issues`, `list_releases`, `get_latest_release`, `list_packages`, `get_package_versions`, `get_workflow_status`, `get_repository_activity`, `get_project_summary`, `search_projects`, `request_refresh`, `get_refresh_status`.

Responses are bounded and paginated and include provider links. Remote transports must sit behind authentication and TLS; stdio is the default.

### opencode

```jsonc
{
  "mcp": {
    "panopticon": {
      "type": "local",
      "command": [
        "/absolute/path/to/panopticon/.venv/bin/python",
        "-m",
        "app.cli",
        "mcp"
      ],
      "cwd": "/absolute/path/to/panopticon",
      "environment": {
        "DATABASE_URL": "sqlite:////absolute/path/to/panopticon/data/personal_api.db"
      },
      "enabled": true
    }
  }
}
```

Restart opencode after editing its config. `cwd` lets the MCP process read the project's `.env` for `GITHUB_TOKEN` and webhook settings; `DATABASE_URL` pins it to the same index.

## Schema changes with dbwarden

`app/models.py` is the schema contract:

```bash
dbwarden make-migrations "describe the change"
dbwarden --dev migrate        # optional local check
dbwarden migrate              # apply to the configured database
dbwarden status
```

Commit the generated `migrations/primary/*.sql` and `.dbwarden/` snapshots. Never hand-edit a migration. To target a different SQLite file, set `DATABASE_URL` or change the URL in `dbwarden.py`.

## Security

- Credentials stay server-side; they are never written to the database or returned by the dashboard.
- `API_AUTH_TOKEN` protects all data endpoints. Private repositories are hidden from unauthenticated listings.
- Webhook payloads require a valid HMAC-SHA256 signature.
- Manual URLs must be HTTPS, cannot contain credentials, and must resolve to public addresses (SSRF guard).
- Provider cache keys include the authentication scope, preventing cross-account reuse.
- Cached payloads are scrubbed of token-like keys and stripped of patches, diffs and file contents.
- Refresh jobs are read-only and can only target registered repositories.

## Testing

```bash
pytest -q
```

Coverage includes normalization, conditional requests and `304`, stale-data survival during outages, rate-limit headers, webhook signature/deduplication, repository rename/transfer identity, private repository authorization, pagination, MCP tool registration, migration coverage and patch stripping.

## Non-goals and limits

- No cloning, shallow fetch, source downloads or code browser.
- No CI/CD runner, deployment system or repository writer.
- History is limited to what a provider API exposes; unsupported operations return a structured unsupported-capability response rather than falling back to a clone.
- Historical star/fork trends only exist from observations recorded after deployment.
- GitHub Packages has no public listing API in this version and is reported as unsupported.
- SQLite is the supported store. Turso/libSQL is not wired in, but the SQLAlchemy layer keeps that path open.
