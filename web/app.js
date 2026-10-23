const API = "/api/v1";
const TOKEN_KEY = "panopticon_token";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

function token() {
  return localStorage.getItem(TOKEN_KEY) || "";
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function fmtDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return escapeHtml(value);
  return date.toLocaleString(undefined, {
    year: "numeric", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}

function day(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "2-digit" });
}

function age(value) {
  if (!value) return "never";
  const seconds = Math.max(0, (Date.now() - new Date(value).getTime()) / 1000);
  if (seconds < 60) return `${Math.floor(seconds)}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

function pill(text, kind = "") {
  return `<span class="pill ${kind}">${escapeHtml(text)}</span>`;
}

function showLogin(message = "Enter the API token to open the index.") {
  if ($("#login-overlay")) return;
  const overlay = document.createElement("div");
  overlay.id = "login-overlay";
  overlay.className = "login";
  overlay.innerHTML = `
    <form class="card">
      <div class="brand">Panopticon<span>.</span></div>
      <p class="muted">${escapeHtml(message)}</p>
      <input id="login-token" type="password" placeholder="API token" autocomplete="current-password">
      <button class="primary" type="submit">Unlock</button>
    </form>`;
  document.body.appendChild(overlay);
  overlay.querySelector("form").addEventListener("submit", (event) => {
    event.preventDefault();
    localStorage.setItem(TOKEN_KEY, $("#login-token").value.trim());
    overlay.remove();
    location.reload();
  });
  $("#login-token").focus();
}

async function api(path, options = {}) {
  const headers = { Accept: "application/json", ...(options.headers || {}) };
  if (token()) headers.Authorization = `Bearer ${token()}`;
  if (options.body) headers["Content-Type"] = "application/json";
  const response = await fetch(`${API}${path}`, { ...options, headers });
  if (response.status === 401) {
    showLogin("Api token required.");
    throw new Error("unauthorized");
  }
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      detail = body.error?.message || detail;
    } catch (_) {}
    throw new Error(detail);
  }
  if (response.status === 204) return null;
  return response.json();
}

function errorBox(error) {
  return `<div class="panel"><div class="error">${escapeHtml(error.message)}</div></div>`;
}

function renderNavToken(container) {
  if (!container) return;
  container.innerHTML = token()
    ? `<button id="logout">Lock</button>`
    : `<button id="login-btn">Unlock</button>`;
  $("#logout", container)?.addEventListener("click", () => {
    localStorage.removeItem(TOKEN_KEY);
    location.reload();
  });
  $("#login-btn", container)?.addEventListener("click", () => showLogin());
}

function refreshButton(repoId, statusEl) {
  return `<button class="primary" id="refresh-btn" data-id="${repoId}">Refresh</button>`;
}

async function doRefresh(repoId, statusEl, scope = "complete") {
  if (!statusEl) return;
  statusEl.innerHTML = `<span class="notice">Enqueuing ${escapeHtml(scope)} refresh…</span>`;
  try {
    const result = await api(`/repositories/${repoId}/refresh`, {
      method: "POST",
      body: JSON.stringify({ scope }),
    });
    statusEl.innerHTML = `<span class="good">Job #${result.job.id} queued (${escapeHtml(result.job.status)}).</span>`;
  } catch (error) {
    statusEl.innerHTML = `<span class="error">${escapeHtml(error.message)}</span>`;
  }
}

// ---------------------------------------------------------------- portfolio
async function initPortfolio() {
  renderNavToken($("#nav-token"));
  const status = $("#status");
  try {
    const summary = await api("/summary");
    renderPortfolio(summary);
  } catch (error) {
    status.innerHTML = errorBox(error);
  }
}

let portfolioState = { summary: null, sort: "activity", language: "", owner: "", health: "" };

