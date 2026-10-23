-- base_checksum: 5f9fdc13f37784f35636839c48e11779672d49bfd4f64c61e24070fe4c595bc1
-- dbwarden: file-severity INFO
-- upgrade

CREATE TABLE IF NOT EXISTS commits (
    id INTEGER NOT NULL PRIMARY KEY,
    repository_id INTEGER NOT NULL REFERENCES repositories(id),
    sha VARCHAR(64) NOT NULL,
    abbreviated_sha VARCHAR(16),
    message TEXT NOT NULL,
    subject TEXT,
    author_name VARCHAR(255),
    author_email VARCHAR(255),
    authored_at DATETIME,
    committer_name VARCHAR(255),
    committer_email VARCHAR(255),
    committed_at DATETIME,
    parents TEXT NOT NULL,
    html_url VARCHAR(1024),
    verified BOOLEAN,
    verification TEXT NOT NULL,
    changed_files INTEGER,
    additions INTEGER,
    deletions INTEGER,
    total_changes INTEGER,
    pull_request_numbers TEXT NOT NULL,
    fetched_at DATETIME,
    last_successful_fetch_at DATETIME,
    source_updated_at DATETIME,
    refresh_status VARCHAR(32) NOT NULL DEFAULT 'never_fetched',
    last_error TEXT,
    CONSTRAINT uq_commit_repo_sha UNIQUE (repository_id, sha)
);

CREATE TABLE IF NOT EXISTS integrations (
    id INTEGER NOT NULL PRIMARY KEY,
    provider VARCHAR(32) NOT NULL,
    name VARCHAR(128) NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    config TEXT NOT NULL,
    credential_ref VARCHAR(128),
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT uq_integrations_provider UNIQUE (provider)
);

CREATE TABLE IF NOT EXISTS issues (
    id INTEGER NOT NULL PRIMARY KEY,
    repository_id INTEGER NOT NULL REFERENCES repositories(id),
    number INTEGER NOT NULL,
    title TEXT NOT NULL,
    url VARCHAR(1024),
    author VARCHAR(255),
    state VARCHAR(32) NOT NULL DEFAULT 'open',
    labels TEXT NOT NULL,
    assignees TEXT NOT NULL,
    created_at DATETIME,
    updated_at DATETIME,
    closed_at DATETIME,
    milestone VARCHAR(255),
    comments_count INTEGER NOT NULL DEFAULT 0,
    linked_pull_requests TEXT NOT NULL,
    fetched_at DATETIME,
    last_successful_fetch_at DATETIME,
    source_updated_at DATETIME,
    refresh_status VARCHAR(32) NOT NULL DEFAULT 'never_fetched',
    last_error TEXT,
    CONSTRAINT uq_issue_repo_number UNIQUE (repository_id, number)
);

