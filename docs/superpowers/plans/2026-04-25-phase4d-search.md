# Phase 4D: Row-Level Search — Implementation Plan

> **Spec:** `docs/superpowers/specs/2026-04-25-phase4d-search.md`
> **Date:** 2026-04-25
> **Depends on:** Phase 4A (sidecar), Phase 4B (export)

---

## Overview

This plan implements brute-force row-level search in six sequential tasks:

1. Core search module (`src/dit/core/search.py`) with unit tests
2. CLI `dit search` command in `src/dit/cli/main.py` with CLI tests
3. Server search endpoint (`src/dit/server/routes/search_api.py`) with server tests
4. Gateway proxy — Go handler, client method, route registration
5. Vue search UI in `DataRepoHome.vue`
6. Final verification

---

## Task 1: Core search module

### 1.1 Write failing tests

Create `tests/test_search.py`:

```python
# tests/test_search.py
import json
import time
from pathlib import Path

import pytest

from dit.core.objects import (
    Commit, Manifest, ManifestEntry,
    serialize_commit, serialize_manifest,
)
from dit.core.store import ObjectStore
from dit.core.tree_builder import build_nested_tree
from dit.core.search import search_rows, _resolve_field, _make_highlight


def _build_repo(tmp_path: Path) -> tuple[ObjectStore, str]:
    """One commit: train.jsonl (3 rows) and eval.jsonl (1 row)."""
    store = ObjectStore(tmp_path / "objects")

    rows_train = [
        json.dumps({"instruction": "实现一个LRU缓存，支持get和put操作", "response": "好的"}),
        json.dumps({"instruction": "LRU缓存淘汰策略是指最近最少使用", "response": "正确"}),
        json.dumps({"instruction": "quicksort algorithm", "response": "ok"}),
    ]
    train_entries = []
    for r in rows_train:
        rh = store.write("rows", r.encode("utf-8"))
        train_entries.append(ManifestEntry(row_hash=rh, query_fingerprint=None))
    train_manifest = Manifest(entries=train_entries)
    train_mh = store.write("manifests", serialize_manifest(train_manifest))

    rows_eval = [
        json.dumps({"messages": [{"role": "user", "content": "LRU缓存的时间复杂度为O(1)"}], "response": "yes"}),
    ]
    eval_entries = []
    for r in rows_eval:
        rh = store.write("rows", r.encode("utf-8"))
        eval_entries.append(ManifestEntry(row_hash=rh, query_fingerprint=None))
    eval_manifest = Manifest(entries=eval_entries)
    eval_mh = store.write("manifests", serialize_manifest(eval_manifest))

    tree_entries = {
        "train.jsonl": ("manifest", train_mh, None),
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


class TestResolveField:
    def test_top_level_key(self):
        row = {"instruction": "hello"}
        assert _resolve_field(row, "instruction") == "hello"

    def test_nested_key(self):
        row = {"meta": {"source": "web"}}
        assert _resolve_field(row, "meta.source") == "web"

    def test_list_index(self):
        row = {"messages": [{"role": "user", "content": "hello"}]}
        assert _resolve_field(row, "messages[0].content") == "hello"

    def test_missing_key_returns_none(self):
        row = {"instruction": "hello"}
        assert _resolve_field(row, "response") is None

    def test_out_of_range_index_returns_none(self):
        row = {"messages": [{"content": "hi"}]}
        assert _resolve_field(row, "messages[5].content") is None

    def test_wrong_type_returns_none(self):
        row = {"instruction": "hello"}
        assert _resolve_field(row, "instruction.nested") is None


class TestMakeHighlight:
    def test_match_in_middle(self):
        text = "a" * 30 + "MATCH" + "b" * 30
        result = _make_highlight(text, "match")
        assert "MATCH" in result
        assert "..." in result

    def test_match_at_start_no_leading_ellipsis(self):
        text = "MATCHsuffix" + "x" * 30
        result = _make_highlight(text, "match")
        assert not result.startswith("...")

    def test_match_at_end_no_trailing_ellipsis(self):
        text = "x" * 30 + "prefix" + "MATCH"
        result = _make_highlight(text, "match")
        assert not result.endswith("...")

    def test_short_text_no_ellipsis(self):
        text = "short MATCH text"
        result = _make_highlight(text, "match")
        assert result == text

    def test_context_chars(self):
        prefix = "a" * 40
        suffix = "b" * 40
        text = prefix + "MATCH" + suffix
        result = _make_highlight(text, "match", context=30)
        # Should have at most 30 chars on each side (+ "...")
        assert "MATCH" in result
        assert len(result) <= 30 + 5 + 30 + 6  # context + "MATCH" + context + 2*"..."


class TestSearchRows:
    def test_returns_commit_hash(self, tmp_path: Path):
        store, commit_hash = _build_repo(tmp_path)
        result = search_rows(store, commit_hash, "LRU")
        assert result["commit_hash"] == commit_hash

    def test_returns_query(self, tmp_path: Path):
        store, commit_hash = _build_repo(tmp_path)
        result = search_rows(store, commit_hash, "LRU")
        assert result["query"] == "LRU"

    def test_field_path_is_none_by_default(self, tmp_path: Path):
        store, commit_hash = _build_repo(tmp_path)
        result = search_rows(store, commit_hash, "LRU")
        assert result["field_path"] is None

    def test_matches_across_files(self, tmp_path: Path):
        store, commit_hash = _build_repo(tmp_path)
        result = search_rows(store, commit_hash, "LRU")
        files = {m["file"] for m in result["matches"]}
        assert "train.jsonl" in files
        assert "eval.jsonl" in files

    def test_case_insensitive(self, tmp_path: Path):
        store, commit_hash = _build_repo(tmp_path)
        result_lower = search_rows(store, commit_hash, "lru")
        result_upper = search_rows(store, commit_hash, "LRU")
        assert len(result_lower["matches"]) == len(result_upper["matches"])

    def test_no_matches_returns_empty_list(self, tmp_path: Path):
        store, commit_hash = _build_repo(tmp_path)
        result = search_rows(store, commit_hash, "zzznomatch")
        assert result["matches"] == []
        assert result["total_scanned"] == 4

    def test_match_has_expected_keys(self, tmp_path: Path):
        store, commit_hash = _build_repo(tmp_path)
        result = search_rows(store, commit_hash, "LRU")
        m = result["matches"][0]
        assert "file" in m
        assert "row_index" in m
        assert "row_hash" in m
        assert "content" in m
        assert "highlight" in m

    def test_match_content_is_dict(self, tmp_path: Path):
        store, commit_hash = _build_repo(tmp_path)
        result = search_rows(store, commit_hash, "LRU")
        m = result["matches"][0]
        assert isinstance(m["content"], dict)

    def test_total_scanned_counts_all_rows(self, tmp_path: Path):
        store, commit_hash = _build_repo(tmp_path)
        result = search_rows(store, commit_hash, "LRU")
        assert result["total_scanned"] == 4  # 3 train + 1 eval

    def test_limit_stops_scanning(self, tmp_path: Path):
        store, commit_hash = _build_repo(tmp_path)
        result = search_rows(store, commit_hash, "LRU", limit=1)
        assert len(result["matches"]) == 1
        assert result["limit_reached"] is True

    def test_limit_not_reached_flag(self, tmp_path: Path):
        store, commit_hash = _build_repo(tmp_path)
        result = search_rows(store, commit_hash, "LRU")
        assert result["limit_reached"] is False

    def test_path_prefix_filter(self, tmp_path: Path):
        store, commit_hash = _build_repo(tmp_path)
        result = search_rows(store, commit_hash, "LRU", path_prefix="train.jsonl")
        files = {m["file"] for m in result["matches"]}
        assert "eval.jsonl" not in files
        assert "train.jsonl" in files

    def test_field_path_matches_specific_field(self, tmp_path: Path):
        store, commit_hash = _build_repo(tmp_path)
        # "LRU" exists in both instruction and full-row; field_path=response should NOT match
        result = search_rows(store, commit_hash, "LRU", field_path="response")
        assert result["matches"] == []

    def test_field_path_matches_nested(self, tmp_path: Path):
        store, commit_hash = _build_repo(tmp_path)
        result = search_rows(store, commit_hash, "LRU", field_path="messages[0].content")
        # Only eval.jsonl row has messages[0].content with "LRU"
        assert len(result["matches"]) == 1
        assert result["matches"][0]["file"] == "eval.jsonl"

    def test_field_path_silently_skips_missing_field(self, tmp_path: Path):
        store, commit_hash = _build_repo(tmp_path)
        # "nonexistent" field — all rows silently skipped, no error
        result = search_rows(store, commit_hash, "LRU", field_path="nonexistent")
        assert result["matches"] == []

    def test_row_index_is_zero_based(self, tmp_path: Path):
        store, commit_hash = _build_repo(tmp_path)
        result = search_rows(store, commit_hash, "LRU缓存淘汰")
        # Second row in train.jsonl
        assert result["matches"][0]["row_index"] == 1

    def test_highlight_contains_query(self, tmp_path: Path):
        store, commit_hash = _build_repo(tmp_path)
        result = search_rows(store, commit_hash, "LRU")
        for m in result["matches"]:
            assert "LRU" in m["highlight"] or "lru" in m["highlight"].lower()

    def test_unknown_commit_raises(self, tmp_path: Path):
        store = ObjectStore(tmp_path / "objects")
        with pytest.raises(FileNotFoundError):
            search_rows(store, "a" * 64, "query")
```

