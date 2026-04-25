# Phase 4A-Gateway Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add gateway proxy routes for sidecar metadata API and enhance Vue components with metadata display.

**Architecture:** 4 new Go proxy routes forwarding to dit-core, Vue components fetch and display sidecar stats.

**Tech Stack:** Go 1.22, Vue 3, JavaScript

**Depends on:** Phase 4A-Server (Python API endpoints must exist)

---

## Overview

This plan covers the Forgejo fork (`datahub-gateway`) changes for Phase 4A. It is entirely contained within `~/code/datahub-gateway/`. There are no changes to `dit-core` in this plan.

The work breaks into three layers:
1. **Go client** — 4 new methods on `Client` that call dit-core
2. **Go handlers + routes** — 4 handler functions wired into the existing dit route group
3. **Vue UI** — `DataRepoHome.vue` gains Tokens/Lang columns; `DataDiffView.vue` gains a metadata delta header

Each Go task is TDD: write failing tests first, then implement.

---

## Task 1 — Go client: MetaCompute (TDD)

**Files:**
- `modules/dit/client.go`
- `modules/dit/client_test.go`

### Step 1A — Write the failing test

Append to `modules/dit/client_test.go`:

```go
func TestMetaCompute(t *testing.T) {
	client := newTestClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		assert.Equal(t, http.MethodPost, r.Method)
		assert.Equal(t, "/api/v1/repos/myrepo/meta/compute", r.URL.Path)
		assert.Equal(t, "application/json", r.Header.Get("Content-Type"))
		assert.Equal(t, "Bearer test-token", r.Header.Get("Authorization"))
		body, _ := io.ReadAll(r.Body)
		assert.Contains(t, string(body), "train.jsonl")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"commit_hash":"abc123","sidecars":[{"file":"train.jsonl","sidecar_hash":"def456"}]}`))
	}))
	data, status, err := client.MetaCompute(context.Background(), "myrepo", []byte(`{"file":"train.jsonl"}`))
	require.NoError(t, err)
	assert.Equal(t, http.StatusOK, status)
	assert.Contains(t, string(data), "commit_hash")
}
```

Run: `cd ~/code/datahub-gateway && go test ./modules/dit/... -run TestMetaCompute` — expect compile error (method not yet defined).

### Step 1B — Implement MetaCompute

Append to `modules/dit/client.go`:

```go
func (c *Client) MetaCompute(ctx context.Context, repoName string, body []byte) ([]byte, int, error) {
	return c.do(ctx, http.MethodPost, "/api/v1/repos/"+repoName+"/meta/compute", body)
}
```

Run: `go test ./modules/dit/... -run TestMetaCompute` — expect pass.

---

## Task 2 — Go client: MetaGet (TDD)

### Step 2A — Write the failing test

Append to `modules/dit/client_test.go`:

```go
func TestMetaGet(t *testing.T) {
	client := newTestClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		assert.Equal(t, http.MethodGet, r.Method)
		assert.Equal(t, "/api/v1/repos/myrepo/meta/abc123/train.jsonl", r.URL.Path)
		assert.Equal(t, "Bearer test-token", r.Header.Get("Authorization"))
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"manifest_hash":"abc123","entries":[]}`))
	}))
	data, status, err := client.MetaGet(context.Background(), "myrepo", "abc123", "train.jsonl")
	require.NoError(t, err)
	assert.Equal(t, http.StatusOK, status)
	assert.Contains(t, string(data), "manifest_hash")
}
```

Run: `go test ./modules/dit/... -run TestMetaGet` — expect compile error.

### Step 2B — Implement MetaGet

Append to `modules/dit/client.go`:

```go
func (c *Client) MetaGet(ctx context.Context, repoName, commit, filePath string) ([]byte, int, error) {
	return c.do(ctx, http.MethodGet, "/api/v1/repos/"+repoName+"/meta/"+commit+"/"+filePath, nil)
}
```

Run: `go test ./modules/dit/... -run TestMetaGet` — expect pass.

---

## Task 3 — Go client: MetaSummary (TDD)

### Step 3A — Write the failing test

Append to `modules/dit/client_test.go`:

