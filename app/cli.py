from __future__ import annotations

import argparse
import sys

from app.config import get_settings
from app.db import Database
from app.services.sync import (
    discover_repositories,
    enqueue_job,
    register_repository_url,
    run_scope,
    schedule_recurring,
)
from app.services import queries


def _db() -> Database:
    settings = get_settings()
    db = Database(settings.database_url)
    db.create_all()
    return db


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="panopticon")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="Create database tables")

    serve = sub.add_parser("serve", help="Run the REST API and dashboard")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")

    discover = sub.add_parser("discover", help="Discover authorized GitHub repositories")
    discover.add_argument("--no-mark-missing", action="store_true")

    sync = sub.add_parser("sync", help="Synchronize one repository")
    sync.add_argument("repository", help="Internal id or owner/name")
    sync.add_argument("--scope", default="complete")
    sync.add_argument("--force", action="store_true")

    register = sub.add_parser("register", help="Register a GitHub repository by URL")
    register.add_argument("url")

    worker = sub.add_parser("worker", help="Run the refresh queue worker")
    worker.add_argument("--once", action="store_true")
    worker.add_argument("--concurrency", type=int, default=1)

    sub.add_parser("schedule", help="Enqueue recurring refresh jobs")

    mcp = sub.add_parser("mcp", help="Run the MCP server")
    mcp.add_argument("--transport", default="stdio", choices=["stdio", "sse", "streamable-http"])
    mcp.add_argument("--host", default="127.0.0.1")
    mcp.add_argument("--port", type=int, default=8001)

    args = parser.parse_args(argv)
    settings = get_settings()
    db = _db()

    if args.command == "init-db":
        print(f"database ready at {settings.database_url}")
        return 0
    if args.command == "serve":
        import uvicorn

        uvicorn.run("app.main:app", host=args.host, port=args.port, reload=args.reload)
        return 0
    if args.command == "discover":
        with db.session() as session:
            result = discover_repositories(
                session, settings, mark_missing=not args.no_mark_missing
            )
            session.commit()
        print(result)
        return 0
    if args.command == "sync":
        with db.session() as session:
            repo = queries.get_repository(session, args.repository)
            if repo is None:
                print(f"repository {args.repository!r} not found", file=sys.stderr)
                return 2
            result = run_scope(
                session, settings, repo, args.scope, {"force": args.force}
            )
            session.commit()
        print(result)
        return 0
    if args.command == "register":
        with db.session() as session:
            repo = register_repository_url(session, settings, args.url)
            session.commit()
            print(f"registered {repo.full_name} (id={repo.id})")
        return 0
    if args.command == "worker":
        from app.worker import run_workers

        run_workers(concurrency=args.concurrency, once=args.once)
        return 0
    if args.command == "schedule":
        with db.session() as session:
            count = schedule_recurring(session, settings)
            session.commit()
        print(f"scheduled {count} jobs")
        return 0
    if args.command == "mcp":
        from app.mcp_server import run_mcp

        run_mcp(transport=args.transport, host=args.host, port=args.port)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