Run to confirm all tests fail:

```
cd /Users/lxs/code/dit && uv run pytest tests/test_search.py -v 2>&1 | head -30
```

Expected: `ImportError: cannot import name 'search_rows' from 'dit.core.search'` or `ModuleNotFoundError`.

- [ ] All test_search.py tests fail with import error

### 1.2 Implement `src/dit/core/search.py`

Create `/Users/lxs/code/dit/src/dit/core/search.py`:

```python
# src/dit/core/search.py
"""Brute-force row-level search across JSONL rows in a commit."""
from __future__ import annotations

import json
import re

from dit.core.objects import deserialize_commit, deserialize_manifest
from dit.core.store import ObjectStore
from dit.core.tree_walker import flatten_tree


def _resolve_field(row: dict, field_path: str) -> object | None:
    """Navigate nested dict/list using dot-notation with bracket indexing.

    Examples:
      "instruction"             -> row["instruction"]
      "messages[0].content"    -> row["messages"][0]["content"]
      "meta.source"            -> row["meta"]["source"]

    Returns None if the path is missing or types don't match.
    """
    # Split on "." but keep bracket notation attached to the segment before the dot
    segments = field_path.split(".")
    current = row
    for segment in segments:
        if current is None:
            return None
        # Check for list index: key[N]
        m = re.fullmatch(r"([^\[]+)\[(\d+)\]", segment)
        if m:
            key, idx = m.group(1), int(m.group(2))
            if not isinstance(current, dict) or key not in current:
                return None
            current = current[key]
            if not isinstance(current, list) or idx >= len(current):
                return None
            current = current[idx]
        else:
            # Plain key
            if not isinstance(current, dict) or segment not in current:
                return None
            current = current[segment]
    return current


def _make_highlight(text: str, query: str, context: int = 30) -> str:
    """Return a short excerpt with the matched substring in context.

    Returns at most `context` characters before and after the match position,
    with '...' prepended/appended if the surrounding text was trimmed.
    """
    pos = text.lower().find(query.lower())
    if pos == -1:
        return text[:context * 2 + len(query)]

    start = max(0, pos - context)
    end = min(len(text), pos + len(query) + context)

    excerpt = text[start:end]

    if start > 0:
        excerpt = "..." + excerpt
    if end < len(text):
        excerpt = excerpt + "..."

    return excerpt


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
      "field_path": "messages[0].content",   # or None
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
      "limit_reached": False
    }

    Raises FileNotFoundError if commit_hash is not found in store.
    Matching is case-insensitive substring search.
    Scanning stops once `limit` matches are collected.
    """
    commit_data = store.read("commits", commit_hash)
    if commit_data is None:
        raise FileNotFoundError(f"Commit {commit_hash[:8]} not found in store")

    commit = deserialize_commit(commit_data)
    flat = flatten_tree(store, commit.tree_hash)

    clean_prefix = path_prefix.lstrip("/") if path_prefix else None
    query_lower = query.lower()

    matches: list[dict] = []
    total_scanned = 0
    limit_reached = False

    for path, (obj_type, obj_hash, _sidecar_hash) in sorted(flat.items()):
        if obj_type != "manifest":
            continue
        if clean_prefix is not None and not path.startswith(clean_prefix):
            continue

        manifest_data = store.read("manifests", obj_hash)
        if manifest_data is None:
            continue

        manifest = deserialize_manifest(manifest_data)

        for row_index, entry in enumerate(manifest.entries):
            row_bytes = store.read("rows", entry.row_hash)
            if row_bytes is None:
                total_scanned += 1
                continue

            row = json.loads(row_bytes)
            total_scanned += 1

            # Match check
            if field_path is None:
                # Full-row mode: serialize to JSON string and search
                text = json.dumps(row, ensure_ascii=False)
                matched = query_lower in text.lower()
                excerpt_source = text
            else:
                # Field mode: extract the field value
                value = _resolve_field(row, field_path)
                if value is None:
                    continue
                value_str = str(value) if not isinstance(value, str) else value
                matched = query_lower in value_str.lower()
                excerpt_source = value_str

            if matched:
                matches.append({
                    "file": path,
                    "row_index": row_index,
                    "row_hash": entry.row_hash,
                    "content": row,
                    "highlight": _make_highlight(excerpt_source, query),
                })

                if len(matches) == limit:
                    limit_reached = True
                    return {
                        "commit_hash": commit_hash,
                        "query": query,
                        "field_path": field_path,
                        "matches": matches,
                        "total_scanned": total_scanned,
                        "limit_reached": limit_reached,
                    }

    return {
        "commit_hash": commit_hash,
        "query": query,
        "field_path": field_path,
        "matches": matches,
        "total_scanned": total_scanned,
        "limit_reached": limit_reached,
    }
```