```go
func TestMetaSummary(t *testing.T) {
	client := newTestClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		assert.Equal(t, http.MethodGet, r.Method)
		assert.Equal(t, "/api/v1/repos/myrepo/meta/abc123/train.jsonl/summary", r.URL.Path)
		assert.Equal(t, "Bearer test-token", r.Header.Get("Authorization"))
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"row_count":1500,"token_estimate":1130250,"lang_distribution":{"zh":0.82}}`))
	}))
	data, status, err := client.MetaSummary(context.Background(), "myrepo", "abc123", "train.jsonl")
	require.NoError(t, err)
	assert.Equal(t, http.StatusOK, status)
	assert.Contains(t, string(data), "row_count")
}
```

Run: `go test ./modules/dit/... -run TestMetaSummary` — expect compile error.

### Step 3B — Implement MetaSummary

Append to `modules/dit/client.go`:

```go
func (c *Client) MetaSummary(ctx context.Context, repoName, commit, filePath string) ([]byte, int, error) {
	return c.do(ctx, http.MethodGet, "/api/v1/repos/"+repoName+"/meta/"+commit+"/"+filePath+"/summary", nil)
}
```

Run: `go test ./modules/dit/... -run TestMetaSummary` — expect pass.

---

## Task 4 — Go client: MetaDiff (TDD)

MetaDiff forwards a `file` query parameter from the incoming request to dit-core.

### Step 4A — Write the failing test

Append to `modules/dit/client_test.go`:

```go
func TestMetaDiff(t *testing.T) {
	client := newTestClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		assert.Equal(t, http.MethodGet, r.Method)
		assert.Equal(t, "/api/v1/repos/myrepo/meta/diff/old123/new456", r.URL.Path)
		assert.Equal(t, "train.jsonl", r.URL.Query().Get("file"))
		assert.Equal(t, "Bearer test-token", r.Header.Get("Authorization"))
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"files":[{"path":"train.jsonl","delta":{"row_count":120}}]}`))
	}))
	data, status, err := client.MetaDiff(context.Background(), "myrepo", "old123", "new456", "train.jsonl")
	require.NoError(t, err)
	assert.Equal(t, http.StatusOK, status)
	assert.Contains(t, string(data), "files")
}
```

Also add a test for the empty-file case (no query param):

```go
func TestMetaDiffNoFile(t *testing.T) {
	client := newTestClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		assert.Equal(t, "", r.URL.Query().Get("file"))
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"files":[]}`))
	}))
	data, status, err := client.MetaDiff(context.Background(), "myrepo", "old123", "new456", "")
	require.NoError(t, err)
	assert.Equal(t, http.StatusOK, status)
	assert.Contains(t, string(data), "files")
}
```

Run: `go test ./modules/dit/... -run TestMetaDiff` — expect compile error.

### Step 4B — Implement MetaDiff

The `file` query parameter is optional. Build the URL with `net/url` to avoid manual escaping.

Append to `modules/dit/client.go`:

```go
func (c *Client) MetaDiff(ctx context.Context, repoName, oldCommit, newCommit, filePath string) ([]byte, int, error) {
	path := "/api/v1/repos/" + repoName + "/meta/diff/" + oldCommit + "/" + newCommit
	if filePath != "" {
		path += "?file=" + url.QueryEscape(filePath)
	}
	return c.do(ctx, http.MethodGet, path, nil)
}
```

Update the import block at the top of `modules/dit/client.go` to include `"net/url"` in the stdlib section:

```go
import (
    "bytes"
    "context"
    "fmt"
    "io"
    "net/http"
    "net/url"
    "strings"
    "sync"

    "forgejo.org/modules/setting"
)
```

Run: `go test ./modules/dit/... -run TestMetaDiff` — expect both tests pass.

Verify the module compiles cleanly: `cd ~/code/datahub-gateway && go build ./modules/dit/...`

Run full client test suite: `go test ./modules/dit/...` — all existing tests must still pass.

---

## Task 5 — Go handlers: DatahubMetaCompute and DatahubMetaGet

**File:** `routers/api/v1/repo/dit.go`

Append to the existing file (after `DatahubGetManifest`):

```go
func DatahubMetaCompute(ctx *context.APIContext) {
	body, ok := readBody(ctx)
	if !ok {
		return
	}
	proxyToDatahub(ctx, func() ([]byte, int, error) {
		return dit.DefaultClient().MetaCompute(ctx, ctx.Repo.Repository.Name, body)
	})
}

