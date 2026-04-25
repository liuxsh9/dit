# Phase 4D: Row-Level Search

> **Parent:** Phase 4 (Metadata & Advanced Features)
> **Date:** 2026-04-25
> **Depends on:** Phase 4A (sidecar metadata system), Phase 4B (export)
> **Blocks:** None (independent leaf)

---

## Overview

Add a `dit search` CLI command, a server API endpoint, and a Web UI search bar that allow users to find rows in a commit by content. Search is brute-force — the system iterates through manifest entries, reads row bytes from the object store, and matches against a query string or a field-specific pattern. No search index is introduced in Phase 4.

The primary use cases are:

- **Local inspection**: `dit search "LRU缓存"` locates all rows in the current commit that contain the substring, showing file, row index, and a short excerpt.
- **Field-scoped search**: `dit search --field messages[0].content "LRU"` narrows matching to a specific JSON field path, avoiding false positives from metadata fields.
- **Web UI lookup**: A search bar above the file tree submits a query and shows a collapsible results list without navigating away from the page.

---

## 1. CLI Command

### 1.1 `dit search`

```
dit search QUERY [PATH] [--ref REF] [--field FIELD_PATH] [--limit N] [--format table|json]
```

| Flag / Arg | Default | Description |
|------------|---------|-------------|
| `QUERY` | _(required)_ | Substring to match, case-insensitive |
| `PATH` | _(all files)_ | Optional file name or directory prefix to restrict the scan |
| `--ref` | current HEAD | Branch name or full commit hex to search |
| `--field` | _(full row)_ | Dot-notation field path to match within (e.g. `messages[0].content`, `instruction`) |
| `--limit` | `50` | Maximum number of matches to return; scanning stops once the limit is reached |
| `--format` | `table` | Output format: `table` (human-readable) or `json` (machine-readable) |

### 1.2 Default output (table)

```
$ dit search "LRU缓存"
Searching heads/main (commit abc12345) for "LRU缓存"

File            Row   Excerpt
────────────────────────────────────────────────────────────────────────
train.jsonl     42    ...实现一个LRU缓存，支持get和put操作...
train.jsonl     187   ...LRU缓存淘汰策略是指最近最少使用...
eval.jsonl      5     ...LRU缓存的时间复杂度为O(1)...
────────────────────────────────────────────────────────────────────────
3 matches (scanned 1700 rows)
```

Notes:
- The `Excerpt` column contains the `highlight` string from the match object (see Section 2.2). It shows ≈30 characters of context on each side of the match, trimmed with `...` if the surrounding text is longer.
- When `--field` is specified, the excerpt is taken from the matched field value rather than the raw row JSON.
- When the limit is reached before all rows are scanned, a footer note is printed: `Limit reached. Pass --limit N to see more.`

### 1.3 Filtered output

```
$ dit search "LRU缓存" train.jsonl
Searching heads/main (commit abc12345) for "LRU缓存" in train.jsonl

File          Row   Excerpt
──────────────────────────────────────────────────────────────────
train.jsonl   42    ...实现一个LRU缓存，支持get和put操作...
train.jsonl   187   ...LRU缓存淘汰策略是指最近最少使用...
──────────────────────────────────────────────────────────────────
2 matches (scanned 1500 rows)
```

When `PATH` is a directory prefix (e.g. `subdir/`), all manifest entries whose path starts with that prefix are scanned.

### 1.4 Field-scoped output

```
$ dit search --field messages[0].content "LRU"
Searching heads/main (commit abc12345) for "LRU" in field messages[0].content

File          Row   Excerpt
──────────────────────────────────────────────────────────────────
train.jsonl   42    ...实现一个LRU缓存...
──────────────────────────────────────────────────────────────────
1 match (scanned 1700 rows)
```

### 1.5 JSON output

`--format json` produces a machine-readable object. The shape mirrors the server API response (Section 3.2).

---

## 2. Core Search Module

### 2.1 New file: `src/dit/core/search.py`