### 1.3 Run tests and verify

```
cd /Users/lxs/code/dit && uv run pytest tests/test_search.py -v
```

Expected output (all passing):
```
tests/test_search.py::TestResolveField::test_top_level_key PASSED
tests/test_search.py::TestResolveField::test_nested_key PASSED
tests/test_search.py::TestResolveField::test_list_index PASSED
tests/test_search.py::TestResolveField::test_missing_key_returns_none PASSED
tests/test_search.py::TestResolveField::test_out_of_range_index_returns_none PASSED
tests/test_search.py::TestResolveField::test_wrong_type_returns_none PASSED
tests/test_search.py::TestMakeHighlight::test_match_in_middle PASSED
tests/test_search.py::TestMakeHighlight::test_match_at_start_no_leading_ellipsis PASSED
tests/test_search.py::TestMakeHighlight::test_match_at_end_no_trailing_ellipsis PASSED
tests/test_search.py::TestMakeHighlight::test_short_text_no_ellipsis PASSED
tests/test_search.py::TestMakeHighlight::test_context_chars PASSED
tests/test_search.py::TestSearchRows::test_returns_commit_hash PASSED
tests/test_search.py::TestSearchRows::test_returns_query PASSED
tests/test_search.py::TestSearchRows::test_field_path_is_none_by_default PASSED
tests/test_search.py::TestSearchRows::test_matches_across_files PASSED
tests/test_search.py::TestSearchRows::test_case_insensitive PASSED
tests/test_search.py::TestSearchRows::test_no_matches_returns_empty_list PASSED
tests/test_search.py::TestSearchRows::test_match_has_expected_keys PASSED
tests/test_search.py::TestSearchRows::test_match_content_is_dict PASSED
tests/test_search.py::TestSearchRows::test_total_scanned_counts_all_rows PASSED
tests/test_search.py::TestSearchRows::test_limit_stops_scanning PASSED
tests/test_search.py::TestSearchRows::test_limit_not_reached_flag PASSED
tests/test_search.py::TestSearchRows::test_path_prefix_filter PASSED
tests/test_search.py::TestSearchRows::test_field_path_matches_specific_field PASSED
tests/test_search.py::TestSearchRows::test_field_path_matches_nested PASSED
tests/test_search.py::TestSearchRows::test_field_path_silently_skips_missing_field PASSED
tests/test_search.py::TestSearchRows::test_row_index_is_zero_based PASSED
tests/test_search.py::TestSearchRows::test_highlight_contains_query PASSED
tests/test_search.py::TestSearchRows::test_unknown_commit_raises PASSED
29 passed
```