func DatahubMetaGet(ctx *context.APIContext) {
	proxyToDatahub(ctx, func() ([]byte, int, error) {
		return dit.DefaultClient().MetaGet(
			ctx,
			ctx.Repo.Repository.Name,
			ctx.Params(":commit"),
			ctx.Params(":path"),
		)
	})
}
```

No new imports needed — `dit`, `context`, and `http` are already imported.

---

## Task 6 — Go handlers: DatahubMetaSummary and DatahubMetaDiff

Append to `routers/api/v1/repo/dit.go`:

```go
func DatahubMetaSummary(ctx *context.APIContext) {
	proxyToDatahub(ctx, func() ([]byte, int, error) {
		return dit.DefaultClient().MetaSummary(
			ctx,
			ctx.Repo.Repository.Name,
			ctx.Params(":commit"),
			ctx.Params(":path"),
		)
	})
}

func DatahubMetaDiff(ctx *context.APIContext) {
	proxyToDatahub(ctx, func() ([]byte, int, error) {
		return dit.DefaultClient().MetaDiff(
			ctx,
			ctx.Repo.Repository.Name,
			ctx.Params(":old"),
			ctx.Params(":new"),
			ctx.FormString("file"),
		)
	})
}
```

`ctx.FormString("file")` reads the `?file=` query parameter. It returns `""` if absent, which is the correct empty-string sentinel for `MetaDiff`.

Verify compilation: `go build ./routers/api/v1/repo/...`

---

## Task 7 — Route registration

**File:** `routers/api/v1/api.go`

Find the existing dit group (search for `m.Group("/dit"`). The current block ends with:

```go
m.Get("/manifest/{hash}", repo.DatahubGetManifest)
```

Add the 4 new routes immediately after that line, before the closing `}` of the dit group:

```go
				m.Post("/meta/compute", repo.DatahubMetaCompute)
				m.Get("/meta/diff/{old}/{new}", repo.DatahubMetaDiff)
				m.Get("/meta/{commit}/{path}", repo.DatahubMetaGet)
				m.Get("/meta/{commit}/{path}/summary", repo.DatahubMetaSummary)
