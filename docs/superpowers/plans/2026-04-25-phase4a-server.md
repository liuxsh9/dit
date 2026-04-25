# Phase 4A-Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add CLI meta commands, server meta API endpoints, and push/pull sidecar sync to datahub.

**Architecture:** CLI uses typer subcommand group; server adds 4 new routes; push/pull includes sidecars in sync.

**Tech Stack:** Python 3.12, FastAPI, typer, pytest, httpx

**Depends on:** Phase 4A-Core (data model, serialization, computation) — all types and functions listed below are assumed to exist before this plan's tasks run.

---

## Assumed 4A-Core Exports

```python
# src/dit/core/objects.py
@dataclass(frozen=True)
class SidecarEntry:
    row_hash: str; char_count: int; token_estimate: int; field_count: int; lang: Optional[str]

@dataclass(frozen=True)
class Sidecar:
    manifest_hash: str; entries: list[SidecarEntry]

class TreeEntry:  # extended with optional sidecar_hash
    name: str; obj_type: str; obj_hash: str; sidecar_hash: Optional[str] = None

def serialize_sidecar(s: Sidecar) -> bytes
def deserialize_sidecar(data: bytes) -> Sidecar

# src/dit/core/sidecar.py
def compute_sidecar(store: ObjectStore, manifest_hash: str) -> Sidecar

# src/dit/core/walker.py
# walk_commit_objects now returns {"commits", "trees", "manifests", "rows", "sidecars"}

# src/dit/core/tree_builder.py
# build_nested_tree now accepts 3-tuples: (obj_type, obj_hash, sidecar_hash)

# src/dit/core/tree_walker.py
# flatten_tree now returns dict[str, tuple[str, str, str | None]]
#   → (obj_type, obj_hash, sidecar_hash)
```

---

## File Structure

```
src/dit/
  cli/
    main.py               # add meta_app typer group + 3 commands; update push upload_order;
                          # update clone and _fetch_objects_since for sidecar download
  server/
    routes/
      meta_api.py         # NEW: 4 meta endpoints
    app.py                # register meta router

tests/
  test_cli.py             # add TestMeta class (compute, show, diff)
  server/
    test_routes_meta.py   # NEW: 4 server endpoint tests
  test_push_sidecar.py    # NEW: push upload order + sidecar inclusion
  test_clone_sidecar.py   # NEW: clone and fetch_objects_since sidecar support
```

**Note on flatten_tree return type change:** After 4A-Core, `flatten_tree` returns
`dict[str, tuple[str, str, str | None]]`. All existing callers in `main.py` that
destructure as `obj_type, obj_hash = flat[path]` must be updated to
`obj_type, obj_hash, sidecar_hash = flat[path]` (or `*_` to discard). Each task
below calls this out explicitly where the command touches flatten_tree.

---

## Task 1: CLI `meta compute` Command

**Files:**
- `src/dit/cli/main.py`
- `tests/test_cli.py`

### Steps

- [ ] **1.1** Add a `TestMeta` class with a `test_meta_compute_all` test to `tests/test_cli.py`.
  The test creates a repo with two committed JSONL files, runs `meta compute`, and asserts:
  - exit code 0
  - output mentions both files
  - output mentions "Created commit"
  - a new commit exists at HEAD with `sidecar_hash` populated on both tree entries

```python
# tests/test_cli.py — append new class

import os
from pathlib import Path
from typer.testing import CliRunner
from dit.cli.main import app
from dit.core.store import ObjectStore
from dit.core.refs import RefStore
from dit.core.objects import deserialize_commit, deserialize_tree

runner = CliRunner()


class TestMeta:
    def _make_repo_with_data(self, tmp_path):
        """Helper: init repo, add two JSONL files, commit them, return dot path."""
        os.chdir(tmp_path)
        runner.invoke(app, ["init"])
        (tmp_path / "train.jsonl").write_text(
            '{"messages": [{"role": "user", "content": "hello world"}]}\n'
            '{"messages": [{"role": "user", "content": "foo bar baz"}]}\n'
        )
        (tmp_path / "eval.jsonl").write_text(
            '{"messages": [{"role": "user", "content": "test"}]}\n'
        )
        runner.invoke(app, ["add", "."])
        runner.invoke(app, ["commit", "-m", "initial data"])
        return tmp_path / ".datahub"

    def test_meta_compute_all(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot = self._make_repo_with_data(tmp_path)

        result = runner.invoke(app, ["meta", "compute"])
        assert result.exit_code == 0, result.output
        assert "train.jsonl" in result.output
        assert "eval.jsonl" in result.output
        assert "Created commit" in result.output

        store = ObjectStore(dot / "objects")
        refs = RefStore(dot)
        head = refs.resolve_head()
        commit_data = store.read("commits", head)
        commit = deserialize_commit(commit_data)

        from dit.core.tree_walker import flatten_tree
        flat = flatten_tree(store, commit.tree_hash)
        # After 4A-Core, flatten_tree returns (obj_type, obj_hash, sidecar_hash)
        for path, (obj_type, obj_hash, sidecar_hash) in flat.items():
            if obj_type == "manifest":
                assert sidecar_hash is not None, f"sidecar_hash missing for {path}"
                assert store.read("sidecars", sidecar_hash) is not None

    def test_meta_compute_single_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot = self._make_repo_with_data(tmp_path)

        result = runner.invoke(app, ["meta", "compute", "--file", "train.jsonl"])
        assert result.exit_code == 0, result.output
        assert "train.jsonl" in result.output
        assert "eval.jsonl" not in result.output
        assert "Created commit" in result.output

    def test_meta_compute_idempotent(self, tmp_path, monkeypatch):
        """Running meta compute twice produces identical commit trees (no duplicate objects)."""
        monkeypatch.chdir(tmp_path)
        dot = self._make_repo_with_data(tmp_path)

        runner.invoke(app, ["meta", "compute"])
        store = ObjectStore(dot / "objects")
        refs = RefStore(dot)
        h1 = refs.resolve_head()

        result2 = runner.invoke(app, ["meta", "compute"])
        assert result2.exit_code == 0
        h2 = refs.resolve_head()
        # Second run is a no-op: no new commit because all sidecars already exist
        assert h1 == h2, "Second meta compute should be a no-op (same HEAD)"

    def test_meta_compute_no_commits(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(app, ["init"])
        result = runner.invoke(app, ["meta", "compute"])
        assert result.exit_code != 0
        assert "No commits" in result.output or "fatal" in result.output.lower()
```

- [ ] **1.2** Run to confirm failure (meta group does not exist yet):

```bash
cd /Users/lxs/code/datahub && uv run pytest tests/test_cli.py::TestMeta -v 2>&1 | head -30
```

Expected: `NoSuchCommand` or `AttributeError` — `meta` not registered.

- [ ] **1.3** Add the `meta_app` typer group and the `meta compute` command to
  `src/dit/cli/main.py`. Insert after the `auth_app` block (after line ~976).

  The `compute` command logic:
  1. Load HEAD commit; abort if no commits.
  2. Call `flatten_tree` to get all paths with `(obj_type, obj_hash, sidecar_hash)`.
  3. Filter to entries where `obj_type == "manifest"` and `sidecar_hash is None`
     (or limit to `--file` if given).
  4. For each, call `compute_sidecar(store, manifest_hash)`, serialize, write to store.
  5. Build a new merged dict with updated 3-tuples, pass to `build_nested_tree`.
  6. Create a commit with message `"meta: compute sidecar metadata"`.
  7. If no sidecars were computed (all already present), print "Nothing to compute." and exit 0 without creating a commit.

```python
# src/dit/cli/main.py — add after auth_app block

meta_app = typer.Typer(name="meta", help="Manage sidecar metadata.")
app.add_typer(meta_app)


@meta_app.command("compute")
def meta_compute(
    file: Optional[str] = typer.Option(None, "--file", help="Limit to a specific file path"),
):
    """Compute sidecar metadata for manifests that lack it, create a new commit."""
    from dit.core.tree_builder import build_nested_tree
    from dit.core.tree_walker import flatten_tree
    from dit.core.sidecar import compute_sidecar
    from dit.core.objects import serialize_sidecar

    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)

    head_hash = refs.resolve_head()
    if head_hash is None:
        typer.echo("fatal: no commits in this repository", err=True)
        raise typer.Exit(1)

    commit_data = store.read("commits", head_hash)
    head_commit = deserialize_commit(commit_data)

    # flatten_tree (post 4A-Core) returns dict[str, tuple[str, str, str | None]]
    flat = flatten_tree(store, head_commit.tree_hash)

    computed_count = 0
    # Build updated merged map: path -> (obj_type, obj_hash, sidecar_hash)
    updated: dict[str, tuple[str, str, Optional[str]]] = {}

    for path, (obj_type, obj_hash, sidecar_hash) in flat.items():
        if obj_type != "manifest":
            updated[path] = (obj_type, obj_hash, sidecar_hash)
            continue
        if file is not None and path != file.lstrip("/"):
            updated[path] = (obj_type, obj_hash, sidecar_hash)
            continue
        if sidecar_hash is not None:
            # Already computed
            updated[path] = (obj_type, obj_hash, sidecar_hash)
            continue

        # Compute sidecar
        manifest_data = store.read("manifests", obj_hash)
        if manifest_data is None:
            typer.echo(f"warning: manifest for {path} not found, skipping", err=True)
            updated[path] = (obj_type, obj_hash, None)
            continue

        sidecar = compute_sidecar(store, obj_hash)
        sidecar_bytes = serialize_sidecar(sidecar)
        new_sidecar_hash = store.write("sidecars", sidecar_bytes)

        row_count = len(sidecar.entries)
        typer.echo(f"Computing metadata for {path} ({row_count} rows)... done (sidecar: {new_sidecar_hash[:8]})")
        updated[path] = (obj_type, obj_hash, new_sidecar_hash)
        computed_count += 1

    if computed_count == 0:
        typer.echo("Nothing to compute (all manifests already have sidecar metadata).")
        raise typer.Exit(0)

    new_tree_hash = build_nested_tree(store, updated)

    parent_hashes = [head_hash]
    new_commit = Commit(
        tree_hash=new_tree_hash,
        parent_hashes=parent_hashes,
        author=_get_author(),
        message="meta: compute sidecar metadata",
        timestamp=int(time.time()),
    )
    commit_bytes = serialize_commit(new_commit)
    new_commit_hash = store.write("commits", commit_bytes)

    branch = refs.current_branch()
    refs.set_branch(branch, new_commit_hash)
    typer.echo(f"Created commit: {new_commit_hash[:8]} \"meta: compute sidecar metadata\"")
```

