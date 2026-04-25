# Phase 3E: Vue 3 Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans.

**Goal:** Build 4 Vue 3 components for data repository views — dataset home, JSONL viewer, diff view, conflict resolver

**Architecture:** Vue 3 SFC components mounted via Go templates, using Forgejo's existing Fomantic-UI + Tailwind styling

**Tech Stack:** Vue 3, JavaScript, Webpack, Fomantic-UI

**Depends on:** Phase 3D (IsDataRepo field + proxy API routes must exist)

---

## Dependency Order

```
Task 1 (Templates) → Task 2 (Webpack) → Task 3 (API util) → Task 4-8 (components, parallel-safe)
```

---

### Task 1: Template Integration

**Files:**
- Modify: `templates/repo/home.tmpl` (~line 80)
- Modify: `routers/web/repo/view.go` (~line 818)

- [ ] **Step 1: Add IsDataRepo conditional in home.tmpl**

Find the existing file tree block in `templates/repo/home.tmpl` (around line 80) and wrap it:

```html
{{if .Repository.IsDataRepo}}
	<div id="data-repo-home"
		data-owner="{{.Repository.OwnerName}}"
		data-repo="{{.Repository.Name}}"
		data-default-branch="{{.Repository.DefaultBranch}}">
	</div>
{{else}}
	{{/* existing git file tree content — leave unchanged */}}
{{end}}
```

The `data-*` attributes pass context to the Vue app without requiring a separate API call.

- [ ] **Step 2: Add IsDataRepo guard in view.go**

In `routers/web/repo/view.go`, find the `IsEmpty` check (~line 818) and add a data repo check before it:

```go
// Data repos render their own Vue-based home page
if ctx.Repo.Repository.IsDataRepo {
    ctx.HTML(http.StatusOK, "repo/home")
    return
}

// existing IsEmpty check follows...
if ctx.Repo.Repository.IsEmpty {
```

- [ ] **Step 3: Verify template renders**

```bash
cd ~/code/datahub-gateway
make build
# Start dev server, create a data repo, verify the mount div appears in page source
```

- [ ] **Step 4: Commit**

```bash
git add templates/repo/home.tmpl routers/web/repo/view.go
git commit -m "feat: add IsDataRepo template conditional and view.go guard"
```

---

### Task 2: Webpack + Feature Loader

**Files:**
- Create: `web_src/js/features/datahub.js`
- Modify: `webpack.config.js`

- [ ] **Step 1: Create feature loader**

```js
// web_src/js/features/datahub.js
const dataRepoHome = document.getElementById('data-repo-home');
if (dataRepoHome) {
  import('../components/DataRepoHome.vue').then(({default: App}) => {
    const {createApp} = require('vue');
    createApp(App, {
      owner: dataRepoHome.dataset.owner,
      repo: dataRepoHome.dataset.repo,
      defaultBranch: dataRepoHome.dataset.defaultBranch,
    }).mount(dataRepoHome);
  });
}

const diffView = document.getElementById('data-diff-view');
if (diffView) {
  import('../components/DataDiffView.vue').then(({default: App}) => {
    const {createApp} = require('vue');
    createApp(App, {
      owner: diffView.dataset.owner,
      repo: diffView.dataset.repo,
      oldCommit: diffView.dataset.oldCommit,
      newCommit: diffView.dataset.newCommit,
    }).mount(diffView);
  });
}

const conflictResolver = document.getElementById('conflict-resolver');
if (conflictResolver) {
  import('../components/ConflictResolver.vue').then(({default: App}) => {
    const {createApp} = require('vue');
    createApp(App, {
      owner: conflictResolver.dataset.owner,
      repo: conflictResolver.dataset.repo,
      pullId: conflictResolver.dataset.pullId,
    }).mount(conflictResolver);
  });
}
```

- [ ] **Step 2: Register in webpack.config.js**

Find the `entry` object in `webpack.config.js` and add:

```js
entry: {
  // ... existing entries
  datahub: [path.join(__dirname, 'web_src/js/features/datahub.js')],
},
```

- [ ] **Step 3: Add script tag to template**

In `templates/repo/home.tmpl`, inside the `{{if .Repository.IsDataRepo}}` block, add:

```html
<script src="{{AssetUrlPrefix}}/js/datahub.js"></script>
```

- [ ] **Step 4: Verify build**

