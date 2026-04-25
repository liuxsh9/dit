# Phase 3E: Vue 3 Frontend — Data Repo Components

> **Parent spec:** 2026-04-24-phase3-web-ui-design.md  
> **Date:** 2026-04-24  
> **Depends on:** Phase 3D (IsDataRepo field + proxy API routes)

---

## Overview

Four new Vue 3 components that replace Forgejo's git-oriented views for data repositories. All components inherit Forgejo's existing Fomantic-UI + Tailwind styling — no new design system.

---

## 1. Template Integration

### 1.1 `templates/repo/home.tmpl`

Add before the existing file tree block (line ~80):

```html
{{if .Repository.IsDataRepo}}
    <div id="data-repo-home"></div>
{{else}}
    {{/* existing git file tree — unchanged */}}
{{end}}
```

### 1.2 IsEmpty handling

Data repos are created with `IsEmpty = true` (from git's perspective). This is critical:

- `services/context/repo.go` `RepoRef` middleware (line ~710) returns early for empty repos → prevents opening nonexistent git directory
- `routers/web/repo/view.go` (line ~818) has an `IsEmpty` branch → add `IsDataRepo` check **before** this branch to render the data repo template instead of the "empty repo" landing page

The flow:

```
view.go handler
  → IsDataRepo? → render data-repo-home template (mount Vue app)
  → IsEmpty? → render empty-repo landing page
  → else → render git file tree
```

### 1.3 Webpack registration

1. Create feature loader: `web_src/js/features/datahub.js`
2. Register as entry point in `webpack.config.js`
3. Feature loader mounts Vue apps on DOM elements rendered by templates

```js
// web_src/js/features/datahub.js
import {createApp} from 'vue';

const dataRepoHome = document.getElementById('data-repo-home');
if (dataRepoHome) {
  import('./components/DataRepoHome.vue').then(({default: DataRepoHome}) => {
    createApp(DataRepoHome).mount(dataRepoHome);
  });
}
```

---

## 2. Components

All in `web_src/js/components/`.

### 2.1 DataRepoHome.vue

Replaces the git file browser for data repos. Main landing page.

**Layout:**
- Top bar: branch/ref selector dropdown (fetches from `GET /datahub/refs`)
- Stats section: row count, file count, total size (from manifest)
- File listing table from tree endpoint with clickable navigation
- Each file row shows: path, row count, size

**API calls:**
- `GET /api/v1/repos/{owner}/{repo}/datahub/refs` — list branches
- `GET /api/v1/repos/{owner}/{repo}/datahub/refs/heads/{branch}` — resolve ref to commit hash
- `GET /api/v1/repos/{owner}/{repo}/datahub/tree/{commit_hash}` — get file tree
- `GET /api/v1/repos/{owner}/{repo}/datahub/manifest/{hash}` — get manifest for stats

**Interactions:**
- Branch selector changes → re-fetch ref → re-fetch tree
- Click file → navigate to JSONL viewer for that file
- Click "Pull Requests" tab → navigate to data PR list

### 2.2 JsonlViewer.vue

Tabular view of JSONL data files.

**Layout:**
- Header: file path breadcrumb + row count
- Table: auto-detected columns from first N rows
- Virtual scrolling for large files (only render visible rows)
- Pagination controls at bottom

**Features:**
- Columns auto-detected from first batch of rows
- Cell expansion: click to expand long values (conversations, code blocks)
- Column resize by dragging
- Row numbers in leftmost column

**API calls:**
- `GET /api/v1/repos/{owner}/{repo}/datahub/manifest/{hash}` — get file chunks
- `GET /api/v1/repos/{owner}/{repo}/datahub/objects/{chunk_hash}` — fetch individual chunks

**Pagination:**
- Manifest contains ordered list of chunk hashes
- Load chunks on demand as user scrolls/pages
- Virtual scroll renders only visible rows (~50 at a time)

### 2.3 DataDiffView.vue

Side-by-side diff view for data pull requests.

**Layout:**
- File navigation sidebar (list of changed files)
- Main area: side-by-side diff table
  - Left: old version (target branch)
  - Right: new version (source branch)
  - Color coding: green = added, red = removed, yellow = refreshed/modified
- Inline comment form (click gutter to add comment)

**Color scheme:** Reuse Forgejo's existing diff CSS variables for consistency.

**API calls:**
- `GET /api/v1/repos/{owner}/{repo}/datahub/diff/{old_commit}/{new_commit}` — get diff summary
- `GET /api/v1/repos/{owner}/{repo}/datahub/objects/{hash}` — fetch row data for display

**Features:**
- Field-level highlighting within changed rows
- File-level navigation: click file in sidebar → scroll to that section
- Expandable context: show surrounding unchanged rows
- Comment thread display (from PR comments API)

### 2.4 ConflictResolver.vue

Interactive merge conflict resolution for data PRs.

**Layout:**
- Conflict list sidebar (files with conflicts)
- Main area: per-row conflict resolution
  - Left panel: source version
  - Right panel: target version
  - Resolution controls per row:
    - "Keep Source" button
    - "Keep Target" button  
    - "Edit" button → inline editor
- Bottom bar: resolution progress + "Submit Resolution" button

**API calls:**
- Uses diff endpoint to identify conflicts
- `POST /api/v1/repos/{owner}/{repo}/datahub/pulls/{id}/merge` — submit resolved version

**State management:**
- Resolution state tracked client-side in a Map<rowHash, resolution>
- Submitted as batch when user clicks "Submit Resolution"
- Unsaved changes warning on navigation

---

## 3. Shared Utilities

### 3.1 `web_src/js/utils/datahub-api.js`

Thin API wrapper:

```js
export async function datahubFetch(owner, repo, path, options = {}) {
  const url = `/api/v1/repos/${owner}/${repo}/datahub${path}`;
  const resp = await fetch(url, {
    headers: {'Content-Type': 'application/json'},
    ...options,
  });
  if (!resp.ok) throw new Error(`${resp.status}: ${await resp.text()}`);
  return resp.json();
}
```

### 3.2 `web_src/js/utils/virtual-scroll.js`

Reusable virtual scroll hook for large tables. Used by both JsonlViewer and DataDiffView.

---

## 4. Style Guidelines

- Use Forgejo's existing Fomantic-UI classes for buttons, dropdowns, tables, segments
- Use Forgejo's Tailwind utility classes for layout (flex, grid, spacing)
- Diff colors: inherit from `.diff-file-box .addition`, `.deletion` CSS classes
- Icons: use Forgejo's SVG icon system (`octiconX` helpers)
- Responsive: tables scroll horizontally on small screens, sidebar collapses

---

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Component framework | Vue 3 (Forgejo's existing) | No new dependencies |
| Styling | Fomantic-UI + Tailwind (existing) | Consistent with Forgejo |
| Virtual scrolling | Custom hook | Avoid heavy table library dependency |
| State management | Component-local + props | No Vuex/Pinia needed for 4 components |
| Chunk loading | On-demand via manifest | Memory-efficient for large datasets |