- [ ] All 29 tests pass

---

## Task 2: CLI `dit search` command

### 2.1 Write failing CLI tests

Create `tests/test_cli_search.py`:

```python
# tests/test_cli_search.py
import json
import os
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dit.cli.main import app
from dit.core.objects import (
    Commit, Manifest, ManifestEntry,
    serialize_commit, serialize_manifest,
)
from dit.core.store import ObjectStore
from dit.core.tree_builder import build_nested_tree
from dit.core.refs import RefStore

runner = CliRunner()


def _init_repo_with_rows(tmp_path: Path) -> tuple[ObjectStore, RefStore, str]:
    """Init a dit repo with train.jsonl (3 rows) and eval.jsonl (1 row)."""
    os.chdir(tmp_path)
    runner.invoke(app, ["init"])

    dot = tmp_path / ".dit"
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)

    rows_train = [
        json.dumps({"instruction": "实现一个LRU缓存，支持get和put操作", "response": "好的"}),
        json.dumps({"instruction": "LRU缓存淘汰策略", "response": "正确"}),
        json.dumps({"instruction": "quicksort algorithm", "response": "ok"}),
    ]
    train_entries = []
    for r in rows_train:
        rh = store.write("rows", r.encode("utf-8"))
        train_entries.append(ManifestEntry(row_hash=rh, query_fingerprint=None))
    train_manifest = Manifest(entries=train_entries)
    train_mh = store.write("manifests", serialize_manifest(train_manifest))

    rows_eval = [
        json.dumps({"messages": [{"role": "user", "content": "LRU缓存的时间复杂度"}], "response": "O(1)"}),
    ]
    eval_entries = []
    for r in rows_eval:
        rh = store.write("rows", r.encode("utf-8"))
        eval_entries.append(ManifestEntry(row_hash=rh, query_fingerprint=None))
    eval_manifest = Manifest(entries=eval_entries)
    eval_mh = store.write("manifests", serialize_manifest(eval_manifest))

    tree_entries = {
        "train.jsonl": ("manifest", train_mh, None),
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


class TestSearchCommand:
    def test_search_exits_0(self, tmp_path: Path):
        _init_repo_with_rows(tmp_path)
        result = runner.invoke(app, ["search", "LRU"])
        assert result.exit_code == 0

    def test_search_shows_header(self, tmp_path: Path):
        _init_repo_with_rows(tmp_path)
        result = runner.invoke(app, ["search", "LRU"])
        assert "Searching" in result.stdout
        assert "LRU" in result.stdout

    def test_search_shows_matching_file(self, tmp_path: Path):
        _init_repo_with_rows(tmp_path)
        result = runner.invoke(app, ["search", "LRU"])
        assert "train.jsonl" in result.stdout

    def test_search_shows_match_count(self, tmp_path: Path):
        _init_repo_with_rows(tmp_path)
        result = runner.invoke(app, ["search", "LRU"])
        assert "match" in result.stdout.lower()

    def test_search_no_matches_exits_0(self, tmp_path: Path):
        _init_repo_with_rows(tmp_path)
        result = runner.invoke(app, ["search", "zzznomatch"])
        assert result.exit_code == 0
        assert "0 match" in result.stdout.lower()

    def test_search_path_filter(self, tmp_path: Path):
        _init_repo_with_rows(tmp_path)
        result = runner.invoke(app, ["search", "LRU", "train.jsonl"])
        assert result.exit_code == 0
        assert "train.jsonl" in result.stdout
        # eval.jsonl should not appear in results when filtered to train.jsonl
        lines = [l for l in result.stdout.splitlines() if "eval.jsonl" in l]
        assert len(lines) == 0

    def test_search_ref_flag(self, tmp_path: Path):
        _init_repo_with_rows(tmp_path)
        result = runner.invoke(app, ["search", "LRU", "--ref", "main"])
        assert result.exit_code == 0

    def test_search_bad_ref_exits_1(self, tmp_path: Path):
        _init_repo_with_rows(tmp_path)
        result = runner.invoke(app, ["search", "LRU", "--ref", "nonexistent"])
        assert result.exit_code == 1

    def test_search_limit_flag(self, tmp_path: Path):
        _init_repo_with_rows(tmp_path)
        result = runner.invoke(app, ["search", "LRU", "--limit", "1"])
        assert result.exit_code == 0
        # Should show limit-reached notice
        assert "limit" in result.stdout.lower()

    def test_search_field_flag(self, tmp_path: Path):
        _init_repo_with_rows(tmp_path)
        result = runner.invoke(app, ["search", "LRU", "--field", "messages[0].content"])
        assert result.exit_code == 0
        assert "eval.jsonl" in result.stdout

    def test_search_json_format(self, tmp_path: Path):
        _init_repo_with_rows(tmp_path)
        result = runner.invoke(app, ["search", "LRU", "--format", "json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert "commit_hash" in data
        assert "query" in data
        assert "matches" in data
        assert "total_scanned" in data
        assert "limit_reached" in data

    def test_search_json_matches_have_expected_keys(self, tmp_path: Path):
        _init_repo_with_rows(tmp_path)
        result = runner.invoke(app, ["search", "LRU", "--format", "json"])
        data = json.loads(result.stdout)
        for m in data["matches"]:
            assert "file" in m
            assert "row_index" in m
            assert "highlight" in m

    def test_search_outside_repo_exits_1(self, tmp_path: Path):
        empty = tmp_path / "empty"
        empty.mkdir()
        os.chdir(empty)
        result = runner.invoke(app, ["search", "LRU"])
        assert result.exit_code == 1

    def test_search_no_commits_exits_1(self, tmp_path: Path):
        os.chdir(tmp_path)
        runner.invoke(app, ["init"])
        result = runner.invoke(app, ["search", "LRU"])
        assert result.exit_code == 1
```