function renderPortfolio(summary) {
  portfolioState.summary = summary;
  const counts = summary.counts;
  $("#portfolio-cards").innerHTML = [
    ["Projects", counts.projects],
    ["Repositories", counts.repositories],
    ["Archived", counts.archived],
    ["Stale", counts.stale],
    ["Private", counts.private],
  ]
    .map(([label, value]) => `<div class="card"><div class="label">${label}</div><div class="value">${value}</div></div>`)
    .join("");

  const languages = [...new Set(summary.repositories.map((repo) => repo.language).filter(Boolean))].sort();
  const owners = [...new Set(summary.repositories.map((repo) => repo.owner).filter(Boolean))].sort();
  $("#filters").innerHTML = `
    <input id="repo-search" placeholder="Filter repositories…">
    <select id="filter-language"><option value="">All languages</option>${languages.map((l) => `<option>${escapeHtml(l)}</option>`).join("")}</select>
    <select id="filter-owner"><option value="">All owners</option>${owners.map((o) => `<option>${escapeHtml(o)}</option>`).join("")}</select>
    <select id="filter-health">
      <option value="">All health</option>
      <option>healthy</option><option>failing</option><option>stale</option>
      <option>quiet</option><option>archived</option><option>unavailable</option>
    </select>
    <select id="sort-by">
      <option value="activity">Recent activity</option>
      <option value="stars">Stars</option>
      <option value="release">Release date</option>
      <option value="name">Name</option>
    </select>`;
  $("#repo-search")?.addEventListener("input", drawPortfolio);
  $("#filter-language")?.addEventListener("change", (event) => { portfolioState.language = event.target.value; drawPortfolio(); });
  $("#filter-owner")?.addEventListener("change", (event) => { portfolioState.owner = event.target.value; drawPortfolio(); });
  $("#filter-health")?.addEventListener("change", (event) => { portfolioState.health = event.target.value; drawPortfolio(); });
  $("#sort-by")?.addEventListener("change", (event) => { portfolioState.sort = event.target.value; drawPortfolio(); });
  drawPortfolio();
}

function drawPortfolio() {
  const { summary } = portfolioState;
  if (!summary) return;
  const search = ($("#repo-search")?.value || "").toLowerCase();
  let repos = summary.repositories.filter((repo) => {
    if (portfolioState.language && repo.language !== portfolioState.language) return false;
    if (portfolioState.owner && repo.owner !== portfolioState.owner) return false;
    if (portfolioState.health && repo.health !== portfolioState.health) return false;
    if (search && !`${repo.full_name} ${repo.description || ""}`.toLowerCase().includes(search)) return false;
    return true;
  });
  const sorters = {
    stars: (a, b) => (b.stars || 0) - (a.stars || 0),
    name: (a, b) => a.full_name.localeCompare(b.full_name),
    release: (a, b) => String(b.latest_release?.published_at || "").localeCompare(String(a.latest_release?.published_at || "")),
    activity: (a, b) => String(b.latest_commit?.committed_at || "").localeCompare(String(a.latest_commit?.committed_at || "")),
  };
  repos.sort(sorters[portfolioState.sort] || sorters.activity);
  $("#portfolio-table").innerHTML = `
    <table>
      <thead><tr>
        <th>Repository</th><th>Stars</th><th>Latest commit</th><th>Release</th>
        <th>Packages</th><th>Open PRs</th><th>CI</th><th>Sync</th>
      </tr></thead>
      <tbody>
        ${repos.map((repo) => `
          <tr>
            <td>
              <a href="/repository.html?id=${repo.id}"><strong>${escapeHtml(repo.full_name)}</strong></a>
              <div class="muted">${escapeHtml(repo.description || "")}</div>
              <div>${(repo.topics || []).slice(0, 3).map((t) => pill(t)).join("")}${repo.archived ? pill("archived", "warn") : ""}${repo.visibility === "private" ? pill("private", "info") : ""}</div>
            </td>
            <td class="mono">${repo.stars ?? 0}</td>
            <td title="${escapeHtml(fmtDate(repo.latest_commit?.committed_at))}">
              <div class="mono">${escapeHtml((repo.latest_commit?.sha || "").slice(0, 7))}</div>
              <div class="muted">${escapeHtml((repo.latest_commit?.subject || "").slice(0, 60))}</div>
              <div class="muted">${escapeHtml(age(repo.latest_commit?.committed_at))}</div>
            </td>
            <td>${repo.latest_release ? `${escapeHtml(repo.latest_release.tag_name)}<div class="muted">${escapeHtml(day(repo.latest_release.published_at))}</div>` : "—"}</td>
            <td class="mono">${(repo.package_versions || []).map((p) => `${escapeHtml(p.registry)}:${escapeHtml(p.version || "?")}`).join("<br>") || "—"}</td>
            <td class="mono">${repo.open_pull_requests ?? 0}</td>
            <td>${ciPill(repo.ci_state)}</td>
            <td>${repo.freshness?.is_stale ? pill("stale", "warn") : pill("fresh", "good")}<div class="muted">${escapeHtml(age(repo.freshness?.fetched_at))}</div></td>
          </tr>`).join("") || `<tr><td colspan="8" class="muted">No repositories indexed. Configure GITHUB_TOKEN and run discovery.</td></tr>`}
      </tbody>
    </table>`;
}

