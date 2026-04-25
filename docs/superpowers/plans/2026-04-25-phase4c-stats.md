# Phase 4C: Stats Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `dit stats` CLI command, a `GET /{repo}/stats/{commit_hash}` server endpoint, a Gateway proxy handler, and a Vue stats panel in `DataRepoHome.vue`. Stats are aggregated on-the-fly from existing sidecar objects — no new storage type.

**Architecture:** `core/stats.py` is a pure module with two functions: `repo_stats()` aggregates sidecar data for all manifest files in one commit; `compare_stats()` diffs two commits. The CLI, server endpoint, and Gateway proxy all delegate to `core/stats.py`. The Vue panel loads lazily when expanded.

**Tech Stack:** Python 3.12, FastAPI, Typer, pytest + typer.testing + httpx AsyncClient; Go 1.21, chi router; Vue 3 Options API with Fomantic-UI CSS.

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `src/dit/core/stats.py` | Create | `repo_stats()` and `compare_stats()` pure functions |
| `src/dit/cli/main.py` | Modify | Add `stats` command under existing app |
| `src/dit/server/routes/stats_api.py` | Create | `GET /{repo}/stats/{commit_hash}` FastAPI endpoint |
| `src/dit/server/app.py` | Modify | Register `stats_router` |
| `tests/test_stats.py` | Create | Unit tests for `core/stats.py` |
| `tests/test_cli_stats.py` | Create | CLI integration tests for `dit stats` |
| `tests/server/test_routes_stats.py` | Create | Server route tests |
| `datahub-gateway/modules/dit/client.go` | Modify | Add `GetStats()` client method |
| `datahub-gateway/routers/api/v1/repo/dit.go` | Modify | Add `DatahubGetStats()` handler |
| `datahub-gateway/routers/api/v1/api.go` | Modify | Register route in dit group |
| `datahub-gateway/web_src/js/components/DataRepoHome.vue` | Modify | Add collapsible stats panel |

---

## Task 1: Core stats module

**Files:**
- Create: `src/dit/core/stats.py`
- Create: `tests/test_stats.py`

`repo_stats()` reads a commit, flattens the tree, collects sidecar summaries for all manifest entries, and returns a plain dict. `compare_stats()` calls `repo_stats()` twice and computes per-file deltas.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_stats.py`:

```python
import json
import time
from pathlib import Path

import pytest

from dit.core.objects import (
    Commit, Manifest, ManifestEntry,
    Sidecar, SidecarEntry,
    serialize_commit, serialize_manifest, serialize_sidecar,
)
from dit.core.store import ObjectStore
from dit.core.tree_builder import build_nested_tree
from dit.core.stats import repo_stats, compare_stats


def _build_repo(tmp_path: Path) -> tuple[ObjectStore, str]:
    """One commit with two manifest files; train.jsonl has a sidecar, eval.jsonl does not."""
    store = ObjectStore(tmp_path / "objects")

    # train.jsonl — 3 rows, with sidecar
    rows_train = [
        json.dumps({"instruction": "hello", "response": "world"}),
        json.dumps({"instruction": "foo", "response": "bar"}),
        json.dumps({"instruction": "baz", "response": "qux"}),
    ]
    train_entries = []
    for r in rows_train:
        rh = store.write("rows", r.encode("utf-8"))
        train_entries.append(ManifestEntry(row_hash=rh, query_fingerprint=None))
    train_manifest = Manifest(entries=train_entries)
    train_mh = store.write("manifests", serialize_manifest(train_manifest))

    sc_entries = [
        SidecarEntry(row_hash=e.row_hash, char_count=40, token_estimate=10, field_count=2, lang="en")
        for e in train_entries
    ]
    train_sidecar = Sidecar(manifest_hash=train_mh, entries=sc_entries)
    train_sc_hash = store.write("sidecars", serialize_sidecar(train_sidecar))

    # eval.jsonl — 1 row, no sidecar
    eval_row = json.dumps({"instruction": "hi", "response": "hey"})
    eval_rh = store.write("rows", eval_row.encode("utf-8"))
    eval_manifest = Manifest(entries=[ManifestEntry(row_hash=eval_rh, query_fingerprint=None)])
    eval_mh = store.write("manifests", serialize_manifest(eval_manifest))

    tree_entries = {
        "train.jsonl": ("manifest", train_mh, train_sc_hash),
        "eval.jsonl": ("manifest", eval_mh, None),
    }
    tree_hash = build_nested_tree(store, tree_entries)

    commit = Commit(
        tree_hash=tree_hash,
        parent_hashes=[],
        author="tester",
        message="initial",
        timestamp=int(time.time()),
    )
    commit_hash = store.write("commits", serialize_commit(commit))
    return store, commit_hash


def _build_second_commit(store: ObjectStore, parent_hash: str) -> str:
    """Second commit: train.jsonl grows to 5 rows (new sidecar), eval.jsonl unchanged (no sidecar)."""
    from dit.core.objects import deserialize_commit
    from dit.core.tree_walker import flatten_tree

    parent_data = store.read("commits", parent_hash)
    parent_commit = deserialize_commit(parent_data)
    old_flat = flatten_tree(store, parent_commit.tree_hash)

    # New train.jsonl with 5 rows
    new_rows = [
        json.dumps({"instruction": f"q{i}", "response": f"a{i}"})
        for i in range(5)
    ]
    new_entries = []
    for r in new_rows:
        rh = store.write("rows", r.encode("utf-8"))
        new_entries.append(ManifestEntry(row_hash=rh, query_fingerprint=None))
    new_manifest = Manifest(entries=new_entries)
    new_mh = store.write("manifests", serialize_manifest(new_manifest))

    sc2_entries = [
        SidecarEntry(row_hash=e.row_hash, char_count=30, token_estimate=7, field_count=2, lang="en")
        for e in new_entries
    ]
    new_sidecar = Sidecar(manifest_hash=new_mh, entries=sc2_entries)
    new_sc_hash = store.write("sidecars", serialize_sidecar(new_sidecar))

    # Keep eval.jsonl from old commit
    _, eval_mh, _ = old_flat["eval.jsonl"]

    tree_entries = {
        "train.jsonl": ("manifest", new_mh, new_sc_hash),
        "eval.jsonl": ("manifest", eval_mh, None),
    }
    tree_hash = build_nested_tree(store, tree_entries)

    commit = Commit(
        tree_hash=tree_hash,
        parent_hashes=[parent_hash],
        author="tester",
        message="second",
        timestamp=int(time.time()),
    )
    return store.write("commits", serialize_commit(commit))


