# Phase 4C: Stats

> **Parent:** Phase 4 (Metadata & Advanced Features)  
> **Date:** 2026-04-25  
> **Depends on:** Phase 4A (sidecar metadata system), Phase 4B (export)  
> **Blocks:** None (independent leaf)

---

## Overview

Add a `dit stats` CLI command, a server API endpoint, and a Web UI stats panel that aggregate sidecar metadata into repo-level and file-level statistics. Stats are computed on-the-fly from existing sidecar objects — no new storage type is introduced.

The primary use cases are:

- **Local inspection**: `dit stats` gives a quick token/row summary for the whole repo or a specific file/directory.
- **Cross-ref comparison**: `dit stats --compare` shows how row counts and token budgets changed between two commits.
- **Web UI panel**: The Dit file view gains an aggregated totals row and a language distribution summary.

---

## 1. CLI Command

### 1.1 `dit stats`

```
dit stats [PATH] [--ref REF] [--compare REF1 REF2] [--format table|json]
```

| Flag / Arg | Default | Description |
|------------|---------|-------------|
| `PATH` | _(all files)_ | Optional path filter: file name or directory prefix |
| `--ref` | current HEAD | Branch name or commit hash to inspect |
| `--compare REF1 REF2` | _(none)_ | Compare stats between two refs; mutually exclusive with `--ref` |
| `--format` | `table` | Output format: `table` (human-readable) or `json` (machine-readable) |

### 1.2 Default output (table, no filter)

Walks all manifest entries in HEAD (or `--ref`), reads their sidecars, and prints one row per file plus a totals row.

```
$ dit stats
Repo stats at heads/main (commit abc12345)

File                   Rows     Tokens    Chars    Avg fields  Lang
─────────────────────────────────────────────────────────────────────
train.jsonl            1,500    ~375K     1.5M     4.2         zh 82%
eval.jsonl               200    ~48K      192K     4.1         zh 79%
subdir/extra.jsonl       300    ~71K      284K     5.0         en 95%
─────────────────────────────────────────────────────────────────────
TOTAL                  2,000    ~494K     1.97M    4.3         zh 71%

2 of 3 files have sidecar metadata. Run 'dit meta compute' to fill gaps.
```

Notes:
- Files without a sidecar are listed with `—` in metric columns; they are excluded from the totals.
- The footer warning is omitted when all files have sidecars.
- `Tokens` column is formatted with `~` prefix and K/M suffix (same as the existing `formatTokens` helper).
- `Lang` shows the dominant language and its percentage of rows.

### 1.3 Filtered output (`--path`)

```
$ dit stats train.jsonl
Repo stats at heads/main (commit abc12345) — train.jsonl

File          Rows     Tokens    Chars    Avg fields  Lang
──────────────────────────────────────────────────────────
train.jsonl   1,500    ~375K     1.5M     4.2         zh 82%
```

When `PATH` is a directory prefix (e.g. `subdir/`), all manifest entries whose path starts with that prefix are included.

### 1.4 Compare output (`--compare`)

```
$ dit stats --compare abc12345 def67890
Stats delta: abc12345 → def67890

File                   Rows (Δ)      Tokens (Δ)    Chars (Δ)
──────────────────────────────────────────────────────────────
train.jsonl            1500 → 1800   (+300)  375K → 450K  (+75K)   1.5M → 1.8M
eval.jsonl             200  → 200    (=0)    48K  → 48K   (=0)     192K → 192K
TOTAL                  1700 → 2000   (+300)  423K → 498K  (+75K)   1.69M → 1.99M

Note: Files with missing sidecars on either side are omitted.
```

A `(=0)` indicator is shown for unchanged files; they are still included in the table for completeness. Files missing sidecars on either side are skipped with no warning per file (a single footer note suffices if any were skipped).

### 1.5 JSON output

`--format json` produces a machine-readable object. The shape mirrors the server API response (Section 3.2).

---

## 2. Core Stats Module

### 2.1 New file: `src/dit/core/stats.py`