function ciPill(state) {
  const map = {
    success: ["success", "good"], failure: ["failure", "bad"], running: ["running", "info"],
    cancelled: ["cancelled", "warn"], skipped: ["skipped", "warn"], no_runs: ["no runs", ""],
  };
  const [label, kind] = map[state] || [state || "unknown", ""];
  return pill(label, kind);
}

// ---------------------------------------------------------------- repository
const repoState = {
  id: null, detail: null, tabsLoaded: new Set(), commitPage: 1,
  commitQuery: "", prState: "", issueState: "",
};

async function initRepository() {
  renderNavToken($("#nav-token"));
  const params = new URLSearchParams(location.search);
  repoState.id = params.get("id");
  if (!repoState.id) {
    $("#repo-root").innerHTML = errorBox(new Error("Missing repository id"));
    return;
  }
  try {
    const detail = await api(`/repositories/${repoState.id}`);
    repoState.detail = detail;
    document.title = `${detail.full_name} · Panopticon`;
    $("#repo-header").innerHTML = `
      <div class="flex">
        <div>
          <h1>${escapeHtml(detail.full_name)}</h1>
          <div class="muted">${escapeHtml(detail.description || "")}</div>
          <div>${(detail.topics || []).map((t) => pill(t)).join("")}
            ${detail.archived ? pill("archived", "warn") : ""}
            ${detail.visibility === "private" ? pill("private", "info") : ""}
            ${detail.freshness?.is_stale ? pill("stale", "warn") : pill("fresh", "good")}
            ${detail.lifecycle !== "active" ? pill(detail.lifecycle, "bad") : ""}
          </div>
        </div>
        <div class="right flex">
          ${refreshButton(detail.id)}
          <span id="refresh-status" class="muted"></span>
        </div>
      </div>`;
    $("#refresh-btn").addEventListener("click", () => doRefresh(detail.id, $("#refresh-status")));
    const tabs = ["overview", "commits", "branches", "pull-requests", "issues", "releases", "packages", "ci", "security", "raw"];
    $("#tabs").innerHTML = tabs.map((tab) => `<div class="tab" data-tab="${tab}">${tab.replace("-", " ")}</div>`).join("");
    $$("#tabs .tab").forEach((el) => el.addEventListener("click", () => selectTab(el.dataset.tab)));
    const initial = location.hash.replace("#", "") || "overview";
    selectTab(tabs.includes(initial) ? initial : "overview");
  } catch (error) {
    $("#repo-root").innerHTML = errorBox(error);
  }
}

function selectTab(tab) {
  $$("#tabs .tab").forEach((el) => el.classList.toggle("active", el.dataset.tab === tab));
  location.hash = tab;
  const root = $("#tab-content");
  root.innerHTML = `<div class="loading">Loading ${escapeHtml(tab)}…</div>`;
  const loaders = {
    overview: loadOverview, commits: loadCommits, branches: loadBranches,
    "pull-requests": loadPullRequests, issues: loadIssues, releases: loadReleases,
    packages: loadPackages, ci: loadCI, security: loadSecurity, raw: loadRaw,
  };
  loaders[tab]().catch((error) => { root.innerHTML = errorBox(error); });
}