```python
def search_rows(
    store: ObjectStore,
    commit_hash: str,
    query: str,
    *,
    path_prefix: str | None = None,
    field_path: str | None = None,
    limit: int = 50,
) -> dict:
    """Brute-force substring search across JSONL rows in a commit.

    Returns:
    {
      "commit_hash": "abc12345...",
      "query": "LRU缓存",
      "field_path": "messages[0].content",   # or null
      "matches": [
        {
          "file": "train.jsonl",
          "row_index": 42,
          "row_hash": "abc...",
          "content": { <full row as dict> },
          "highlight": "...实现一个LRU缓存，支持get和put..."
        },
        ...
      ],
      "total_scanned": 1700,
      "limit_reached": false
    }

    Raises FileNotFoundError if commit_hash is not found in store.
    Matching is case-insensitive substring search.
    Scanning stops once `limit` matches are collected.
    """
```

### 2.2 Implementation details

**Top-level flow:**

1. Read and deserialize commit from `store.read("commits", commit_hash)`.
2. Call `flatten_tree(store, commit.tree_hash)` to get the flat path map.
3. Filter entries to `obj_type == "manifest"`. If `path_prefix` is given, further filter to paths starting with the cleaned prefix.
4. For each manifest path (sorted), read the manifest object and iterate its entries.
5. For each `ManifestEntry`, read the row bytes from `store.read("rows", entry.row_hash)`.
6. Parse the row bytes as JSON.
7. Run the match check (see below).
8. If matched, append a match dict to the results list.
9. Stop when `len(matches) == limit`.

**Match check:**

- Lower-case `query` once before the loop.
- If `field_path` is `None`: convert the parsed row back to a JSON string (compact), lower-case it, and check `query in text`.
- If `field_path` is set: extract the value at that path (see field path parsing below), convert to string, lower-case, and check `query in value_str`. If the path does not exist in the row, the row is silently skipped (no match, no error).

**Field path parsing (`_resolve_field`):**

A helper function accepts a parsed row dict and a dot-notation path string, and returns the nested value or `None` if the path is missing.

```python
def _resolve_field(row: dict, field_path: str) -> object | None:
    """Navigate nested dict/list using dot-notation with bracket indexing.

    Examples:
      "instruction"             → row["instruction"]
      "messages[0].content"    → row["messages"][0]["content"]
      "meta.source"            → row["meta"]["source"]
    """
```

Parsing rules:
- Split on `.` to get path segments, but handle bracket notation within a segment.
- Each segment may be a plain key (`instruction`) or a key with a list index (`messages[0]`).
- For a segment with an index, access the dict key first, then the list index.
- If at any step the key is missing, the index is out of range, or the current value is not a dict/list as expected, return `None`.

**Highlight generation (`_make_highlight`):**

```python
def _make_highlight(text: str, query: str, context: int = 30) -> str:
    """Return a short excerpt with the matched substring in context.

    Returns at most `context` characters before and after the match position,
    with '...' prepended/appended if the surrounding text was trimmed.
    """
```

- Locate the first occurrence of `query.lower()` in `text.lower()`.
- Extract `text[max(0, pos-context) : pos+len(query)+context]`.
- Prepend `...` if `pos > context`; append `...` if `pos + len(query) + context < len(text)`.
- The match itself is not altered (no ANSI codes — the CLI may add styling separately).

**Scanned count:** `total_scanned` is incremented for every row read from the store, regardless of whether it matched. It reflects how many rows were examined before the search finished.

### 2.3 Return type

`search_rows` returns a plain `dict` (consistent with `repo_stats` and `export_commit` conventions). All values are JSON-serializable without extra conversion.

---

## 3. Server API

### 3.1 New endpoint

```
POST /api/v1/repos/{repo}/search
```

**Request body (JSON):**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `ref` | string | `"heads/main"` | Branch name or commit hash to search |
| `query` | string | _(required)_ | Substring to match |
| `file` | string \| null | `null` | Optional file/directory prefix filter |
| `field` | string \| null | `null` | Optional dot-notation field path |
| `limit` | integer | `50` | Max results to return |

```json
{
  "ref": "heads/main",
  "query": "LRU缓存",
  "file": "train.jsonl",
  "field": "messages[0].content",
  "limit": 50
}
```

**Why POST:** Query strings can be long and contain special characters (Chinese text, punctuation, bracket notation). Using a POST body avoids URL encoding issues and keeps the query readable in logs.

**Authentication:** `require_permission("read")` (same as all other read endpoints).

**Response (200):**