Run to confirm failure:

```
cd /Users/lxs/code/dit && uv run pytest tests/test_cli_search.py -v 2>&1 | head -20
```

Expected: all tests fail with `No such command 'search'`.

- [ ] All CLI tests fail as expected

### 2.2 Implement the `dit search` command

Add this command to `/Users/lxs/code/dit/src/dit/cli/main.py`, before `_fmt_tokens`:

```python
@app.command()
def search(
    query: str = typer.Argument(..., help="Substring to match (case-insensitive)"),
    path: str = typer.Argument("", help="Optional file name or directory prefix to restrict the scan"),
    ref: str = typer.Option("main", "--ref", help="Branch name or commit hash to search"),
    field: Optional[str] = typer.Option(None, "--field", help="Dot-notation field path to match within"),
    limit: int = typer.Option(50, "--limit", help="Maximum number of matches to return"),
    format: str = typer.Option("table", "--format", help="Output format: table or json"),
):
    """Search for rows matching QUERY in a commit."""
    import json as _json
    from dit.core.search import search_rows

    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)

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
        result = search_rows(
            store,
            commit_hash,
            query,
            path_prefix=path or None,
            field_path=field,
            limit=limit,
        )
    except FileNotFoundError as exc:
        typer.echo(f"fatal: {exc}", err=True)
        raise typer.Exit(1)

    if format == "json":
        typer.echo(_json.dumps(result, indent=2))
        return

    # Table format header
    ref_display = f"heads/{ref}" if refs.get_branch(ref) else ref
    if field:
        typer.echo(f'Searching {ref_display} (commit {commit_hash[:8]}) for "{query}" in field {field}')
    elif path:
        typer.echo(f'Searching {ref_display} (commit {commit_hash[:8]}) for "{query}" in {path}')
    else:
        typer.echo(f'Searching {ref_display} (commit {commit_hash[:8]}) for "{query}"')
    typer.echo("")

    matches = result["matches"]

    if not matches:
        typer.echo("0 matches")
        typer.echo(f"(scanned {result['total_scanned']} rows)")
        return

    # Column widths
    col_file = max(len(m["file"]) for m in matches)
    col_file = max(col_file, 4)
    sep = "\u2500" * (col_file + 8 + 50)

    header = f"{'File':<{col_file}}  {'Row':>5}  Excerpt"
    typer.echo(header)
    typer.echo(sep)

    for m in matches:
        excerpt = m["highlight"].replace("\n", " ")
        typer.echo(f"{m['file']:<{col_file}}  {m['row_index']:>5}  {excerpt}")

    typer.echo(sep)

    match_word = "match" if len(matches) == 1 else "matches"
    typer.echo(f"{len(matches)} {match_word} (scanned {result['total_scanned']} rows)")

    if result["limit_reached"]:
        typer.echo(f"Limit reached. Pass --limit N to see more.")
```

### 2.3 Run CLI tests and verify

```
cd /Users/lxs/code/dit && uv run pytest tests/test_cli_search.py -v
```

Expected output:
```
tests/test_cli_search.py::TestSearchCommand::test_search_exits_0 PASSED
tests/test_cli_search.py::TestSearchCommand::test_search_shows_header PASSED
tests/test_cli_search.py::TestSearchCommand::test_search_shows_matching_file PASSED
tests/test_cli_search.py::TestSearchCommand::test_search_shows_match_count PASSED
tests/test_cli_search.py::TestSearchCommand::test_search_no_matches_exits_0 PASSED
tests/test_cli_search.py::TestSearchCommand::test_search_path_filter PASSED
tests/test_cli_search.py::TestSearchCommand::test_search_ref_flag PASSED
tests/test_cli_search.py::TestSearchCommand::test_search_bad_ref_exits_1 PASSED
tests/test_cli_search.py::TestSearchCommand::test_search_limit_flag PASSED
tests/test_cli_search.py::TestSearchCommand::test_search_field_flag PASSED
tests/test_cli_search.py::TestSearchCommand::test_search_json_format PASSED
tests/test_cli_search.py::TestSearchCommand::test_search_json_matches_have_expected_keys PASSED
tests/test_cli_search.py::TestSearchCommand::test_search_outside_repo_exits_1 PASSED
tests/test_cli_search.py::TestSearchCommand::test_search_no_commits_exits_1 PASSED
14 passed
```