```

**Route ordering note:** `meta/diff/{old}/{new}` must appear before `meta/{commit}/{path}` to avoid the diff path being swallowed by the generic `{commit}/{path}` pattern. The router matches in declaration order.

After editing, verify: `go build ./routers/...`

Run all dit-related tests: `go test ./modules/dit/... ./routers/...`

---

## Task 8 — DataRepoHome.vue: fetch sidecar summary and show Tokens/Lang columns

**File:** `web_src/js/components/DataRepoHome.vue`

### What changes

1. Add `sidecars` map to component data (keyed by filename, value is summary JSON or `null` meaning not-yet-computed)
2. In `loadTree()`, after resolving tree entries, fetch summary for each manifest entry using the commit hash
3. Add `Tokens` and `Lang` columns to the table header
4. In each table row, show token estimate and language from `sidecars[entry.name]`, or show `—` + a "Compute" button if `null`
5. Add `computeMeta(entry)` method that calls the compute endpoint and refreshes

### Step 8A — Data and helpers

In the `data()` return object, add:
```javascript
sidecars: {},
computingMeta: {},
```

Add a helper to `methods`:
```javascript
formatTokens(n) {
  if (!n && n !== 0) return '—';
  if (n >= 1000000) return `~${(n / 1000000).toFixed(2)}M`;
  if (n >= 1000) return `~${(n / 1000).toFixed(0)}K`;
  return String(n);
},
formatLang(dist) {
  if (!dist || Object.keys(dist).length === 0) return '—';
  const top = Object.entries(dist).sort((a, b) => b[1] - a[1])[0];
  return `${top[0]} ${Math.round(top[1] * 100)}%`;
},
```

### Step 8B — Fetch sidecars in loadTree

Replace the `loadTree` method with:

```javascript
async loadTree() {
  const ref = await ditFetch(this.owner, this.repo, `/refs/${this.currentBranch}`);
  const commitHash = ref.target_hash;
  this.tree = await ditFetch(this.owner, this.repo, `/tree/${commitHash}`);
  let totalRows = 0;
  let fileCount = 0;
  const sidecars = {};
  for (const entry of this.tree.entries || []) {
    if (entry.type === 'manifest') {
      fileCount++;
      totalRows += entry.row_count || 0;
      // Fetch sidecar summary; null means not yet computed
      try {
        const summary = await ditFetch(
          this.owner, this.repo,
          `/meta/${commitHash}/${encodeURIComponent(entry.name)}/summary`,
        );
        sidecars[entry.name] = summary;
      } catch {
        sidecars[entry.name] = null;
      }
    }
  }
  this.sidecars = sidecars;
  this.stats = {fileCount, rowCount: totalRows};
},
```

### Step 8C — Add computeMeta method

```javascript
async computeMeta(entry) {
  this.computingMeta = {...this.computingMeta, [entry.name]: true};
  try {
    await ditFetch(this.owner, this.repo, '/meta/compute', {
      method: 'POST',
      body: JSON.stringify({file: entry.name}),
    });
    // Reload tree to pick up new commit with sidecar_hash
    await this.loadTree();
  } catch (e) {
    // Silently ignore; UI shows — still
  } finally {
    const next = {...this.computingMeta};
    delete next[entry.name];
    this.computingMeta = next;
  }
},
```

### Step 8D — Update the table template

Replace the `<table>` block in the template with:

```html
<table class="ui very basic table">
  <thead>
    <tr>
      <th>Name</th>
      <th class="right aligned">Rows</th>
      <th class="right aligned">Size</th>
      <th class="right aligned">Tokens</th>
      <th class="right aligned">Lang</th>
    </tr>
  </thead>
  <tbody>
    <tr v-for="entry in tree.entries" :key="entry.name">
      <td>
        <i :class="entry.type === 'tree' ? 'folder icon' : 'file outline icon'"></i>
        <a v-if="entry.type === 'manifest'" href="#" @click.prevent="selectFile(entry)">{{ entry.name }}</a>
        <span v-else>{{ entry.name }}</span>
      </td>
      <td class="right aligned">{{ entry.row_count || '—' }}</td>
      <td class="right aligned">{{ formatSize(entry.size) }}</td>
      <td class="right aligned">
        <template v-if="entry.type === 'manifest'">
          <span v-if="sidecars[entry.name]">
            {{ formatTokens(sidecars[entry.name].token_estimate) }}
          </span>
          <span v-else-if="sidecars[entry.name] === null">
            <span>—</span>
            <button
              class="ui mini basic button"
              :class="{loading: computingMeta[entry.name]}"
              :disabled="computingMeta[entry.name]"
              @click="computeMeta(entry)"
            >Compute</button>
          </span>
        </template>
        <span v-else>—</span>
      </td>
      <td class="right aligned">
        <template v-if="entry.type === 'manifest' && sidecars[entry.name]">
          {{ formatLang(sidecars[entry.name].lang_distribution) }}
        </template>
        <span v-else>—</span>
      </td>
    </tr>
  </tbody>