class TestRepoStats:
    def test_returns_commit_hash(self, tmp_path: Path):
        store, commit_hash = _build_repo(tmp_path)
        result = repo_stats(store, commit_hash)
        assert result["commit_hash"] == commit_hash

    def test_files_list_has_correct_count(self, tmp_path: Path):
        store, commit_hash = _build_repo(tmp_path)
        result = repo_stats(store, commit_hash)
        assert len(result["files"]) == 2

    def test_file_with_sidecar_has_stats(self, tmp_path: Path):
        store, commit_hash = _build_repo(tmp_path)
        result = repo_stats(store, commit_hash)
        files_by_path = {f["path"]: f for f in result["files"]}
        train = files_by_path["train.jsonl"]
        assert train["has_sidecar"] is True
        assert train["row_count"] == 3
        assert train["char_count"] == 120       # 3 rows * 40 chars
        assert train["token_estimate"] == 30    # 3 rows * 10 tokens
        assert train["avg_fields"] == pytest.approx(2.0, rel=0.01)
        assert train["lang_distribution"] == {"en": 3}

    def test_file_without_sidecar_has_none_fields(self, tmp_path: Path):
        store, commit_hash = _build_repo(tmp_path)
        result = repo_stats(store, commit_hash)
        files_by_path = {f["path"]: f for f in result["files"]}
        eval_f = files_by_path["eval.jsonl"]
        assert eval_f["has_sidecar"] is False
        assert eval_f["row_count"] is None
        assert eval_f["char_count"] is None
        assert eval_f["token_estimate"] is None
        assert eval_f["avg_fields"] is None
        assert eval_f["lang_distribution"] is None

    def test_totals_count_all_files(self, tmp_path: Path):
        store, commit_hash = _build_repo(tmp_path)
        result = repo_stats(store, commit_hash)
        assert result["totals"]["file_count"] == 2

    def test_totals_count_only_sidecar_files(self, tmp_path: Path):
        store, commit_hash = _build_repo(tmp_path)
        result = repo_stats(store, commit_hash)
        assert result["totals"]["files_with_sidecar"] == 1

    def test_totals_aggregate_only_sidecar_files(self, tmp_path: Path):
        store, commit_hash = _build_repo(tmp_path)
        result = repo_stats(store, commit_hash)
        totals = result["totals"]
        assert totals["row_count"] == 3
        assert totals["char_count"] == 120
        assert totals["token_estimate"] == 30
        assert totals["lang_distribution"] == {"en": 3}

    def test_path_prefix_filter(self, tmp_path: Path):
        store = ObjectStore(tmp_path / "objects")

        # Create a repo with files in two directories
        rows = [json.dumps({"x": "y"})]
        rh = store.write("rows", rows[0].encode("utf-8"))
        mh = store.write("manifests", serialize_manifest(Manifest(entries=[ManifestEntry(row_hash=rh, query_fingerprint=None)])))
        sc = Sidecar(manifest_hash=mh, entries=[SidecarEntry(row_hash=rh, char_count=10, token_estimate=2, field_count=1, lang="en")])
        sc_hash = store.write("sidecars", serialize_sidecar(sc))

        tree_entries = {
            "sub/a.jsonl": ("manifest", mh, sc_hash),
            "other/b.jsonl": ("manifest", mh, sc_hash),
        }
        tree_hash = build_nested_tree(store, tree_entries)
        commit = Commit(tree_hash=tree_hash, parent_hashes=[], author="t", message="m", timestamp=int(time.time()))
        commit_hash = store.write("commits", serialize_commit(commit))

        result = repo_stats(store, commit_hash, path_prefix="sub/")
        assert len(result["files"]) == 1
        assert result["files"][0]["path"] == "sub/a.jsonl"

    def test_unknown_commit_raises(self, tmp_path: Path):
        store = ObjectStore(tmp_path / "objects")
        with pytest.raises(FileNotFoundError):
            repo_stats(store, "a" * 64)


class TestCompareStats:
    def test_returns_commit_hashes(self, tmp_path: Path):
        store, c1 = _build_repo(tmp_path)
        c2 = _build_second_commit(store, c1)
        result = compare_stats(store, c1, c2)
        assert result["commit1"] == c1
        assert result["commit2"] == c2

    def test_files_list_includes_common_paths(self, tmp_path: Path):
        store, c1 = _build_repo(tmp_path)
        c2 = _build_second_commit(store, c1)
        result = compare_stats(store, c1, c2)
        paths = {f["path"] for f in result["files"]}
        assert "train.jsonl" in paths

    def test_delta_for_file_with_both_sidecars(self, tmp_path: Path):
        store, c1 = _build_repo(tmp_path)
        c2 = _build_second_commit(store, c1)
        result = compare_stats(store, c1, c2)
        files_by_path = {f["path"]: f for f in result["files"]}
        train = files_by_path["train.jsonl"]
        # old: 3 rows * 40 chars = 120 chars, 30 tokens
        # new: 5 rows * 30 chars = 150 chars, 35 tokens
        assert train["delta"]["row_count"] == 2        # 5 - 3
        assert train["delta"]["char_count"] == 30      # 150 - 120
        assert train["delta"]["token_estimate"] == 5   # 35 - 30

    def test_file_missing_sidecar_on_either_side_excluded_from_delta(self, tmp_path: Path):
        store, c1 = _build_repo(tmp_path)
        c2 = _build_second_commit(store, c1)
        result = compare_stats(store, c1, c2)
        files_by_path = {f["path"]: f for f in result["files"]}
        # eval.jsonl has no sidecar in either commit — should not appear
        assert "eval.jsonl" not in files_by_path

    def test_totals_delta(self, tmp_path: Path):
        store, c1 = _build_repo(tmp_path)
        c2 = _build_second_commit(store, c1)
        result = compare_stats(store, c1, c2)
        assert result["totals_delta"]["row_count"] == 2
        assert result["totals_delta"]["char_count"] == 30
        assert result["totals_delta"]["token_estimate"] == 5
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/lxs/code/dit
uv run pytest tests/test_stats.py -v
```

Expected: `FAILED` — `ModuleNotFoundError: No module named 'dit.core.stats'`

- [ ] **Step 3: Create `src/dit/core/stats.py`**

```python
# src/dit/core/stats.py
"""Repo-level stats aggregated from sidecar objects."""
from __future__ import annotations