async function loadOverview() {
  const [stats, activity] = await Promise.all([
    api(`/repositories/${repoState.id}/stats`),
    api(`/repositories/${repoState.id}/activity?days=90`),
  ]);
  const repo = repoState.detail;
  const versions = stats.version_alignment || {};
  $("#tab-content").innerHTML = `
    <div class="grid cards">
      ${card("Stars", repo.stars)}${card("Forks", repo.forks)}${card("Open issues", repo.open_issues)}
      ${card("Branches", stats.locally_indexed.branches)}${card("Tags", stats.locally_indexed.tags)}
      ${card("Commits indexed", stats.locally_indexed.commits_indexed)}
    </div>
    <div class="grid" style="grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); margin-top:16px">
      <div class="panel">
        <h2>Repository</h2>
        <div class="mono">
          <div>Provider: ${escapeHtml(repo.provider)} / ${escapeHtml(repo.provider_id)}</div>
          <div>Default branch: ${escapeHtml(repo.default_branch || "—")}</div>
          <div>Language: ${escapeHtml(repo.language || "—")}</div>
          <div>License: ${escapeHtml(repo.license || "—")}</div>
          <div>Created: ${escapeHtml(day(repo.provider_created_at))}</div>
          <div>Updated: ${escapeHtml(day(repo.provider_updated_at))}</div>
          <div>Clone: ${escapeHtml(repo.clone_url || "—")}</div>
          <div>Last synced: ${escapeHtml(age(repo.freshness?.fetched_at))}</div>
        </div>
        <h3>Links</h3>
        <div class="flex">
          ${Object.entries(repo.links || {}).filter(([, v]) => v).map(([k, v]) => `<a class="pill info" href="${escapeHtml(v)}" target="_blank" rel="noreferrer">${escapeHtml(k)}</a>`).join("")}
        </div>
      </div>
      <div class="panel">
        <h2>Version alignment</h2>
        <div class="mono">
          <div>Latest Git tag: ${escapeHtml(versions.values?.latest_git_tag || "—")}</div>
          <div>Stable release: ${escapeHtml(versions.values?.latest_stable_release || "—")}</div>
          <div>Prerelease: ${escapeHtml(versions.values?.latest_prerelease || "—")}</div>
          <div>Package: ${escapeHtml(versions.values?.latest_package_version || "—")}</div>
        </div>
        <p>${versions.all_equal ? pill("aligned", "good") : pill("versions differ", "warn")} ${versions.ordering_known ? "" : pill("ordering unknown", "warn")}</p>
        <h3>Activity (derived, 90d)</h3>
        <div class="mono">
          <div>Commits indexed: ${activity.derived.commits_in_period}</div>
          <div>Releases: ${activity.derived.releases_in_period}</div>
          <div>Merged PRs: ${activity.derived.merged_pull_requests_in_period}</div>
        </div>
        <div class="muted">${escapeHtml(activity.derived.note)}</div>
      </div>
    </div>`;
}

function card(label, value) {
  return `<div class="card"><div class="label">${escapeHtml(label)}</div><div class="value">${escapeHtml(value ?? 0)}</div></div>`;
}

async function loadCommits() {
  const root = $("#tab-content");
  root.innerHTML = `
    <div class="toolbar">
      <input id="commit-search" placeholder="Search indexed commit messages…" value="${escapeHtml(repoState.commitQuery)}">
      <button id="commit-search-btn">Search</button>
      <span class="muted">Commit files are metadata only; no patches are stored.</span>
    </div>
    <div id="commit-list"></div>`;
  $("#commit-search-btn").addEventListener("click", () => {
    repoState.commitQuery = $("#commit-search").value.trim();
    repoState.commitPage = 1;
    drawCommits();
  });
  $("#commit-search").addEventListener("keydown", (event) => {
    if (event.key === "Enter") $("#commit-search-btn").click();
  });
  await drawCommits();
}

async function drawCommits() {
  const target = $("#commit-list");
  const query = new URLSearchParams({ page: repoState.commitPage, per_page: 20 });
  if (repoState.commitQuery) query.set("q", repoState.commitQuery);
  const data = await api(`/repositories/${repoState.id}/commits?${query}`);
  target.innerHTML = `
    <table><thead><tr><th>Commit</th><th>Author</th><th>Date</th><th>Source</th></tr></thead><tbody>
      ${data.items.map((commit) => `
        <tr class="commit-row" data-sha="${escapeHtml(commit.sha)}">
          <td><span class="mono">${escapeHtml(commit.abbreviated_sha || commit.sha.slice(0, 7))}</span>
            <div>${escapeHtml(commit.subject || "")}</div></td>
          <td>${escapeHtml(commit.author?.name || "—")}</td>
          <td>${escapeHtml(day(commit.author?.timestamp || commit.committer?.timestamp))}</td>
          <td class="muted">${escapeHtml(data.source)}</td>
        </tr>`).join("") || `<tr><td colspan="4" class="muted">No commits indexed. Request a metadata refresh or open with live history.</td></tr>`}
    </tbody></table>
    <div class="pagination">
      <button id="prev-page" ${data.pagination.has_previous ? "" : "disabled"}>Prev</button>
      <span class="muted">Page ${data.pagination.page} / ${data.pagination.pages || "?"} · ${data.pagination.total} commits</span>
      <button id="next-page" ${data.pagination.has_next ? "" : "disabled"}>Next</button>
    </div>
    <div id="commit-detail"></div>`;
  $("#prev-page").addEventListener("click", () => { repoState.commitPage--; drawCommits(); });
  $("#next-page").addEventListener("click", () => { repoState.commitPage++; drawCommits(); });
  $$(".commit-row").forEach((row) => row.addEventListener("click", () => showCommit(row.dataset.sha)));
}