</table>
```

**Rationale for `null` vs `undefined`:** The `sidecars` map uses `null` to mean "fetch returned 404 / not computed". Before the tree loads, a key is absent (which renders as `—` with no button). After load, `null` means "show Compute button".

---

## Task 9 — DataDiffView.vue: fetch meta diff and show delta header

**File:** `web_src/js/components/DataDiffView.vue`

### What changes

1. Add `metaDiff` to component data (array of per-file delta objects, or `null` if not available)
2. After loading the diff in `mounted()`, attempt to fetch `/meta/diff/{oldCommit}/{newCommit}`; if it fails (sidecars not computed), silently set `metaDiff = null`
3. Add a delta header above the file sidebar showing per-file token/row deltas

### Step 9A — Add metaDiff to data

```javascript
data() {
  return {
    files: [],
    activeFile: null,
    activeChanges: null,
    loading: false,
    metaDiff: null,
  };
},
```

### Step 9B — Fetch meta diff in mounted

Replace `mounted()` with:

```javascript
async mounted() {
  const diff = await ditFetch(this.owner, this.repo, `/diff/${this.oldCommit}/${this.newCommit}`);
  this.files = diff.files || [];
  if (this.files.length > 0) {
    this.activeFile = this.files[0].path;
    this.activeChanges = this.files[0].changes || [];
  }
  // Fetch meta diff; non-fatal if sidecars not computed
  try {
    const meta = await ditFetch(
      this.owner, this.repo,
      `/meta/diff/${this.oldCommit}/${this.newCommit}`,
    );
    this.metaDiff = meta.files || [];
  } catch {
    this.metaDiff = null;
  }
},
```

### Step 9C — Add computed helper for delta display

Add to `computed`:

```javascript
metaDeltaByPath() {
  if (!this.metaDiff) return {};
  const map = {};
  for (const f of this.metaDiff) {
    map[f.path] = f.delta || {};
  }
  return map;
},
```

Add to `methods`:

```javascript
formatDelta(delta) {
  if (!delta) return null;
  const parts = [];
  if (delta.row_count != null) {
    const sign = delta.row_count >= 0 ? '+' : '';
    parts.push(`${sign}${delta.row_count} rows`);
  }
  if (delta.token_estimate != null) {
    const sign = delta.token_estimate >= 0 ? '+' : '';
    const abs = Math.abs(delta.token_estimate);
    const fmt = abs >= 1000 ? `${sign}${Math.round(delta.token_estimate / 1000)}K` : `${sign}${delta.token_estimate}`;
    parts.push(`${fmt} tokens`);
  }
  return parts.length ? parts.join(', ') : null;
},
```

### Step 9D — Add delta header to template

Add the following block immediately before `<!-- File sidebar -->` in the template:

```html
<!-- Metadata delta header -->
<div class="sixteen wide column" v-if="metaDiff && metaDiff.length">
  <div class="ui info message">
    <div class="ui list">
      <div class="item" v-for="f in metaDiff" :key="f.path">
        <strong>{{ f.path }}</strong>:
        <span v-if="formatDelta(f.delta)">{{ formatDelta(f.delta) }}</span>
        <span v-else class="dimmed">no metadata change</span>
      </div>
    </div>
  </div>
</div>
```

Wrap the existing sidebar + content in a `<div class="ui grid">` if not already wrapped (it is — the existing template root is `<div class="ui grid">`).

---

## Task 10 — Final build verification

Run from `~/code/datahub-gateway/`:

```bash
# Go: full build + all tests
go build ./...
go test ./modules/dit/... ./routers/...

# Frontend: lint + build (adjust command to match project's npm scripts)
npm run lint --prefix web_src
npm run build --prefix web_src
```

Confirm:
- All 4 new client methods compile and their tests pass
- All pre-existing client tests still pass (`TestListRefs`, `TestGetManifest`, etc.)
- `go build ./routers/...` succeeds (handlers and routes compile)
- No Vue/JS lint errors in the two modified components

---

## Summary of all changed files

| File | Change |
|------|--------|
| `modules/dit/client.go` | Add `MetaCompute`, `MetaGet`, `MetaSummary`, `MetaDiff` methods; add `net/url` import |
| `modules/dit/client_test.go` | Add `TestMetaCompute`, `TestMetaGet`, `TestMetaSummary`, `TestMetaDiff`, `TestMetaDiffNoFile` |
| `routers/api/v1/repo/dit.go` | Add `DatahubMetaCompute`, `DatahubMetaGet`, `DatahubMetaSummary`, `DatahubMetaDiff` handlers |
| `routers/api/v1/api.go` | Add 4 routes to dit group (ordered: diff before generic path) |
| `web_src/js/components/DataRepoHome.vue` | Add `sidecars`/`computingMeta` state, sidecar fetch in `loadTree`, Tokens/Lang columns, Compute button |
| `web_src/js/components/DataDiffView.vue` | Add `metaDiff` state, meta diff fetch in `mounted`, delta header above file list |