- [ ] **1.4** Run the meta compute tests:

```bash
cd /Users/lxs/code/datahub && uv run pytest tests/test_cli.py::TestMeta::test_meta_compute_all tests/test_cli.py::TestMeta::test_meta_compute_single_file tests/test_cli.py::TestMeta::test_meta_compute_idempotent tests/test_cli.py::TestMeta::test_meta_compute_no_commits -v
```

Expected: 4 passed.

- [ ] **1.5** Run the full CLI test suite to check for regressions caused by the
  `flatten_tree` return-type change (all callers that do `obj_type, obj_hash = flat[p]`
  must be updated to unpack 3 values):

```bash
cd /Users/lxs/code/datahub && uv run pytest tests/test_cli.py -v 2>&1 | tail -30
```

If any tests fail with `ValueError: too many values to unpack`, update those callers in
`main.py` to use:

```python
obj_type, obj_hash, _sidecar = flat[path]
# or
obj_type, obj_hash, sidecar_hash = flat[path]
```

Search for all callsites:

```bash
grep -n "obj_type, obj_hash" /Users/lxs/code/datahub/src/dit/cli/main.py
```

**Explicit callsites that MUST be updated** (grep will find these, but do not miss them):

1. **`status` command (~line 146):**
   ```python
   # Before:
   for path, (obj_type, obj_hash) in flat.items():
   # After:
   for path, (obj_type, obj_hash, _sidecar) in flat.items():
   ```

2. **`commit` command (~line 198) — type annotation:**
   ```python
   # Before:
   existing_entries: dict[str, tuple[str, str]] = {}
   # After:
   existing_entries: dict[str, tuple[str, str, str | None]] = {}
   ```

3. **`commit` command (~line 205) — merged dict type annotation:**
   ```python
   # Before:
   merged: dict[str, tuple[str, str]] = {**existing_entries, **staged_typed}
   # After:
   merged: dict[str, tuple[str, str, str | None]] = {**existing_entries, **staged_typed}
   ```
   Note: `staged_typed` from `index.entries_typed()` still returns 2-tuples. The new
   `build_nested_tree` handles both 2-tuple and 3-tuple values via `len(value) >= 3`,
   so the mixed dict is runtime-correct; the type annotation is updated for mypy.

4. **`_has_uncommitted_changes` (~line 401):**
   ```python
   # Before:
   for path, (obj_type, obj_hash) in flat.items()
   # After:
   for path, (obj_type, obj_hash, _sidecar) in flat.items()
   ```

5. **`_materialize_tree` (~line 429):**
   ```python
   # Before:
   new_files = {path: obj_hash for path, (obj_type, obj_hash) in new_flat.items() if obj_type == "manifest"}
   # After:
   new_files = {path: obj_hash for path, (obj_type, obj_hash, _sidecar) in new_flat.items() if obj_type == "manifest"}
   ```

6. **`_materialize_tree` (~line 434):**
   ```python
   # Before:
   old_files = {path: obj_hash for path, (obj_type, obj_hash) in old_flat.items() if obj_type == "manifest"}
   # After:
   old_files = {path: obj_hash for path, (obj_type, obj_hash, _sidecar) in old_flat.items() if obj_type == "manifest"}
   ```

Also check the `status` command (~line 275) for a `head_manifests` dict comprehension:
```python
# Before:
head_manifests = {k: v for k, (t, v) in flat.items() if t == "manifest"}
# After:
head_manifests = {k: v for k, (t, v, _sc) in flat.items() if t == "manifest"}
```

- [ ] **1.6** Commit:

```bash
cd /Users/lxs/code/datahub && git add src/dit/cli/main.py tests/test_cli.py && git commit -m "feat: dit meta compute — CLI subcommand group + sidecar computation and commit"
```

---

## Task 2: CLI `meta show` Command

**Files:**
- `src/dit/cli/main.py`
- `tests/test_cli.py`

### Steps

- [ ] **2.1** Add tests to `TestMeta` in `tests/test_cli.py`:

```python
# tests/test_cli.py — append to TestMeta class

    def test_meta_show_table(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot = self._make_repo_with_data(tmp_path)
        runner.invoke(app, ["meta", "compute"])

        result = runner.invoke(app, ["meta", "show", "train.jsonl"])
        assert result.exit_code == 0, result.output
        assert "train.jsonl" in result.output
        assert "Total chars" in result.output or "total" in result.output.lower()
        assert "Token estimate" in result.output or "token" in result.output.lower()

    def test_meta_show_json(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot = self._make_repo_with_data(tmp_path)
        runner.invoke(app, ["meta", "compute"])

        result = runner.invoke(app, ["meta", "show", "train.jsonl", "--format", "json"])
        assert result.exit_code == 0, result.output
        import json
        data = json.loads(result.output)
        assert "manifest_hash" in data
        assert "entries" in data
        assert len(data["entries"]) == 2

    def test_meta_show_no_sidecar(self, tmp_path, monkeypatch):
        """meta show on a file without sidecar exits with error."""
        monkeypatch.chdir(tmp_path)
        dot = self._make_repo_with_data(tmp_path)
        # Do NOT run meta compute — no sidecars exist

        result = runner.invoke(app, ["meta", "show", "train.jsonl"])
        assert result.exit_code != 0
        assert "no sidecar" in result.output.lower() or "not found" in result.output.lower()

    def test_meta_show_file_not_in_tree(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot = self._make_repo_with_data(tmp_path)
        runner.invoke(app, ["meta", "compute"])

        result = runner.invoke(app, ["meta", "show", "nonexistent.jsonl"])
        assert result.exit_code != 0
```

- [ ] **2.2** Run to confirm failure:

```bash
cd /Users/lxs/code/datahub && uv run pytest tests/test_cli.py::TestMeta::test_meta_show_table tests/test_cli.py::TestMeta::test_meta_show_json -v 2>&1 | head -20
```

- [ ] **2.3** Add the `meta show` command to `src/dit/cli/main.py` (inside the `meta_app` block):

```python
# src/dit/cli/main.py — add inside meta_app block

@meta_app.command("show")
def meta_show(
    file: str = typer.Argument(..., help="File path (e.g. train.jsonl)"),
    format: str = typer.Option("table", "--format", help="Output format: table or json"),
):
    """Display sidecar metadata stats for a file at HEAD."""
    from dit.core.tree_walker import flatten_tree
    from dit.core.objects import deserialize_sidecar

    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)

    head_hash = refs.resolve_head()
    if head_hash is None:
        typer.echo("fatal: no commits in this repository", err=True)
        raise typer.Exit(1)

    commit_data = store.read("commits", head_hash)
    head_commit = deserialize_commit(commit_data)
    flat = flatten_tree(store, head_commit.tree_hash)

    clean = file.lstrip("/")
    if clean not in flat:
        typer.echo(f"fatal: '{file}' not found in current HEAD tree", err=True)
        raise typer.Exit(1)

    obj_type, obj_hash, sidecar_hash = flat[clean]
    if obj_type != "manifest":
        typer.echo(f"fatal: '{file}' is not a manifest file (type={obj_type})", err=True)
        raise typer.Exit(1)

    if sidecar_hash is None:
        typer.echo(
            f"fatal: no sidecar for '{file}' — run 'dit meta compute' first",
            err=True,
        )
        raise typer.Exit(1)

    sidecar_data = store.read("sidecars", sidecar_hash)
    if sidecar_data is None:
        typer.echo(f"fatal: sidecar object {sidecar_hash[:8]} missing from store", err=True)
        raise typer.Exit(1)

    sidecar = deserialize_sidecar(sidecar_data)

    if format == "json":
        import json as _json
        out = {
            "manifest_hash": sidecar.manifest_hash,
            "entries": [
                {
                    "row_hash": e.row_hash,
                    "char_count": e.char_count,
                    "token_estimate": e.token_estimate,
                    "field_count": e.field_count,
                    "lang": e.lang,
                }
                for e in sidecar.entries
            ],
        }
        typer.echo(_json.dumps(out, indent=2))
        return

    # Table format: aggregate stats
    row_count = len(sidecar.entries)
    if row_count == 0:
        typer.echo(f"File: {file} (0 rows)")
        typer.echo("  No data.")
        return

    total_chars = sum(e.char_count for e in sidecar.entries)
    total_tokens = sum(e.token_estimate for e in sidecar.entries)
    avg_fields = sum(e.field_count for e in sidecar.entries) / row_count

    lang_counts: dict[str, int] = {}
    for e in sidecar.entries:
        lang_key = e.lang or "unknown"
        lang_counts[lang_key] = lang_counts.get(lang_key, 0) + 1
    lang_pcts = {
        lang: f"{count / row_count * 100:.0f}%"
        for lang, count in sorted(lang_counts.items(), key=lambda x: -x[1])
    }
    lang_str = ", ".join(f"{lang} ({pct})" for lang, pct in lang_pcts.items())

    typer.echo(f"File: {file} ({row_count} rows)")
    typer.echo(f"Sidecar: {sidecar_hash[:8]}")
    typer.echo("")
    typer.echo(f"  Total chars:    {total_chars:,}")
    typer.echo(f"  Token estimate: {total_tokens:,}")
    typer.echo(f"  Avg fields/row: {avg_fields:.1f}")
    typer.echo(f"  Languages:      {lang_str}")
```

