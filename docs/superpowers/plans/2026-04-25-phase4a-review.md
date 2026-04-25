# Phase 4A Plans — Review Report

> **Reviewed:** 2026-04-25  
> **Spec:** `docs/superpowers/specs/2026-04-24-phase4a-sidecar-metadata.md`  
> **Plans reviewed:**
> - `docs/superpowers/plans/2026-04-25-phase4a-core.md`
> - `docs/superpowers/plans/2026-04-25-phase4a-server.md`
> - `docs/superpowers/plans/2026-04-25-phase4a-gateway.md`

---

## Summary

All three plans are detailed, TDD-ordered, and internally coherent. The core plan is the strongest — every task has complete code, red-green-refactor structure, and explicit commit steps. The server plan correctly identifies all call-site breakage from the `flatten_tree` 2→3-tuple change and addresses it. The gateway plan follows the existing `proxyToDatahub` pattern faithfully. However, six issues require fixes before execution: two CRITICAL (a runtime-failing call-site not covered by the server plan, and a FastAPI route ordering hazard), and four IMPORTANT gaps (clone's tree traversal only walks one level deep, missing `Repo` import in the server plan, the server test's `tmp_path` fixture assumption, and a missing `net/url` import check in the gateway plan).

---

## Spec Coverage Matrix

| Spec Section | Plan | Task | Status |
|---|---|---|---|
| 1.1 SidecarEntry / Sidecar dataclasses | core | Task 1 | ✅ |
| 1.2 TreeEntry sidecar_hash optional field | core | Task 2 | ✅ |
| 1.3 Object store layout (.datahub/objects/sidecars/) | core | Tasks 1, 6 | ✅ (store.write("sidecars",...) used correctly) |
| 2.1 serialize_sidecar / deserialize_sidecar | core | Task 1 | ✅ |
| 2.2 serialize_tree extension (omit None) | core | Task 3 | ✅ |
| 2.3 deserialize_tree extension (.get) | core | Task 4 | ✅ |
| 2.x Hash stability invariant | core | Task 5 | ✅ |
| 3.1 compute_sidecar function (core/sidecar.py) | core | Tasks 6+7 | ✅ |
| 3.2 detect_lang heuristic | core | Task 6 | ✅ |
| 3.3 Attach sidecar to tree (build_nested_tree 3-tuple) | core | Task 8 | ✅ |
| 4.1 dit meta compute CLI | server | Task 1 | ✅ |
| 4.2 dit meta show CLI | server | Task 2 | ✅ |
| 4.3 dit meta diff CLI | server | Task 3 | ✅ |
| 5.1 POST /meta/compute server endpoint | server | Task 4 | ✅ |
| 5.2 GET /meta/{commit}/{file_path:path} | server | Task 4 | ✅ |
| 5.3 GET /meta/{commit}/{file_path:path}/summary | server | Task 4 | ✅ |
| 5.4 GET /meta/diff/{old}/{new} | server | Task 4 | ✅ |
| 6.1 Gateway routes (4 proxy routes) | gateway | Tasks 5+7 | ✅ |
| 6.2 Gateway client (4 methods) | gateway | Tasks 1–4 | ✅ |
| 6.3 DataRepoHome.vue Tokens/Lang columns | gateway | Task 8 | ✅ |
| 6.4 DataDiffView.vue metadata delta header | gateway | Task 9 | ✅ |
| 7.1 Walker "sidecars" set extension | core | Task 9 | ✅ |
| 7.2 Push order sidecars between manifests and trees | server | Task 5 | ✅ |
| 7.3 Clone / _fetch_objects_since sidecar download | server | Task 6 | ⚠️ (partial — see C-1) |
| 7.4 batch-exists works for "sidecars" unchanged | server | Task 5 | ✅ (no changes needed, acknowledged) |

---

## Issues Found

### CRITICAL

**[C-1] clone's inner tree traversal only descends one level — sidecar download added to wrong loop**

**Plan:** 4A-Server, Task 6, Step 6.3  
**File:** `src/dit/cli/main.py` (existing clone command, ~line 1163–1177)