CREATE TABLE IF NOT EXISTS observations (
    id INTEGER NOT NULL PRIMARY KEY,
    repository_id INTEGER REFERENCES repositories(id),
    package_id INTEGER REFERENCES packages(id),
    kind VARCHAR(64) NOT NULL,
    value INTEGER NOT NULL,
    source VARCHAR(32) NOT NULL DEFAULT 'provider',
    observed_at DATETIME NOT NULL,
    metadata TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS package_versions (
    id INTEGER NOT NULL PRIMARY KEY,
    package_id INTEGER NOT NULL REFERENCES packages(id),
    version VARCHAR(128) NOT NULL,
    published_at DATETIME,
    yanked BOOLEAN NOT NULL DEFAULT FALSE,
    deprecated BOOLEAN NOT NULL DEFAULT FALSE,
    is_prerelease BOOLEAN NOT NULL DEFAULT FALSE,
    metadata TEXT NOT NULL,
    fetched_at DATETIME,
    CONSTRAINT uq_version_package_version UNIQUE (package_id, version)
);

CREATE TABLE IF NOT EXISTS packages (
    id INTEGER NOT NULL PRIMARY KEY,
    repository_id INTEGER REFERENCES repositories(id),
    project_id INTEGER REFERENCES projects(id),
    registry VARCHAR(32) NOT NULL,
    ecosystem VARCHAR(32) NOT NULL,
    name VARCHAR(512) NOT NULL,
    url VARCHAR(1024),
    latest_version VARCHAR(128),
    latest_stable_version VARCHAR(128),
    published_at DATETIME,
    description TEXT,
    license VARCHAR(255),
    maintainers TEXT NOT NULL,
    downloads INTEGER,
    downloads_period VARCHAR(32),
    deprecated BOOLEAN NOT NULL DEFAULT FALSE,
    yanked BOOLEAN NOT NULL DEFAULT FALSE,
    package_type VARCHAR(32),
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    fetched_at DATETIME,
    last_successful_fetch_at DATETIME,
    source_updated_at DATETIME,
    refresh_status VARCHAR(32) NOT NULL DEFAULT 'never_fetched',
    last_error TEXT,
    CONSTRAINT uq_package_registry_name UNIQUE (registry, name)
);

CREATE TABLE IF NOT EXISTS project_repositories (
    project_id INTEGER NOT NULL REFERENCES projects(id),
    repository_id INTEGER NOT NULL REFERENCES repositories(id),
    PRIMARY KEY (project_id, repository_id)
);

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER NOT NULL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    slug VARCHAR(255) NOT NULL,
    description TEXT,
    owner VARCHAR(255),
    homepage VARCHAR(1024),
    visibility VARCHAR(32) NOT NULL DEFAULT 'public',
    aliases TEXT NOT NULL,
    tags TEXT NOT NULL,
    categories TEXT NOT NULL,
    metadata TEXT NOT NULL,
    is_group BOOLEAN NOT NULL DEFAULT FALSE,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT uq_projects_slug UNIQUE (slug)
);

CREATE TABLE IF NOT EXISTS provider_cache (
    id INTEGER NOT NULL PRIMARY KEY,
    provider VARCHAR(32) NOT NULL,
    resource VARCHAR(255) NOT NULL,
    scope VARCHAR(64) NOT NULL DEFAULT 'default',
    cache_key VARCHAR(512) NOT NULL,
    etag VARCHAR(512),
    last_modified VARCHAR(255),
    payload TEXT,
    status VARCHAR(32) NOT NULL DEFAULT 'never_fetched',
    error TEXT,
    fetched_at DATETIME,
    last_successful_fetch_at DATETIME,
    last_attempt_at DATETIME,
    next_refresh_at DATETIME,
    source_updated_at DATETIME,
    hits INTEGER NOT NULL DEFAULT 0,
    misses INTEGER NOT NULL DEFAULT 0,
    CONSTRAINT uq_provider_cache_key UNIQUE (cache_key)
);

CREATE TABLE IF NOT EXISTS provider_state (
    id INTEGER NOT NULL PRIMARY KEY,
    provider VARCHAR(32) NOT NULL,
    family VARCHAR(32) NOT NULL,
    "limit" INTEGER,
    remaining INTEGER,
    reset_at DATETIME,
    updated_at DATETIME NOT NULL,
    last_error TEXT,
    CONSTRAINT uq_provider_state_family UNIQUE (provider, family)
);

CREATE TABLE IF NOT EXISTS pull_requests (
    id INTEGER NOT NULL PRIMARY KEY,
    repository_id INTEGER NOT NULL REFERENCES repositories(id),
    number INTEGER NOT NULL,
    title TEXT NOT NULL,
    url VARCHAR(1024),
    author VARCHAR(255),
    state VARCHAR(32) NOT NULL DEFAULT 'open',
    is_draft BOOLEAN NOT NULL DEFAULT FALSE,
    created_at DATETIME,
    updated_at DATETIME,
    merged_at DATETIME,
    closed_at DATETIME,
    merge_commit_sha VARCHAR(64),
    base_branch VARCHAR(255),
    head_branch VARCHAR(255),
    head_sha VARCHAR(64),
    review_status VARCHAR(32),
    requested_reviewers TEXT NOT NULL,
    labels TEXT NOT NULL,
    linked_issues TEXT NOT NULL,
    changed_files INTEGER,
    additions INTEGER,
    deletions INTEGER,
    checks_status VARCHAR(32),
    milestone VARCHAR(255),
    assignees TEXT NOT NULL,
    fetched_at DATETIME,
    last_successful_fetch_at DATETIME,
    source_updated_at DATETIME,
    refresh_status VARCHAR(32) NOT NULL DEFAULT 'never_fetched',
    last_error TEXT,
    CONSTRAINT uq_pr_repo_number UNIQUE (repository_id, number)
);