- [ ] **2.4** Run the show tests:

```bash
cd /Users/lxs/code/datahub && uv run pytest tests/test_cli.py::TestMeta::test_meta_show_table tests/test_cli.py::TestMeta::test_meta_show_json tests/test_cli.py::TestMeta::test_meta_show_no_sidecar tests/test_cli.py::TestMeta::test_meta_show_file_not_in_tree -v
```

Expected: 4 passed.

- [ ] **2.5** Commit:

```bash
cd /Users/lxs/code/datahub && git add src/dit/cli/main.py tests/test_cli.py && git commit -m "feat: dit meta show — table and JSON sidecar display"
```

---

## Task 3: CLI `meta diff` Command

**Files:**
- `src/dit/cli/main.py`
- `tests/test_cli.py`

### Steps

- [ ] **3.1** Add tests to `TestMeta`:

```python
# tests/test_cli.py — append to TestMeta class

    def _make_two_commits_with_sidecars(self, tmp_path):
        """Helper: create initial commit + meta compute, then add rows + second meta compute.
        Returns (dot, commit1_hash, commit2_hash)."""
        os.chdir(tmp_path)
        runner.invoke(app, ["init"])
        (tmp_path / "train.jsonl").write_text(
            '{"messages": [{"role": "user", "content": "hello"}]}\n'
        )
        runner.invoke(app, ["add", "."])
        runner.invoke(app, ["commit", "-m", "v1"])
        runner.invoke(app, ["meta", "compute"])

        dot = tmp_path / ".datahub"
        refs = RefStore(dot)
        commit1_hash = refs.resolve_head()

        # Add more rows
        (tmp_path / "train.jsonl").write_text(
            '{"messages": [{"role": "user", "content": "hello"}]}\n'
            '{"messages": [{"role": "user", "content": "world"}]}\n'
        )
        runner.invoke(app, ["add", "."])
        runner.invoke(app, ["commit", "-m", "v2"])
        runner.invoke(app, ["meta", "compute"])
        commit2_hash = refs.resolve_head()

        return dot, commit1_hash, commit2_hash

    def test_meta_diff_shows_changes(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot, c1, c2 = self._make_two_commits_with_sidecars(tmp_path)

        result = runner.invoke(app, ["meta", "diff", c1, c2])
        assert result.exit_code == 0, result.output
        assert "train.jsonl" in result.output
        # Should show row count change: 1 → 2
        assert "1" in result.output
        assert "2" in result.output

    def test_meta_diff_with_file_filter(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot, c1, c2 = self._make_two_commits_with_sidecars(tmp_path)

        result = runner.invoke(app, ["meta", "diff", c1, c2, "--file", "train.jsonl"])
        assert result.exit_code == 0, result.output
        assert "train.jsonl" in result.output

    def test_meta_diff_invalid_commit(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(app, ["init"])
        (tmp_path / "train.jsonl").write_text('{"a": 1}\n')
        runner.invoke(app, ["add", "."])
        runner.invoke(app, ["commit", "-m", "v1"])
        runner.invoke(app, ["meta", "compute"])

        result = runner.invoke(app, ["meta", "diff", "z" * 64, "z" * 64])
        assert result.exit_code != 0
```

- [ ] **3.2** Run to confirm failure:

```bash
cd /Users/lxs/code/datahub && uv run pytest tests/test_cli.py::TestMeta::test_meta_diff_shows_changes -v 2>&1 | head -20
```

- [ ] **3.3** Add the `meta diff` command to `src/dit/cli/main.py`:

```python
# src/dit/cli/main.py — add inside meta_app block

@meta_app.command("diff")
def meta_diff(
    commit1: str = typer.Argument(..., help="Old commit hash"),
    commit2: str = typer.Argument(..., help="New commit hash"),
    file: Optional[str] = typer.Option(None, "--file", help="Limit diff to this file"),
):
    """Compare sidecar stats between two commits."""
    from dit.core.tree_walker import flatten_tree
    from dit.core.objects import deserialize_sidecar

    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")

    def _load_sidecars(commit_hash: str) -> dict[str, "Sidecar"]:
        """Return path -> Sidecar for all manifest entries in the commit."""
        commit_data = store.read("commits", commit_hash)
        if commit_data is None:
            typer.echo(f"fatal: commit {commit_hash[:8]} not found", err=True)
            raise typer.Exit(1)
        commit = deserialize_commit(commit_data)
        flat = flatten_tree(store, commit.tree_hash)
        result = {}
        for path, (obj_type, obj_hash, sidecar_hash) in flat.items():
            if obj_type == "manifest" and sidecar_hash is not None:
                sc_data = store.read("sidecars", sidecar_hash)
                if sc_data is not None:
                    result[path] = deserialize_sidecar(sc_data)
        return result

    old_sidecars = _load_sidecars(commit1)
    new_sidecars = _load_sidecars(commit2)

    all_paths = sorted(set(old_sidecars) | set(new_sidecars))
    if file is not None:
        clean = file.lstrip("/")
        all_paths = [p for p in all_paths if p == clean]

    any_output = False
    for path in all_paths:
        old_sc = old_sidecars.get(path)
        new_sc = new_sidecars.get(path)

        def _summary(sc):
            if sc is None:
                return {"rows": 0, "tokens": 0, "langs": {}}
            rows = len(sc.entries)
            tokens = sum(e.token_estimate for e in sc.entries)
            lc: dict[str, int] = {}
            for e in sc.entries:
                k = e.lang or "unknown"
                lc[k] = lc.get(k, 0) + 1
            lang_pcts = {k: f"{v / rows * 100:.0f}%" for k, v in lc.items()} if rows > 0 else {}
            return {"rows": rows, "tokens": tokens, "langs": lang_pcts}

        os = _summary(old_sc)
        ns = _summary(new_sc)

        if os == ns:
            continue

        any_output = True
        typer.echo(f"{path}:")

        row_delta = ns["rows"] - os["rows"]
        sign = "+" if row_delta >= 0 else ""
        typer.echo(f"  Rows:           {os['rows']} → {ns['rows']} ({sign}{row_delta})")

        tok_delta = ns["tokens"] - os["tokens"]
        sign = "+" if tok_delta >= 0 else ""
        typer.echo(f"  Token estimate: {os['tokens']:,} → {ns['tokens']:,} ({sign}{tok_delta:,})")

        if os["langs"] != ns["langs"]:
            old_lang_str = ", ".join(f"{l} {p}" for l, p in os["langs"].items())
            new_lang_str = ", ".join(f"{l} {p}" for l, p in ns["langs"].items())
            typer.echo(f"  Languages:      {old_lang_str} → {new_lang_str}")

    if not any_output:
        typer.echo("No metadata differences.")
```

- [ ] **3.4** Run meta diff tests:

```bash
cd /Users/lxs/code/datahub && uv run pytest tests/test_cli.py::TestMeta::test_meta_diff_shows_changes tests/test_cli.py::TestMeta::test_meta_diff_with_file_filter tests/test_cli.py::TestMeta::test_meta_diff_invalid_commit -v
```

Expected: 3 passed.

- [ ] **3.5** Run the full TestMeta suite:

```bash
cd /Users/lxs/code/datahub && uv run pytest tests/test_cli.py::TestMeta -v
```

Expected: all pass.

- [ ] **3.6** Commit:

```bash
cd /Users/lxs/code/datahub && git add src/dit/cli/main.py tests/test_cli.py && git commit -m "feat: dit meta diff — compare sidecar stats between two commits"
```

---

## Task 4: Server Meta API — All 4 Endpoints

**Files:**
- `src/dit/server/routes/meta_api.py` (new)
- `src/dit/server/app.py`
- `tests/server/test_routes_meta.py` (new)

All four server endpoints live in a single new route file. We write the tests first
(TDD), then implement.

### Steps

- [ ] **4.1** Write `tests/server/test_routes_meta.py`:

  **Fixture path verification:** The `client` fixture in `tests/server/conftest.py`
  configures `app.state.data_dir = tmp_path / "data"` (via
  `ServerSettings(data_dir=str(tmp_path / "data"))`). The `_build_repo_with_sidecar`
  helper below writes directly to `tmp_path / "data" / "repos" / repo / "objects"`,
  which matches this exactly. If the conftest ever changes its `data_dir` path, this
  helper must be updated to match — otherwise all pre-populated store tests will get 404s.