The spec (§7.3) says clone must also fetch sidecars. The plan adds sidecar download inside the loop that iterates `tree.entries`. However, the actual clone code at lines 1164–1177 only downloads the **root-level tree** for each commit and walks its immediate entries. It does **not** recursively descend into subtrees — `entry.obj_type == "tree"` entries are silently ignored. This means:

1. For any repo with a subdirectory structure (e.g., `data/train.jsonl`), the plan's added sidecar check `if entry.sidecar_hash` will never be reached for manifest entries inside subdirs.
2. More critically: the plan's patch block includes an `elif entry.obj_type == "tree": pass` — this incorrectly suggests the recursive case is handled, when it is not even in the original code for manifests either.

The correct fix is either:
- Use `walk_commit_objects` (which now collects sidecars via the walker extension) for the clone loop, consistent with what push uses; or
- Add a recursive tree-descent function in clone similar to `_fetch_objects_since`.

The `_fetch_objects_since` fix in Step 6.4 has the same structure flaw but it does handle recursion via the `queue.extend(commit.parent_hashes)` loop for commits — however the tree traversal itself is still flat (only immediate entries of each commit's tree). For flat repos (no subdirs) the fix works. For nested repos it misses sub-tree entries.

**Required fix:** In Task 6 Step 6.3, replace the per-entry block with a recursive helper that walks subtrees, similar to `_walk_tree` in `walker.py`. Alternatively, refactor clone to use `walk_commit_objects` (which already handles recursion and now collects sidecars) and download each hash in the result set. The same recursive fix applies to Step 6.4 for `_fetch_objects_since`.

---

**[C-2] FastAPI summary route with `:path` converter will shadow itself — runtime 404 on `/summary` requests**

**Plan:** 4A-Server, Task 4, Step 4.3  
**File:** `src/dit/server/routes/meta_api.py`

The server plan registers routes in this order (correctly noted in comments):
1. `GET /{repo}/meta/diff/{old}/{new}`
2. `GET /{repo}/meta/{commit_hash}/{file_path:path}/summary`
3. `GET /{repo}/meta/{commit_hash}/{file_path:path}`

The intent is for route 2 to catch `/summary`-suffixed requests before route 3 swallows them with its `:path` converter. This is **not how FastAPI/Starlette path routing works** for `:path` parameters.

FastAPI's `{file_path:path}` converter is greedy and will match everything including slashes. When a request comes in for `/api/v1/repos/r/meta/abc123/train.jsonl/summary`, FastAPI evaluates routes in registration order and **route 2** (`/{file_path:path}/summary`) has a literal `/summary` suffix — but Starlette's path regex for route 2 becomes something like `^/api/v1/repos/{repo}/meta/{commit_hash}/(?P<file_path>.+)/summary$`. Route 3 is `^.../(?P<file_path>.+)$`. Both can match the same URL. FastAPI **does** pick the first registered match, so the ordering is correct in principle.

However, the summary handler then strips `/summary` from `file_path` defensively:
```python
if clean.endswith("/summary"):
    clean = clean[: -len("/summary")]
```
This defensive strip is **wrong** because if route 2 matched properly, `file_path` will be `train.jsonl` (not `train.jsonl/summary`). The `/summary` suffix is consumed by the literal route pattern. Applying the strip would leave `file_path` unchanged (since it doesn't end in `/summary`), which is fine — but the comment misleads implementors.

The real risk: in testing, if route 3 is accidentally matched first (e.g., wrong import order or router include order), the summary endpoint returns the raw sidecar JSON instead of the summary, without a 404. The plan's test `test_summary_basic` would then fail with a JSON parse mismatch. **The plan must explicitly verify in Step 4.5 that both `meta_summary` and `meta_get` are registered with `meta_summary` first in `meta_api.py`, and that the router is included in `app.py` before any other router that has overlapping prefix patterns.**

Additionally, the defensive strip logic should be removed from the implementation to avoid confusion.

**Required fix:** Remove the `/summary` defensive strip from `meta_summary`. Add an explicit assertion in the verification step that route ordering is `meta_diff → meta_summary → meta_get` within the same router file.

---

### IMPORTANT

**[I-1] `commit` command in main.py: stale type annotation after 3-tuple change — will cause mypy failures and may confuse the next worker**

**Plan:** 4A-Server, Task 1, Step 1.5  
**File:** `src/dit/cli/main.py` (~line 198, 205)

The `commit` command at ~line 198 contains:
```python
existing_entries: dict[str, tuple[str, str]] = {}
...
existing_entries = flatten_tree(store, old_commit.tree_hash)
...
merged: dict[str, tuple[str, str]] = {**existing_entries, **staged_typed}
```

After 4A-Core Task 10, `flatten_tree` returns `dict[str, tuple[str, str, str | None]]`. The type annotations on both lines are wrong. `build_nested_tree` accepts 3-tuples, so runtime behaviour is correct (3-tuples from `flatten_tree` pass through fine), but:

1. Any type-checker or mypy run will flag this as a type error.
2. The `merged` dict merges 3-tuples from `flatten_tree` with 2-tuples from `staged_typed` (from `index.entries_typed()`). This mixed dict will have inconsistent value types. `build_nested_tree` in the 4A-Core plan handles both via `len(value) >= 3`, so runtime is fine.

The server plan's Step 1.5 instructs to grep for `obj_type, obj_hash` patterns and update them — but it does **not** explicitly call out the `commit` command's `existing_entries` type annotation lines as needing an update. A worker following the plan will fix unpacking calls but may miss the type annotations.

The `_has_uncommitted_changes` function (~line 399–403) and `_materialize_tree` function (~line 428–434) also unpack `flatten_tree` results as 2-tuples with the `(obj_type, obj_hash)` pattern. These are implicitly covered by the grep instruction in Step 1.5 but should be called out explicitly.

**Required fix:** In Task 1 Step 1.5, add explicit callouts for:
- `commit` command lines 198, 205: update type annotations to `dict[str, tuple[str, str, str | None]]`
- `_has_uncommitted_changes` line ~399: `for path, (obj_type, obj_hash) in flat.items()` → add `sidecar_hash` or `*_`
- `_materialize_tree` lines ~429, 433: same unpack update

---

**[I-2] Server test fixture: `tmp_path` does not match server's `data_dir` without a conftest fixture**

**Plan:** 4A-Server, Task 4, Step 4.1  
**File:** `tests/server/test_routes_meta.py`

The test helper `_build_repo_with_sidecar` writes objects directly to `tmp_path / "data" / "repos" / repo / "objects"`. This assumes the test's `client` fixture configures `app.state.data_dir = tmp_path / "data"`. Looking at existing server tests (`tests/server/`), the `client` fixture is defined in `tests/server/conftest.py` and likely uses a fixed temp directory or a fixture-scoped temp path, not `tmp_path` from the test function.

If the server's `data_dir` does not equal `tmp_path / "data"`, then `_store_for_repo` in the route handler will read from a different location than where the test wrote the objects, causing all tests that pre-populate the store to fail with 404 or unexpected results.

The existing server tests (e.g., `tests/server/test_routes_manifest.py`) should be examined to see how they set up the object store — the pattern needs to match exactly.

**Required fix:** Before Step 4.1, add a note: "Verify that the `client` fixture in `tests/server/conftest.py` exposes `tmp_path` as `app.state.data_dir`. If the conftest uses a different fixture for `data_dir`, update `_build_repo_with_sidecar` to use the same path." Then confirm the exact conftest pattern and update the helper accordingly.

---

**[I-3] Gateway Task 4B: `net/url` import — `url.QueryEscape` will not compile without explicit import verification**

**Plan:** 4A-Gateway, Task 4, Step 4B  
**File:** `modules/datahub/client.go`

The plan adds:
```go
import "net/url"
```
and uses `url.QueryEscape(filePath)`.

The existing `client.go` does not import `"net/url"`. The plan says "add `net/url` to the import block" but does not show the exact updated import block. Go's goimports will handle this automatically if the worker uses `goimports`, but if they just use `go build` they may get a compile error if goimports is not run.

More importantly, `url.QueryEscape` escapes `/` as `%2F`, which is correct for query parameter values. However, if `filePath` contains slashes (e.g., `subdir/train.jsonl`), the diff endpoint on the Python side receives `?file=subdir%2Ftrain.jsonl`. The Python server uses `file.lstrip("/")` for path cleaning but does not URL-decode the query parameter — FastAPI's `Query()` will decode it automatically, so this is fine. But the gateway test `TestMetaDiff` only tests a simple filename (`train.jsonl`) — a test with a path containing a slash would be valuable to confirm end-to-end encoding correctness.

**Required fix:** In Step 4B, show the full updated import block for `client.go`. Add a note about running `goimports` or `go build ./modules/datahub/...` immediately after to catch the import.

---

**[I-4] Server plan: `meta_compute` endpoint uses inline `from dit.server.models import Ref` but Repo is not imported via `_get_repo`**

**Plan:** 4A-Server, Task 4, Step 4.3  
**File:** `src/dit/server/routes/meta_api.py`

The `meta_compute` handler calls `await _get_repo(repo, session)` and assigns the result to `r`, then uses `r.id`. However, `_get_repo` raises `HTTPException(404)` if the repo is not found and returns a `Repo` object. The code is:

```python
r = await _get_repo(repo, session)
store = _store_for_repo(request, repo)
result = await session.execute(
    select(Ref).where(Ref.repo_id == r.id, Ref.name == "heads/main")
)
```

This pattern is correct and matches `diff_api.py`. However, the import `from dit.server.models import Ref` is inside the function body as a local import, while `Repo` (used implicitly through `r`) is imported via `_get_repo`. The `Ref` model needs to be importable; looking at existing routes like `diff_api.py` at line 13: `from dit.server.models import Ref, Repo` — this is a top-level import. The plan puts it inside the function. This is not an error (Python allows local imports) but is inconsistent with the codebase pattern and may confuse a worker.

Additionally, the `select` function from SQLAlchemy is used in the handler but not imported anywhere in the plan's code. The existing `diff_api.py` imports `from sqlalchemy import select` at the top. The meta_api plan has `from sqlalchemy import select` missing from its top-level imports — only `from sqlalchemy.ext.asyncio import AsyncSession` is shown.

**Required fix:** Add `from sqlalchemy import select` to the top-level imports in the `meta_api.py` template. Move the `from dit.server.models import Ref` import to the top-level block, matching the convention in `diff_api.py`.

---

### MINOR

**[M-1] Core Task 10: `flatten_tree` prefix path construction has a subtle logic bug**

**Plan:** 4A-Core, Task 10, Step 3  
**File:** `src/dit/core/tree_walker.py`

The plan's new `flatten_tree` uses:
```python
full_path = f"{prefix}{entry.name}" if not prefix else f"{prefix}/{entry.name}"
```

This reproduces the existing implementation exactly — but the condition is inverted from what the string interpolation implies. When `prefix` is empty (`not prefix` is True), it produces `f"{prefix}{entry.name}"` which is just `entry.name` (correct). When `prefix` is non-empty, it produces `f"{prefix}/{entry.name}"` (correct). The logic is correct but the conditional reads backwards — `if not prefix` produces the no-separator form. This is a style issue that makes the code harder to read. A clearer form: `full_path = f"{prefix}/{entry.name}" if prefix else entry.name`. This already exists in the current codebase as-is, so the plan faithfully reproduces it. Note it for future refactoring but no change required now.

---

**[M-2] Server Task 1: `meta_compute` uses `Sidecar` type annotation as a string reference in inner function**

**Plan:** 4A-Server, Task 3, Step 3.3  
**File:** `src/dit/cli/main.py`

The `meta_diff` command defines an inner function `_load_sidecars` with the return type annotation `-> dict[str, "Sidecar"]` — using a forward reference string. `Sidecar` is imported at call time via `from dit.core.objects import deserialize_sidecar` but not imported at the top of the function body. This works at runtime (return annotation is not evaluated), but the string forward reference is unnecessary noise. Should be `-> dict` or import `Sidecar` at the top of the function.

---

**[M-3] Server Task 4: `_sidecar_summary` is a module-level helper but is duplicated in spirit in CLI Task 2**

The server plan defines `_sidecar_summary(sidecar)` in `meta_api.py` and the CLI plan implements the same aggregation logic inline in `meta_show`. This is acceptable (CLI and server are separate layers), but if the aggregation logic changes it must be updated in two places. Note for future refactoring — extract to `core/sidecar.py` as a `sidecar_summary(sidecar)` helper.

---

**[M-4] Gateway Task 7: Route ordering comment says `diff` must come before generic `{commit}/{path}` — but the Forgejo router may not respect declaration order**

**Plan:** 4A-Gateway, Task 7  
**File:** `routers/api/v1/api.go`

The plan correctly notes that `meta/diff/{old}/{new}` must be registered before `meta/{commit}/{path}` to avoid the diff path being parsed as a commit hash. However, the Forgejo router is based on chi, not FastAPI. Chi uses a trie-based router where exact path segments take priority over wildcard segments by default — meaning `/meta/diff/...` (which has a literal `diff` segment) should naturally be matched before `/{commit}/...` regardless of registration order.

This is actually **safer** than the plan implies — the route ordering concern is a FastAPI-ism and does not apply in chi. The current phrasing may confuse a worker into thinking a specific order is required when chi handles it automatically. A minor clarification would help.

---

## Cross-Plan Consistency

### 4A-Core → 4A-Server Interface

**flatten_tree return type:** Core Task 10 changes `flatten_tree` from `dict[str, tuple[str, str]]` to `dict[str, tuple[str, str, str | None]]`. The server plan documents this explicitly in its preamble and handles it in Tasks 1–3 (CLI commands) and Task 4 (server routes). All new code in the server plan correctly unpacks 3-tuples. The call-site audit instruction in Task 1 Step 1.5 covers existing callers via grep. However, it does not explicitly list all affected sites — see I-1 above for three sites the grep may miss.

**build_nested_tree 3-tuple:** Core Task 8 extends `build_nested_tree` to accept `(obj_type, obj_hash, sidecar_hash)`. The server plan's `meta_compute` (Task 1 Step 1.3 and Task 4 Step 4.3) passes `updated: dict[str, tuple[str, str, Optional[str]]]` to `build_nested_tree`. This is consistent. The commit command's `merged` dict (existing code) will mix 2-tuples from `staged_typed` with 3-tuples from `flatten_tree` — the new `build_nested_tree` handles both via `len(value) >= 3`, so this is correct.

**walker "sidecars" set:** Core Task 9 adds `"sidecars"` key to `walk_commit_objects` result. The server plan's push Task 5 consumes `new_objects.get("sidecars", set())` in the upload loop. However, looking at the actual push code at line 1067–1072, `new_objects` is built as:
```python
new_objects: dict[str, set[str]] = {
    obj_type: local_objects[obj_type] - remote_objects[obj_type]
    for obj_type in local_objects
}
```
After Core Task 9, `local_objects` will include `"sidecars"` key. `remote_objects` is built from `walk_commit_objects(store, remote_hash)` — but if the remote commit predates 4A (no sidecar_hash in any TreeEntry), `remote_objects["sidecars"]` will be an empty set. The set subtraction `local_objects["sidecars"] - remote_objects["sidecars"]` will correctly produce all local sidecar hashes. This is consistent and correct.

**SidecarEntry / Sidecar types:** The server plan imports `SidecarEntry`, `Sidecar`, `serialize_sidecar`, `deserialize_sidecar` from `dit.core.objects`. Core Task 1 adds these to `objects.py`. Consistent.

### 4A-Server → 4A-Gateway Interface

**API paths:** The server plan registers routes at `/api/v1/repos/{repo}/meta/compute`, `/meta/diff/{old}/{new}`, `/meta/{commit}/{file_path:path}`, `/meta/{commit}/{file_path:path}/summary`. The gateway client methods build URLs as:
- `MetaCompute`: `/api/v1/repos/{repo}/meta/compute` ✅
- `MetaGet`: `/api/v1/repos/{repo}/meta/{commit}/{filePath}` ✅
- `MetaSummary`: `/api/v1/repos/{repo}/meta/{commit}/{filePath}/summary` ✅
- `MetaDiff`: `/api/v1/repos/{repo}/meta/diff/{old}/{new}` ✅

All four URLs match the server routes exactly.

**Response shapes:** The gateway proxies raw JSON bytes — no transformation. The Vue components parse the JSON directly from the proxy response. The response shapes from `meta_get`, `meta_summary`, and `meta_diff` in the server plan match what the Vue components expect (`entries`, `row_count`, `token_estimate`, `lang_distribution`, `files`, `delta`). Consistent.

**Gateway route params:** The gateway handlers use `ctx.Params(":commit")` and `ctx.Params(":path")` for `DatahubMetaGet`/`DatahubMetaSummary`, and `ctx.Params(":old")`/`ctx.Params(":new")` for `DatahubMetaDiff`. These match the route registration in `api.go`:
```go
m.Get("/meta/{commit}/{path}", repo.DatahubMetaGet)
m.Get("/meta/{commit}/{path}/summary", repo.DatahubMetaSummary)
m.Get("/meta/diff/{old}/{new}", repo.DatahubMetaDiff)
```
Consistent.

---

## Recommendation

The following changes must be made before any worker begins execution:

### Fixes required in `2026-04-25-phase4a-server.md`

1. **[C-1] Task 6, Steps 6.3 and 6.4:** Replace the flat per-entry sidecar download patch with a recursive helper. Options:
   - Refactor `clone` to use `walk_commit_objects` (which now returns `"sidecars"`) and download each set in dependency order (rows → manifests → sidecars → trees → commits). This aligns clone with the walker and eliminates the flat-traversal bug entirely.
   - If keeping the manual traversal in `_fetch_objects_since`, add a recursive `_walk_tree_for_clone` helper that descends into subtrees, and call sidecar download inside it.

2. **[C-2] Task 4, Step 4.3:** In `meta_summary`, remove the defensive `/summary` strip. Add a verification sub-step confirming route registration order in `meta_api.py` is exactly: `meta_diff` → `meta_summary` → `meta_get`.

3. **[I-1] Task 1, Step 1.5:** Add explicit grep targets beyond just the new-code call sites. Name these three existing sites that must be updated after Core Task 10:
   - `commit` command (~line 198): `existing_entries: dict[str, tuple[str, str]]` → `dict[str, tuple[str, str, str | None]]`
   - `commit` command (~line 205): `merged: dict[str, tuple[str, str]]` → `dict[str, tuple[str, str, str | None]]`
   - `_has_uncommitted_changes` (~line 399): `for path, (obj_type, obj_hash) in flat.items()` → `for path, (obj_type, obj_hash, _sidecar) in flat.items()`
   - `_materialize_tree` (~line 429, 433): same unpack update

4. **[I-2] Task 4, Step 4.1:** Add a note before the test code: "The `ObjectStore` path used in `_build_repo_with_sidecar` (`tmp_path / 'data' / 'repos' / repo / 'objects'`) must match the `data_dir` configured in the `client` fixture in `tests/server/conftest.py`. Verify this before running the tests."

5. **[I-4] Task 4, Step 4.3:** Move `from sqlalchemy import select` and `from dit.server.models import Ref, Repo` to the top-level imports of `meta_api.py`. Remove them from inside function bodies.

### Fixes required in `2026-04-25-phase4a-gateway.md`

6. **[I-3] Task 4, Step 4B:** Show the complete updated import block for `client.go`. Add a `go build ./modules/datahub/...` step immediately after the implementation to catch the import.