CREATE TABLE IF NOT EXISTS releases (
    id INTEGER NOT NULL PRIMARY KEY,
    repository_id INTEGER NOT NULL REFERENCES repositories(id),
    provider_id VARCHAR(128) NOT NULL,
    tag_name VARCHAR(255) NOT NULL,
    title TEXT,
    url VARCHAR(1024),
    published_at DATETIME,
    created_at DATETIME,
    draft BOOLEAN NOT NULL DEFAULT FALSE,
    prerelease BOOLEAN NOT NULL DEFAULT FALSE,
    notes TEXT,
    target_sha VARCHAR(64),
    assets TEXT NOT NULL,
    fetched_at DATETIME,
    last_successful_fetch_at DATETIME,
    source_updated_at DATETIME,
    refresh_status VARCHAR(32) NOT NULL DEFAULT 'never_fetched',
    last_error TEXT,
    CONSTRAINT uq_release_repo_provider UNIQUE (repository_id, provider_id)
);

CREATE TABLE IF NOT EXISTS repositories (
    id INTEGER NOT NULL PRIMARY KEY,
    provider VARCHAR(32) NOT NULL DEFAULT 'github',
    provider_id VARCHAR(128) NOT NULL,
    auth_scope VARCHAR(64) NOT NULL DEFAULT 'default',
    full_name VARCHAR(512) NOT NULL,
    owner VARCHAR(255) NOT NULL,
    owner_type VARCHAR(32),
    name VARCHAR(255) NOT NULL,
    description TEXT,
    url VARCHAR(1024),
    clone_url VARCHAR(1024),
    homepage VARCHAR(1024),
    visibility VARCHAR(32) NOT NULL DEFAULT 'public',
    default_branch VARCHAR(255),
    language VARCHAR(64),
    languages TEXT NOT NULL,
    topics TEXT NOT NULL,
    license VARCHAR(255),
    license_spdx VARCHAR(64),
    provider_created_at DATETIME,
    provider_updated_at DATETIME,
    archived BOOLEAN NOT NULL DEFAULT FALSE,
    fork BOOLEAN NOT NULL DEFAULT FALSE,
    is_template BOOLEAN NOT NULL DEFAULT FALSE,
    upstream_full_name VARCHAR(512),
    size_kb INTEGER,
    stars INTEGER NOT NULL DEFAULT 0,
    forks INTEGER NOT NULL DEFAULT 0,
    watchers INTEGER NOT NULL DEFAULT 0,
    open_issues INTEGER NOT NULL DEFAULT 0,
    contributors_count INTEGER,
    features TEXT NOT NULL,
    raw_provider TEXT NOT NULL,
    lifecycle VARCHAR(32) NOT NULL DEFAULT 'active',
    last_seen_at DATETIME,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    fetched_at DATETIME,
    last_successful_fetch_at DATETIME,
    source_updated_at DATETIME,
    refresh_status VARCHAR(32) NOT NULL DEFAULT 'never_fetched',
    last_error TEXT,
    CONSTRAINT uq_repository_provider_id UNIQUE (provider, provider_id),
    CONSTRAINT uq_repository_full_name UNIQUE (provider, full_name)
);

CREATE TABLE IF NOT EXISTS repository_branches (
    id INTEGER NOT NULL PRIMARY KEY,
    repository_id INTEGER NOT NULL REFERENCES repositories(id),
    name VARCHAR(255) NOT NULL,
    head_sha VARCHAR(64),
    head_committed_at DATETIME,
    latest_commit_message TEXT,
    is_default BOOLEAN NOT NULL DEFAULT FALSE,
    ahead INTEGER,
    behind INTEGER,
    protected BOOLEAN,
    required_checks TEXT NOT NULL,
    updated_at DATETIME NOT NULL,
    fetched_at DATETIME,
    last_successful_fetch_at DATETIME,
    source_updated_at DATETIME,
    refresh_status VARCHAR(32) NOT NULL DEFAULT 'never_fetched',
    last_error TEXT,
    CONSTRAINT uq_branch_repo_name UNIQUE (repository_id, name)
);