- [ ] All 14 CLI tests pass

---

## Task 3: Server search endpoint

### 3.1 Write failing server tests

Create `tests/server/test_routes_search.py`:

```python
# tests/server/test_routes_search.py
import json
import time
from pathlib import Path

import pytest
from httpx import AsyncClient

from dit.core.objects import (
    Commit, Manifest, ManifestEntry,
    serialize_commit, serialize_manifest,
)
from dit.core.store import ObjectStore
from dit.core.tree_builder import build_nested_tree


async def _create_repo_with_rows(
    client: AsyncClient,
    tmp_path: Path,
    repo: str = "search-repo",
) -> tuple[ObjectStore, str]:
    resp = await client.post("/api/v1/repos", json={"name": repo})
    assert resp.status_code == 201

    data_dir = tmp_path / "data"
    store = ObjectStore(data_dir / "repos" / repo / "objects")

    rows_train = [
        json.dumps({"instruction": "实现一个LRU缓存，支持get和put操作", "response": "好的"}),
        json.dumps({"instruction": "LRU缓存淘汰策略", "response": "正确"}),
        json.dumps({"instruction": "quicksort", "response": "ok"}),
    ]
    train_entries = []
    for r in rows_train:
        rh = store.write("rows", r.encode("utf-8"))
        train_entries.append(ManifestEntry(row_hash=rh, query_fingerprint=None))
    train_manifest = Manifest(entries=train_entries)
    train_mh = store.write("manifests", serialize_manifest(train_manifest))

    rows_eval = [
        json.dumps({"messages": [{"role": "user", "content": "LRU缓存时间复杂度"}], "response": "O(1)"}),
    ]
    eval_entries = []
    for r in rows_eval:
        rh = store.write("rows", r.encode("utf-8"))
        eval_entries.append(ManifestEntry(row_hash=rh, query_fingerprint=None))
    eval_manifest = Manifest(entries=eval_entries)
    eval_mh = store.write("manifests", serialize_manifest(eval_manifest))

    tree_entries = {
        "train.jsonl": ("manifest", train_mh, None),
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
class TestSearchEndpoint:
    async def test_search_returns_200(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash = await _create_repo_with_rows(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/search-repo/search",
            json={"ref": commit_hash, "query": "LRU"},
        )
        assert resp.status_code == 200

    async def test_search_response_has_required_keys(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash = await _create_repo_with_rows(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/search-repo/search",
            json={"ref": commit_hash, "query": "LRU"},
        )
        data = resp.json()
        assert "commit_hash" in data
        assert "query" in data
        assert "matches" in data
        assert "total_scanned" in data
        assert "limit_reached" in data

    async def test_search_returns_matches(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash = await _create_repo_with_rows(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/search-repo/search",
            json={"ref": commit_hash, "query": "LRU"},
        )
        data = resp.json()
        assert len(data["matches"]) > 0

    async def test_search_match_has_expected_keys(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash = await _create_repo_with_rows(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/search-repo/search",
            json={"ref": commit_hash, "query": "LRU"},
        )
        m = resp.json()["matches"][0]
        assert "file" in m
        assert "row_index" in m
        assert "row_hash" in m
        assert "content" in m
        assert "highlight" in m

    async def test_search_no_matches_returns_empty_list(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash = await _create_repo_with_rows(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/search-repo/search",
            json={"ref": commit_hash, "query": "zzznomatch"},
        )
        assert resp.status_code == 200
        assert resp.json()["matches"] == []

    async def test_search_with_file_filter(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash = await _create_repo_with_rows(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/search-repo/search",
            json={"ref": commit_hash, "query": "LRU", "file": "train.jsonl"},
        )
        data = resp.json()
        files = {m["file"] for m in data["matches"]}
        assert "eval.jsonl" not in files

    async def test_search_with_field_filter(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash = await _create_repo_with_rows(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/search-repo/search",
            json={"ref": commit_hash, "query": "LRU", "field": "messages[0].content"},
        )
        data = resp.json()
        assert len(data["matches"]) == 1
        assert data["matches"][0]["file"] == "eval.jsonl"

    async def test_search_with_limit(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash = await _create_repo_with_rows(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/search-repo/search",
            json={"ref": commit_hash, "query": "LRU", "limit": 1},
        )
        data = resp.json()
        assert len(data["matches"]) == 1
        assert data["limit_reached"] is True

    async def test_search_with_branch_ref(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash = await _create_repo_with_rows(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/search-repo/search",
            json={"ref": "heads/main", "query": "LRU"},
        )
        assert resp.status_code == 200

    async def test_search_missing_query_returns_422(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash = await _create_repo_with_rows(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/search-repo/search",
            json={"ref": commit_hash},
        )
        assert resp.status_code == 422

    async def test_search_bad_ref_returns_404(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash = await _create_repo_with_rows(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/search-repo/search",
            json={"ref": "heads/nonexistent", "query": "LRU"},
        )
        assert resp.status_code == 404

    async def test_search_unknown_repo_returns_404(self, client: AsyncClient, tmp_path: Path):
        resp = await client.post(
            "/api/v1/repos/no-such-repo/search",
            json={"ref": "heads/main", "query": "LRU"},
        )
        assert resp.status_code == 404
```