async function showCommit(sha) {
  const target = $("#commit-detail");
  target.innerHTML = `<div class="loading">Loading commit ${escapeHtml(sha.slice(0, 7))}…</div>`;
  try {
    const detail = await api(`/repositories/${repoState.id}/commits/${encodeURIComponent(sha)}?include_files=true`);
    target.innerHTML = `
      <div class="detail">
        <div class="flex"><h2>${escapeHtml(detail.subject || detail.sha)}</h2>
          <a class="right" href="${escapeHtml(detail.url || "#")}" target="_blank" rel="noreferrer">Open on provider</a></div>
        <pre>${escapeHtml(detail.message || "")}</pre>
        <div class="mono">
          <div>SHA: ${escapeHtml(detail.sha)}</div>
          <div>Parents: ${escapeHtml((detail.parents || []).join(", ") || "—")}</div>
          <div>Author: ${escapeHtml(detail.author?.name || "—")} &lt;${escapeHtml(detail.author?.email || "")}&gt; ${escapeHtml(fmtDate(detail.author?.timestamp))}</div>
          <div>Committer: ${escapeHtml(detail.committer?.name || "—")} ${escapeHtml(fmtDate(detail.committer?.timestamp))}</div>
          <div>Verification: ${detail.verified === null ? "unknown" : detail.verified ? pill("verified", "good") : pill("unverified", "warn")}</div>
          <div>Stats: ${detail.stats?.changed_files ?? "?"} files · +${detail.stats?.additions ?? "?"} / -${detail.stats?.deletions ?? "?"}</div>
        </div>
        <h3>Changed files (metadata only)</h3>
        <table><thead><tr><th>File</th><th>Status</th><th>Additions</th><th>Deletions</th></tr></thead><tbody>
          ${(detail.files || []).map((file) => `<tr><td class="mono">${escapeHtml(file.filename)}</td><td>${escapeHtml(file.status)}</td><td>${file.additions}</td><td>${file.deletions}</td></tr>`).join("") || `<tr><td colspan="4" class="muted">No file summary available.</td></tr>`}
        </tbody></table>
      </div>`;
  } catch (error) {
    target.innerHTML = errorBox(error);
  }
}

async function loadBranches() {
  const [branches, tags] = await Promise.all([
    api(`/repositories/${repoState.id}/branches`),
    api(`/repositories/${repoState.id}/tags`),
  ]);
  $("#tab-content").innerHTML = `
    <div class="grid" style="grid-template-columns: repeat(auto-fit, minmax(420px, 1fr))">
      <div class="panel"><h2>Branches</h2>
        <table><thead><tr><th>Name</th><th>Head</th><th>Updated</th><th>Flags</th></tr></thead><tbody>
          ${branches.items.map((b) => `<tr><td class="mono">${escapeHtml(b.name)}</td>
            <td class="mono">${escapeHtml((b.head_sha || "").slice(0, 10))}<div class="muted">${escapeHtml((b.latest_commit_message || "").slice(0, 70))}</div></td>
            <td>${escapeHtml(day(b.head_committed_at))}</td>
            <td>${b.is_default ? pill("default", "info") : ""}${b.protected ? pill("protected", "good") : ""}</td></tr>`).join("") || `<tr><td colspan="4" class="muted">No branches indexed.</td></tr>`}
        </tbody></table></div>
      <div class="panel"><h2>Tags</h2>
        <table><thead><tr><th>Name</th><th>Target</th><th>Type</th></tr></thead><tbody>
          ${tags.items.map((t) => `<tr><td class="mono">${escapeHtml(t.name)}</td>
            <td class="mono">${escapeHtml((t.target_sha || "").slice(0, 10))}</td>
            <td>${t.is_semver ? pill("semver", "info") : ""}${escapeHtml(t.tag_type || "")}${t.release_id ? pill("release", "good") : ""}</td></tr>`).join("") || `<tr><td colspan="3" class="muted">No tags indexed.</td></tr>`}
        </tbody></table></div>
    </div>`;
}