```python
# tests/server/test_routes_meta.py
import time
import json
import pytest
from pathlib import Path
from httpx import AsyncClient

from dit.core.store import ObjectStore
from dit.core.objects import (
    Commit, Manifest, ManifestEntry,
    serialize_commit, serialize_manifest,
    serialize_sidecar, Sidecar, SidecarEntry,
)
from dit.core.tree_builder import build_nested_tree
from dit.core.refs import RefStore


AUTH = {"Authorization": "Bearer test-token"}


async def _create_repo(client: AsyncClient, name: str = "meta-repo"):
    resp = await client.post("/api/v1/repos", json={"name": name}, headers=AUTH)
    assert resp.status_code == 201


async def _build_repo_with_sidecar(client: AsyncClient, tmp_path: Path, repo: str = "meta-repo"):
    """Create a repo with one committed manifest + sidecar. Returns (store, commit_hash, sidecar_hash)."""
    await _create_repo(client, repo)
    data_dir = tmp_path / "data"
    store = ObjectStore(data_dir / "repos" / repo / "objects")

    # Build row data: 3 rows
    row_hashes = []
    for i, content in enumerate(["hello world", "foo bar", "test data"]):
        row = json.dumps({"messages": [{"role": "user", "content": content}]})
        row_bytes = row.encode("utf-8")
        rh = store.write("rows", row_bytes)
        row_hashes.append(rh)

    manifest = Manifest(entries=[ManifestEntry(row_hash=rh, query_fingerprint=None) for rh in row_hashes])
    m_hash = store.write("manifests", serialize_manifest(manifest))

    sidecar_entries = [
        SidecarEntry(row_hash=row_hashes[0], char_count=11, token_estimate=2, field_count=1, lang="en"),
        SidecarEntry(row_hash=row_hashes[1], char_count=7, token_estimate=1, field_count=1, lang="en"),
        SidecarEntry(row_hash=row_hashes[2], char_count=9, token_estimate=2, field_count=1, lang="en"),
    ]
    sidecar = Sidecar(manifest_hash=m_hash, entries=sidecar_entries)
    sidecar_bytes = serialize_sidecar(sidecar)
    sc_hash = store.write("sidecars", sidecar_bytes)

    staged = {"train.jsonl": ("manifest", m_hash, sc_hash)}
    tree_hash = build_nested_tree(store, staged)
    commit = Commit(
        tree_hash=tree_hash,
        parent_hashes=[],
        author="tester",
        message="initial",
        timestamp=int(time.time()),
    )
    commit_hash = store.write("commits", serialize_commit(commit))

    refs = RefStore(data_dir / "repos" / repo)
    await client.post(
        f"/api/v1/repos/{repo}/refs/heads/main",
        json={"old": None, "new": commit_hash},
        headers=AUTH,
    )

    return store, commit_hash, sc_hash


class TestMetaCompute:
    async def test_compute_all(self, client: AsyncClient, tmp_path: Path):
        """POST /meta/compute without body computes sidecars for all files."""
        await _create_repo(client)
        data_dir = tmp_path / "data"
        store = ObjectStore(data_dir / "repos" / "meta-repo" / "objects")

        # Build manifest WITHOUT sidecar
        manifest = Manifest(entries=[
            ManifestEntry(row_hash="a" * 64, query_fingerprint=None),
        ])
        m_hash = store.write("manifests", serialize_manifest(manifest))
        # Write the row bytes so compute_sidecar can read them
        row_json = json.dumps({"messages": [{"role": "user", "content": "hi"}]})
        store.write("rows", row_json.encode("utf-8"))

        staged = {"train.jsonl": ("manifest", m_hash, None)}
        tree_hash = build_nested_tree(store, staged)
        commit = Commit(
            tree_hash=tree_hash, parent_hashes=[], author="t",
            message="initial", timestamp=int(time.time()),
        )
        commit_hash = store.write("commits", serialize_commit(commit))
        await client.post(
            "/api/v1/repos/meta-repo/refs/heads/main",
            json={"old": None, "new": commit_hash},
            headers=AUTH,
        )

        resp = await client.post(
            "/api/v1/repos/meta-repo/meta/compute",
            json={},
            headers=AUTH,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "commit_hash" in data
        assert "sidecars" in data
        assert len(data["sidecars"]) >= 1
        assert data["sidecars"][0]["file"] == "train.jsonl"
        assert data["sidecars"][0]["sidecar_hash"] is not None

    async def test_compute_idempotent(self, client: AsyncClient, tmp_path: Path):
        """Computing when all sidecars exist returns same commit and empty sidecars list."""
        store, commit_hash, sc_hash = await _build_repo_with_sidecar(client, tmp_path)

        resp = await client.post(
            "/api/v1/repos/meta-repo/meta/compute",
            json={},
            headers=AUTH,
        )
        assert resp.status_code == 200
        data = resp.json()
        # No new commit created — returns existing HEAD
        assert data["commit_hash"] == commit_hash
        assert data["sidecars"] == []

    async def test_compute_single_file(self, client: AsyncClient, tmp_path: Path):
        """POST /meta/compute with file= limits to that file."""
        await _create_repo(client, "meta-repo2")
        data_dir = tmp_path / "data"
        store = ObjectStore(data_dir / "repos" / "meta-repo2" / "objects")

        m1 = Manifest(entries=[ManifestEntry(row_hash="a" * 64, query_fingerprint=None)])
        m2 = Manifest(entries=[ManifestEntry(row_hash="b" * 64, query_fingerprint=None)])
        mh1 = store.write("manifests", serialize_manifest(m1))
        mh2 = store.write("manifests", serialize_manifest(m2))

        row1 = json.dumps({"messages": [{"role": "user", "content": "row1"}]})
        row2 = json.dumps({"messages": [{"role": "user", "content": "row2"}]})
        store.write("rows", row1.encode())
        store.write("rows", row2.encode())

        staged = {
            "train.jsonl": ("manifest", mh1, None),
            "eval.jsonl": ("manifest", mh2, None),
        }
        tree_hash = build_nested_tree(store, staged)
        commit = Commit(tree_hash=tree_hash, parent_hashes=[], author="t", message="v1", timestamp=int(time.time()))
        commit_hash = store.write("commits", serialize_commit(commit))
        await client.post(
            "/api/v1/repos/meta-repo2/refs/heads/main",
            json={"old": None, "new": commit_hash},
            headers=AUTH,
        )

        resp = await client.post(
            "/api/v1/repos/meta-repo2/meta/compute",
            json={"file": "train.jsonl"},
            headers=AUTH,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["sidecars"]) == 1
        assert data["sidecars"][0]["file"] == "train.jsonl"

    async def test_compute_repo_not_found(self, client: AsyncClient, tmp_path: Path):
        resp = await client.post(
            "/api/v1/repos/no-such-repo/meta/compute",
            json={},
            headers=AUTH,
        )
        assert resp.status_code == 404

    async def test_compute_no_head(self, client: AsyncClient, tmp_path: Path):
        """Repo with no commits returns 400."""
        await _create_repo(client, "empty-repo")
        resp = await client.post(
            "/api/v1/repos/empty-repo/meta/compute",
            json={},
            headers=AUTH,
        )
        assert resp.status_code == 400


class TestMetaGet:
    async def test_get_sidecar(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash, sc_hash = await _build_repo_with_sidecar(client, tmp_path)

        resp = await client.get(
            f"/api/v1/repos/meta-repo/meta/{commit_hash}/train.jsonl",
            headers=AUTH,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "entries" in data
        assert len(data["entries"]) == 3
        assert data["entries"][0]["char_count"] == 11
        assert data["entries"][0]["lang"] == "en"

    async def test_get_commit_not_found(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash, _ = await _build_repo_with_sidecar(client, tmp_path)
        resp = await client.get(
            f"/api/v1/repos/meta-repo/meta/{'z' * 64}/train.jsonl",
            headers=AUTH,
        )
        assert resp.status_code == 404

    async def test_get_file_no_sidecar(self, client: AsyncClient, tmp_path: Path):
        """File in tree without sidecar_hash returns 404."""
        await _create_repo(client, "nosidecar-repo")
        data_dir = tmp_path / "data"
        store = ObjectStore(data_dir / "repos" / "nosidecar-repo" / "objects")
        manifest = Manifest(entries=[ManifestEntry(row_hash="a" * 64, query_fingerprint=None)])
        mh = store.write("manifests", serialize_manifest(manifest))
        staged = {"train.jsonl": ("manifest", mh, None)}
        tree_hash = build_nested_tree(store, staged)
        commit = Commit(tree_hash=tree_hash, parent_hashes=[], author="t", message="v1", timestamp=int(time.time()))
        commit_hash = store.write("commits", serialize_commit(commit))
        await client.post(
            "/api/v1/repos/nosidecar-repo/refs/heads/main",
            json={"old": None, "new": commit_hash},
            headers=AUTH,
        )

        resp = await client.get(
            f"/api/v1/repos/nosidecar-repo/meta/{commit_hash}/train.jsonl",
            headers=AUTH,
        )
        assert resp.status_code == 404

    async def test_get_path_not_found(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash, _ = await _build_repo_with_sidecar(client, tmp_path)
        resp = await client.get(
            f"/api/v1/repos/meta-repo/meta/{commit_hash}/nonexistent.jsonl",
            headers=AUTH,
        )
        assert resp.status_code == 404


class TestMetaSummary:
    async def test_summary_basic(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash, _ = await _build_repo_with_sidecar(client, tmp_path)

        resp = await client.get(
            f"/api/v1/repos/meta-repo/meta/{commit_hash}/train.jsonl/summary",
            headers=AUTH,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["row_count"] == 3
        assert data["char_count"] == 11 + 7 + 9
        assert data["token_estimate"] == 2 + 1 + 2
        assert "avg_fields" in data
        assert "lang_distribution" in data
        assert data["lang_distribution"].get("en", 0) == 3

    async def test_summary_empty_manifest(self, client: AsyncClient, tmp_path: Path):
        """Empty sidecar returns zeros, not division-by-zero."""
        await _create_repo(client, "empty-sc-repo")
        data_dir = tmp_path / "data"
        store = ObjectStore(data_dir / "repos" / "empty-sc-repo" / "objects")

        m_hash = store.write("manifests", serialize_manifest(Manifest(entries=[])))
        sidecar = Sidecar(manifest_hash=m_hash, entries=[])
        sc_hash = store.write("sidecars", serialize_sidecar(sidecar))

        staged = {"empty.jsonl": ("manifest", m_hash, sc_hash)}
        tree_hash = build_nested_tree(store, staged)
        commit = Commit(tree_hash=tree_hash, parent_hashes=[], author="t", message="v1", timestamp=int(time.time()))
        commit_hash = store.write("commits", serialize_commit(commit))
        await client.post(
            "/api/v1/repos/empty-sc-repo/refs/heads/main",
            json={"old": None, "new": commit_hash},
            headers=AUTH,
        )

        resp = await client.get(
            f"/api/v1/repos/empty-sc-repo/meta/{commit_hash}/empty.jsonl/summary",
            headers=AUTH,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["row_count"] == 0
        assert data["char_count"] == 0
        assert data["avg_fields"] == 0.0


class TestMetaDiff:
    async def _build_two_commits(self, client: AsyncClient, tmp_path: Path, repo: str):
        await _create_repo(client, repo)
        data_dir = tmp_path / "data"
        store = ObjectStore(data_dir / "repos" / repo / "objects")

        def _make_sidecar(m_hash: str, rows: list[tuple[str, int]]) -> str:
            entries = [
                SidecarEntry(row_hash=rh, char_count=cc, token_estimate=cc // 4,
                             field_count=1, lang="en")
                for rh, cc in rows
            ]
            sc = Sidecar(manifest_hash=m_hash, entries=entries)
            return store.write("sidecars", serialize_sidecar(sc))

        m1 = Manifest(entries=[ManifestEntry(row_hash="a" * 64, query_fingerprint=None)])
        mh1 = store.write("manifests", serialize_manifest(m1))
        sc1 = _make_sidecar(mh1, [("a" * 64, 100)])

        m2 = Manifest(entries=[
            ManifestEntry(row_hash="a" * 64, query_fingerprint=None),
            ManifestEntry(row_hash="b" * 64, query_fingerprint=None),
        ])
        mh2 = store.write("manifests", serialize_manifest(m2))
        sc2 = _make_sidecar(mh2, [("a" * 64, 100), ("b" * 64, 200)])

        staged1 = {"train.jsonl": ("manifest", mh1, sc1)}
        staged2 = {"train.jsonl": ("manifest", mh2, sc2)}
        th1 = build_nested_tree(store, staged1)
        th2 = build_nested_tree(store, staged2)

        c1 = Commit(tree_hash=th1, parent_hashes=[], author="t", message="v1", timestamp=1000)
        h1 = store.write("commits", serialize_commit(c1))
        c2 = Commit(tree_hash=th2, parent_hashes=[h1], author="t", message="v2", timestamp=2000)
        h2 = store.write("commits", serialize_commit(c2))

        return store, h1, h2

    async def test_diff_basic(self, client: AsyncClient, tmp_path: Path):
        store, h1, h2 = await self._build_two_commits(client, tmp_path, "diff-meta-repo")

        resp = await client.get(
            f"/api/v1/repos/diff-meta-repo/meta/diff/{h1}/{h2}",
            headers=AUTH,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "files" in data
        assert len(data["files"]) == 1
        f = data["files"][0]
        assert f["path"] == "train.jsonl"
        assert f["new_stats"]["row_count"] == 2
        assert f["old_stats"]["row_count"] == 1
        assert f["delta"]["row_count"] == 1
        assert f["delta"]["token_estimate"] > 0

    async def test_diff_with_file_filter(self, client: AsyncClient, tmp_path: Path):
        store, h1, h2 = await self._build_two_commits(client, tmp_path, "diff-meta-repo2")

        resp = await client.get(
            f"/api/v1/repos/diff-meta-repo2/meta/diff/{h1}/{h2}?file=train.jsonl",
            headers=AUTH,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["files"]) == 1

    async def test_diff_commit_not_found(self, client: AsyncClient, tmp_path: Path):
        await _create_repo(client, "diff-meta-repo3")
        resp = await client.get(
            f"/api/v1/repos/diff-meta-repo3/meta/diff/{'z' * 64}/{'y' * 64}",
            headers=AUTH,
        )
        assert resp.status_code == 404
```