```json
{
  "commit_hash": "abc1234567890abcd...",
  "query": "LRU缓存",
  "field_path": "messages[0].content",
  "matches": [
    {
      "file": "train.jsonl",
      "row_index": 42,
      "row_hash": "3a9f...",
      "content": {
        "instruction": "实现一个LRU缓存",
        "messages": [{"role": "user", "content": "实现一个LRU缓存，支持get和put操作"}],
        "response": "..."
      },
      "highlight": "...实现一个LRU缓存，支持get和put..."
    }
  ],
  "total_scanned": 1700,
  "limit_reached": false
}
```

**Error cases:**

- `422` if `query` is missing or empty.
- `404` if the ref cannot be resolved to a commit hash.
- `200` with empty `matches` list if the query matches nothing (not an error).

### 3.2 New file: `src/dit/server/routes/search_api.py`

```python
from pydantic import BaseModel

class SearchRequest(BaseModel):
    ref: str = "heads/main"
    query: str
    file: str | None = None
    field: str | None = None
    limit: int = 50

router = APIRouter(prefix="/api/v1/repos", tags=["search"])

@router.post("/{repo}/search")
async def repo_search_endpoint(
    repo: str,
    body: SearchRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("read")),
):
    from dit.core.search import search_rows
    ...
```

The handler:
1. Calls `_get_repo(repo, session)` to verify repo exists (raises `404` if not).
2. Resolves `body.ref` to a commit hash via `RefStore` (branch names) or uses it directly for hex hashes.
3. Builds `store = _store_for_repo(request, repo)` (same helper as `stats_api.py`).
4. Calls `search_rows(store, commit_hash, body.query, path_prefix=body.file, field_path=body.field, limit=body.limit)`.
5. Returns the dict directly; FastAPI serializes it.

Register the router in `src/dit/server/app.py` alongside `meta_router`, `export_router`, and `stats_router`.

---

## 4. Gateway + Web UI

### 4.1 Gateway route

Add one route to the `dit` group in `routers/api/v1/api.go`:

```go
m.Post("/search", repo.DatahubSearch)
```

This is a POST passthrough — no URL parameters need extraction.

### 4.2 Handler in `routers/api/v1/repo/dit.go`

```go
func DatahubSearch(ctx *context.APIContext) {
    body, err := io.ReadAll(ctx.Req.Body)
    if err != nil {
        ctx.Error(http.StatusBadRequest, "read body", err)
        return
    }
    data, status, err := dit.DefaultClient().Search(ctx, ctx.Repo.Repository.Name, body)
    proxyResponse(ctx, data, status, err)
}
```

### 4.3 Client method in `modules/dit/client.go`

```go
func (c *Client) Search(ctx context.Context, repoName string, body []byte) ([]byte, int, error) {
    return c.do(ctx, http.MethodPost, "/api/v1/repos/"+repoName+"/search", body)
}
```

### 4.4 DataRepoHome.vue: Search panel

Add a search bar above the file tree, and a collapsible results panel below it. Search is triggered on form submit (Enter or button click), not on every keystroke.

**Template additions (inside `<div class="ui segments">`, before the file tree segment):**

```html
<!-- Search bar -->
<div class="ui segment" v-if="commitHash">
  <div class="ui action input" style="width:100%;">
    <input
      type="text"
      placeholder='Search rows (e.g. "LRU缓存")'
      v-model="searchQuery"
      @keyup.enter="submitSearch"
    />
    <select class="ui compact selection dropdown" v-model="searchField" style="min-width:160px;">
      <option value="">Full row</option>
      <option value="instruction">instruction</option>
      <option value="response">response</option>
      <option value="messages[0].content">messages[0].content</option>
    </select>
    <button class="ui button" :class="{loading: searchLoading}" @click="submitSearch">
      <i class="search icon"></i> Search
    </button>
  </div>
</div>

<!-- Search results (collapsible) -->
<div class="ui segment" v-if="searchResults">
  <div class="ui accordion">
    <div class="title" @click="searchResultsOpen = !searchResultsOpen" style="cursor:pointer;">
      <i class="dropdown icon"></i>
      <strong>Search Results</strong>
      <span class="ui small label" style="margin-left:8px;">
        {{ searchResults.matches.length }} match{{ searchResults.matches.length !== 1 ? 'es' : '' }}
        (scanned {{ searchResults.total_scanned.toLocaleString() }} rows)
      </span>
      <span v-if="searchResults.limit_reached" class="ui small yellow label" style="margin-left:4px;">
        limit reached
      </span>
    </div>
    <div class="content" v-show="searchResultsOpen">
      <div v-if="searchError" class="ui small negative message">{{ searchError }}</div>
      <div v-else-if="searchResults.matches.length === 0" class="ui small message">
        No matches found for "{{ searchResults.query }}".
      </div>
      <table v-else class="ui very basic compact table">
        <thead>
          <tr>
            <th>File</th>
            <th>Row</th>
            <th>Excerpt</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="m in searchResults.matches" :key="m.file + ':' + m.row_index">
            <td>{{ m.file }}</td>
            <td class="right aligned">{{ m.row_index }}</td>
            <td style="font-family:monospace;font-size:0.9em;white-space:pre-wrap;">{{ m.highlight }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</div>
```