async function loadPullRequests() {
  const data = await api(`/repositories/${repoState.id}/pull-requests?per_page=50${repoState.prState ? `&state=${repoState.prState}` : ""}`);
  $("#tab-content").innerHTML = `
    <div class="toolbar">
      <select id="pr-state"><option value="">All states</option>
        ${["open", "closed", "merged"].map((s) => `<option ${repoState.prState === s ? "selected" : ""}>${s}</option>`).join("")}
      </select>
    </div>
    <div class="panel"><table><thead><tr><th>#</th><th>Title</th><th>State</th><th>Author</th><th>Updated</th><th>Changes</th></tr></thead><tbody>
      ${data.items.map((pr) => `<tr>
        <td class="mono">${pr.number}</td>
        <td><a href="${escapeHtml(pr.url)}" target="_blank" rel="noreferrer">${escapeHtml(pr.title)}</a>
          <div>${(pr.labels || []).map((l) => pill(l)).join("")}</div></td>
        <td>${pill(pr.state, pr.state === "open" ? "good" : pr.state === "merged" ? "info" : "")}${pr.draft ? pill("draft", "warn") : ""}</td>
        <td>${escapeHtml(pr.author || "—")}</td>
        <td>${escapeHtml(day(pr.updated_at))}</td>
        <td class="mono">${pr.changed_files ?? "?"} files · +${pr.additions ?? "?"} / -${pr.deletions ?? "?"}</td>
      </tr>`).join("") || `<tr><td colspan="6" class="muted">No pull requests indexed.</td></tr>`}
    </tbody></table></div>`;
  $("#pr-state").addEventListener("change", (event) => {
    repoState.prState = event.target.value;
    loadPullRequests();
  });
}

async function loadIssues() {
  const data = await api(`/repositories/${repoState.id}/issues?per_page=50${repoState.issueState ? `&state=${repoState.issueState}` : ""}`);
  $("#tab-content").innerHTML = `
    <div class="toolbar">
      <select id="issue-state"><option value="">All states</option>
        ${["open", "closed"].map((s) => `<option ${repoState.issueState === s ? "selected" : ""}>${s}</option>`).join("")}
      </select>
    </div>
    <div class="panel"><table><thead><tr><th>#</th><th>Title</th><th>State</th><th>Author</th><th>Updated</th><th>Comments</th></tr></thead><tbody>
      ${data.items.map((issue) => `<tr>
        <td class="mono">${issue.number}</td>
        <td><a href="${escapeHtml(issue.url)}" target="_blank" rel="noreferrer">${escapeHtml(issue.title)}</a>
          <div>${(issue.labels || []).map((l) => pill(l)).join("")}</div></td>
        <td>${pill(issue.state, issue.state === "open" ? "good" : "")}</td>
        <td>${escapeHtml(issue.author || "—")}</td>
        <td>${escapeHtml(day(issue.updated_at))}</td>
        <td class="mono">${issue.comments_count}</td>
      </tr>`).join("") || `<tr><td colspan="6" class="muted">No issues indexed.</td></tr>`}
    </tbody></table></div>`;
  $("#issue-state").addEventListener("change", (event) => {
    repoState.issueState = event.target.value;
    loadIssues();
  });
}