CREATE TABLE IF NOT EXISTS repository_tags (
    id INTEGER NOT NULL PRIMARY KEY,
    repository_id INTEGER NOT NULL REFERENCES repositories(id),
    name VARCHAR(255) NOT NULL,
    target_sha VARCHAR(64),
    tag_type VARCHAR(32),
    tagger_name VARCHAR(255),
    tagger_email VARCHAR(255),
    tagger_date DATETIME,
    release_id INTEGER REFERENCES releases(id),
    is_semver BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at DATETIME NOT NULL,
    fetched_at DATETIME,
    last_successful_fetch_at DATETIME,
    source_updated_at DATETIME,
    refresh_status VARCHAR(32) NOT NULL DEFAULT 'never_fetched',
    last_error TEXT,
    CONSTRAINT uq_tag_repo_name UNIQUE (repository_id, name)
);

CREATE TABLE IF NOT EXISTS sync_jobs (
    id INTEGER NOT NULL PRIMARY KEY,
    repository_id INTEGER REFERENCES repositories(id),
    project_id INTEGER REFERENCES projects(id),
    scope VARCHAR(64) NOT NULL,
    priority INTEGER NOT NULL DEFAULT 50,
    status VARCHAR(32) NOT NULL DEFAULT 'queued',
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 5,
    error TEXT,
    payload TEXT NOT NULL,
    started_at DATETIME,
    finished_at DATETIME,
    next_run_at DATETIME,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);

CREATE TABLE IF NOT EXISTS webhook_deliveries (
    id INTEGER NOT NULL PRIMARY KEY,
    delivery_id VARCHAR(128) NOT NULL,
    event VARCHAR(64) NOT NULL,
    "action" VARCHAR(64),
    repository_id INTEGER REFERENCES repositories(id),
    status VARCHAR(32) NOT NULL DEFAULT 'received',
    received_at DATETIME NOT NULL,
    processed_at DATETIME,
    error TEXT,
    payload_hash VARCHAR(64),
    payload TEXT NOT NULL,
    CONSTRAINT uq_webhook_deliveries_delivery_id UNIQUE (delivery_id)
);

CREATE TABLE IF NOT EXISTS workflow_runs (
    id INTEGER NOT NULL PRIMARY KEY,
    repository_id INTEGER NOT NULL REFERENCES repositories(id),
    provider_id VARCHAR(128) NOT NULL,
    workflow_name VARCHAR(255),
    workflow_id VARCHAR(128),
    run_number INTEGER,
    status VARCHAR(32),
    conclusion VARCHAR(32),
    branch VARCHAR(255),
    head_sha VARCHAR(64),
    event VARCHAR(64),
    started_at DATETIME,
    completed_at DATETIME,
    duration_seconds INTEGER,
    html_url VARCHAR(1024),
    failed_jobs TEXT NOT NULL,
    fetched_at DATETIME,
    last_successful_fetch_at DATETIME,
    source_updated_at DATETIME,
    refresh_status VARCHAR(32) NOT NULL DEFAULT 'never_fetched',
    last_error TEXT,
    CONSTRAINT uq_workflow_repo_provider UNIQUE (repository_id, provider_id)
);

-- rollback

DROP TABLE workflow_runs

DROP TABLE webhook_deliveries

DROP TABLE sync_jobs

DROP TABLE repository_tags

DROP TABLE repository_branches

DROP TABLE repositories

DROP TABLE releases

DROP TABLE pull_requests

DROP TABLE provider_state

DROP TABLE provider_cache

DROP TABLE projects

DROP TABLE project_repositories

DROP TABLE packages

DROP TABLE package_versions

DROP TABLE observations

DROP TABLE issues

DROP TABLE integrations

DROP TABLE commits