- [ ] **4.2** Run to confirm failure:

```bash
cd /Users/lxs/code/datahub && uv run pytest tests/server/test_routes_meta.py -v 2>&1 | head -30
```

Expected: `404` on all endpoints — router not registered yet.

- [ ] **4.3** Create `src/dit/server/routes/meta_api.py`:

```python
# src/dit/server/routes/meta_api.py
"""Sidecar metadata API endpoints.

Routes:
  POST /{repo}/meta/compute                               — compute sidecars, new commit
  GET  /{repo}/meta/{commit_hash}/{file_path:path}        — full sidecar JSON
  GET  /{repo}/meta/{commit_hash}/{file_path:path}/summary — aggregated stats
  GET  /{repo}/meta/diff/{old_commit}/{new_commit}        — delta stats

Note on FastAPI route ordering: /meta/diff/{old}/{new} must be registered BEFORE
/{commit_hash}/{file_path:path} to avoid the path converter swallowing "diff".
This is handled by the order of @router decorators in this file.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.auth import get_session, require_permission
from dit.server.models import Ref
from dit.server.routes._helpers import _get_repo

router = APIRouter(prefix="/api/v1/repos", tags=["meta"])


def _store_for_repo(request: Request, repo_name: str):
    from dit.core.store import ObjectStore
    data_dir: Path = request.app.state.data_dir
    return ObjectStore(Path(data_dir) / "repos" / repo_name / "objects")


def _sidecar_summary(sidecar) -> dict:
    """Compute aggregated stats from a Sidecar object."""
    row_count = len(sidecar.entries)
    if row_count == 0:
        return {
            "row_count": 0,
            "char_count": 0,
            "token_estimate": 0,
            "avg_fields": 0.0,
            "lang_distribution": {},
        }
    total_chars = sum(e.char_count for e in sidecar.entries)
    total_tokens = sum(e.token_estimate for e in sidecar.entries)
    avg_fields = sum(e.field_count for e in sidecar.entries) / row_count
    lang_counts: dict[str, int] = {}
    for e in sidecar.entries:
        k = e.lang or "unknown"
        lang_counts[k] = lang_counts.get(k, 0) + 1
    return {
        "row_count": row_count,
        "char_count": total_chars,
        "token_estimate": total_tokens,
        "avg_fields": round(avg_fields, 2),
        "lang_distribution": lang_counts,
    }


# ── POST /{repo}/meta/compute ────────────────────────────────────────────────

class MetaComputeRequest(BaseModel):
    file: Optional[str] = None  # if set, compute only this file


@router.post("/{repo}/meta/compute")
async def meta_compute(
    repo: str,
    body: MetaComputeRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("push")),
):
    """Compute sidecars for all manifests lacking one. Creates a new commit if anything changed."""
    from dit.core.objects import (
        deserialize_commit, serialize_commit, serialize_sidecar, Commit,
    )
    from dit.core.tree_walker import flatten_tree
    from dit.core.tree_builder import build_nested_tree
    from dit.core.sidecar import compute_sidecar

    r = await _get_repo(repo, session)
    store = _store_for_repo(request, repo)

    # Resolve HEAD
    result = await session.execute(
        select(Ref).where(Ref.repo_id == r.id, Ref.name == "heads/main")
    )
    ref_obj = result.scalar_one_or_none()
    if ref_obj is None:
        raise HTTPException(status_code=400, detail="Repository has no commits (no heads/main ref)")

    head_hash = ref_obj.target_hash
    commit_data = store.read("commits", head_hash)
    if commit_data is None:
        raise HTTPException(status_code=400, detail="HEAD commit not found in object store")

    head_commit = deserialize_commit(commit_data)
    flat = flatten_tree(store, head_commit.tree_hash)

    computed: list[dict] = []
    updated: dict[str, tuple] = {}

    for path, (obj_type, obj_hash, sidecar_hash) in flat.items():
        if obj_type != "manifest":
            updated[path] = (obj_type, obj_hash, sidecar_hash)
            continue
        if body.file is not None and path != body.file.lstrip("/"):
            updated[path] = (obj_type, obj_hash, sidecar_hash)
            continue
        if sidecar_hash is not None:
            updated[path] = (obj_type, obj_hash, sidecar_hash)
            continue

        sidecar = compute_sidecar(store, obj_hash)
        sidecar_bytes = serialize_sidecar(sidecar)
        new_sc_hash = store.write("sidecars", sidecar_bytes)
        updated[path] = (obj_type, obj_hash, new_sc_hash)
        computed.append({"file": path, "sidecar_hash": new_sc_hash})

    if not computed:
        # No new sidecars — return current HEAD unchanged
        return {"commit_hash": head_hash, "sidecars": []}

    new_tree_hash = build_nested_tree(store, updated)
    new_commit = Commit(
        tree_hash=new_tree_hash,
        parent_hashes=[head_hash],
        author="server",
        message="meta: compute sidecar metadata",
        timestamp=int(time.time()),
    )
    commit_bytes = serialize_commit(new_commit)
    new_commit_hash = store.write("commits", commit_bytes)

    # Update refs/heads/main via CAS
    from sqlalchemy import update as sa_update
    stmt = (
        sa_update(Ref)
        .where(Ref.repo_id == r.id, Ref.name == "heads/main", Ref.target_hash == head_hash)
        .values(target_hash=new_commit_hash)
        .execution_options(synchronize_session=False)
    )
    update_result = await session.execute(stmt)
    if update_result.rowcount == 0:
        raise HTTPException(status_code=409, detail="HEAD was updated concurrently — retry")
    await session.commit()

    return {"commit_hash": new_commit_hash, "sidecars": computed}


# ── GET /{repo}/meta/diff/{old_commit}/{new_commit} ──────────────────────────
# IMPORTANT: this route must be registered BEFORE the {commit_hash}/{file_path:path}
# route to avoid "diff" being parsed as a commit hash.

@router.get("/{repo}/meta/diff/{old_commit}/{new_commit}")
async def meta_diff(
    repo: str,
    old_commit: str,
    new_commit: str,
    request: Request,
    file: Optional[str] = Query(default=None, description="Filter to specific file"),
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("read")),
):
    """Return delta sidecar stats between two commits."""
    from dit.core.objects import deserialize_commit, deserialize_sidecar
    from dit.core.tree_walker import flatten_tree

    await _get_repo(repo, session)
    store = _store_for_repo(request, repo)

    def _load_flat(commit_hash: str) -> dict:
        data = store.read("commits", commit_hash)
        if data is None:
            raise HTTPException(status_code=404, detail=f"Commit {commit_hash[:8]} not found")
        commit = deserialize_commit(data)
        return flatten_tree(store, commit.tree_hash)

    old_flat = _load_flat(old_commit)
    new_flat = _load_flat(new_commit)

    all_paths = sorted(set(old_flat) | set(new_flat))
    if file:
        clean = file.lstrip("/")
        all_paths = [p for p in all_paths if p == clean]

    files = []
    for path in all_paths:
        old_entry = old_flat.get(path)
        new_entry = new_flat.get(path)

        if old_entry and old_entry[0] != "manifest":
            continue
        if new_entry and new_entry[0] != "manifest":
            continue

        def _get_summary(entry):
            if entry is None:
                return {"row_count": 0, "char_count": 0, "token_estimate": 0, "avg_fields": 0.0, "lang_distribution": {}}
            _, _, sc_hash = entry
            if sc_hash is None:
                return None
            sc_data = store.read("sidecars", sc_hash)
            if sc_data is None:
                return None
            return _sidecar_summary(deserialize_sidecar(sc_data))

        old_stats = _get_summary(old_entry)
        new_stats = _get_summary(new_entry)

        if old_stats is None or new_stats is None:
            continue
        if old_stats == new_stats:
            continue

        delta = {
            "row_count": new_stats["row_count"] - old_stats["row_count"],
            "char_count": new_stats["char_count"] - old_stats["char_count"],
            "token_estimate": new_stats["token_estimate"] - old_stats["token_estimate"],
        }

        files.append({
            "path": path,
            "old_stats": old_stats,
            "new_stats": new_stats,
            "delta": delta,
        })

    return {
        "old_commit": old_commit,
        "new_commit": new_commit,
        "files": files,
    }


# ── GET /{repo}/meta/{commit_hash}/{file_path:path}/summary ─────────────────
# Must be registered BEFORE the bare {file_path:path} route so FastAPI matches
# the "/summary" suffix first. Both are registered; the more specific one wins
# because FastAPI tries routes in registration order.

@router.get("/{repo}/meta/{commit_hash}/{file_path:path}/summary")
async def meta_summary(
    repo: str,
    commit_hash: str,
    file_path: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("read")),
):
    """Return aggregated sidecar stats for a file at the given commit."""
    from dit.core.objects import deserialize_commit, deserialize_sidecar
    from dit.core.tree_walker import flatten_tree

    await _get_repo(repo, session)
    store = _store_for_repo(request, repo)

    commit_data = store.read("commits", commit_hash)
    if commit_data is None:
        raise HTTPException(status_code=404, detail="Commit not found")

    commit = deserialize_commit(commit_data)
    flat = flatten_tree(store, commit.tree_hash)

    # FastAPI's path converter consumes the literal "/summary" suffix —
    # file_path will be e.g. "train.jsonl", NOT "train.jsonl/summary".
    # Do NOT strip "/summary" here; it is already excluded by the route pattern.
    clean = file_path.lstrip("/")

    if clean not in flat:
        raise HTTPException(status_code=404, detail=f"Path '{clean}' not found")

    obj_type, obj_hash, sidecar_hash = flat[clean]
    if obj_type != "manifest":
        raise HTTPException(status_code=404, detail=f"'{clean}' is not a manifest")
    if sidecar_hash is None:
        raise HTTPException(status_code=404, detail=f"No sidecar for '{clean}'")

    sc_data = store.read("sidecars", sidecar_hash)
    if sc_data is None:
        raise HTTPException(status_code=404, detail="Sidecar object missing from store")

    sidecar = deserialize_sidecar(sc_data)
    return _sidecar_summary(sidecar)


# ── GET /{repo}/meta/{commit_hash}/{file_path:path} ──────────────────────────

@router.get("/{repo}/meta/{commit_hash}/{file_path:path}")
async def meta_get(
    repo: str,
    commit_hash: str,
    file_path: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("read")),
):
    """Return full sidecar JSON for a file at the given commit."""
    from dit.core.objects import deserialize_commit, deserialize_sidecar
    from dit.core.tree_walker import flatten_tree

    await _get_repo(repo, session)
    store = _store_for_repo(request, repo)

    commit_data = store.read("commits", commit_hash)
    if commit_data is None:
        raise HTTPException(status_code=404, detail="Commit not found")

    commit = deserialize_commit(commit_data)
    flat = flatten_tree(store, commit.tree_hash)

    clean = file_path.lstrip("/")
    if clean not in flat:
        raise HTTPException(status_code=404, detail=f"Path '{clean}' not found")

    obj_type, obj_hash, sidecar_hash = flat[clean]
    if obj_type != "manifest":
        raise HTTPException(status_code=404, detail=f"'{clean}' is not a manifest")
    if sidecar_hash is None:
        raise HTTPException(status_code=404, detail=f"No sidecar computed for '{clean}'")

    sc_data = store.read("sidecars", sidecar_hash)
    if sc_data is None:
        raise HTTPException(status_code=404, detail="Sidecar object missing from store")

    sidecar = deserialize_sidecar(sc_data)
    return {
        "commit_hash": commit_hash,
        "path": clean,
        "manifest_hash": sidecar.manifest_hash,
        "entries": [
            {
                "row_hash": e.row_hash,
                "char_count": e.char_count,
                "token_estimate": e.token_estimate,
                "field_count": e.field_count,
                "lang": e.lang,
            }
            for e in sidecar.entries
        ],
    }
```