from dit.core.objects import deserialize_commit, deserialize_sidecar
from dit.core.sidecar import sidecar_summary
from dit.core.store import ObjectStore
from dit.core.tree_walker import flatten_tree


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

    Raises FileNotFoundError if commit_hash is not found in store.
    Files without a sidecar are included with has_sidecar=False and numeric
    fields set to None. Totals aggregate only files with has_sidecar=True.
    """
    commit_data = store.read("commits", commit_hash)
    if commit_data is None:
        raise FileNotFoundError(f"Commit {commit_hash[:8]} not found in store")

    commit = deserialize_commit(commit_data)
    flat = flatten_tree(store, commit.tree_hash)

    clean_prefix = path_prefix.lstrip("/") if path_prefix else None

    files: list[dict] = []
    for path, (obj_type, obj_hash, sidecar_hash) in sorted(flat.items()):
        if obj_type != "manifest":
            continue
        if clean_prefix is not None and not path.startswith(clean_prefix):
            continue

        if sidecar_hash is None:
            files.append({
                "path": path,
                "row_count": None,
                "char_count": None,
                "token_estimate": None,
                "avg_fields": None,
                "lang_distribution": None,
                "has_sidecar": False,
            })
            continue

        sc_data = store.read("sidecars", sidecar_hash)
        if sc_data is None:
            files.append({
                "path": path,
                "row_count": None,
                "char_count": None,
                "token_estimate": None,
                "avg_fields": None,
                "lang_distribution": None,
                "has_sidecar": False,
            })
            continue

        sidecar = deserialize_sidecar(sc_data)
        summary = sidecar_summary(sidecar)
        files.append({
            "path": path,
            "row_count": summary["row_count"],
            "char_count": summary["char_count"],
            "token_estimate": summary["token_estimate"],
            "avg_fields": summary["avg_fields"],
            "lang_distribution": summary["lang_distribution"],
            "has_sidecar": True,
        })

    # Compute totals over files with sidecars only
    with_sidecar = [f for f in files if f["has_sidecar"]]
    total_lang: dict[str, int] = {}
    for f in with_sidecar:
        for lang, count in (f["lang_distribution"] or {}).items():
            total_lang[lang] = total_lang.get(lang, 0) + count

    totals: dict = {
        "file_count": len(files),
        "files_with_sidecar": len(with_sidecar),
        "row_count": sum(f["row_count"] for f in with_sidecar) if with_sidecar else None,
        "char_count": sum(f["char_count"] for f in with_sidecar) if with_sidecar else None,
        "token_estimate": sum(f["token_estimate"] for f in with_sidecar) if with_sidecar else None,
        "lang_distribution": total_lang if with_sidecar else {},
    }

    return {"commit_hash": commit_hash, "files": files, "totals": totals}


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
          "old": { <file entry as in repo_stats> },
          "new": { <file entry as in repo_stats> },
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

    Only includes files where BOTH old and new have has_sidecar=True.
    Files present in only one commit or missing sidecar on either side are omitted.
    """
    old_result = repo_stats(store, commit1, path_prefix=path_prefix)
    new_result = repo_stats(store, commit2, path_prefix=path_prefix)

    old_by_path = {f["path"]: f for f in old_result["files"]}
    new_by_path = {f["path"]: f for f in new_result["files"]}

    all_paths = sorted(set(old_by_path) & set(new_by_path))

    files: list[dict] = []
    for path in all_paths:
        old_f = old_by_path[path]
        new_f = new_by_path[path]
        if not old_f["has_sidecar"] or not new_f["has_sidecar"]:
            continue
        delta = {
            "row_count": new_f["row_count"] - old_f["row_count"],
            "char_count": new_f["char_count"] - old_f["char_count"],
            "token_estimate": new_f["token_estimate"] - old_f["token_estimate"],
        }
        files.append({"path": path, "old": old_f, "new": new_f, "delta": delta})

    totals_delta = {
        "row_count": sum(f["delta"]["row_count"] for f in files),
        "char_count": sum(f["delta"]["char_count"] for f in files),
        "token_estimate": sum(f["delta"]["token_estimate"] for f in files),
    }

    return {
        "commit1": commit1,
        "commit2": commit2,
        "files": files,
        "totals_delta": totals_delta,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_stats.py -v
```

Expected: all tests `PASSED`

- [ ] **Step 5: Run full test suite to confirm no regressions**

```bash
uv run pytest tests/ -v
```

Expected: all tests `PASSED`

- [ ] **Step 6: Commit**

```bash
git add src/dit/core/stats.py tests/test_stats.py
git commit -m "feat: core stats module — repo_stats() and compare_stats()"
```

---

## Task 2: CLI `dit stats` command

**Files:**
- Modify: `src/dit/cli/main.py`
- Create: `tests/test_cli_stats.py`

The `stats` command resolves a ref or commit hash via `RefStore`, calls `repo_stats()` or `compare_stats()`, and prints a table or JSON. The `--compare` flag is mutually exclusive with `--ref`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli_stats.py`:

```python
import json
import os
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dit.cli.main import app
from dit.core.objects import (
    Commit, Manifest, ManifestEntry,
    Sidecar, SidecarEntry,
    serialize_commit, serialize_manifest, serialize_sidecar,
)
from dit.core.store import ObjectStore
from dit.core.tree_builder import build_nested_tree
from dit.core.refs import RefStore

runner = CliRunner()


def _init_repo_with_sidecar(tmp_path: Path) -> tuple[ObjectStore, RefStore, str]:
    """Init a dit repo with train.jsonl (with sidecar) and eval.jsonl (no sidecar)."""
    os.chdir(tmp_path)
    runner.invoke(app, ["init"])

    dot = tmp_path / ".dit"
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)

    row = json.dumps({"instruction": "hello", "response": "world"})
    rh = store.write("rows", row.encode("utf-8"))
    manifest = Manifest(entries=[ManifestEntry(row_hash=rh, query_fingerprint=None)])
    mh = store.write("manifests", serialize_manifest(manifest))

    sc = Sidecar(
        manifest_hash=mh,
        entries=[SidecarEntry(row_hash=rh, char_count=40, token_estimate=10, field_count=2, lang="en")],
    )
    sc_hash = store.write("sidecars", serialize_sidecar(sc))

    eval_row = json.dumps({"q": "hi"})
    eval_rh = store.write("rows", eval_row.encode("utf-8"))
    eval_mh = store.write("manifests", serialize_manifest(Manifest(entries=[ManifestEntry(row_hash=eval_rh, query_fingerprint=None)])))

    tree_entries = {
        "train.jsonl": ("manifest", mh, sc_hash),
        "eval.jsonl": ("manifest", eval_mh, None),
    }
    tree_hash = build_nested_tree(store, tree_entries)
    commit = Commit(
        tree_hash=tree_hash,
        parent_hashes=[],
        author="tester",
        message="initial",
        timestamp=int(time.time()),
    )
    commit_hash = store.write("commits", serialize_commit(commit))
    refs.set_branch("main", commit_hash)
    return store, refs, commit_hash


class TestStatsCommand:
    def test_stats_default_exits_0(self, tmp_path: Path):
        _init_repo_with_sidecar(tmp_path)
        result = runner.invoke(app, ["stats"])
        assert result.exit_code == 0

    def test_stats_default_shows_file_with_sidecar(self, tmp_path: Path):
        _init_repo_with_sidecar(tmp_path)
        result = runner.invoke(app, ["stats"])
        assert "train.jsonl" in result.stdout

    def test_stats_default_shows_file_without_sidecar(self, tmp_path: Path):
        _init_repo_with_sidecar(tmp_path)
        result = runner.invoke(app, ["stats"])
        assert "eval.jsonl" in result.stdout

    def test_stats_default_shows_totals(self, tmp_path: Path):
        _init_repo_with_sidecar(tmp_path)
        result = runner.invoke(app, ["stats"])
        assert "TOTAL" in result.stdout

    def test_stats_default_shows_sidecar_warning(self, tmp_path: Path):
        _init_repo_with_sidecar(tmp_path)
        result = runner.invoke(app, ["stats"])
        # 1 of 2 files lacks a sidecar — footer warning expected
        assert "sidecar" in result.stdout.lower() or "meta" in result.stdout.lower()

    def test_stats_json_format_is_valid(self, tmp_path: Path):
        _init_repo_with_sidecar(tmp_path)
        result = runner.invoke(app, ["stats", "--format", "json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert "commit_hash" in data
        assert "files" in data
        assert "totals" in data

    def test_stats_json_format_files_have_has_sidecar(self, tmp_path: Path):
        _init_repo_with_sidecar(tmp_path)
        result = runner.invoke(app, ["stats", "--format", "json"])
        data = json.loads(result.stdout)
        has_sidecar_values = {f["path"]: f["has_sidecar"] for f in data["files"]}
        assert has_sidecar_values["train.jsonl"] is True
        assert has_sidecar_values["eval.jsonl"] is False

    def test_stats_path_filter(self, tmp_path: Path):
        _init_repo_with_sidecar(tmp_path)
        result = runner.invoke(app, ["stats", "train.jsonl"])
        assert result.exit_code == 0
        assert "train.jsonl" in result.stdout
        assert "eval.jsonl" not in result.stdout

    def test_stats_ref_flag(self, tmp_path: Path):
        _init_repo_with_sidecar(tmp_path)
        result = runner.invoke(app, ["stats", "--ref", "main"])
        assert result.exit_code == 0
        assert "train.jsonl" in result.stdout

    def test_stats_bad_ref_exits_1(self, tmp_path: Path):
        _init_repo_with_sidecar(tmp_path)
        result = runner.invoke(app, ["stats", "--ref", "nonexistent"])
        assert result.exit_code == 1

    def test_stats_compare_exits_0(self, tmp_path: Path):
        store, refs, c1 = _init_repo_with_sidecar(tmp_path)

        # Second commit: new train.jsonl with 2 rows and sidecar
        row2 = json.dumps({"instruction": "q2", "response": "a2"})
        rh2 = store.write("rows", row2.encode("utf-8"))
        mh2 = store.write("manifests", serialize_manifest(Manifest(entries=[
            ManifestEntry(row_hash=rh2, query_fingerprint=None)
        ])))
        sc2 = Sidecar(manifest_hash=mh2, entries=[
            SidecarEntry(row_hash=rh2, char_count=30, token_estimate=7, field_count=2, lang="en")
        ])
        sc2_hash = store.write("sidecars", serialize_sidecar(sc2))

        eval_row = json.dumps({"q": "hi"})
        eval_rh = store.write("rows", eval_row.encode("utf-8"))
        eval_mh = store.write("manifests", serialize_manifest(Manifest(entries=[ManifestEntry(row_hash=eval_rh, query_fingerprint=None)])))

        tree_entries2 = {
            "train.jsonl": ("manifest", mh2, sc2_hash),
            "eval.jsonl": ("manifest", eval_mh, None),
        }
        tree_hash2 = build_nested_tree(store, tree_entries2)
        c2_obj = Commit(tree_hash=tree_hash2, parent_hashes=[c1], author="t", message="second", timestamp=int(time.time()))
        c2 = store.write("commits", serialize_commit(c2_obj))
        refs.set_branch("main", c2)

        result = runner.invoke(app, ["stats", "--compare", c1, c2])
        assert result.exit_code == 0

    def test_stats_compare_shows_delta(self, tmp_path: Path):
        store, refs, c1 = _init_repo_with_sidecar(tmp_path)

        row2 = json.dumps({"instruction": "q2", "response": "a2"})
        rh2 = store.write("rows", row2.encode("utf-8"))
        mh2 = store.write("manifests", serialize_manifest(Manifest(entries=[
            ManifestEntry(row_hash=rh2, query_fingerprint=None)
        ])))
        sc2 = Sidecar(manifest_hash=mh2, entries=[
            SidecarEntry(row_hash=rh2, char_count=30, token_estimate=7, field_count=2, lang="en")
        ])
        sc2_hash = store.write("sidecars", serialize_sidecar(sc2))

        eval_row = json.dumps({"q": "hi"})
        eval_rh = store.write("rows", eval_row.encode("utf-8"))
        eval_mh = store.write("manifests", serialize_manifest(Manifest(entries=[ManifestEntry(row_hash=eval_rh, query_fingerprint=None)])))

        tree_entries2 = {
            "train.jsonl": ("manifest", mh2, sc2_hash),
            "eval.jsonl": ("manifest", eval_mh, None),
        }
        tree_hash2 = build_nested_tree(store, tree_entries2)
        c2_obj = Commit(tree_hash=tree_hash2, parent_hashes=[c1], author="t", message="second", timestamp=int(time.time()))
        c2 = store.write("commits", serialize_commit(c2_obj))

        result = runner.invoke(app, ["stats", "--compare", c1, c2])
        assert "train.jsonl" in result.stdout
        # Should show a row count delta — old was 1, new is 1 (same), so delta = 0 or shown
        assert result.exit_code == 0

    def test_stats_no_commits_exits_1(self, tmp_path: Path):
        os.chdir(tmp_path)
        runner.invoke(app, ["init"])
        result = runner.invoke(app, ["stats"])
        assert result.exit_code == 1

    def test_stats_outside_repo_exits_1(self, tmp_path: Path):
        empty = tmp_path / "empty"
        empty.mkdir()
        os.chdir(empty)
        result = runner.invoke(app, ["stats"])
        assert result.exit_code == 1
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_cli_stats.py -v
```

Expected: `FAILED` — `Error: No such command 'stats'`

- [ ] **Step 3: Add `stats` command to `src/dit/cli/main.py`**

Add the following command after the existing `export` command (around line 1707 in the existing file, before `_get_author()`):

```python
@app.command()
def stats(
    path: str = typer.Argument("", help="Optional path filter: file name or directory prefix"),
    ref: str = typer.Option("main", "--ref", help="Branch name or commit hash to inspect"),
    compare: tuple[str, str] = typer.Option((None, None), "--compare", help="Compare two refs: --compare REF1 REF2"),
    format: str = typer.Option("table", "--format", help="Output format: table or json"),
):
    """Show repo-level stats aggregated from sidecar metadata."""
    import json as _json
    from dit.core.stats import repo_stats, compare_stats

    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)

    comparing = compare[0] is not None and compare[1] is not None

    if comparing:
        commit1, commit2 = compare
        try:
            result = compare_stats(store, commit1, commit2, path_prefix=path or None)
        except FileNotFoundError as exc:
            typer.echo(f"fatal: {exc}", err=True)
            raise typer.Exit(1)

        if format == "json":
            typer.echo(_json.dumps(result, indent=2))
            return

        typer.echo(f"Stats delta: {commit1[:8]} -> {commit2[:8]}")
        typer.echo("")
        if not result["files"]:
            typer.echo("No files with sidecars on both sides.")
            return

        col_file = max(len(f["path"]) for f in result["files"])
        col_file = max(col_file, 4)
        header = f"{'File':<{col_file}}  {'Rows (delta)':>20}  {'Tokens (delta)':>18}  {'Chars (delta)':>18}"
        typer.echo(header)
        typer.echo("-" * len(header))

        for f in result["files"]:
            delta = f["delta"]
            row_sign = "+" if delta["row_count"] >= 0 else ""
            tok_sign = "+" if delta["token_estimate"] >= 0 else ""
            char_sign = "+" if delta["char_count"] >= 0 else ""
            rows_str = f"{f['old']['row_count']} -> {f['new']['row_count']} ({row_sign}{delta['row_count']})"
            old_tok = _fmt_tokens(f["old"]["token_estimate"])
            new_tok = _fmt_tokens(f["new"]["token_estimate"])
            delta_tok = _fmt_tokens(abs(delta["token_estimate"]))
            tok_str = f"{old_tok} -> {new_tok} ({tok_sign}{delta_tok})"
            char_str = f"{_fmt_chars(f['old']['char_count'])} -> {_fmt_chars(f['new']['char_count'])} ({char_sign}{_fmt_chars(delta['char_count'])})"
            typer.echo(f"{f['path']:<{col_file}}  {rows_str:>20}  {tok_str:>18}  {char_str:>18}")

        typer.echo("-" * len(header))
        td = result["totals_delta"]
        row_sign = "+" if td["row_count"] >= 0 else ""
        tok_sign = "+" if td["token_estimate"] >= 0 else ""
        char_sign = "+" if td["char_count"] >= 0 else ""
        typer.echo(f"{'TOTAL':<{col_file}}  {row_sign}{td['row_count']:>19}  {tok_sign}{_fmt_tokens(abs(td['token_estimate'])):>17}  {char_sign}{_fmt_chars(td['char_count']):>17}")
        return

    # Single-ref mode
    commit_hash = refs.get_branch(ref)
    if commit_hash is None:
        if len(ref) == 64 and all(c in "0123456789abcdef" for c in ref):
            commit_hash = ref
        else:
            typer.echo(f"fatal: ref '{ref}' not found", err=True)
            raise typer.Exit(1)

    if commit_hash is None:
        typer.echo(f"fatal: no commits on branch '{ref}'", err=True)
        raise typer.Exit(1)

    try:
        result = repo_stats(store, commit_hash, path_prefix=path or None)
    except FileNotFoundError as exc:
        typer.echo(f"fatal: {exc}", err=True)
        raise typer.Exit(1)

    if format == "json":
        typer.echo(_json.dumps(result, indent=2))
        return

    # Table mode
    path_filter_str = f" — {path}" if path else ""
    typer.echo(f"Repo stats at {ref} (commit {commit_hash[:8]}){path_filter_str}")
    typer.echo("")

    if not result["files"]:
        typer.echo("No manifest files found.")
        return

    col_file = max(len(f["path"]) for f in result["files"])
    col_file = max(col_file, 4)
    header = f"{'File':<{col_file}}  {'Rows':>8}  {'Tokens':>10}  {'Chars':>10}  {'Avg fields':>10}  Lang"
    sep = "\u2500" * len(header)
    typer.echo(header)
    typer.echo(sep)

    for f in result["files"]:
        if f["has_sidecar"]:
            rows_str = f"{f['row_count']:,}"
            tok_str = _fmt_tokens(f["token_estimate"])
            char_str = _fmt_chars(f["char_count"])
            avg_str = f"{f['avg_fields']:.1f}"
            lang_str = _fmt_lang(f["lang_distribution"])
        else:
            rows_str = tok_str = char_str = avg_str = lang_str = "\u2014"
        typer.echo(f"{f['path']:<{col_file}}  {rows_str:>8}  {tok_str:>10}  {char_str:>10}  {avg_str:>10}  {lang_str}")

    typer.echo(sep)
    totals = result["totals"]
    if totals["files_with_sidecar"]:
        tot_rows = f"{totals['row_count']:,}"
        tot_tok = _fmt_tokens(totals["token_estimate"])
        tot_char = _fmt_chars(totals["char_count"])
        tot_lang = _fmt_lang(totals["lang_distribution"])
    else:
        tot_rows = tot_tok = tot_char = tot_lang = "\u2014"
    typer.echo(f"{'TOTAL':<{col_file}}  {tot_rows:>8}  {tot_tok:>10}  {tot_char:>10}  {'':>10}  {tot_lang}")

    missing = totals["file_count"] - totals["files_with_sidecar"]
    if missing > 0:
        typer.echo("")
        typer.echo(f"{missing} of {totals['file_count']} files have no sidecar metadata. Run 'dit meta compute' to fill gaps.")


def _fmt_tokens(n: int | None) -> str:
    if n is None:
        return "\u2014"
    if n >= 1_000_000:
        return f"~{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"~{n / 1_000:.0f}K"
    return str(n)


def _fmt_chars(n: int | None) -> str:
    if n is None:
        return "\u2014"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


def _fmt_lang(dist: dict | None) -> str:
    if not dist:
        return "\u2014"
    top_lang, top_count = max(dist.items(), key=lambda kv: kv[1])
    total = sum(dist.values())
    pct = round(top_count / total * 100) if total > 0 else 0
    return f"{top_lang} {pct}%"
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_cli_stats.py -v
```

Expected: all tests `PASSED`

- [ ] **Step 5: Run full test suite**

```bash
uv run pytest tests/ -v
```

Expected: all tests `PASSED`

- [ ] **Step 6: Commit**

```bash
git add src/dit/cli/main.py tests/test_cli_stats.py
git commit -m "feat: dit stats CLI command"
```

---

## Task 3: Server stats endpoint

**Files:**
- Create: `src/dit/server/routes/stats_api.py`
- Modify: `src/dit/server/app.py`
- Create: `tests/server/test_routes_stats.py`

`GET /api/v1/repos/{repo}/stats/{commit_hash}?path=...` returns a JSON object shaped exactly like `repo_stats()`. Auth uses `require_permission("read")`.

- [ ] **Step 1: Write the failing tests**

Create `tests/server/test_routes_stats.py`:

```python
import json
import time
from pathlib import Path

import pytest
from httpx import AsyncClient

from dit.core.objects import (
    Commit, Manifest, ManifestEntry,
    Sidecar, SidecarEntry,
    serialize_commit, serialize_manifest, serialize_sidecar,
)
from dit.core.store import ObjectStore
from dit.core.tree_builder import build_nested_tree


async def _create_repo_with_sidecars(
    client: AsyncClient,
    tmp_path: Path,
    repo: str = "stats-repo",
) -> tuple[ObjectStore, str]:
    resp = await client.post("/api/v1/repos", json={"name": repo})
    assert resp.status_code == 201

    data_dir = tmp_path / "data"
    store = ObjectStore(data_dir / "repos" / repo / "objects")

    row = json.dumps({"instruction": "hello", "response": "world"})
    rh = store.write("rows", row.encode("utf-8"))
    manifest = Manifest(entries=[ManifestEntry(row_hash=rh, query_fingerprint=None)])
    mh = store.write("manifests", serialize_manifest(manifest))
    sc = Sidecar(
        manifest_hash=mh,
        entries=[SidecarEntry(row_hash=rh, char_count=40, token_estimate=10, field_count=2, lang="en")],
    )
    sc_hash = store.write("sidecars", serialize_sidecar(sc))

    eval_row = json.dumps({"q": "hi"})
    eval_rh = store.write("rows", eval_row.encode("utf-8"))
    eval_mh = store.write("manifests", serialize_manifest(Manifest(entries=[ManifestEntry(row_hash=eval_rh, query_fingerprint=None)])))

    tree_entries = {
        "train.jsonl": ("manifest", mh, sc_hash),
        "eval.jsonl": ("manifest", eval_mh, None),
    }
    tree_hash = build_nested_tree(store, tree_entries)
    commit = Commit(
        tree_hash=tree_hash,
        parent_hashes=[],
        author="tester",
        message="initial",
        timestamp=int(time.time()),
    )
    commit_hash = store.write("commits", serialize_commit(commit))

    await client.post(
        f"/api/v1/repos/{repo}/refs/heads/main",
        json={"old": None, "new": commit_hash},
    )
    return store, commit_hash


@pytest.mark.asyncio
class TestStatsEndpoint:
    async def test_stats_returns_200(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash = await _create_repo_with_sidecars(client, tmp_path)
        resp = await client.get(f"/api/v1/repos/stats-repo/stats/{commit_hash}")
        assert resp.status_code == 200

    async def test_stats_response_has_commit_hash(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash = await _create_repo_with_sidecars(client, tmp_path)
        resp = await client.get(f"/api/v1/repos/stats-repo/stats/{commit_hash}")
        data = resp.json()
        assert data["commit_hash"] == commit_hash

    async def test_stats_response_has_files_and_totals(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash = await _create_repo_with_sidecars(client, tmp_path)
        resp = await client.get(f"/api/v1/repos/stats-repo/stats/{commit_hash}")
        data = resp.json()
        assert "files" in data
        assert "totals" in data

    async def test_stats_file_with_sidecar_has_stats(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash = await _create_repo_with_sidecars(client, tmp_path)
        resp = await client.get(f"/api/v1/repos/stats-repo/stats/{commit_hash}")
        data = resp.json()
        files_by_path = {f["path"]: f for f in data["files"]}
        train = files_by_path["train.jsonl"]
        assert train["has_sidecar"] is True
        assert train["row_count"] == 1
        assert train["token_estimate"] == 10

    async def test_stats_file_without_sidecar_has_null_fields(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash = await _create_repo_with_sidecars(client, tmp_path)
        resp = await client.get(f"/api/v1/repos/stats-repo/stats/{commit_hash}")
        data = resp.json()
        files_by_path = {f["path"]: f for f in data["files"]}
        eval_f = files_by_path["eval.jsonl"]
        assert eval_f["has_sidecar"] is False
        assert eval_f["row_count"] is None

    async def test_stats_totals_files_with_sidecar(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash = await _create_repo_with_sidecars(client, tmp_path)
        resp = await client.get(f"/api/v1/repos/stats-repo/stats/{commit_hash}")
        data = resp.json()
        assert data["totals"]["file_count"] == 2
        assert data["totals"]["files_with_sidecar"] == 1

    async def test_stats_path_filter(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash = await _create_repo_with_sidecars(client, tmp_path)
        resp = await client.get(
            f"/api/v1/repos/stats-repo/stats/{commit_hash}",
            params={"path": "train.jsonl"},
        )
        data = resp.json()
        assert len(data["files"]) == 1
        assert data["files"][0]["path"] == "train.jsonl"

    async def test_stats_commit_not_found_returns_404(self, client: AsyncClient, tmp_path: Path):
        await _create_repo_with_sidecars(client, tmp_path)
        resp = await client.get(f"/api/v1/repos/stats-repo/stats/{'z' * 64}")
        assert resp.status_code == 404

    async def test_stats_repo_not_found_returns_404(self, client: AsyncClient, tmp_path: Path):
        resp = await client.get(f"/api/v1/repos/no-such-repo/stats/{'a' * 64}")
        assert resp.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/server/test_routes_stats.py -v
```

Expected: `FAILED` — `404 Not Found` for all tests (route not registered yet)

- [ ] **Step 3: Create `src/dit/server/routes/stats_api.py`**

```python
# src/dit/server/routes/stats_api.py
"""Repo-level stats endpoint: GET /{repo}/stats/{commit_hash}"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.auth import get_session, require_permission
from dit.server.routes._helpers import _get_repo

router = APIRouter(prefix="/api/v1/repos", tags=["stats"])


def _store_for_repo(request: Request, repo_name: str):
    from dit.core.store import ObjectStore
    data_dir: Path = request.app.state.data_dir
    return ObjectStore(Path(data_dir) / "repos" / repo_name / "objects")


@router.get("/{repo}/stats/{commit_hash}")
async def repo_stats_endpoint(
    repo: str,
    commit_hash: str,
    request: Request,
    path: Optional[str] = Query(default=None, description="Filter to file/directory prefix"),
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("read")),
):
    """Return aggregated sidecar stats for all manifest files in a commit."""
    from dit.core.stats import repo_stats

    await _get_repo(repo, session)
    store = _store_for_repo(request, repo)

    try:
        result = repo_stats(store, commit_hash, path_prefix=path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return result
```

- [ ] **Step 4: Register the router in `src/dit/server/app.py`**

In `src/dit/server/app.py`, add after the `export_router` registration (the last `include_router` before `return application`):

```python
    from dit.server.routes.stats_api import router as stats_router
    application.include_router(stats_router)
```

The tail of `create_app()` should now read:

```python
    from dit.server.routes.meta_api import router as meta_router
    application.include_router(meta_router)

    from dit.server.routes.export_api import router as export_router
    application.include_router(export_router)

    from dit.server.routes.stats_api import router as stats_router
    application.include_router(stats_router)

    return application
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/server/test_routes_stats.py -v
```

Expected: all 9 tests `PASSED`

- [ ] **Step 6: Run full test suite**

```bash
uv run pytest tests/ -v
```

Expected: all tests `PASSED`

- [ ] **Step 7: Commit**

```bash
git add src/dit/server/routes/stats_api.py src/dit/server/app.py tests/server/test_routes_stats.py
git commit -m "feat: server stats endpoint GET /{repo}/stats/{commit_hash}"
```

---

## Task 4: Gateway proxy

**Files (all in `/Users/lxs/code/datahub-gateway/`):**
- Modify: `modules/dit/client.go`
- Modify: `routers/api/v1/repo/dit.go`
- Modify: `routers/api/v1/api.go`

Add a `GetStats` client method, a `DatahubGetStats` handler that proxies GET requests with an optional `path` query param, and register the route in the `dit` group.

- [ ] **Step 1: Add `GetStats` to `modules/dit/client.go`**

Open `/Users/lxs/code/datahub-gateway/modules/dit/client.go`. After the `MetaDiff` method (currently the last method in the file), append:

```go
func (c *Client) GetStats(ctx context.Context, repoName, commitHash, pathFilter string) ([]byte, int, error) {
	path := "/api/v1/repos/" + repoName + "/stats/" + commitHash
	if pathFilter != "" {
		path += "?path=" + url.QueryEscape(pathFilter)
	}
	return c.do(ctx, http.MethodGet, path, nil)
}
```

`url` is already imported in the file (`"net/url"`), so no new import is needed.

- [ ] **Step 2: Add `DatahubGetStats` handler to `routers/api/v1/repo/dit.go`**

Open `/Users/lxs/code/datahub-gateway/routers/api/v1/repo/dit.go`. After the `DatahubMetaDiff` function (the last function in the file), append:

```go
func DatahubGetStats(ctx *context.APIContext) {
	proxyToDatahub(ctx, func() ([]byte, int, error) {
		return dit.DefaultClient().GetStats(
			ctx,
			ctx.Repo.Repository.Name,
			ctx.Params(":commit"),
			ctx.FormString("path"),
		)
	})
}
```

- [ ] **Step 3: Register the route in `routers/api/v1/api.go`**

Open `/Users/lxs/code/datahub-gateway/routers/api/v1/api.go`. Find the `dit` group block (around line 1424):

```go
m.Group("/dit", func() {
    m.Get("/refs", repo.DatahubListRefs)
    ...
    m.Get("/meta/{commit}/{path}/summary", repo.DatahubMetaSummary)
})
```

Add the stats route as the last line inside the group, before the closing `}`:

```go
    m.Get("/stats/{commit}", repo.DatahubGetStats)
```

The complete group should end:

```go
				m.Get("/meta/diff/{old}/{new}", repo.DatahubMetaDiff)
				m.Get("/meta/{commit}/{path}", repo.DatahubMetaGet)
				m.Get("/meta/{commit}/{path}/summary", repo.DatahubMetaSummary)
				m.Get("/stats/{commit}", repo.DatahubGetStats)
			})
```

- [ ] **Step 4: Verify Go build compiles**

```bash
cd /Users/lxs/code/datahub-gateway
go build ./...
```

Expected: exits 0, no errors.

- [ ] **Step 5: Run Go dit tests**

```bash
cd /Users/lxs/code/datahub-gateway
go test ./modules/dit/... -v
```

Expected: all tests `PASS`. (These tests mock the HTTP client; `GetStats` has the same signature pattern as `MetaDiff` so they will pass without additional test additions.)

- [ ] **Step 6: Commit (gateway repo)**

```bash
cd /Users/lxs/code/datahub-gateway
git add modules/dit/client.go routers/api/v1/repo/dit.go routers/api/v1/api.go
git commit -m "feat: gateway proxy for dit stats endpoint"
```

---

## Task 5: Vue stats panel in DataRepoHome.vue

**File:** `/Users/lxs/code/datahub-gateway/web_src/js/components/DataRepoHome.vue`

Add a collapsible "Dataset Stats" section below the file table. Stats load lazily when the user expands the accordion. On branch change, `repoStats` is reset so stale data is not shown.

- [ ] **Step 1: Add reactive data fields and computed property**

In `DataRepoHome.vue`, find the `data()` return object (around line 109). It currently ends with:

```js
      computingMeta: {},
    };
```

Replace that closing with:

```js
      computingMeta: {},
      commitHash: null,
      statsOpen: false,
      statsLoading: false,
      statsError: null,
      repoStats: null,
    };
```

After the closing `},` of `data()`, add a `computed` block before `methods`:

```js
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
```

- [ ] **Step 2: Update `loadTree()` to capture `commitHash` and reset stats**

In `methods.loadTree()`, find the line:

```js
      const commitHash = ref.target_hash;
```

Immediately after that line (before the `this.tree = ...` line), add:

```js
      this.commitHash = commitHash;
      this.repoStats = null;
      this.statsOpen = false;
```

- [ ] **Step 3: Add `toggleStats` and `loadStats` methods**

At the bottom of the `methods` object, before the closing `},` of `methods`, add:

```js
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
```

- [ ] **Step 4: Add stats panel template block**

In the `<template>` section, find the closing `</div>` of the file tree segment (the `<!-- File tree -->` segment ends at the `</div>` before `<!-- JSONL Viewer -->`). After that `</div>` and before the `<!-- JSONL Viewer -->` comment, insert:

```html
    <!-- Stats panel (collapsed by default) -->
    <div class="ui segment" v-if="commitHash">
      <div class="ui accordion">
        <div class="title" @click="toggleStats" style="cursor:pointer;">
          <i class="dropdown icon"></i>
          <strong>Dataset Stats</strong>
          <span v-if="repoStats" class="ui small label" style="margin-left:8px;">
            {{ formatTokens(repoStats.totals.token_estimate) }} tokens
          </span>
        </div>
        <div class="content" v-show="statsOpen">
          <div v-if="statsLoading" class="ui active centered inline loader" style="margin:1em 0;"></div>
          <div v-else-if="statsError" class="ui small negative message">{{ statsError }}</div>
          <div v-else-if="repoStats">

            <!-- Totals row -->
            <div class="ui tiny statistics" style="margin-bottom:1em;">
              <div class="statistic">
                <div class="value">{{ repoStats.totals.row_count != null ? repoStats.totals.row_count.toLocaleString() : '—' }}</div>
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

            <!-- Language distribution bars -->
            <div v-if="topLangs.length > 0" style="margin-bottom:1em;">
              <strong>Language distribution</strong>
              <div v-for="([lang, pct]) in topLangs" :key="lang" style="margin-top:4px;">
                <span style="display:inline-block;width:4em;">{{ lang }}</span>
                <span
                  style="display:inline-block;background:#2185d0;height:10px;vertical-align:middle;"
                  :style="{width: (pct * 2) + 'px'}"
                ></span>
                <span style="margin-left:6px;font-size:0.9em;">{{ Math.round(pct) }}%</span>
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
                  <td class="right aligned">{{ f.row_count != null ? f.row_count.toLocaleString() : '—' }}</td>
                  <td class="right aligned">{{ f.has_sidecar ? formatTokens(f.token_estimate) : '—' }}</td>
                  <td class="right aligned">{{ f.avg_fields != null ? f.avg_fields.toFixed(1) : '—' }}</td>
                  <td class="right aligned">{{ f.has_sidecar ? formatLang(f.lang_distribution) : '—' }}</td>
                </tr>
              </tbody>
            </table>

          </div>
        </div>
      </div>
    </div>
```

- [ ] **Step 5: Build the frontend to check for syntax errors**

```bash
cd /Users/lxs/code/datahub-gateway
make frontend
```

Expected: exits 0 with no errors. If `make frontend` is not available, run:

```bash
cd /Users/lxs/code/datahub-gateway
npx vite build --mode development 2>&1 | head -30
```

Expected: no Vue template compilation errors.

- [ ] **Step 6: Commit (gateway repo)**

```bash
cd /Users/lxs/code/datahub-gateway
git add web_src/js/components/DataRepoHome.vue
git commit -m "feat: lazy stats panel in DataRepoHome.vue"
```

---

## Task 6: Final verification

**Files:** No changes — run the full suites and smoke-test.

- [ ] **Step 1: Run Python test suite**

```bash
cd /Users/lxs/code/dit
uv run pytest tests/ -v
```

Expected: all tests `PASSED`, zero failures.

- [ ] **Step 2: Smoke-test the CLI**

```bash
cd /tmp
rm -rf stats-smoke && mkdir stats-smoke && cd stats-smoke
dit init
printf '{"instruction":"hello","response":"world"}\n{"instruction":"foo","response":"bar"}\n' > train.jsonl
printf '{"q":"test"}\n' > eval.jsonl
dit add .
dit commit -m "initial"
dit meta compute
dit stats
```

Expected output (exact widths may vary):

```
Repo stats at main (commit <hash>)

File         Rows    Tokens    Chars  Avg fields  Lang
────────────────────────────────────────────────────────
train.jsonl     2      ~...     ...          2.0  en ...%
eval.jsonl      —         —       —            —  —
────────────────────────────────────────────────────────
TOTAL           2      ~...     ...               en ...%

1 of 2 files have no sidecar metadata. Run 'dit meta compute' to fill gaps.
```

After `dit meta compute` also covers `eval.jsonl`, re-run:

```bash
dit meta compute
dit stats
```

Expected: footer warning is gone; both files show stats.

- [ ] **Step 3: Smoke-test JSON format**

```bash
dit stats --format json | python3 -m json.tool | head -20
```

Expected: valid JSON with `commit_hash`, `files`, `totals` keys.

- [ ] **Step 4: Smoke-test `--compare`**

```bash
# Record first commit hash
C1=$(cd /tmp/stats-smoke && git -C .dit log --format='%H' 2>/dev/null || dit log | grep '^commit' | head -1 | awk '{print $2}')
# Add a row and commit
printf '{"instruction":"new","response":"row"}\n' >> /tmp/stats-smoke/train.jsonl
dit add .
dit commit -m "second"
dit meta compute
C2=$(dit log | grep '^commit' | head -1 | awk '{print $2}')
dit stats --compare $C1 $C2
```

Expected: table showing `train.jsonl` with row delta `+1`.

- [ ] **Step 5: Run Go build in gateway**

```bash
cd /Users/lxs/code/datahub-gateway
go build ./...
go test ./modules/dit/... -v
```

Expected: build succeeds, tests `PASS`.

---

## Self-Review

### Spec coverage check

| Spec section | Covered by task |
|---|---|
| `dit stats [PATH] [--ref] [--compare] [--format table\|json]` | Task 2 |
| Default table output: per-file row + totals row | Task 2 |
| `—` for files without sidecar; excluded from totals | Task 1, 2 |
| Footer warning when some files lack sidecar | Task 2 |
| `--compare` shows delta per file, totals delta | Task 1, 2 |
| Files missing sidecar on either side omitted from compare | Task 1, 2 |
| `--format json` mirrors server API response shape | Task 2 |
| `GET /api/v1/repos/{repo}/stats/{commit_hash}?path=` | Task 3 |
| 404 when commit not found | Task 3 |
| 200 with empty files list for commit with no manifests | Task 3 (server returns `repo_stats()` result directly) |
| Register router in `app.py` | Task 3 |
| Gateway route `GET /stats/{commit}` in dit group | Task 4 |
| `DatahubGetStats` handler with optional `path` param | Task 4 |
| `GetStats` client method | Task 4 |
| Vue collapsible stats panel, lazy-loaded | Task 5 |
| Totals summary (rows, tokens, chars, files w/ meta) | Task 5 |
| Language distribution bars | Task 5 |
| Per-file breakdown table | Task 5 |
| Reset `repoStats` on branch change | Task 5 |

### Placeholder scan

No TBD/TODO/placeholder text in code blocks. All function signatures, return shapes, column formats, and Vue template bindings are fully specified.

### Type consistency check

- `repo_stats(store, commit_hash, path_prefix) -> dict` — defined in Task 1, used in CLI (Task 2), server (Task 3), and smoke tests (Task 6). The `path_prefix` parameter maps to the `path` query param in the server and the positional `PATH` arg in the CLI.
- `compare_stats(store, commit1, commit2, path_prefix) -> dict` — defined in Task 1, used in CLI (Task 2).
- `_fmt_tokens`, `_fmt_chars`, `_fmt_lang` — private helpers in `cli/main.py`, not imported anywhere else.
- `_store_for_repo(request, repo_name) -> ObjectStore` — copy-paste pattern from `meta_api.py` and `export_api.py`; identical implementation in `stats_api.py`.
- Go `GetStats(ctx, repoName, commitHash, pathFilter)` — mirrors `MetaDiff` pattern: builds URL, appends query param if non-empty, calls `c.do`.
- Vue `repoStats` — populated by `ditFetch(owner, repo, /stats/${commitHash})`, which returns the same JSON shape as `repo_stats()` via the server.