```python
def repo_stats(
    store: ObjectStore,
    commit_hash: str,
    path_prefix: str | None = None,
) -> dict:
    """Aggregate sidecar data for all manifest files in a commit.

    Returns:
    {
      "commit_hash": "abc12345...",
      "files": [
        {
          "path": "train.jsonl",
          "row_count": 1500,
          "char_count": 1500000,
          "token_estimate": 375000,
          "avg_fields": 4.2,
          "lang_distribution": {"zh": 1230, "en": 270},
          "has_sidecar": True,
        },
        ...
      ],
      "totals": {
        "file_count": 3,
        "files_with_sidecar": 3,
        "row_count": 2000,
        "char_count": 1970000,
        "token_estimate": 494000,
        "lang_distribution": {"zh": 1420, "en": 580},
      }
    }
    """
```

```python
def compare_stats(
    store: ObjectStore,
    commit1: str,
    commit2: str,
    path_prefix: str | None = None,
) -> dict:
    """Compute delta between two commits' sidecar aggregates.

    Returns:
    {
      "commit1": "abc12345...",
      "commit2": "def67890...",
      "files": [
        {
          "path": "train.jsonl",
          "old": { <file entry as above> },
          "new": { <file entry as above> },
          "delta": {
            "row_count": 300,
            "char_count": 300000,
            "token_estimate": 75000,
          }
        },
        ...
      ],
      "totals_delta": {
        "row_count": 300,
        "char_count": 300000,
        "token_estimate": 75000,
      }
    }
    """
```

### 2.2 Implementation details

**`repo_stats`:**

1. Read and deserialize commit from `store.read("commits", commit_hash)`.
2. Call `flatten_tree(store, commit.tree_hash)` to get the flat path map.
3. Filter entries to `obj_type == "manifest"`. If `path_prefix` is given, further filter to paths that start with the cleaned prefix.
4. For each manifest entry:
   - If `sidecar_hash` is `None`: append a file entry with `has_sidecar: False` and all numeric fields set to `None`.
   - Otherwise: read the sidecar object, call `sidecar_summary()` from `core/sidecar.py`, and include results with `has_sidecar: True`.
5. Compute totals only over files where `has_sidecar == True`. Lang distribution in totals is a merged count dict (raw counts, not percentages).

**`compare_stats`:**