Run to confirm failure:

```
cd /Users/lxs/code/dit && uv run pytest tests/server/test_routes_search.py -v 2>&1 | head -20
```

Expected: `404 Not Found` (route not registered yet).

- [ ] All server tests fail as expected (404)

### 3.2 Implement `src/dit/server/routes/search_api.py`

Create `/Users/lxs/code/dit/src/dit/server/routes/search_api.py`:

```python
# src/dit/server/routes/search_api.py
"""Row-level search endpoint: POST /{repo}/search"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.auth import get_session, require_permission
from dit.server.models import Ref
from dit.server.routes._helpers import _get_repo

router = APIRouter(prefix="/api/v1/repos", tags=["search"])


def _store_for_repo(request: Request, repo_name: str):
    from dit.core.store import ObjectStore
    data_dir: Path = request.app.state.data_dir
    return ObjectStore(Path(data_dir) / "repos" / repo_name / "objects")


class SearchRequest(BaseModel):
    ref: str = "heads/main"
    query: str
    file: str | None = None
    field: str | None = None
    limit: int = 50


@router.post("/{repo}/search")
async def repo_search_endpoint(
    repo: str,
    body: SearchRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("read")),
):
    """Brute-force substring search across rows in a commit."""
    from dit.core.search import search_rows

    r = await _get_repo(repo, session)

    # Resolve ref to commit hash
    # If body.ref looks like a full hex hash (64 chars), use it directly
    if len(body.ref) == 64 and all(c in "0123456789abcdef" for c in body.ref):
        commit_hash = body.ref
    else:
        # Treat body.ref as a ref name (e.g. "heads/main")
        result = await session.execute(
            select(Ref).where(Ref.repo_id == r.id, Ref.name == body.ref)
        )
        ref_obj = result.scalar_one_or_none()
        if ref_obj is None:
            raise HTTPException(status_code=404, detail=f"Ref '{body.ref}' not found")
        commit_hash = ref_obj.target_hash

    store = _store_for_repo(request, repo)

    try:
        result = search_rows(
            store,
            commit_hash,
            body.query,
            path_prefix=body.file,
            field_path=body.field,
            limit=body.limit,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return result
```

### 3.3 Register the router in `src/dit/server/app.py`

In `/Users/lxs/code/dit/src/dit/server/app.py`, add after the `stats_router` block:

```python
    from dit.server.routes.search_api import router as search_router
    application.include_router(search_router)
```

The diff from the current end of `create_app`:

```python
    from dit.server.routes.stats_api import router as stats_router
    application.include_router(stats_router)

    from dit.server.routes.search_api import router as search_router
    application.include_router(search_router)

    return application
```

### 3.4 Run server tests and verify

```
cd /Users/lxs/code/dit && uv run pytest tests/server/test_routes_search.py -v
```

Expected output:
```
tests/server/test_routes_search.py::TestSearchEndpoint::test_search_returns_200 PASSED
tests/server/test_routes_search.py::TestSearchEndpoint::test_search_response_has_required_keys PASSED
tests/server/test_routes_search.py::TestSearchEndpoint::test_search_returns_matches PASSED
tests/server/test_routes_search.py::TestSearchEndpoint::test_search_match_has_expected_keys PASSED
tests/server/test_routes_search.py::TestSearchEndpoint::test_search_no_matches_returns_empty_list PASSED
tests/server/test_routes_search.py::TestSearchEndpoint::test_search_with_file_filter PASSED
tests/server/test_routes_search.py::TestSearchEndpoint::test_search_with_field_filter PASSED
tests/server/test_routes_search.py::TestSearchEndpoint::test_search_with_limit PASSED
tests/server/test_routes_search.py::TestSearchEndpoint::test_search_with_branch_ref PASSED
tests/server/test_routes_search.py::TestSearchEndpoint::test_search_missing_query_returns_422 PASSED
tests/server/test_routes_search.py::TestSearchEndpoint::test_search_bad_ref_returns_404 PASSED
tests/server/test_routes_search.py::TestSearchEndpoint::test_search_unknown_repo_returns_404 PASSED
12 passed
```

- [ ] All 12 server tests pass

---

## Task 4: Gateway proxy

### 4.1 Add client method to `modules/dit/client.go`

File: `/Users/lxs/code/datahub-gateway/modules/dit/client.go`

Add after the `GetStats` method at the bottom of the file:

```go
func (c *Client) Search(ctx context.Context, repoName string, body []byte) ([]byte, int, error) {
	return c.do(ctx, http.MethodPost, "/api/v1/repos/"+repoName+"/search", body)
}
```

### 4.2 Add handler to `routers/api/v1/repo/dit.go`

