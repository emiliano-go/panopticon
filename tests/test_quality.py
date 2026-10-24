from __future__ import annotations

import asyncio
from pathlib import Path

from app.utils import compare_versions, is_semver, slugify, strip_patches, validate_remote_url


def test_semver_helpers():
    assert is_semver("v1.2.3")
    assert is_semver("2.0.0-rc.1")
    assert not is_semver("nightly")
    assert compare_versions("1.2.0", "1.1.0") == "left_newer"
    assert compare_versions("1.2.0", "1.2.0") == "equal"
    assert compare_versions("nightly", "1.0.0") == "unknown"


def test_strip_patches_removes_source_contents_everywhere():
    payload = {
        "files": [{"filename": "a.py", "patch": "@@ secret @@"}],
        "nested": {"diff": "secret", "contents": "secret", "safe": 1},
    }
    cleaned = strip_patches(payload)
    assert cleaned == {"files": [{"filename": "a.py"}], "nested": {"safe": 1}}


def test_ssrf_guard_rejects_private_and_non_https():
    for url in ("http://github.com/a/b", "https://127.0.0.1/a", "https://localhost/a"):
        try:
            validate_remote_url(url)
        except ValueError:
            continue
        raise AssertionError(f"{url} should be rejected")


def test_slugify():
    assert slugify("Octo / Widgets!") == "octo-widgets"


def test_initial_migration_covers_every_core_table():
    sql_files = sorted(Path("migrations/primary").glob("*.sql"))
    assert sql_files, "dbwarden migration was not generated"
    sql = "\n".join(path.read_text() for path in sql_files)
    expected = {
        "projects",
        "repositories",
        "project_repositories",
        "repository_branches",
        "repository_tags",
        "commits",
        "pull_requests",
        "issues",
        "releases",
        "packages",
        "package_versions",
        "workflow_runs",
        "observations",
        "provider_cache",
        "sync_jobs",
        "webhook_deliveries",
        "integrations",
        "provider_state",
    }
    for table in expected:
        assert table in sql, f"{table} missing from dbwarden migration"


def test_mcp_exposes_required_tools():
    import app.mcp_server as mcp

    tools = asyncio.run(mcp.server.list_tools())
    names = {tool.name for tool in tools}
    required = {
        "list_projects",
        "get_project",
        "get_repository",
        "get_repository_stats",
        "get_latest_commits",
        "get_git_log",
        "get_commit",
        "compare_commits",
        "list_branches",
        "list_tags",
        "list_pull_requests",
        "get_pull_request",
        "list_issues",
        "list_releases",
        "get_latest_release",
        "list_packages",
        "get_package_versions",
        "get_workflow_status",
        "get_repository_activity",
        "get_project_summary",
        "search_projects",
        "request_refresh",
        "get_refresh_status",
        "list_repositories",
        "get_portfolio_summary",
        "get_recent_activity",
        "get_workflows",
        "get_workflow_runs",
        "get_security",
        "get_dependencies",
        "get_issue",
        "get_release",
        "get_package",
        "get_branch",
        "get_tag",
        "get_branch_commits",
        "list_repository_packages",
        "get_workflow_run_logs",
        "get_job",
        "list_jobs",
        "get_provider_status",
    }
    assert required <= names