1. Call `repo_stats(store, commit1)` and `repo_stats(store, commit2)`.
2. Build a union of paths across both results.
3. For each path, include a `files` entry with `old` and `new` sub-objects (either may be absent if the file didn't exist in that commit — represented as `None`).
4. Compute delta only for files where both `old.has_sidecar` and `new.has_sidecar` are `True`; otherwise omit the path from the delta.
5. `totals_delta` is the sum of all included per-file deltas.

### 2.3 Return types

Both functions return plain `dict` (consistent with `sidecar_summary()` convention — no dataclasses). This keeps the output directly JSON-serializable without extra conversion.

---

## 3. Server API

### 3.1 New endpoint

```
GET /api/v1/repos/{repo}/stats/{commit_hash}
```

Optional query params:

| Param | Default | Description |
|-------|---------|-------------|
| `path` | _(none)_ | Filter to file/directory prefix |

**Authentication:** `require_permission("read")` (same as other read endpoints).

**Response (200):**

```json
{
  "commit_hash": "abc1234567890abcd...",
  "files": [
    {
      "path": "train.jsonl",
      "row_count": 1500,
      "char_count": 1500000,
      "token_estimate": 375000,
      "avg_fields": 4.2,
      "lang_distribution": {"zh": 1230, "en": 270},
      "has_sidecar": true
    },
    {
      "path": "eval.jsonl",
      "row_count": null,
      "char_count": null,
      "token_estimate": null,
      "avg_fields": null,
      "lang_distribution": null,
      "has_sidecar": false
    }
  ],
  "totals": {
    "file_count": 2,
    "files_with_sidecar": 1,
    "row_count": 1500,
    "char_count": 1500000,
    "token_estimate": 375000,
    "lang_distribution": {"zh": 1230, "en": 270}
  }
}
```

**Error cases:**

- `404` if commit hash is not found.
- `200` with empty `files` list if the commit has no manifest entries (not an error).

### 3.2 New file: `src/dit/server/routes/stats_api.py`

```python
router = APIRouter(prefix="/api/v1/repos", tags=["stats"])

@router.get("/{repo}/stats/{commit_hash}")
async def repo_stats_endpoint(
    repo: str,
    commit_hash: str,
    path: Optional[str] = Query(default=None),
    request: Request,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("read")),
):
    from dit.core.stats import repo_stats
    ...
```

The handler:
1. Calls `_get_repo(repo, session)` to verify repo exists.
2. Builds `store = _store_for_repo(request, repo)` (same pattern as other route files).
3. Calls `repo_stats(store, commit_hash, path_prefix=path)`.
4. Returns the dict directly; FastAPI serializes it.

Register the router in `src/dit/server/app.py` alongside `meta_router` and `export_router`.

---

## 4. Gateway + Web UI

### 4.1 Gateway route

Add one route to the `dit` group in `routers/api/v1/api.go`:

```go
m.Get("/stats/{commit}", repo.DatahubGetStats)
```

This is a GET with an optional `path` query param, so no body reading is needed.

### 4.2 Handler in `routers/api/v1/repo/dit.go`

```go
func DatahubGetStats(ctx *context.APIContext) {
    proxyToDatahub(ctx, func() ([]byte, int, error) {
        path := "/api/v1/repos/" + ctx.Repo.Repository.Name + "/stats/" + ctx.Params(":commit")
        if p := ctx.FormString("path"); p != "" {
            path += "?path=" + url.QueryEscape(p)
        }
        return dit.DefaultClient().GetStats(ctx, ctx.Repo.Repository.Name, ctx.Params(":commit"), ctx.FormString("path"))
    })
}
```

### 4.3 Client method in `modules/dit/client.go`

```go
func (c *Client) GetStats(ctx context.Context, repoName, commitHash, pathFilter string) ([]byte, int, error) {
    path := "/api/v1/repos/" + repoName + "/stats/" + commitHash
    if pathFilter != "" {
        path += "?path=" + url.QueryEscape(pathFilter)
    }
    return c.do(ctx, http.MethodGet, path, nil)
}
```

### 4.4 DataRepoHome.vue: Stats panel

Add a collapsible "Stats" section below the file table. It is loaded lazily when the user expands it (to avoid extra latency on initial load).

**Template additions (below the file table `<div class="ui segment">`)**:

```html
<!-- Stats panel (collapsed by default) -->
<div class="ui segment" v-if="commitHash">
  <div class="ui accordion" ref="statsAccordion">
    <div class="title" @click="toggleStats">
      <i class="dropdown icon"></i>
      <strong>Dataset Stats</strong>
      <span v-if="repoStats" class="ui small label">
        {{ formatTokens(repoStats.totals.token_estimate) }} tokens
      </span>
    </div>
    <div class="content" v-show="statsOpen">
      <div v-if="statsLoading" class="ui active centered inline loader"></div>
      <div v-else-if="statsError" class="ui small negative message">{{ statsError }}</div>
      <div v-else-if="repoStats">

        <!-- Totals row -->
        <div class="ui tiny statistics" style="margin-bottom: 1em;">
          <div class="statistic">
            <div class="value">{{ repoStats.totals.row_count?.toLocaleString() ?? '—' }}</div>
            <div class="label">Total Rows</div>
          </div>
          <div class="statistic">
            <div class="value">{{ formatTokens(repoStats.totals.token_estimate) }}</div>
            <div class="label">Est. Tokens</div>
          </div>
          <div class="statistic">
            <div class="value">{{ formatSize(repoStats.totals.char_count) }}</div>
            <div class="label">Chars</div>
          </div>
          <div class="statistic">
            <div class="value">{{ repoStats.totals.files_with_sidecar }}/{{ repoStats.totals.file_count }}</div>
            <div class="label">Files w/ Meta</div>
          </div>
        </div>

        <!-- Language distribution (text bars) -->
        <div v-if="topLangs.length > 0" style="margin-bottom: 1em;">
          <strong>Language distribution</strong>
          <div v-for="([lang, pct]) in topLangs" :key="lang" style="margin-top: 4px;">
            <span style="display:inline-block; width: 4em;">{{ lang }}</span>
            <span
              style="display:inline-block; background:#2185d0; height:10px; vertical-align:middle;"
              :style="{width: (pct * 2) + 'px'}"
            ></span>
            <span style="margin-left: 6px; font-size: 0.9em;">{{ Math.round(pct) }}%</span>
          </div>
        </div>

        <!-- Per-file breakdown table -->
        <table class="ui very basic compact table">
          <thead>
            <tr>
              <th>File</th>
              <th class="right aligned">Rows</th>
              <th class="right aligned">Tokens</th>
              <th class="right aligned">Avg fields</th>
              <th class="right aligned">Top lang</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="f in repoStats.files" :key="f.path">
              <td>{{ f.path }}</td>
              <td class="right aligned">{{ f.row_count?.toLocaleString() ?? '—' }}</td>
              <td class="right aligned">{{ f.has_sidecar ? formatTokens(f.token_estimate) : '—' }}</td>
              <td class="right aligned">{{ f.avg_fields?.toFixed(1) ?? '—' }}</td>
              <td class="right aligned">{{ f.has_sidecar ? formatLang(f.lang_distribution) : '—' }}</td>
            </tr>
          </tbody>
        </table>

      </div>
    </div>
  </div>
</div>
```

**Script additions:**

```js
data() {
  return {
    // ... existing fields ...
    statsOpen: false,
    statsLoading: false,
    statsError: null,
    repoStats: null,
    commitHash: null,   // set in loadTree()
  };
},
computed: {
  topLangs() {
    if (!this.repoStats?.totals?.lang_distribution) return [];
    const dist = this.repoStats.totals.lang_distribution;
    const total = Object.values(dist).reduce((a, b) => a + b, 0);
    if (total === 0) return [];
    return Object.entries(dist)
      .map(([lang, count]) => [lang, (count / total) * 100])
      .sort((a, b) => b[1] - a[1])
      .slice(0, 5);
  },
},
methods: {
  // In loadTree(), after resolving commitHash:
  //   this.commitHash = commitHash;
  //   this.repoStats = null;   // reset on branch change

  async toggleStats() {
    this.statsOpen = !this.statsOpen;
    if (this.statsOpen && !this.repoStats && !this.statsLoading) {
      await this.loadStats();
    }
  },
  async loadStats() {
    this.statsLoading = true;
    this.statsError = null;
    try {
      this.repoStats = await ditFetch(
        this.owner, this.repo,
        `/stats/${this.commitHash}`,
      );
    } catch (e) {
      this.statsError = e.message;
    } finally {
      this.statsLoading = false;
    }
  },
},
```

The stats panel is **not** loaded automatically on page mount — the user clicks "Dataset Stats" to expand it. On branch change, `repoStats` is reset to `null` so stale data is not shown.

---

## 5. Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Stats storage | None — computed on-the-fly from sidecars | Sidecar objects are already aggregatable; a stats cache would require invalidation logic and adds complexity for Phase 4C scope |
| Partial coverage | Include files without sidecars as `has_sidecar: false`; exclude from totals | Graceful degradation — users see which files lack metadata rather than silently wrong totals |
| `lang_distribution` in totals | Raw counts (not percentages) | Counts aggregate naturally across files; percentages can be computed by the caller from counts |
| `--compare` skips files missing sidecars on either side | Skip with footer note | A delta is meaningless when one side has no data; silent skip avoids noisy output |
| Web UI stats panel | Lazy-loaded, collapsed by default | Avoids extra HTTP round-trip on initial page load; keeps the file tree fast |
| Language bar chart | Pure CSS/HTML inline bars, no charting library | Keeps the Vue component dependency-free; simple enough for the data volume involved |
| Token display | `~` prefix + K/M abbreviation (reuse `formatTokens`) | Consistent with existing file table column; estimated values shouldn't imply false precision |
| `dit stats --ref` | Accept branch name or full commit hex | Consistent with how `dit export --ref` works; `RefStore.get_branch()` is tried first, then raw hash |

---

## 6. Out of Scope

- **Stats caching / background computation.** Phase 4C stats are always computed at request time. For very large repos (thousands of files), a pre-computed stats object could be stored as a commit attachment — this is deferred to Phase 5.
- **Time-series stats / commit history graphs.** Tracking how totals evolved across the commit log is a UI feature for a later phase.
- **Token distribution histogram.** Per-row token bucket counts (e.g., how many rows fall in 0–100, 100–500, 500+ token ranges) require iterating sidecar entries rather than just summaries. The per-file `sidecar_summary()` does not expose this. Deferred until there is a clear need.
- **`dit stats` against working directory.** The command always operates on committed data (HEAD or a named ref). On-disk unstaged data is out of scope.
- **Gateway export of stats as CSV.** Stats output is structured JSON; CSV reshaping is not needed for Phase 4C.