```bash
npx webpack
# Expected: new chunk at public/assets/js/datahub.*.js
ls public/assets/js/datahub*
```

- [ ] **Step 5: Commit**

```bash
git add web_src/js/features/datahub.js webpack.config.js templates/repo/home.tmpl
git commit -m "feat: add datahub feature loader and webpack entry"
```

---

### Task 3: API Utility

**Files:**
- Create: `web_src/js/utils/datahub-api.js`

- [ ] **Step 1: Create API wrapper**

```js
// web_src/js/utils/datahub-api.js

export async function datahubFetch(owner, repo, path, options = {}) {
  const url = `/api/v1/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/datahub${path}`;
  const resp = await fetch(url, {
    headers: {
      'Content-Type': 'application/json',
      'X-Csrf-Token': document.querySelector('meta[name=_csrf]')?.content || '',
    },
    ...options,
  });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`Datahub API ${resp.status}: ${text}`);
  }
  return resp.json();
}

export async function datahubFetchRaw(owner, repo, path, options = {}) {
  const url = `/api/v1/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/datahub${path}`;
  const resp = await fetch(url, {
    headers: {
      'X-Csrf-Token': document.querySelector('meta[name=_csrf]')?.content || '',
    },
    ...options,
  });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`Datahub API ${resp.status}: ${text}`);
  }
  return resp;
}
```

Note: `datahubFetchRaw` returns the Response object directly — used by JsonlViewer for streaming large objects.

- [ ] **Step 2: Commit**

```bash
git add web_src/js/utils/datahub-api.js
git commit -m "feat: add datahub API fetch utility"
```

---

### Task 4: DataRepoHome.vue

**Files:**
- Create: `web_src/js/components/DataRepoHome.vue`

- [ ] **Step 1: Create component**