- [ ] **4.4** Register the router in `src/dit/server/app.py`. Add after the diff_api router:

```python
# src/dit/server/app.py — add after diff_api_router include

    from dit.server.routes.meta_api import router as meta_router
    application.include_router(meta_router)
```

- [ ] **4.5** Run the meta server tests:

```bash
cd /Users/lxs/code/datahub && uv run pytest tests/server/test_routes_meta.py -v
```

Expected: all tests pass.

  **Route registration order — mandatory verification:** Before running, confirm that
  the `@router.get`/`@router.post` decorators appear in exactly this order in
  `meta_api.py`:
  1. `meta_diff` (`/{repo}/meta/diff/{old_commit}/{new_commit}`)
  2. `meta_summary` (`/{repo}/meta/{commit_hash}/{file_path:path}/summary`)
  3. `meta_get` (`/{repo}/meta/{commit_hash}/{file_path:path}`)

  FastAPI evaluates `{file_path:path}` routes in registration order. The literal
  `/summary` suffix in route 2 must be seen before the greedy `:path` route 3, and the
  literal `diff` segment in route 1 must be seen before the `{commit_hash}` capture in
  route 3. The code above already registers them in this order; do not reorder them.

  **If the `/summary` tests fail with wrong JSON shape:** Check that the `/summary`
  strip was NOT re-introduced — `file_path` received by `meta_summary` will already be
  the clean filename (e.g. `train.jsonl`), not `train.jsonl/summary`.

- [ ] **4.6** Run full server test suite to confirm no regressions:

```bash
cd /Users/lxs/code/datahub && uv run pytest tests/server/ -v 2>&1 | tail -20
```

- [ ] **4.7** Commit:

```bash
cd /Users/lxs/code/datahub && git add src/dit/server/routes/meta_api.py src/dit/server/app.py tests/server/test_routes_meta.py && git commit -m "feat: server meta API — compute, get, summary, diff endpoints"
```

---

## Task 5: Push Upload Order — Add Sidecars

**Files:**
- `src/dit/cli/main.py`
- `tests/test_push_sidecar.py` (new)

### Steps

- [ ] **5.1** Create `tests/test_push_sidecar.py`:

```python
# tests/test_push_sidecar.py
"""Verify that push includes sidecars in the correct upload order."""
import os
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, call
from typer.testing import CliRunner

from dit.cli.main import app
from dit.core.store import ObjectStore
from dit.core.refs import RefStore
from dit.core.objects import (
    Commit, Manifest, ManifestEntry, Sidecar, SidecarEntry, Tree, TreeEntry,
    serialize_commit, serialize_manifest, serialize_sidecar, serialize_tree,
)

runner = CliRunner()


class TestPushUploadOrder:
    def _build_repo_with_sidecar(self, tmp_path: Path):
        """Create a repo dir with one committed manifest + sidecar, return dot path."""
        dot = tmp_path / ".datahub"
        dot.mkdir()
        (dot / "objects").mkdir()
        refs = RefStore(dot)
        refs.init()
        store = ObjectStore(dot / "objects")

        manifest = Manifest(entries=[ManifestEntry(row_hash="a" * 64, query_fingerprint=None)])
        m_hash = store.write("manifests", serialize_manifest(manifest))

        sidecar_entries = [
            SidecarEntry(row_hash="a" * 64, char_count=10, token_estimate=2, field_count=1, lang="en")
        ]
        sidecar = Sidecar(manifest_hash=m_hash, entries=sidecar_entries)
        sc_hash = store.write("sidecars", serialize_sidecar(sidecar))

        row_data = json.dumps({"messages": [{"role": "user", "content": "hello"}]}).encode()
        store.write("rows", row_data)

        tree = Tree(entries=[TreeEntry(name="train.jsonl", obj_type="manifest", obj_hash=m_hash, sidecar_hash=sc_hash)])
        tree_hash = store.write("trees", serialize_tree(tree))

        commit = Commit(tree_hash=tree_hash, parent_hashes=[], author="t", message="v1", timestamp=1000)
        commit_hash = store.write("commits", serialize_commit(commit))

        refs.set_branch("main", commit_hash)
        (dot / "HEAD").write_text("ref:main\n")

        # Write remote config
        import json as _json
        config = {"remotes": {"origin": {"url": "http://localhost:9999/owner/testrepo", "token": "tok"}}}
        (dot / "config.json").write_text(_json.dumps(config))

        return dot, store, commit_hash, sc_hash

    def test_push_upload_order_includes_sidecars(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot, store, commit_hash, sc_hash = self._build_repo_with_sidecar(tmp_path)

        uploaded_types: list[str] = []

        def fake_batch_exists(obj_type, hashes):
            return {h: False for h in hashes}

        def fake_upload(obj_type, hash_hex, data):
            uploaded_types.append(obj_type)

        def fake_get_ref(ref_type, name):
            return None

        def fake_cas_ref(ref_type, name, old, new):
            return True

        mock_rc = MagicMock()
        mock_rc.get_ref.side_effect = fake_get_ref
        mock_rc.batch_exists.side_effect = fake_batch_exists
        mock_rc.upload_object.side_effect = fake_upload
        mock_rc.cas_ref.side_effect = fake_cas_ref

        with patch("dit.cli.main._build_remote_client", return_value=mock_rc):
            result = runner.invoke(app, ["push", "--remote", "origin", "--branch", "main"])

        assert result.exit_code == 0, result.output

        # Sidecars must appear in the uploaded types
        assert "sidecars" in uploaded_types, f"'sidecars' not in upload sequence: {uploaded_types}"

        # Verify ordering: manifests before sidecars, sidecars before trees
        if "manifests" in uploaded_types and "sidecars" in uploaded_types:
            assert uploaded_types.index("manifests") < uploaded_types.index("sidecars"), \
                "manifests must be uploaded before sidecars"
        if "sidecars" in uploaded_types and "trees" in uploaded_types:
            assert uploaded_types.index("sidecars") < uploaded_types.index("trees"), \
                "sidecars must be uploaded before trees"
        if "trees" in uploaded_types and "commits" in uploaded_types:
            assert uploaded_types.index("trees") < uploaded_types.index("commits"), \
                "trees must be uploaded before commits"

    def test_push_batch_exists_called_for_sidecars(self, tmp_path, monkeypatch):
        """batch_exists is called with obj_type='sidecars' during push."""
        monkeypatch.chdir(tmp_path)
        dot, store, commit_hash, sc_hash = self._build_repo_with_sidecar(tmp_path)

        batch_exists_calls: list[str] = []

        def fake_batch_exists(obj_type, hashes):
            batch_exists_calls.append(obj_type)
            return {h: False for h in hashes}

        mock_rc = MagicMock()
        mock_rc.get_ref.return_value = None
        mock_rc.batch_exists.side_effect = fake_batch_exists
        mock_rc.upload_object.return_value = None
        mock_rc.cas_ref.return_value = True

        with patch("dit.cli.main._build_remote_client", return_value=mock_rc):
            runner.invoke(app, ["push", "--remote", "origin", "--branch", "main"])

        assert "sidecars" in batch_exists_calls, \
            f"batch_exists was not called for sidecars. Calls: {batch_exists_calls}"
```