async function loadReleases() {
  const data = await api(`/repositories/${repoState.id}/releases?per_page=50`);
  $("#tab-content").innerHTML = `
    <div class="panel"><table><thead><tr><th>Tag</th><th>Title</th><th>Published</th><th>Flags</th><th>Assets</th></tr></thead><tbody>
      ${data.items.map((release) => `<tr>
        <td class="mono">${escapeHtml(release.tag_name)}</td>
        <td><a href="${escapeHtml(release.url)}" target="_blank" rel="noreferrer">${escapeHtml(release.title || "")}</a></td>
        <td>${escapeHtml(day(release.published_at))}</td>
        <td>${release.prerelease ? pill("prerelease", "warn") : pill("stable", "good")}${release.draft ? pill("draft", "warn") : ""}</td>
        <td class="mono">${(release.assets || []).length} assets<div id="notes-${release.id}"></div>
          <button data-notes="${release.id}">Notes</button></td>
      </tr>`).join("") || `<tr><td colspan="5" class="muted">No releases indexed.</td></tr>`}
    </tbody></table></div>`;
  $$("[data-notes]").forEach((button) => button.addEventListener("click", async () => {
    const id = button.dataset.notes;
    const target = $(`#notes-${id}`);
    target.innerHTML = `<div class="loading">Loading notes…</div>`;
    try {
      const release = await api(`/repositories/${repoState.id}/releases/${id}?include_notes=true`);
      target.innerHTML = `<pre>${escapeHtml(release.notes || "No notes.")}</pre>`;
    } catch (error) {
      target.innerHTML = `<div class="error">${escapeHtml(error.message)}</div>`;
    }
  }));
}

async function loadPackages() {
  const repository = repoState.detail;
  let all = [];
  try {
    all = (await api(`/repositories/${repoState.id}/packages`)).items;
  } catch (_) {}
  $("#tab-content").innerHTML = `
    <div class="panel">
      <h2>Packages</h2>
      <table><thead><tr><th>Registry</th><th>Name</th><th>Latest</th><th>Stable</th><th>Downloads</th><th>Alignment</th><th></th></tr></thead><tbody>
        ${all.map((pkg) => `<tr>
          <td>${escapeHtml(pkg.registry)}</td>
          <td><a href="${escapeHtml(pkg.url)}" target="_blank" rel="noreferrer">${escapeHtml(pkg.name)}</a></td>
          <td class="mono">${escapeHtml(pkg.latest_version || "—")}</td>
          <td class="mono">${escapeHtml(pkg.latest_stable_version || "—")}</td>
          <td class="mono">${pkg.downloads ?? "—"}</td>
          <td>${pkg.version_alignment?.aligned ? pill("aligned", "good") : pill("differs", "warn")}</td>
          <td><button data-versions="${pkg.id}">Versions</button></td>
        </tr><tr><td colspan="7"><div id="versions-${pkg.id}"></div></td></tr>`).join("") || `<tr><td colspan="7" class="muted">No packages linked to this repository.</td></tr>`}
      </tbody></table>
    </div>`;
  $$("[data-versions]").forEach((button) => button.addEventListener("click", async () => {
    const id = button.dataset.versions;
    const target = $(`#versions-${id}`);
    const data = await api(`/packages/${id}/versions?per_page=30`);
    target.innerHTML = `<div class="detail"><div class="mono">${data.items.map((v) => `${escapeHtml(v.version)} ${v.prerelease ? pill("pre", "warn") : ""} ${v.yanked ? pill("yanked", "bad") : ""}`).join("<br>")}</div></div>`;
  }));
}