```vue
<template>
  <div class="ui segments">
    <!-- Branch selector -->
    <div class="ui segment">
      <div class="ui inline fields">
        <div class="field">
          <select class="ui dropdown" v-model="currentBranch" @change="onBranchChange">
            <option v-for="ref in refs" :key="ref.name" :value="ref.name">
              {{ ref.name.replace('heads/', '') }}
            </option>
          </select>
        </div>
        <div class="field" v-if="stats">
          <span class="ui label">{{ stats.fileCount }} files</span>
          <span class="ui label">{{ stats.rowCount }} rows</span>
        </div>
      </div>
    </div>

    <!-- Loading -->
    <div class="ui segment" v-if="loading">
      <div class="ui active centered inline loader"></div>
    </div>

    <!-- Error -->
    <div class="ui segment" v-else-if="error">
      <div class="ui negative message">
        <p>{{ error }}</p>
      </div>
    </div>

    <!-- File tree -->
    <div class="ui segment" v-else-if="tree">
      <table class="ui very basic table">
        <thead>
          <tr>
            <th>Name</th>
            <th class="right aligned">Rows</th>
            <th class="right aligned">Size</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="entry in tree.entries" :key="entry.name">
            <td>
              <i :class="entry.type === 'tree' ? 'folder icon' : 'file outline icon'"></i>
              <a v-if="entry.type === 'manifest'" :href="viewerUrl(entry)">{{ entry.name }}</a>
              <span v-else>{{ entry.name }}</span>
            </td>
            <td class="right aligned">{{ entry.row_count || '—' }}</td>
            <td class="right aligned">{{ formatSize(entry.size) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script>
import {datahubFetch} from '../utils/datahub-api.js';

export default {
  props: {
    owner: String,
    repo: String,
    defaultBranch: String,
  },
  data() {
    return {
      refs: [],
      currentBranch: '',
      tree: null,
      stats: null,
      loading: true,
      error: null,
    };
  },
  async mounted() {
    try {
      const refsData = await datahubFetch(this.owner, this.repo, '/refs');
      this.refs = refsData.filter((r) => r.name.startsWith('heads/'));
      this.currentBranch = this.refs.find((r) => r.name === `heads/${this.defaultBranch}`)?.name || this.refs[0]?.name || '';
      if (this.currentBranch) await this.loadTree();
    } catch (e) {
      this.error = e.message;
    } finally {
      this.loading = false;
    }
  },
  methods: {
    async onBranchChange() {
      this.loading = true;
      this.error = null;
      try {
        await this.loadTree();
      } catch (e) {
        this.error = e.message;
      } finally {
        this.loading = false;
      }
    },
    async loadTree() {
      const ref = await datahubFetch(this.owner, this.repo, `/refs/${this.currentBranch}`);
      this.tree = await datahubFetch(this.owner, this.repo, `/tree/${ref.target_hash}`);
      let totalRows = 0;
      let fileCount = 0;
      for (const entry of this.tree.entries || []) {
        if (entry.type === 'manifest') {
          fileCount++;
          totalRows += entry.row_count || 0;
        }
      }
      this.stats = {fileCount, rowCount: totalRows};
    },
    viewerUrl(entry) {
      const branch = this.currentBranch.replace('heads/', '');
      return `/${this.owner}/${this.repo}/src/branch/${branch}/${entry.name}`;
    },
    formatSize(bytes) {
      if (!bytes) return '—';
      if (bytes < 1024) return `${bytes} B`;
      if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`;
      return `${(bytes / 1048576).toFixed(1)} MB`;
    },
  },
};
</script>
```

- [ ] **Step 2: Verify build**

```bash
npx webpack
# Should compile without errors
```

- [ ] **Step 3: Commit**

```bash
git add web_src/js/components/DataRepoHome.vue
git commit -m "feat: add DataRepoHome Vue component"
```

---

### Task 5: JsonlViewer.vue

**Files:**
- Create: `web_src/js/components/JsonlViewer.vue`

- [ ] **Step 1: Create component**

```vue
<template>
  <div class="ui segment">
    <!-- Header -->
    <div class="ui top attached header">
      <span>{{ filePath }}</span>
      <span class="ui label" v-if="totalRows">{{ totalRows }} rows</span>
    </div>

    <!-- Table -->
    <div class="datahub-jsonl-table" ref="scrollContainer" @scroll="onScroll">
      <table class="ui very basic compact table">
        <thead>
          <tr>
            <th class="collapsing">#</th>
            <th v-for="col in columns" :key="col" :style="{minWidth: '150px'}">{{ col }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(row, idx) in visibleRows" :key="startIndex + idx">
            <td class="collapsing">{{ startIndex + idx + 1 }}</td>
            <td v-for="col in columns" :key="col" @click="toggleExpand(startIndex + idx, col)">
              <div :class="{'datahub-cell-truncated': !isExpanded(startIndex + idx, col)}">
                {{ formatCell(row[col]) }}
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Pagination -->
    <div class="ui bottom attached segment" v-if="totalPages > 1">
      <div class="ui pagination menu">
        <a class="item" :class="{disabled: currentPage <= 1}" @click="goPage(currentPage - 1)">Prev</a>
        <div class="item">Page {{ currentPage }} / {{ totalPages }}</div>
        <a class="item" :class="{disabled: currentPage >= totalPages}" @click="goPage(currentPage + 1)">Next</a>
      </div>
    </div>
  </div>
</template>

<script>
import {datahubFetch} from '../utils/datahub-api.js';

const PAGE_SIZE = 50;

export default {
  props: {
    owner: String,
    repo: String,
    manifestHash: String,
    filePath: String,
  },
  data() {
    return {
      rows: [],
      columns: [],
      totalRows: 0,
      currentPage: 1,
      totalPages: 1,
      expandedCells: new Set(),
      startIndex: 0,
    };
  },
  computed: {
    visibleRows() {
      const start = (this.currentPage - 1) * PAGE_SIZE;
      return this.rows.slice(start, start + PAGE_SIZE);
    },
  },
  async mounted() {
    await this.loadManifest();
  },
  methods: {
    async loadManifest() {
      const manifest = await datahubFetch(this.owner, this.repo, `/manifest/${this.manifestHash}`);
      this.totalRows = manifest.row_count || 0;
      this.totalPages = Math.ceil(this.totalRows / PAGE_SIZE);
      if (manifest.chunks && manifest.chunks.length > 0) {
        await this.loadChunk(manifest.chunks[0]);
      }
    },
    async loadChunk(chunkHash) {
      const data = await datahubFetch(this.owner, this.repo, `/objects/${chunkHash}`);
      if (typeof data === 'string') {
        this.rows = data.split('\n').filter(Boolean).map((line) => JSON.parse(line));
      } else if (Array.isArray(data)) {
        this.rows = data;
      }
      if (this.rows.length > 0) {
        this.columns = Object.keys(this.rows[0]);
      }
    },
    formatCell(value) {
      if (value === null || value === undefined) return '—';
      if (typeof value === 'object') return JSON.stringify(value).slice(0, 200);
      return String(value).slice(0, 200);
    },
    toggleExpand(rowIdx, col) {
      const key = `${rowIdx}:${col}`;
      if (this.expandedCells.has(key)) {
        this.expandedCells.delete(key);
      } else {
        this.expandedCells.add(key);
      }
    },
    isExpanded(rowIdx, col) {
      return this.expandedCells.has(`${rowIdx}:${col}`);
    },
    goPage(page) {
      if (page < 1 || page > this.totalPages) return;
      this.currentPage = page;
      this.startIndex = (page - 1) * PAGE_SIZE;
    },
    onScroll() {
      // placeholder for virtual scroll enhancement
    },
  },
};
</script>

<style scoped>
.datahub-jsonl-table {
  max-height: 600px;
  overflow: auto;
}
.datahub-cell-truncated {
  max-width: 300px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  cursor: pointer;
}
</style>
```

- [ ] **Step 2: Commit**

```bash
git add web_src/js/components/JsonlViewer.vue
git commit -m "feat: add JsonlViewer Vue component"
```

---

### Task 6: DataDiffView.vue

**Files:**
- Create: `web_src/js/components/DataDiffView.vue`

- [ ] **Step 1: Create component**

```vue
<template>
  <div class="ui grid">
    <!-- File sidebar -->
    <div class="four wide column">
      <div class="ui segment">
        <div class="ui list">
          <a class="item" v-for="file in files" :key="file.path"
             :class="{active: file.path === activeFile}"
             @click="activeFile = file.path">
            <span>{{ file.path }}</span>
            <div class="ui mini labels">
              <span class="ui green label" v-if="file.added">+{{ file.added }}</span>
              <span class="ui red label" v-if="file.removed">-{{ file.removed }}</span>
              <span class="ui yellow label" v-if="file.refreshed">~{{ file.refreshed }}</span>
            </div>
          </a>
        </div>
      </div>
    </div>

    <!-- Diff content -->
    <div class="twelve wide column">
      <div class="ui segment" v-if="loading">
        <div class="ui active centered inline loader"></div>
      </div>

      <div class="ui segment" v-else-if="activeChanges">
        <!-- Added rows -->
        <div v-if="addedRows.length" class="datahub-diff-section">
          <h4 class="ui header">Added ({{ addedRows.length }})</h4>
          <table class="ui very basic table">
            <tr v-for="row in addedRows" :key="row.row_hash" class="positive">
              <td class="collapsing">{{ row.row_hash?.slice(0, 8) }}</td>
              <td><pre class="datahub-diff-content">{{ formatRow(row.row_content) }}</pre></td>
            </tr>
          </table>
        </div>

        <!-- Removed rows -->
        <div v-if="removedRows.length" class="datahub-diff-section">
          <h4 class="ui header">Removed ({{ removedRows.length }})</h4>
          <table class="ui very basic table">
            <tr v-for="row in removedRows" :key="row.row_hash" class="negative">
              <td class="collapsing">{{ row.row_hash?.slice(0, 8) }}</td>
              <td><pre class="datahub-diff-content">{{ formatRow(row.row_content) }}</pre></td>
            </tr>
          </table>
        </div>

        <!-- Refreshed rows -->
        <div v-if="refreshedRows.length" class="datahub-diff-section">
          <h4 class="ui header">Refreshed ({{ refreshedRows.length }})</h4>
          <table class="ui very basic table">
            <tr v-for="row in refreshedRows" :key="row.new_row_hash" class="warning">
              <td class="collapsing">{{ row.new_row_hash?.slice(0, 8) }}</td>
              <td>
                <div class="datahub-diff-side negative"><pre>{{ formatRow(row.old_content) }}</pre></div>
                <div class="datahub-diff-side positive"><pre>{{ formatRow(row.new_content) }}</pre></div>
              </td>
            </tr>
          </table>
        </div>
      </div>
    </div>
  </div>
</template>

<script>
import {datahubFetch} from '../utils/datahub-api.js';

export default {
  props: {
    owner: String,
    repo: String,
    oldCommit: String,
    newCommit: String,
  },
  data() {
    return {
      files: [],
      activeFile: null,
      activeChanges: null,
      loading: false,
    };
  },
  computed: {
    addedRows() {
      return (this.activeChanges || []).filter((c) => c.type === 'added');
    },
    removedRows() {
      return (this.activeChanges || []).filter((c) => c.type === 'removed');
    },
    refreshedRows() {
      return (this.activeChanges || []).filter((c) => c.type === 'refreshed');
    },
  },
  async mounted() {
    const diff = await datahubFetch(this.owner, this.repo, `/diff/${this.oldCommit}/${this.newCommit}`);
    this.files = diff.files || [];
    if (this.files.length > 0) {
      this.activeFile = this.files[0].path;
      this.activeChanges = this.files[0].changes || [];
    }
  },
  watch: {
    activeFile(newPath) {
      const file = this.files.find((f) => f.path === newPath);
      this.activeChanges = file?.changes || [];
    },
  },
  methods: {
    formatRow(content) {
      if (!content) return '';
      return JSON.stringify(content, null, 2);
    },
  },
};
</script>

<style scoped>
.datahub-diff-section {
  margin-bottom: 1em;
}
.datahub-diff-content {
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 200px;
  overflow: auto;
  font-size: 12px;
  margin: 0;
}
.datahub-diff-side {
  padding: 4px 8px;
  margin: 2px 0;
  border-radius: 3px;
}
.datahub-diff-side.negative {
  background-color: var(--color-diff-removed-row-bg, #ffeef0);
}
.datahub-diff-side.positive {
  background-color: var(--color-diff-added-row-bg, #e6ffec);
}
</style>
```

- [ ] **Step 2: Commit**

```bash
git add web_src/js/components/DataDiffView.vue
git commit -m "feat: add DataDiffView Vue component"
```

---

### Task 7: ConflictResolver.vue

**Files:**
- Create: `web_src/js/components/ConflictResolver.vue`

- [ ] **Step 1: Create component**

```vue
<template>
  <div class="ui grid">
    <!-- Conflict file sidebar -->
    <div class="four wide column">
      <div class="ui segment">
        <h4 class="ui header">Conflict Files</h4>
        <div class="ui list">
          <a class="item" v-for="file in conflictFiles" :key="file"
             :class="{active: file === activeFile}" @click="activeFile = file">
            <i class="warning sign icon"></i> {{ file }}
          </a>
        </div>
      </div>
      <div class="ui segment">
        <div class="ui small statistic">
          <div class="value">{{ resolvedCount }} / {{ totalConflicts }}</div>
          <div class="label">Resolved</div>
        </div>
      </div>
    </div>

    <!-- Conflict rows -->
    <div class="twelve wide column">
      <div class="ui segment" v-for="conflict in activeConflicts" :key="conflict.row_hash">
        <div class="ui two column grid">
          <div class="column">
            <h5 class="ui header">Source</h5>
            <pre class="datahub-conflict-content">{{ formatRow(conflict.source) }}</pre>
          </div>
          <div class="column">
            <h5 class="ui header">Target</h5>
            <pre class="datahub-conflict-content">{{ formatRow(conflict.target) }}</pre>
          </div>
        </div>
        <div class="ui buttons" style="margin-top: 8px;">
          <button class="ui button" :class="{green: getResolution(conflict.row_hash) === 'source'}"
                  @click="resolve(conflict.row_hash, 'source')">Keep Source</button>
          <button class="ui button" :class="{blue: getResolution(conflict.row_hash) === 'target'}"
                  @click="resolve(conflict.row_hash, 'target')">Keep Target</button>
        </div>
      </div>

      <!-- Submit -->
      <div class="ui segment" v-if="totalConflicts > 0">
        <button class="ui primary button" :disabled="resolvedCount < totalConflicts || submitting"
                :class="{loading: submitting}" @click="submitResolutions">
          Submit Resolution ({{ resolvedCount }}/{{ totalConflicts }})
        </button>
      </div>
    </div>
  </div>
</template>

<script>
import {datahubFetch} from '../utils/datahub-api.js';

export default {
  props: {
    owner: String,
    repo: String,
    pullId: String,
  },
  data() {
    return {
      conflictFiles: [],
      conflicts: {},
      resolutions: {},
      activeFile: null,
      submitting: false,
    };
  },
  computed: {
    activeConflicts() {
      return this.conflicts[this.activeFile] || [];
    },
    totalConflicts() {
      let count = 0;
      for (const file of Object.values(this.conflicts)) count += file.length;
      return count;
    },
    resolvedCount() {
      return Object.keys(this.resolutions).length;
    },
  },
  async mounted() {
    const pr = await datahubFetch(this.owner, this.repo, `/pulls/${this.pullId}`);
    if (pr.conflict_files) {
      this.conflictFiles = JSON.parse(pr.conflict_files);
      // Load conflicts for each file from diff endpoint
      for (const file of this.conflictFiles) {
        const diff = await datahubFetch(this.owner, this.repo,
          `/diff/${pr.target_commit}/${pr.source_commit}`);
        const fileData = diff.files?.find((f) => f.path === file);
        if (fileData) this.conflicts[file] = fileData.changes?.filter((c) => c.conflict) || [];
      }
      if (this.conflictFiles.length > 0) this.activeFile = this.conflictFiles[0];
    }
  },
  methods: {
    resolve(rowHash, choice) {
      this.resolutions[rowHash] = choice;
    },
    getResolution(rowHash) {
      return this.resolutions[rowHash] || null;
    },
    formatRow(content) {
      if (!content) return '';
      return JSON.stringify(content, null, 2);
    },
    async submitResolutions() {
      this.submitting = true;
      try {
        await datahubFetch(this.owner, this.repo, `/pulls/${this.pullId}/merge`, {
          method: 'POST',
          body: JSON.stringify({resolutions: this.resolutions}),
        });
        window.location.reload();
      } catch (e) {
        alert(`Resolution failed: ${e.message}`);
      } finally {
        this.submitting = false;
      }
    },
  },
};
</script>

<style scoped>
.datahub-conflict-content {
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 300px;
  overflow: auto;
  font-size: 12px;
  background: var(--color-body, #f8f8f8);
  padding: 8px;
  border-radius: 4px;
  margin: 0;
}
</style>
```

- [ ] **Step 2: Commit**

```bash
git add web_src/js/components/ConflictResolver.vue
git commit -m "feat: add ConflictResolver Vue component"
```

---

### Task 8: Virtual Scroll Utility

**Files:**
- Create: `web_src/js/utils/virtual-scroll.js`

- [ ] **Step 1: Create utility**

```js
// web_src/js/utils/virtual-scroll.js

/**
 * Virtual scroll state manager for large tables.
 * Returns reactive state: { visibleItems, containerStyle, onScroll }
 *
 * Usage:
 *   const vs = createVirtualScroll({ items, itemHeight: 36, containerHeight: 600 });
 *   // In template: <div :style="vs.containerStyle" @scroll="vs.onScroll">
 *   //   <tr v-for="item in vs.visibleItems">
 */
export function createVirtualScroll({items, itemHeight = 36, containerHeight = 600, overscan = 5}) {
  const state = {
    scrollTop: 0,
    get startIndex() {
      return Math.max(0, Math.floor(state.scrollTop / itemHeight) - overscan);
    },
    get endIndex() {
      const visible = Math.ceil(containerHeight / itemHeight);
      return Math.min(items.length, state.startIndex + visible + overscan * 2);
    },
    get visibleItems() {
      return items.slice(state.startIndex, state.endIndex);
    },
    get containerStyle() {
      return {
        height: `${items.length * itemHeight}px`,
        position: 'relative',
      };
    },
    get offsetStyle() {
      return {
        transform: `translateY(${state.startIndex * itemHeight}px)`,
      };
    },
    onScroll(event) {
      state.scrollTop = event.target.scrollTop;
    },
  };
  return state;
}
```

- [ ] **Step 2: Commit**

```bash
git add web_src/js/utils/virtual-scroll.js
git commit -m "feat: add virtual scroll utility for large tables"
```

---

## Key Constraints

| Constraint | Details |
|------------|---------|
| No `ReferencesGitRepo` | Data repo pages must never trigger git repo opening |
| `IsEmpty = true` always | Data repos stay "empty" from git perspective |
| Fomantic-UI classes | Use `ui segment`, `ui table`, `ui button` etc. — no custom design system |
| CSRF token | All API calls must include `X-Csrf-Token` header (from meta tag) |
| Dynamic imports | Components loaded via dynamic `import()` to keep main bundle small |