**Script additions (`data()`):**

```js
searchQuery: '',
searchField: '',
searchLoading: false,
searchError: null,
searchResults: null,
searchResultsOpen: true,
```

**Script additions (`methods`):**

```js
async submitSearch() {
  if (!this.searchQuery.trim()) return;
  this.searchLoading = true;
  this.searchError = null;
  this.searchResults = null;
  try {
    this.searchResults = await ditFetch(
      this.owner, this.repo,
      '/search',
      {
        method: 'POST',
        body: JSON.stringify({
          ref: this.commitHash,
          query: this.searchQuery.trim(),
          field: this.searchField || null,
          limit: 50,
        }),
      },
    );
    this.searchResultsOpen = true;
  } catch (e) {
    this.searchError = e.message;
  } finally {
    this.searchLoading = false;
  }
},
```

In `loadTree()` (on branch change), reset search state:

```js
this.searchResults = null;
this.searchQuery = '';
this.searchField = '';
```

The search panel is **not** shown until at least one search has been submitted. Results persist across branch changes are cleared when the branch changes.

---

## 5. Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Search algorithm | Brute-force row iteration | Phase 4 scope; adding a search index (e.g. SQLite FTS) would require maintaining it across commits and is deferred to Phase 5 |
| HTTP method for search | POST | Query strings can be long and contain special characters; a POST body is safer and more readable |
| Case sensitivity | Case-insensitive by default | Matches user expectations; most data contains mixed-case content |
| `field_path` resolution | Silent skip if path missing | Sparse schemas are common in SFT data; skipping is less surprising than an error |
| `highlight` context | 30 chars on each side | Enough to show surrounding sentence fragment without overwhelming the table column |
| `highlight` input | Raw JSON string (full-row mode) or extracted field string (field mode) | Field mode makes the excerpt directly readable; full-row mode is a fallback |
| Limit default | 50 | Keeps memory and latency predictable; CLI users can raise it with `--limit` |
| `limit_reached` flag | Explicit boolean in response | Lets the client distinguish "no more results" from "results were cut" without parsing footers |
| Web UI field selector | Fixed dropdown with common field names | Avoids free-text field entry complexity in the first iteration; users with unusual schemas can use the CLI |
| Web UI search trigger | On Enter or button click only | Avoids issuing a brute-force scan on every keystroke |
| `total_scanned` in response | Always included | Gives the caller a sense of search cost; useful for spotting slow queries |

---

## 6. Out of Scope

- **Search index / FTS.** Building and maintaining an inverted index or SQLite FTS table requires a background compute step and index invalidation on new commits. This is deferred to Phase 5.
- **Regex search.** Only fixed-string substring matching is supported. Regex introduces complexity in sanitizing inputs and is not needed for the primary use case.
- **Fuzzy / approximate matching.** Edit-distance search (e.g. via `difflib`) is out of scope.
- **Search across commits / history.** The search operates on a single commit ref. Cross-commit search would require iterating the commit log and is a distinct feature.
- **Real-time search-as-you-type.** The Web UI triggers search only on submit to avoid hammering the brute-force scan endpoint.
- **Highlighted rendering in the Web UI.** The `highlight` string is rendered as plain monospace text. Bold-wrapping the matched substring in the browser (e.g. with `<mark>`) is a cosmetic improvement deferred to a later UI polish pass.
- **Export filtered by search results.** Combining `dit search` output with `dit export` (e.g. export only matching rows) is a workflow that can be composed by piping `--format json` output, but no first-class `--filter` flag on `dit export` is added in Phase 4D.
- **`dit search` against the working directory.** The command always operates on committed data (HEAD or a named ref). Searching uncommitted local changes is out of scope.