async function loadCI() {
  const [workflows, runs, status] = await Promise.all([
    api(`/repositories/${repoState.id}/workflows`),
    api(`/repositories/${repoState.id}/workflow-runs?per_page=30`),
    api(`/repositories/${repoState.id}/workflow-runs?per_page=1`),
  ]);
  const summary = status.summary;
  $("#tab-content").innerHTML = `
    <div class="grid cards">
      <div class="card"><div class="label">CI state</div><div class="value">${ciPill(summary.ci_state)}</div></div>
      <div class="card"><div class="label">Default head</div><div class="value mono">${escapeHtml((summary.default_branch_head_sha || "").slice(0, 10))}</div></div>
      <div class="card"><div class="label">Head has success</div><div class="value">${summary.head_has_successful_run ? "yes" : "no"}</div></div>
      <div class="card"><div class="label">Recent failures</div><div class="value">${summary.recent_failures.length}</div></div>
    </div>
    <div class="panel" style="margin-top:16px"><h2>Workflows</h2>
      <table><thead><tr><th>Workflow</th><th>Latest run</th><th>Runs indexed</th><th>Failures</th></tr></thead><tbody>
        ${workflows.items.map((w) => `<tr><td>${escapeHtml(w.workflow_name || w.workflow_id)}</td>
          <td>${ciPill(w.latest_run?.conclusion || w.latest_run?.status)} <span class="muted">${escapeHtml(age(w.latest_run?.started_at))}</span></td>
          <td class="mono">${w.runs}</td><td class="mono">${w.failures}</td></tr>`).join("") || `<tr><td colspan="4" class="muted">No workflow runs indexed.</td></tr>`}
      </tbody></table>
    </div>
    <div class="panel" style="margin-top:16px"><h2>Recent runs</h2>
      <table><thead><tr><th>Run</th><th>Status</th><th>Branch</th><th>Commit</th><th>Duration</th></tr></thead><tbody>
        ${runs.items.map((run) => `<tr><td><a href="${escapeHtml(run.url)}" target="_blank" rel="noreferrer">#${run.run_number || run.provider_id}</a><div class="muted">${escapeHtml(run.workflow_name || "")}</div></td>
          <td>${ciPill(run.conclusion || run.status)}</td><td class="mono">${escapeHtml(run.branch || "—")}</td>
          <td class="mono">${escapeHtml((run.head_sha || "").slice(0, 8))}</td>
          <td class="mono">${run.duration_seconds ?? "?"}s</td></tr>`).join("") || `<tr><td colspan="5" class="muted">No runs.</td></tr>`}
      </tbody></table>
    </div>`;
}

async function loadSecurity() {
  const data = await api(`/repositories/${repoState.id}/security`);
  $("#tab-content").innerHTML = `
    <div class="panel">
      <h2>Security</h2>
      <p class="muted">${escapeHtml(data.note)}</p>
      <h3>Dependabot alerts (${data.dependabot_alerts.length})</h3>
      <table><thead><tr><th>Package</th><th>Severity</th><th>Summary</th><th>Fix</th></tr></thead><tbody>
        ${data.dependabot_alerts.map((alert) => `<tr><td class="mono">${escapeHtml(alert.package || "—")}</td>
          <td>${pill(alert.severity || "unknown", alert.severity === "critical" || alert.severity === "high" ? "bad" : "warn")}</td>
          <td>${escapeHtml(alert.summary || "")}</td><td class="mono">${escapeHtml(alert.fixed_version || "—")}</td></tr>`).join("") || `<tr><td colspan="4" class="muted">No alerts returned.</td></tr>`}
      </tbody></table>
      <h3>Advisories (${data.advisories.length})</h3>
      <table><thead><tr><th>ID</th><th>Severity</th><th>Summary</th></tr></thead><tbody>
        ${data.advisories.map((a) => `<tr><td class="mono">${escapeHtml(a.ghsa_id || a.cve_id || "")}</td><td>${escapeHtml(a.severity || "")}</td><td>${escapeHtml(a.summary || "")}</td></tr>`).join("") || `<tr><td colspan="3" class="muted">No advisories returned.</td></tr>`}
      </tbody></table>
      ${Object.keys(data.errors || {}).length ? `<h3>Errors</h3><pre>${escapeHtml(JSON.stringify(data.errors, null, 2))}</pre>` : ""}
    </div>`;
}

async function loadRaw() {
  const [status] = await Promise.all([api(`/repositories/${repoState.id}/refresh-status`)]);
  $("#tab-content").innerHTML = `
    <div class="panel">
      <div class="flex"><h2>Raw metadata and synchronization</h2>${refreshButton(repoState.id)}<span id="raw-refresh-status" class="muted"></span></div>
      <h3>Freshness</h3><pre>${escapeHtml(JSON.stringify(status.freshness, null, 2))}</pre>
      <h3>Recent jobs</h3><pre>${escapeHtml(JSON.stringify(status.jobs, null, 2))}</pre>
      <h3>Raw provider metadata</h3><pre>${escapeHtml(JSON.stringify(repoState.detail.raw_metadata, null, 2))}</pre>
    </div>`;
  $("#refresh-btn").addEventListener("click", () => doRefresh(repoState.id, $("#raw-refresh-status")));
}

document.addEventListener("DOMContentLoaded", () => {
  const page = document.body.dataset.page;
  if (page === "portfolio") initPortfolio();
  if (page === "repository") initRepository();
});