File: `/Users/lxs/code/datahub-gateway/routers/api/v1/repo/dit.go`

Add after the `DatahubGetStats` function at the bottom of the file:

```go
func DatahubSearch(ctx *context.APIContext) {
	body, ok := readBody(ctx)
	if !ok {
		return
	}
	proxyToDatahub(ctx, func() ([]byte, int, error) {
		return dit.DefaultClient().Search(ctx, ctx.Repo.Repository.Name, body)
	})
}
```

### 4.3 Register route in `routers/api/v1/api.go`

File: `/Users/lxs/code/datahub-gateway/routers/api/v1/api.go`

Add `m.Post("/search", repo.DatahubSearch)` inside the `/dit` group, after `m.Get("/stats/{commit}", repo.DatahubGetStats)`:

```go
				m.Get("/stats/{commit}", repo.DatahubGetStats)
				m.Post("/search", repo.DatahubSearch)
			})
```

### 4.4 Verify gateway compiles

```
cd /Users/lxs/code/datahub-gateway && git diff --stat
```

Then verify it builds without errors:

```
cd /Users/lxs/code/datahub-gateway && go build ./...
```

Expected: exits 0 with no output.

- [ ] `go build ./...` exits 0

---

## Task 5: Vue search UI

### 5.1 Edit `DataRepoHome.vue`

File: `/Users/lxs/code/datahub-gateway/web_src/js/components/DataRepoHome.vue`

**Template change:** Insert the search bar and results panel between the Stats panel and the JSONL Viewer. In the template, after the closing `</div>` of the Stats panel (line ending `</div>` before `<!-- JSONL Viewer -->`), add:

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

**Script `data()` change:** Add these six fields to the `data()` return object, after `repoStats: null`:

```js
      searchQuery: '',
      searchField: '',
      searchLoading: false,
      searchError: null,
      searchResults: null,
      searchResultsOpen: true,
```

**Script `loadTree()` change:** In `loadTree()`, after the lines that reset `repoStats` and `statsOpen`, add the three search-reset lines:

```js
      this.commitHash = commitHash;
      this.repoStats = null;
      this.statsOpen = false;
      this.searchResults = null;
      this.searchQuery = '';
      this.searchField = '';
```

**Script `methods` change:** Add `submitSearch` after `loadStats`:

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

### 5.2 Verify no syntax errors

```
cd /Users/lxs/code/datahub-gateway && node --input-type=module < /dev/null 2>&1 || true
```

For a quick sanity check, verify the Vue file has balanced template tags:

```
cd /Users/lxs/code/datahub-gateway && python3 -c "
import re, sys
content = open('web_src/js/components/DataRepoHome.vue').read()
# Count template open/close
opens = len(re.findall(r'<template', content))
closes = len(re.findall(r'</template>', content))
print(f'<template> opens: {opens}, </template> closes: {closes}')
assert opens == closes, 'Mismatched template tags'
print('OK')
"
```

Expected: `OK`

- [ ] Vue file has balanced template tags
- [ ] `searchQuery`, `searchField`, `searchLoading`, `searchError`, `searchResults`, `searchResultsOpen` present in `data()`
- [ ] `submitSearch` method present

---

## Task 6: Final verification

### 6.1 Run all new tests together

```
cd /Users/lxs/code/dit && uv run pytest tests/test_search.py tests/test_cli_search.py tests/server/test_routes_search.py -v
```

Expected: 55 passed (29 + 14 + 12).

- [ ] 55 tests pass

### 6.2 Run full test suite to check for regressions

```
cd /Users/lxs/code/dit && uv run pytest tests/ -v 2>&1 | tail -20
```

Expected: all pre-existing tests still pass; no regressions.

- [ ] No regressions in the existing test suite

### 6.3 Gateway build check

```
cd /Users/lxs/code/datahub-gateway && go build ./...
```

Expected: exits 0.

- [ ] Gateway builds cleanly

### 6.4 Smoke test (optional, requires running server)

If the server is running at `http://localhost:8000`, verify the endpoint responds:

```
curl -s -X POST http://localhost:8000/api/v1/repos/test-repo/search \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer <token>' \
  -d '{"ref":"heads/main","query":"hello","limit":5}' | python3 -m json.tool
```

Expected: JSON object with `commit_hash`, `query`, `matches`, `total_scanned`, `limit_reached`.

- [ ] Smoke test returns valid JSON

---

## File change summary

| File | Action |
|------|--------|
| `src/dit/core/search.py` | Create — `search_rows`, `_resolve_field`, `_make_highlight` |
| `src/dit/cli/main.py` | Edit — add `search` command |
| `src/dit/server/routes/search_api.py` | Create — `SearchRequest`, `repo_search_endpoint` |
| `src/dit/server/app.py` | Edit — register `search_router` |
| `tests/test_search.py` | Create — 29 unit tests |
| `tests/test_cli_search.py` | Create — 14 CLI tests |
| `tests/server/test_routes_search.py` | Create — 12 server tests |
| `routers/api/v1/repo/dit.go` | Edit — add `DatahubSearch` handler |
| `modules/dit/client.go` | Edit — add `Search` method |
| `routers/api/v1/api.go` | Edit — register `m.Post("/search", ...)` |
| `web_src/js/components/DataRepoHome.vue` | Edit — search bar, results panel, data fields, `submitSearch` |