- [ ] **5.2** Run to confirm failure (upload_order doesn't include "sidecars" yet):

```bash
cd /Users/lxs/code/datahub && uv run pytest tests/test_push_sidecar.py -v 2>&1 | tail -20
```

- [ ] **5.3** Update the `push` command in `src/dit/cli/main.py`. Find the
  `upload_order = ["rows", "manifests", "trees", "commits"]` line (around line 1074)
  and change it to:

```python
# src/dit/cli/main.py — inside push() command, replace upload_order line

    upload_order = ["rows", "manifests", "sidecars", "trees", "commits"]
```

- [ ] **5.4** Run push tests:

```bash
cd /Users/lxs/code/datahub && uv run pytest tests/test_push_sidecar.py -v
```

Expected: 2 passed.

- [ ] **5.5** Confirm existing push/pull tests still pass:

```bash
cd /Users/lxs/code/datahub && uv run pytest tests/ -k "push or pull or remote" -v
```

- [ ] **5.6** Commit:

```bash
cd /Users/lxs/code/datahub && git add src/dit/cli/main.py tests/test_push_sidecar.py && git commit -m "fix: push upload_order includes sidecars between manifests and trees"
```

---

## Task 6: Clone and `_fetch_objects_since` Sidecar Support

**Files:**
- `src/dit/cli/main.py`
- `tests/test_clone_sidecar.py` (new)

### Steps

- [ ] **6.1** Create `tests/test_clone_sidecar.py`:

```python
# tests/test_clone_sidecar.py
"""Verify clone and _fetch_objects_since download sidecar objects."""
import json
import time
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from typer.testing import CliRunner

from dit.cli.main import app, _fetch_objects_since
from dit.core.store import ObjectStore
from dit.core.objects import (
    Commit, Manifest, ManifestEntry, Sidecar, SidecarEntry, Tree, TreeEntry,
    serialize_commit, serialize_manifest, serialize_sidecar, serialize_tree,
    deserialize_commit, deserialize_sidecar,
)

runner = CliRunner()


def _build_remote_objects():
    """Return (objects_dict, commit_hash, sidecar_hash).

    objects_dict: obj_type -> hash -> bytes, simulating a remote store.
    """
    objects: dict[str, dict[str, bytes]] = {
        "rows": {}, "manifests": {}, "sidecars": {}, "trees": {}, "commits": {},
    }

    def _write(obj_type: str, data: bytes) -> str:
        from dit.core.objects import object_hash
        h = object_hash(data)
        objects[obj_type][h] = data
        return h

    row_data = json.dumps({"messages": [{"role": "user", "content": "hello"}]}).encode()
    row_hash = _write("rows", row_data)

    manifest = Manifest(entries=[ManifestEntry(row_hash=row_hash, query_fingerprint=None)])
    m_hash = _write("manifests", serialize_manifest(manifest))

    sidecar = Sidecar(
        manifest_hash=m_hash,
        entries=[SidecarEntry(row_hash=row_hash, char_count=5, token_estimate=1, field_count=1, lang="en")],
    )
    sc_hash = _write("sidecars", serialize_sidecar(sidecar))

    tree = Tree(entries=[
        TreeEntry(name="train.jsonl", obj_type="manifest", obj_hash=m_hash, sidecar_hash=sc_hash)
    ])
    tree_hash = _write("trees", serialize_tree(tree))

    commit = Commit(
        tree_hash=tree_hash, parent_hashes=[], author="t",
        message="initial", timestamp=int(time.time()),
    )
    commit_hash = _write("commits", serialize_commit(commit))

    return objects, commit_hash, sc_hash


class TestCloneSidecar:
    def test_clone_downloads_sidecar(self, tmp_path, monkeypatch):
        """Clone fetches sidecar objects referenced by tree entries."""
        objects, commit_hash, sc_hash = _build_remote_objects()

        def fake_download(obj_type, hash_hex):
            return objects.get(obj_type, {}).get(hash_hex)

        def fake_get_ref(ref_type, name):
            if name == "main":
                return commit_hash
            return None

        mock_rc = MagicMock()
        mock_rc.get_ref.side_effect = fake_get_ref
        mock_rc.download_object.side_effect = fake_download

        dest = tmp_path / "cloned"
        with patch("dit.cli.main.RemoteClient", return_value=mock_rc):
            result = runner.invoke(
                app,
                ["clone", "http://fake:9999/owner/repo", str(dest), "--token", "tok"],
            )

        assert result.exit_code == 0, result.output

        local_store = ObjectStore(dest / ".datahub" / "objects")
        assert local_store.read("sidecars", sc_hash) is not None, \
            "Sidecar object should have been downloaded during clone"

    def test_clone_sidecar_missing_is_nonfatal(self, tmp_path, monkeypatch):
        """Clone proceeds even if sidecar download returns None."""
        objects, commit_hash, sc_hash = _build_remote_objects()

        def fake_download(obj_type, hash_hex):
            if obj_type == "sidecars":
                return None  # simulate missing sidecar on remote
            return objects.get(obj_type, {}).get(hash_hex)

        def fake_get_ref(ref_type, name):
            if name == "main":
                return commit_hash
            return None

        mock_rc = MagicMock()
        mock_rc.get_ref.side_effect = fake_get_ref
        mock_rc.download_object.side_effect = fake_download

        dest = tmp_path / "cloned2"
        with patch("dit.cli.main.RemoteClient", return_value=mock_rc):
            result = runner.invoke(
                app,
                ["clone", "http://fake:9999/owner/repo", str(dest), "--token", "tok"],
            )

        # Must succeed even though sidecar is missing
        assert result.exit_code == 0, result.output
        local_store = ObjectStore(dest / ".datahub" / "objects")
        # Sidecar is missing — that's OK
        assert local_store.read("sidecars", sc_hash) is None


class TestFetchObjectsSince:
    def test_fetch_downloads_sidecar(self, tmp_path):
        """_fetch_objects_since fetches sidecar when tree entry has sidecar_hash."""
        objects, commit_hash, sc_hash = _build_remote_objects()

        local_store = ObjectStore(tmp_path / "objects")

        def fake_download(obj_type, hash_hex):
            return objects.get(obj_type, {}).get(hash_hex)

        mock_rc = MagicMock()
        mock_rc.download_object.side_effect = fake_download

        downloaded, manifest_hashes = _fetch_objects_since(mock_rc, local_store, commit_hash, stop_at=None)

        assert local_store.read("sidecars", sc_hash) is not None, \
            "_fetch_objects_since should download sidecar referenced by tree entry"

    def test_fetch_sidecar_missing_is_nonfatal(self, tmp_path):
        """_fetch_objects_since continues if sidecar download returns None."""
        objects, commit_hash, sc_hash = _build_remote_objects()

        local_store = ObjectStore(tmp_path / "objects")

        def fake_download(obj_type, hash_hex):
            if obj_type == "sidecars":
                return None
            return objects.get(obj_type, {}).get(hash_hex)

        mock_rc = MagicMock()
        mock_rc.download_object.side_effect = fake_download

        # Should not raise
        downloaded, manifest_hashes = _fetch_objects_since(mock_rc, local_store, commit_hash, stop_at=None)

        # Commit + tree + manifest + row downloaded (sidecar skipped gracefully)
        assert downloaded > 0
```

- [ ] **6.2** Run to confirm failure:

```bash
cd /Users/lxs/code/datahub && uv run pytest tests/test_clone_sidecar.py -v 2>&1 | tail -30
```

Expected: `test_clone_downloads_sidecar` fails — sidecar not fetched.

- [ ] **6.3** Update the `clone` command in `src/dit/cli/main.py`. The current tree
  traversal (lines 1168–1177) only walks root-level tree entries and silently ignores
  subtrees, so repos with directory structure (e.g. `data/train.jsonl`) never have
  their manifests or sidecars downloaded. Replace the flat loop with a recursive helper
  `_clone_tree_objects` that descends into subtrees:

```python
# src/dit/cli/main.py — add helper before or inside clone(), then replace the
# per-commit tree traversal block

def _clone_tree_objects(
    rc: "RemoteClient",
    store: ObjectStore,
    tree_hash: str,
    manifest_hashes: set,
):
    """Recursively download all manifest, sidecar, and subtree objects for a tree hash."""
    from dit.core.objects import deserialize_tree, deserialize_manifest

    tree_data = rc.download_object("trees", tree_hash)
    if not tree_data:
        return
    store.write("trees", tree_data)
    tree = deserialize_tree(tree_data)

    for entry in tree.entries:
        if entry.obj_type == "manifest":
            m_data = rc.download_object("manifests", entry.obj_hash)
            if m_data:
                store.write("manifests", m_data)
                manifest_hashes.add(entry.obj_hash)
            # Download sidecar if present (non-fatal if missing on remote)
            if entry.sidecar_hash and not store.exists("sidecars", entry.sidecar_hash):
                sc_data = rc.download_object("sidecars", entry.sidecar_hash)
                if sc_data:
                    store.write("sidecars", sc_data)
                else:
                    typer.echo(
                        f"  warning: sidecar {entry.sidecar_hash[:8]} not found on remote (skipped)",
                        err=True,
                    )
        elif entry.obj_type == "tree":
            # Recurse into subtree
            _clone_tree_objects(rc, store, entry.obj_hash, manifest_hashes)
```

  Then replace the inner tree-fetch loop in `clone` (the block starting at ~line 1164)
  to call this helper instead of doing a flat walk:

```python
# src/dit/cli/main.py — inside clone(), replace the per-commit tree block
# (was: tree_data = rc.download_object("trees", commit.tree_hash); flat loop over entries)

    manifest_hashes: set[str] = set()
    for chash in commits_to_fetch:
        commit_data = store.read("commits", chash)
        commit = deserialize_commit(commit_data)
        _clone_tree_objects(rc, store, commit.tree_hash, manifest_hashes)
```

  The second tree traversal in `clone` (the materialize-HEAD pass, ~line 1196–1203)
  reads from the **local** store and does not download — no changes needed there.

- [ ] **6.4** Update `_fetch_objects_since` in `src/dit/cli/main.py`. The current inner
  loop (lines 1245–1258) only iterates root-level tree entries, missing nested subtrees.
  Replace it with a call to a recursive helper `_fetch_tree_objects` that descends into
  subtrees. Add the helper alongside `_clone_tree_objects` (or reuse it if the signatures
  align), then replace the flat traversal inside `_fetch_objects_since`:

```python
# src/dit/cli/main.py — add helper (can share file scope with _clone_tree_objects)

def _fetch_tree_objects(
    rc: "RemoteClient",
    store: ObjectStore,
    tree_hash: str,
    manifest_hashes: set,
) -> int:
    """Recursively download manifest, sidecar, row, and subtree objects. Returns count downloaded."""
    from dit.core.objects import deserialize_tree, deserialize_manifest

    downloaded = 0
    if store.exists("trees", tree_hash):
        # Tree already local — still need to inspect it for missing manifests/sidecars
        from dit.core.objects import deserialize_tree as _dt
        tree_data = store.read("trees", tree_hash)
        if tree_data is None:
            return 0
        tree = _dt(tree_data)
    else:
        tree_data = rc.download_object("trees", tree_hash)
        if not tree_data:
            return 0
        store.write("trees", tree_data)
        downloaded += 1
        tree = deserialize_tree(tree_data)

    for entry in tree.entries:
        if entry.obj_type == "manifest":
            if not store.exists("manifests", entry.obj_hash):
                m_data = rc.download_object("manifests", entry.obj_hash)
                if m_data:
                    store.write("manifests", m_data)
                    downloaded += 1
                    manifest_hashes.add(entry.obj_hash)
                    m = deserialize_manifest(m_data)
                    for me in m.entries:
                        if not store.exists("rows", me.row_hash):
                            row_data = rc.download_object("rows", me.row_hash)
                            if row_data:
                                store.write("rows", row_data)
                                downloaded += 1
            # Download sidecar if referenced (non-fatal if missing on remote)
            if entry.sidecar_hash and not store.exists("sidecars", entry.sidecar_hash):
                sc_data = rc.download_object("sidecars", entry.sidecar_hash)
                if sc_data:
                    store.write("sidecars", sc_data)
                    downloaded += 1
        elif entry.obj_type == "tree":
            # Recurse into subtree
            downloaded += _fetch_tree_objects(rc, store, entry.obj_hash, manifest_hashes)

    return downloaded
```

  Then replace the flat tree block inside `_fetch_objects_since` (~lines 1239–1258)
  to call `_fetch_tree_objects`:

```python
# src/dit/cli/main.py — inside _fetch_objects_since(), replace the tree block

        if not store.exists("trees", commit.tree_hash):
            downloaded += _fetch_tree_objects(rc, store, commit.tree_hash, manifest_hashes)
        else:
            # Tree already local but may have new manifests/sidecars from subtrees
            downloaded += _fetch_tree_objects(rc, store, commit.tree_hash, manifest_hashes)
```

  (The two branches are identical since `_fetch_tree_objects` handles the local-exists
  check internally. Simplify to a single call:)

```python
# Simplified form:
        downloaded += _fetch_tree_objects(rc, store, commit.tree_hash, manifest_hashes)
```

- [ ] **6.5** Run the clone sidecar tests:

```bash
cd /Users/lxs/code/datahub && uv run pytest tests/test_clone_sidecar.py -v
```

Expected: 4 passed.

- [ ] **6.6** Run the full test suite to verify no regressions:

```bash
cd /Users/lxs/code/datahub && uv run pytest tests/ -v 2>&1 | tail -30
```

Expected: all tests pass.

- [ ] **6.7** Commit:

```bash
cd /Users/lxs/code/datahub && git add src/dit/cli/main.py tests/test_clone_sidecar.py && git commit -m "feat: clone and fetch_objects_since download sidecar objects (non-fatal if missing)"
```

---

## Final Verification

- [ ] **7.1** Run the complete test suite one final time:

```bash
cd /Users/lxs/code/datahub && uv run pytest tests/ -v 2>&1 | tail -30
```

Expected: all tests pass, zero failures.

- [ ] **7.2** Confirm all new server routes are registered:

```bash
cd /Users/lxs/code/datahub && uv run python -c "
from dit.server.app import create_app
app = create_app()
meta_routes = [(list(r.methods)[0] if hasattr(r, 'methods') else '?', r.path)
               for r in app.routes if hasattr(r, 'path') and 'meta' in r.path]
for m, p in sorted(meta_routes, key=lambda x: x[1]):
    print(m, p)
"
```

Expected output:
```
GET /api/v1/repos/{repo}/meta/diff/{old_commit}/{new_commit}
POST /api/v1/repos/{repo}/meta/compute
GET /api/v1/repos/{repo}/meta/{commit_hash}/{file_path}
GET /api/v1/repos/{repo}/meta/{commit_hash}/{file_path}/summary
```

- [ ] **7.3** Confirm `dit meta` subcommand group appears in CLI help:

```bash
cd /Users/lxs/code/datahub && uv run dit meta --help
```

Expected: shows `compute`, `show`, `diff` subcommands.

- [ ] **7.4** Confirm push upload order:

```bash
grep -n "upload_order" /Users/lxs/code/datahub/src/dit/cli/main.py
```

Expected: `["rows", "manifests", "sidecars", "trees", "commits"]`.

- [ ] **7.5** Confirm no uncommitted changes:

```bash
cd /Users/lxs/code/datahub && git status
```

Expected: `nothing to commit, working tree clean`.
