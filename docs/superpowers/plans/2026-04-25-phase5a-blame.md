# Phase 5A: Blame — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `dit blame` CLI command, core blame module, server API endpoint, gateway proxy route, and Vue UI blame panel that trace every JSONL row to the commit that introduced or last refreshed it.

**Architecture:** Walk commit history backward (first-parent), using `diff_manifests` at each step to identify which rows were introduced. Early exit when all rows are attributed. Row history tracks a single row via `query_fingerprint` across refreshes.

**Tech Stack:** Python (core + CLI + FastAPI), Go (gateway proxy), Vue 3 (web UI)

---

## File Structure

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `src/dit/core/blame.py` | Core `blame_file()` and `row_history()` functions |
| Create | `tests/test_blame.py` | Unit tests for core blame module |
| Create | `src/dit/server/routes/blame_api.py` | FastAPI endpoint for blame |
| Create | `tests/server/test_routes_blame.py` | Server route tests |
| Modify | `src/dit/server/app.py` | Register blame_router |
| Modify | `src/dit/cli/main.py` | Add `dit blame` command |
| Create | `tests/test_cli_blame.py` | CLI blame command tests |
| Modify | `~/code/datahub-gateway/modules/dit/client.go` | Add `GetBlame()` method |
| Modify | `~/code/datahub-gateway/routers/api/v1/repo/dit.go` | Add `DatahubGetBlame` handler |
| Modify | `~/code/datahub-gateway/routers/api/v1/api.go` | Register blame route |
| Modify | `~/code/datahub-gateway/web_src/js/components/DataRepoHome.vue` | Blame panel in file detail view |

---

### Task 1: Core blame module — `blame_file()`

**Files:**
- Create: `src/dit/core/blame.py`
- Create: `tests/test_blame.py`

- [ ] **Step 1: Write failing tests for `blame_file`**

Create `tests/test_blame.py` with a test helper that builds a repo with multiple commits and then tests blame attribution:

```python
"""Tests for dit.core.blame module."""
import json
import time

from dit.core.blame import blame_file, row_history
from dit.core.objects import (
    Commit, Manifest, ManifestEntry, Tree, TreeEntry,
    serialize_manifest, serialize_tree, serialize_commit,
    object_hash,
)
from dit.core.store import ObjectStore


def _write_row(store: ObjectStore, content: dict) -> str:
    """Write a row object and return its hash."""
    data = json.dumps(content, separators=(",", ":"), sort_keys=True).encode()
    return store.write("rows", data)


def _make_commit(
    store: ObjectStore,
    files: dict[str, list[dict]],
    parent_hashes: list[str] | None = None,
    author: str = "alice",
    message: str = "commit",
    timestamp: int | None = None,
) -> str:
    """Build a commit with the given file→rows mapping. Returns commit hash."""
    from dit.core.hash import row_hash as compute_row_hash, query_fingerprint as compute_qfp

    tree_entries = []
    for fname, rows in files.items():
        entries = []
        for row in rows:
            rh = compute_row_hash(row)
            _write_row(store, row)
            qfp = compute_qfp(row) if "messages" in row else None
            entries.append(ManifestEntry(row_hash=rh, query_fingerprint=qfp))
        manifest = Manifest(entries=entries)
        m_bytes = serialize_manifest(manifest)
        m_hash = store.write("manifests", m_bytes)
        tree_entries.append(TreeEntry(name=fname, obj_type="manifest", obj_hash=m_hash))

    tree = Tree(entries=tree_entries)
    t_bytes = serialize_tree(tree)
    t_hash = store.write("trees", t_bytes)

    c = Commit(
        tree_hash=t_hash,
        parent_hashes=parent_hashes or [],
        author=author,
        message=message,
        timestamp=timestamp or int(time.time()),
    )
    c_bytes = serialize_commit(c)
    return store.write("commits", c_bytes)


class TestBlameFile:
    """Tests for blame_file()."""

    def test_single_commit_all_rows_blamed_to_it(self, tmp_path):
        store = ObjectStore(tmp_path / "objects")
        rows = [
            {"text": "hello", "label": "pos"},
            {"text": "world", "label": "neg"},
        ]
        c1 = _make_commit(store, {"train.jsonl": rows}, author="alice", timestamp=1000)

        result = blame_file(store, c1, "train.jsonl")

        assert result["file"] == "train.jsonl"
        assert result["commit_hash"] == c1
        assert len(result["entries"]) == 2
        for entry in result["entries"]:
            assert entry["commit_hash"] == c1
            assert entry["author"] == "alice"
            assert entry["timestamp"] == 1000
        assert result["summary"]["total_rows"] == 2
        assert result["summary"]["unique_commits"] == 1
        assert result["summary"]["unique_authors"] == 1

    def test_two_commits_new_rows_blamed_to_second(self, tmp_path):
        store = ObjectStore(tmp_path / "objects")
        row_a = {"text": "hello", "label": "pos"}
        row_b = {"text": "world", "label": "neg"}

        c1 = _make_commit(store, {"train.jsonl": [row_a]}, author="alice", timestamp=1000)
        c2 = _make_commit(
            store, {"train.jsonl": [row_a, row_b]},
            parent_hashes=[c1], author="bob", timestamp=2000,
        )

        result = blame_file(store, c2, "train.jsonl")

        entries = result["entries"]
        assert len(entries) == 2
        # row_a was in c1
        assert entries[0]["commit_hash"] == c1
        assert entries[0]["author"] == "alice"
        # row_b was added in c2
        assert entries[1]["commit_hash"] == c2
        assert entries[1]["author"] == "bob"

    def test_refreshed_row_blamed_to_refresh_commit(self, tmp_path):
        store = ObjectStore(tmp_path / "objects")
        # Same query, different response → refresh
        row_v1 = {"messages": [{"role": "user", "content": "hi"}], "response": "old"}
        row_v2 = {"messages": [{"role": "user", "content": "hi"}], "response": "new"}

        c1 = _make_commit(store, {"train.jsonl": [row_v1]}, author="alice", timestamp=1000)
        c2 = _make_commit(
            store, {"train.jsonl": [row_v2]},
            parent_hashes=[c1], author="bob", timestamp=2000,
        )

        result = blame_file(store, c2, "train.jsonl")
        entries = result["entries"]
        assert len(entries) == 1
        # The refreshed row should be blamed to c2
        assert entries[0]["commit_hash"] == c2
        assert entries[0]["author"] == "bob"

    def test_unchanged_rows_blamed_to_original_commit(self, tmp_path):
        store = ObjectStore(tmp_path / "objects")
        row_a = {"text": "stable", "label": "pos"}
        row_b = {"text": "also stable", "label": "neg"}

        c1 = _make_commit(store, {"train.jsonl": [row_a, row_b]}, author="alice", timestamp=1000)
        # c2 has same rows — no changes to train.jsonl
        c2 = _make_commit(
            store, {"train.jsonl": [row_a, row_b]},
            parent_hashes=[c1], author="bob", timestamp=2000,
        )

        result = blame_file(store, c2, "train.jsonl")
        entries = result["entries"]
        assert len(entries) == 2
        # Both rows should be blamed to c1 (original)
        for entry in entries:
            assert entry["commit_hash"] == c1
            assert entry["author"] == "alice"

    def test_file_not_found_raises(self, tmp_path):
        store = ObjectStore(tmp_path / "objects")
        c1 = _make_commit(store, {"train.jsonl": [{"a": 1}]}, timestamp=1000)

        import pytest
        with pytest.raises(FileNotFoundError):
            blame_file(store, c1, "nonexistent.jsonl")

    def test_commit_not_found_raises(self, tmp_path):
        store = ObjectStore(tmp_path / "objects")

        import pytest
        with pytest.raises(FileNotFoundError):
            blame_file(store, "0" * 64, "train.jsonl")

    def test_three_commits_mixed_attribution(self, tmp_path):
        store = ObjectStore(tmp_path / "objects")
        row_a = {"text": "alpha", "label": "a"}
        row_b = {"text": "beta", "label": "b"}
        row_c = {"text": "gamma", "label": "c"}

        c1 = _make_commit(store, {"train.jsonl": [row_a]}, author="alice", timestamp=1000)
        c2 = _make_commit(
            store, {"train.jsonl": [row_a, row_b]},
            parent_hashes=[c1], author="bob", timestamp=2000,
        )
        c3 = _make_commit(
            store, {"train.jsonl": [row_a, row_b, row_c]},
            parent_hashes=[c2], author="carol", timestamp=3000,
        )

        result = blame_file(store, c3, "train.jsonl")
        entries = result["entries"]
        assert len(entries) == 3
        assert entries[0]["commit_hash"] == c1  # row_a from alice
        assert entries[1]["commit_hash"] == c2  # row_b from bob
        assert entries[2]["commit_hash"] == c3  # row_c from carol

    def test_content_preview_present(self, tmp_path):
        store = ObjectStore(tmp_path / "objects")
        row = {"text": "hello world this is a test", "label": "positive"}
        c1 = _make_commit(store, {"train.jsonl": [row]}, timestamp=1000)

        result = blame_file(store, c1, "train.jsonl")
        assert "content_preview" in result["entries"][0]
        assert len(result["entries"][0]["content_preview"]) > 0


class TestRowHistory:
    """Tests for row_history()."""

    def test_single_commit_shows_added_event(self, tmp_path):
        store = ObjectStore(tmp_path / "objects")
        row = {"text": "hello", "label": "pos"}
        c1 = _make_commit(store, {"train.jsonl": [row]}, author="alice", timestamp=1000)

        result = row_history(store, c1, "train.jsonl", 0)
        assert result["row_index"] == 0
        assert len(result["events"]) == 1
        assert result["events"][0]["event"] == "added"
        assert result["events"][0]["commit_hash"] == c1

    def test_refresh_shows_both_events(self, tmp_path):
        store = ObjectStore(tmp_path / "objects")
        row_v1 = {"messages": [{"role": "user", "content": "hi"}], "response": "old"}
        row_v2 = {"messages": [{"role": "user", "content": "hi"}], "response": "new"}

        c1 = _make_commit(store, {"train.jsonl": [row_v1]}, author="alice", timestamp=1000)
        c2 = _make_commit(
            store, {"train.jsonl": [row_v2]},
            parent_hashes=[c1], author="bob", timestamp=2000,
        )

        result = row_history(store, c2, "train.jsonl", 0)
        events = result["events"]
        assert len(events) == 2
        # Newest first
        assert events[0]["event"] == "refresh"
        assert events[0]["commit_hash"] == c2
        assert events[1]["event"] == "added"
        assert events[1]["commit_hash"] == c1

    def test_row_index_out_of_range_raises(self, tmp_path):
        store = ObjectStore(tmp_path / "objects")
        c1 = _make_commit(store, {"train.jsonl": [{"a": 1}]}, timestamp=1000)

        import pytest
        with pytest.raises(IndexError):
            row_history(store, c1, "train.jsonl", 5)

    def test_row_without_query_fingerprint_tracks_by_hash(self, tmp_path):
        store = ObjectStore(tmp_path / "objects")
        row = {"text": "simple row", "label": "x"}
        c1 = _make_commit(store, {"train.jsonl": [row]}, author="alice", timestamp=1000)
        c2 = _make_commit(
            store, {"train.jsonl": [row]},
            parent_hashes=[c1], author="bob", timestamp=2000,
        )

        result = row_history(store, c2, "train.jsonl", 0)
        assert result["query_fingerprint"] is None
        assert len(result["events"]) == 1
        assert result["events"][0]["event"] == "added"
        assert result["events"][0]["commit_hash"] == c1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/code/dit && uv run pytest tests/test_blame.py -v`
Expected: `ModuleNotFoundError: No module named 'dit.core.blame'`

- [ ] **Step 3: Implement `blame_file()` and `row_history()`**

Create `src/dit/core/blame.py`:

```python
"""Blame: trace each row in a file to the commit that introduced it."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

from dit.core.diff import diff_manifests
from dit.core.objects import (
    Manifest, deserialize_commit, deserialize_manifest,
)
from dit.core.store import ObjectStore
from dit.core.tree_walker import flatten_tree


@dataclass(frozen=True)
class BlameEntry:
    row_index: int
    row_hash: str
    commit_hash: str
    author: str
    timestamp: int
    query_fingerprint: Optional[str]


def _get_manifest_for_file(
    store: ObjectStore, commit_hash: str, file_path: str,
) -> tuple[Manifest, str]:
    """Load a commit's manifest for a given file path.

    Returns (manifest, manifest_hash).
    Raises FileNotFoundError if commit or file not found.
    """
    commit_data = store.read("commits", commit_hash)
    if commit_data is None:
        raise FileNotFoundError(f"Commit {commit_hash[:8]} not found")
    commit = deserialize_commit(commit_data)
    flat = flatten_tree(store, commit.tree_hash)
    clean = file_path.lstrip("/")
    if clean not in flat:
        raise FileNotFoundError(f"File '{clean}' not found in commit {commit_hash[:8]}")
    obj_type, obj_hash, _sidecar = flat[clean]
    if obj_type != "manifest":
        raise FileNotFoundError(f"'{clean}' is not a manifest (type={obj_type})")
    m_data = store.read("manifests", obj_hash)
    if m_data is None:
        raise FileNotFoundError(f"Manifest object {obj_hash[:8]} missing from store")
    return deserialize_manifest(m_data), obj_hash


def _try_get_manifest(
    store: ObjectStore, commit_hash: str, file_path: str,
) -> Manifest | None:
    """Like _get_manifest_for_file but returns None if file not found."""
    try:
        m, _ = _get_manifest_for_file(store, commit_hash, file_path)
        return m
    except FileNotFoundError:
        return None


def _content_preview(store: ObjectStore, row_hash: str, max_len: int = 60) -> str:
    """Read a row object and return a truncated JSON string."""
    data = store.read("rows", row_hash)
    if data is None:
        return ""
    text = data.decode("utf-8", errors="replace")
    if len(text) > max_len:
        return text[:max_len] + "..."
    return text


def blame_file(
    store: ObjectStore,
    commit_hash: str,
    file_path: str,
) -> dict:
    target_manifest, _ = _get_manifest_for_file(store, commit_hash, file_path)

    unattributed: set[str] = {e.row_hash for e in target_manifest.entries}
    blame_map: dict[str, BlameEntry] = {}

    current_hash = commit_hash
    current_manifest = target_manifest

    while current_hash and unattributed:
        commit_data = store.read("commits", current_hash)
        if commit_data is None:
            break
        commit = deserialize_commit(commit_data)

        parent_hash = commit.parent_hashes[0] if commit.parent_hashes else None

        if parent_hash is None:
            for i, entry in enumerate(target_manifest.entries):
                if entry.row_hash in unattributed:
                    blame_map[entry.row_hash] = BlameEntry(
                        row_index=i,
                        row_hash=entry.row_hash,
                        commit_hash=current_hash,
                        author=commit.author,
                        timestamp=commit.timestamp,
                        query_fingerprint=entry.query_fingerprint,
                    )
            unattributed.clear()
            break

        parent_manifest = _try_get_manifest(store, parent_hash, file_path)

        if parent_manifest is None:
            for i, entry in enumerate(target_manifest.entries):
                if entry.row_hash in unattributed:
                    blame_map[entry.row_hash] = BlameEntry(
                        row_index=i,
                        row_hash=entry.row_hash,
                        commit_hash=current_hash,
                        author=commit.author,
                        timestamp=commit.timestamp,
                        query_fingerprint=entry.query_fingerprint,
                    )
            unattributed.clear()
            break

        diff = diff_manifests(parent_manifest, current_manifest)

        for added_entry in diff.added:
            if added_entry.row_hash in unattributed:
                idx = next(
                    i for i, e in enumerate(target_manifest.entries)
                    if e.row_hash == added_entry.row_hash
                )
                blame_map[added_entry.row_hash] = BlameEntry(
                    row_index=idx,
                    row_hash=added_entry.row_hash,
                    commit_hash=current_hash,
                    author=commit.author,
                    timestamp=commit.timestamp,
                    query_fingerprint=added_entry.query_fingerprint,
                )
                unattributed.discard(added_entry.row_hash)

        for old_hash, new_hash, qfp in diff.refreshed:
            if new_hash in unattributed:
                idx = next(
                    i for i, e in enumerate(target_manifest.entries)
                    if e.row_hash == new_hash
                )
                blame_map[new_hash] = BlameEntry(
                    row_index=idx,
                    row_hash=new_hash,
                    commit_hash=current_hash,
                    author=commit.author,
                    timestamp=commit.timestamp,
                    query_fingerprint=qfp,
                )
                unattributed.discard(new_hash)

        current_hash = parent_hash
        current_manifest = parent_manifest

    entries = []
    for i, me in enumerate(target_manifest.entries):
        be = blame_map.get(me.row_hash)
        entries.append({
            "row_index": i,
            "row_hash": me.row_hash,
            "commit_hash": be.commit_hash if be else commit_hash,
            "author": be.author if be else "unknown",
            "timestamp": be.timestamp if be else 0,
            "query_fingerprint": me.query_fingerprint,
            "content_preview": _content_preview(store, me.row_hash),
        })

    unique_commits = set(e["commit_hash"] for e in entries)
    unique_authors = set(e["author"] for e in entries)

    return {
        "commit_hash": commit_hash,
        "file": file_path.lstrip("/"),
        "entries": entries,
        "summary": {
            "total_rows": len(entries),
            "unique_commits": len(unique_commits),
            "unique_authors": len(unique_authors),
        },
    }


def row_history(
    store: ObjectStore,
    commit_hash: str,
    file_path: str,
    row_index: int,
) -> dict:
    target_manifest, _ = _get_manifest_for_file(store, commit_hash, file_path)

    if row_index < 0 or row_index >= len(target_manifest.entries):
        raise IndexError(
            f"Row index {row_index} out of range (file has {len(target_manifest.entries)} rows)"
        )

    target_entry = target_manifest.entries[row_index]
    qfp = target_entry.query_fingerprint
    tracked_hash = target_entry.row_hash

    events: list[dict] = []
    current_hash = commit_hash
    current_manifest = target_manifest

    while current_hash:
        commit_data = store.read("commits", current_hash)
        if commit_data is None:
            break
        commit = deserialize_commit(commit_data)
        parent_hash = commit.parent_hashes[0] if commit.parent_hashes else None

        if parent_hash is None:
            current_hashes = {e.row_hash for e in current_manifest.entries}
            if tracked_hash in current_hashes:
                events.append({
                    "commit_hash": current_hash,
                    "author": commit.author,
                    "timestamp": commit.timestamp,
                    "event": "added",
                    "row_hash": tracked_hash,
                    "content_preview": _content_preview(store, tracked_hash),
                })
            break

        parent_manifest = _try_get_manifest(store, parent_hash, file_path)

        if parent_manifest is None:
            current_hashes = {e.row_hash for e in current_manifest.entries}
            if tracked_hash in current_hashes:
                events.append({
                    "commit_hash": current_hash,
                    "author": commit.author,
                    "timestamp": commit.timestamp,
                    "event": "added",
                    "row_hash": tracked_hash,
                    "content_preview": _content_preview(store, tracked_hash),
                })
            break

        diff = diff_manifests(parent_manifest, current_manifest)

        for added_entry in diff.added:
            if added_entry.row_hash == tracked_hash:
                events.append({
                    "commit_hash": current_hash,
                    "author": commit.author,
                    "timestamp": commit.timestamp,
                    "event": "added",
                    "row_hash": tracked_hash,
                    "content_preview": _content_preview(store, tracked_hash),
                })
                tracked_hash = None
                break

        if tracked_hash is None:
            break

        for old_hash, new_hash, r_qfp in diff.refreshed:
            if new_hash == tracked_hash:
                events.append({
                    "commit_hash": current_hash,
                    "author": commit.author,
                    "timestamp": commit.timestamp,
                    "event": "refresh",
                    "row_hash": new_hash,
                    "content_preview": _content_preview(store, new_hash),
                })
                tracked_hash = old_hash
                break

        current_hash = parent_hash
        current_manifest = parent_manifest

    return {
        "commit_hash": commit_hash,
        "file": file_path.lstrip("/"),
        "row_index": row_index,
        "query_fingerprint": qfp,
        "events": events,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/code/dit && uv run pytest tests/test_blame.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
cd ~/code/dit
git add src/dit/core/blame.py tests/test_blame.py
git commit -m "feat: add core blame module with blame_file() and row_history()"
```

---

### Task 2: Server blame endpoint

**Files:**
- Create: `src/dit/server/routes/blame_api.py`
- Modify: `src/dit/server/app.py`
- Create: `tests/server/test_routes_blame.py`

- [ ] **Step 1: Write failing tests for the blame endpoint**

Create `tests/server/test_routes_blame.py`:

```python
"""Tests for the blame API endpoint."""
import json
import time

import pytest
from httpx import ASGITransport, AsyncClient

from dit.core.objects import (
    Commit, Manifest, ManifestEntry, Tree, TreeEntry,
    serialize_manifest, serialize_tree, serialize_commit,
)
from dit.core.store import ObjectStore
from dit.core.hash import row_hash as compute_row_hash, query_fingerprint as compute_qfp


@pytest.fixture
def setup_blame_repo(app, data_dir, admin_token, create_repo):
    """Create a repo with multiple commits for blame testing."""
    import asyncio

    async def _setup():
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            await create_repo(client, "blame-test", admin_token)

        store = ObjectStore(data_dir / "repos" / "blame-test" / "objects")

        row_a = {"text": "alpha", "label": "a"}
        row_b = {"text": "beta", "label": "b"}

        def _write_row(content):
            data = json.dumps(content, separators=(",", ":"), sort_keys=True).encode()
            return store.write("rows", data), compute_row_hash(content)

        _write_row(row_a)
        _write_row(row_b)

        rh_a = compute_row_hash(row_a)
        rh_b = compute_row_hash(row_b)

        # Commit 1: just row_a
        m1 = Manifest(entries=[ManifestEntry(row_hash=rh_a, query_fingerprint=None)])
        m1_bytes = serialize_manifest(m1)
        m1_hash = store.write("manifests", m1_bytes)
        t1 = Tree(entries=[TreeEntry(name="train.jsonl", obj_type="manifest", obj_hash=m1_hash)])
        t1_bytes = serialize_tree(t1)
        t1_hash = store.write("trees", t1_bytes)
        c1 = Commit(tree_hash=t1_hash, parent_hashes=[], author="alice", message="c1", timestamp=1000)
        c1_hash = store.write("commits", serialize_commit(c1))

        # Commit 2: row_a + row_b
        m2 = Manifest(entries=[
            ManifestEntry(row_hash=rh_a, query_fingerprint=None),
            ManifestEntry(row_hash=rh_b, query_fingerprint=None),
        ])
        m2_bytes = serialize_manifest(m2)
        m2_hash = store.write("manifests", m2_bytes)
        t2 = Tree(entries=[TreeEntry(name="train.jsonl", obj_type="manifest", obj_hash=m2_hash)])
        t2_bytes = serialize_tree(t2)
        t2_hash = store.write("trees", t2_bytes)
        c2 = Commit(tree_hash=t2_hash, parent_hashes=[c1_hash], author="bob", message="c2", timestamp=2000)
        c2_hash = store.write("commits", serialize_commit(c2))

        # Update ref
        from sqlalchemy.ext.asyncio import AsyncSession
        from dit.server.models import Ref
        from sqlalchemy import update as sa_update

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            pass

        return c1_hash, c2_hash, store

    return asyncio.get_event_loop().run_until_complete(_setup())


@pytest.fixture
def blame_commits(app, data_dir, admin_token, create_repo):
    """Simpler fixture that returns commit hashes after setting up a blame repo."""
    pass


class TestBlameEndpoint:

    @pytest.mark.asyncio
    async def test_blame_full_file(self, app, data_dir, admin_token, create_repo):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            await create_repo(client, "blame-repo", admin_token)

        store = ObjectStore(data_dir / "repos" / "blame-repo" / "objects")

        row_a = {"text": "alpha", "label": "a"}
        row_b = {"text": "beta", "label": "b"}

        rh_a = compute_row_hash(row_a)
        rh_b = compute_row_hash(row_b)
        store.write("rows", json.dumps(row_a, separators=(",", ":"), sort_keys=True).encode())
        store.write("rows", json.dumps(row_b, separators=(",", ":"), sort_keys=True).encode())

        m1 = Manifest(entries=[ManifestEntry(row_hash=rh_a, query_fingerprint=None)])
        m1_hash = store.write("manifests", serialize_manifest(m1))
        t1 = Tree(entries=[TreeEntry(name="train.jsonl", obj_type="manifest", obj_hash=m1_hash)])
        t1_hash = store.write("trees", serialize_tree(t1))
        c1 = Commit(tree_hash=t1_hash, parent_hashes=[], author="alice", message="c1", timestamp=1000)
        c1_hash = store.write("commits", serialize_commit(c1))

        m2 = Manifest(entries=[
            ManifestEntry(row_hash=rh_a, query_fingerprint=None),
            ManifestEntry(row_hash=rh_b, query_fingerprint=None),
        ])
        m2_hash = store.write("manifests", serialize_manifest(m2))
        t2 = Tree(entries=[TreeEntry(name="train.jsonl", obj_type="manifest", obj_hash=m2_hash)])
        t2_hash = store.write("trees", serialize_tree(t2))
        c2 = Commit(tree_hash=t2_hash, parent_hashes=[c1_hash], author="bob", message="c2", timestamp=2000)
        c2_hash = store.write("commits", serialize_commit(c2))

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                f"/api/v1/repos/blame-repo/blame/{c2_hash}/train.jsonl",
                headers={"Authorization": f"Bearer {admin_token}"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["file"] == "train.jsonl"
        assert len(body["entries"]) == 2
        assert body["entries"][0]["commit_hash"] == c1_hash
        assert body["entries"][1]["commit_hash"] == c2_hash

    @pytest.mark.asyncio
    async def test_blame_row_history(self, app, data_dir, admin_token, create_repo):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            await create_repo(client, "blame-rh", admin_token)

        store = ObjectStore(data_dir / "repos" / "blame-rh" / "objects")

        row = {"text": "hello", "label": "pos"}
        rh = compute_row_hash(row)
        store.write("rows", json.dumps(row, separators=(",", ":"), sort_keys=True).encode())

        m = Manifest(entries=[ManifestEntry(row_hash=rh, query_fingerprint=None)])
        m_hash = store.write("manifests", serialize_manifest(m))
        t = Tree(entries=[TreeEntry(name="data.jsonl", obj_type="manifest", obj_hash=m_hash)])
        t_hash = store.write("trees", serialize_tree(t))
        c = Commit(tree_hash=t_hash, parent_hashes=[], author="alice", message="init", timestamp=1000)
        c_hash = store.write("commits", serialize_commit(c))

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                f"/api/v1/repos/blame-rh/blame/{c_hash}/data.jsonl?row=0",
                headers={"Authorization": f"Bearer {admin_token}"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["row_index"] == 0
        assert len(body["events"]) == 1
        assert body["events"][0]["event"] == "added"

    @pytest.mark.asyncio
    async def test_blame_commit_not_found(self, app, admin_token, create_repo):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            await create_repo(client, "blame-404", admin_token)
            resp = await client.get(
                f"/api/v1/repos/blame-404/blame/{'0' * 64}/train.jsonl",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_blame_file_not_found(self, app, data_dir, admin_token, create_repo):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            await create_repo(client, "blame-nf", admin_token)

        store = ObjectStore(data_dir / "repos" / "blame-nf" / "objects")
        m = Manifest(entries=[ManifestEntry(row_hash="a" * 64, query_fingerprint=None)])
        m_hash = store.write("manifests", serialize_manifest(m))
        t = Tree(entries=[TreeEntry(name="other.jsonl", obj_type="manifest", obj_hash=m_hash)])
        t_hash = store.write("trees", serialize_tree(t))
        c = Commit(tree_hash=t_hash, parent_hashes=[], author="x", message="x", timestamp=1)
        c_hash = store.write("commits", serialize_commit(c))

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                f"/api/v1/repos/blame-nf/blame/{c_hash}/missing.jsonl",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_blame_row_out_of_range(self, app, data_dir, admin_token, create_repo):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            await create_repo(client, "blame-oor", admin_token)

        store = ObjectStore(data_dir / "repos" / "blame-oor" / "objects")
        row = {"a": 1}
        rh = compute_row_hash(row)
        store.write("rows", json.dumps(row, separators=(",", ":"), sort_keys=True).encode())
        m = Manifest(entries=[ManifestEntry(row_hash=rh, query_fingerprint=None)])
        m_hash = store.write("manifests", serialize_manifest(m))
        t = Tree(entries=[TreeEntry(name="f.jsonl", obj_type="manifest", obj_hash=m_hash)])
        t_hash = store.write("trees", serialize_tree(t))
        c = Commit(tree_hash=t_hash, parent_hashes=[], author="x", message="x", timestamp=1)
        c_hash = store.write("commits", serialize_commit(c))

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                f"/api/v1/repos/blame-oor/blame/{c_hash}/f.jsonl?row=99",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_blame_requires_auth(self, app, create_repo, admin_token):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            await create_repo(client, "blame-auth", admin_token)
            resp = await client.get(
                f"/api/v1/repos/blame-auth/blame/{'a' * 64}/f.jsonl",
            )
        assert resp.status_code in (401, 403)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/code/dit && uv run pytest tests/server/test_routes_blame.py -v`
Expected: ImportError or 404 (route not registered)

- [ ] **Step 3: Implement the blame API route**

Create `src/dit/server/routes/blame_api.py`:

```python
"""Blame API endpoints."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.auth import get_session, require_permission
from dit.server.routes._helpers import _get_repo

router = APIRouter(prefix="/api/v1/repos", tags=["blame"])


def _store_for_repo(request: Request, repo_name: str):
    from dit.core.store import ObjectStore
    from pathlib import Path
    data_dir: Path = request.app.state.data_dir
    return ObjectStore(Path(data_dir) / "repos" / repo_name / "objects")


@router.get("/{repo}/blame/{commit_hash}/{file_path:path}")
async def blame_endpoint(
    repo: str,
    commit_hash: str,
    file_path: str,
    row: Optional[int] = Query(default=None),
    request: Request = None,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("read")),
):
    from dit.core.blame import blame_file, row_history

    await _get_repo(repo, session)
    store = _store_for_repo(request, repo)

    try:
        if row is not None:
            try:
                return row_history(store, commit_hash, file_path, row)
            except IndexError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
        else:
            return blame_file(store, commit_hash, file_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
```

- [ ] **Step 4: Register the router in app.py**

In `src/dit/server/app.py`, add:

```python
from dit.server.routes.blame_api import router as blame_router
app.include_router(blame_router)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd ~/code/dit && uv run pytest tests/server/test_routes_blame.py -v`
Expected: All tests PASS

- [ ] **Step 6: Run full test suite**

Run: `cd ~/code/dit && uv run pytest tests/ -v`
Expected: All tests PASS (no regressions)

- [ ] **Step 7: Commit**

```bash
cd ~/code/dit
git add src/dit/server/routes/blame_api.py src/dit/server/app.py tests/server/test_routes_blame.py
git commit -m "feat: add blame API endpoint (GET /repos/{repo}/blame/{commit}/{path})"
```

---

### Task 3: CLI `dit blame` command

**Files:**
- Modify: `src/dit/cli/main.py`
- Create: `tests/test_cli_blame.py`

- [ ] **Step 1: Write failing tests for the CLI blame command**

Create `tests/test_cli_blame.py`:

```python
"""Tests for dit blame CLI command."""
import json
import time
from pathlib import Path

from typer.testing import CliRunner

from dit.cli.main import app
from dit.core.objects import (
    Commit, Manifest, ManifestEntry, Tree, TreeEntry,
    serialize_manifest, serialize_tree, serialize_commit,
)
from dit.core.refs import RefStore
from dit.core.store import ObjectStore
from dit.core.hash import row_hash as compute_row_hash

runner = CliRunner()


def _init_repo(tmp_path: Path):
    dot = tmp_path / ".dit"
    dot.mkdir()
    (dot / "objects").mkdir()
    RefStore(dot).init()
    return dot


def _write_row(store, content):
    data = json.dumps(content, separators=(",", ":"), sort_keys=True).encode()
    return store.write("rows", data), compute_row_hash(content)


def _make_commit(store, files, parent_hashes=None, author="alice", msg="c", ts=None):
    tree_entries = []
    for fname, rows in files.items():
        entries = []
        for row in rows:
            rh = compute_row_hash(row)
            data = json.dumps(row, separators=(",", ":"), sort_keys=True).encode()
            store.write("rows", data)
            entries.append(ManifestEntry(row_hash=rh, query_fingerprint=None))
        manifest = Manifest(entries=entries)
        m_bytes = serialize_manifest(manifest)
        m_hash = store.write("manifests", m_bytes)
        tree_entries.append(TreeEntry(name=fname, obj_type="manifest", obj_hash=m_hash))
    tree = Tree(entries=tree_entries)
    t_bytes = serialize_tree(tree)
    t_hash = store.write("trees", t_bytes)
    c = Commit(
        tree_hash=t_hash,
        parent_hashes=parent_hashes or [],
        author=author, message=msg, timestamp=ts or int(time.time()),
    )
    return store.write("commits", serialize_commit(c))


class TestBlameCommand:

    def test_blame_table_output(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot = _init_repo(tmp_path)
        store = ObjectStore(dot / "objects")
        refs = RefStore(dot)

        row_a = {"text": "hello", "label": "pos"}
        row_b = {"text": "world", "label": "neg"}
        c1 = _make_commit(store, {"train.jsonl": [row_a]}, author="alice", ts=1000)
        c2 = _make_commit(store, {"train.jsonl": [row_a, row_b]}, parent_hashes=[c1], author="bob", ts=2000)
        refs.set_branch("main", c2)

        result = runner.invoke(app, ["blame", "train.jsonl"])
        assert result.exit_code == 0
        assert "alice" in result.output
        assert "bob" in result.output
        assert "train.jsonl" in result.output

    def test_blame_json_output(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot = _init_repo(tmp_path)
        store = ObjectStore(dot / "objects")
        refs = RefStore(dot)

        row = {"text": "test", "label": "x"}
        c1 = _make_commit(store, {"data.jsonl": [row]}, author="alice", ts=1000)
        refs.set_branch("main", c1)

        result = runner.invoke(app, ["blame", "data.jsonl", "--format", "json"])
        assert result.exit_code == 0
        body = json.loads(result.output)
        assert body["file"] == "data.jsonl"
        assert len(body["entries"]) == 1

    def test_blame_with_ref(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot = _init_repo(tmp_path)
        store = ObjectStore(dot / "objects")
        refs = RefStore(dot)

        row = {"text": "test", "label": "x"}
        c1 = _make_commit(store, {"f.jsonl": [row]}, ts=1000)
        refs.set_branch("main", c1)
        refs.set_branch("dev", c1)

        result = runner.invoke(app, ["blame", "f.jsonl", "--ref", "dev"])
        assert result.exit_code == 0

    def test_blame_file_not_found(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot = _init_repo(tmp_path)
        store = ObjectStore(dot / "objects")
        refs = RefStore(dot)

        c1 = _make_commit(store, {"other.jsonl": [{"a": 1}]}, ts=1000)
        refs.set_branch("main", c1)

        result = runner.invoke(app, ["blame", "missing.jsonl"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower() or "fatal" in result.output.lower()

    def test_blame_row_history(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot = _init_repo(tmp_path)
        store = ObjectStore(dot / "objects")
        refs = RefStore(dot)

        row = {"text": "hello", "label": "pos"}
        c1 = _make_commit(store, {"train.jsonl": [row]}, author="alice", ts=1000)
        refs.set_branch("main", c1)

        result = runner.invoke(app, ["blame", "train.jsonl", "--row", "0"])
        assert result.exit_code == 0
        assert "added" in result.output.lower()

    def test_blame_row_history_json(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot = _init_repo(tmp_path)
        store = ObjectStore(dot / "objects")
        refs = RefStore(dot)

        row = {"text": "hello", "label": "pos"}
        c1 = _make_commit(store, {"train.jsonl": [row]}, author="alice", ts=1000)
        refs.set_branch("main", c1)

        result = runner.invoke(app, ["blame", "train.jsonl", "--row", "0", "--format", "json"])
        assert result.exit_code == 0
        body = json.loads(result.output)
        assert body["row_index"] == 0

    def test_blame_no_commits(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _init_repo(tmp_path)

        result = runner.invoke(app, ["blame", "train.jsonl"])
        assert result.exit_code == 1

    def test_blame_summary_line(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot = _init_repo(tmp_path)
        store = ObjectStore(dot / "objects")
        refs = RefStore(dot)

        row_a = {"text": "a", "label": "1"}
        row_b = {"text": "b", "label": "2"}
        c1 = _make_commit(store, {"f.jsonl": [row_a]}, author="alice", ts=1000)
        c2 = _make_commit(store, {"f.jsonl": [row_a, row_b]}, parent_hashes=[c1], author="bob", ts=2000)
        refs.set_branch("main", c2)

        result = runner.invoke(app, ["blame", "f.jsonl"])
        assert result.exit_code == 0
        assert "2 rows" in result.output
        assert "2 commits" in result.output
        assert "2 authors" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/code/dit && uv run pytest tests/test_cli_blame.py -v`
Expected: `No such command 'blame'`

- [ ] **Step 3: Implement the `dit blame` command in `main.py`**

Add the following to `src/dit/cli/main.py` (after the `validate` command):

```python
@app.command()
def blame(
    file: str = typer.Argument(..., help="File path (e.g. train.jsonl)"),
    ref: str = typer.Option("main", "--ref", help="Branch name or commit hash"),
    row: Optional[int] = typer.Option(None, "--row", help="Show history for a specific row index"),
    format: str = typer.Option("table", "--format", help="Output format: table or json"),
):
    """Show which commit introduced each row in a file."""
    import json as _json
    from datetime import datetime, timezone
    from dit.core.blame import blame_file as _blame_file, row_history as _row_history

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
        if row is not None:
            result = _row_history(store, commit_hash, file, row)

            if format == "json":
                typer.echo(_json.dumps(result, indent=2))
                return

            ref_display = f"heads/{ref}" if refs.get_branch(ref) else ref
            typer.echo(f"History for {file} row {row} at {ref_display}")
            typer.echo("")

            events = result["events"]
            if not events:
                typer.echo("No events found.")
                return

            col_commit = 9
            col_author = max(len(e["author"]) for e in events)
            col_author = max(col_author, 6)
            header = f"  {'Commit':<{col_commit}}  {'Author':<{col_author}}  {'Date':<21}  Event     Content"
            sep = "\u2500" * max(len(header), 80)
            typer.echo(header)
            typer.echo(sep)
            for e in events:
                ts = datetime.fromtimestamp(e["timestamp"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                preview = e.get("content_preview", "")[:50]
                typer.echo(f"  {e['commit_hash'][:7]:<{col_commit}}  {e['author']:<{col_author}}  {ts:<21}  {e['event']:<8}  {preview}")
            typer.echo(sep)

            qfp = result.get("query_fingerprint")
            if qfp:
                typer.echo(f"{len(events)} events (query_fingerprint: {qfp[:8]}...{qfp[-4:]})")
            else:
                typer.echo(f"{len(events)} events")

        else:
            result = _blame_file(store, commit_hash, file)

            if format == "json":
                typer.echo(_json.dumps(result, indent=2))
                return

            ref_display = f"heads/{ref}" if refs.get_branch(ref) else ref
            typer.echo(f"Blame for {file} at {ref_display} (commit {commit_hash[:8]})")
            typer.echo("")

            entries = result["entries"]
            if not entries:
                typer.echo("No rows.")
                return

            col_author = max(len(e["author"]) for e in entries)
            col_author = max(col_author, 6)
            header = f" {'Row':>4}  {'Commit':<9}  {'Author':<{col_author}}  {'Date':<21}  Content"
            sep = "\u2500" * max(len(header) + 40, 80)
            typer.echo(header)
            typer.echo(sep)
            for e in entries:
                ts = datetime.fromtimestamp(e["timestamp"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                preview = e.get("content_preview", "")[:60]
                typer.echo(f" {e['row_index']:>4}  {e['commit_hash'][:7]:<9}  {e['author']:<{col_author}}  {ts:<21}  {preview}")
            typer.echo(sep)

            s = result["summary"]
            typer.echo(f"{s['total_rows']} rows, {s['unique_commits']} commits, {s['unique_authors']} authors")

    except FileNotFoundError as exc:
        typer.echo(f"fatal: {exc}", err=True)
        raise typer.Exit(1)
    except IndexError as exc:
        typer.echo(f"fatal: {exc}", err=True)
        raise typer.Exit(1)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/code/dit && uv run pytest tests/test_cli_blame.py -v`
Expected: All tests PASS

- [ ] **Step 5: Run full test suite**

Run: `cd ~/code/dit && uv run pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
cd ~/code/dit
git add src/dit/cli/main.py tests/test_cli_blame.py
git commit -m "feat: add 'dit blame' CLI command with table/json output and row history"
```

---

### Task 4: Gateway proxy route + client method

**Files:**
- Modify: `~/code/datahub-gateway/modules/dit/client.go`
- Modify: `~/code/datahub-gateway/routers/api/v1/repo/dit.go`
- Modify: `~/code/datahub-gateway/routers/api/v1/api.go`

- [ ] **Step 1: Add `GetBlame` method to `client.go`**

In `~/code/datahub-gateway/modules/dit/client.go`, add:

```go
func (c *Client) GetBlame(ctx context.Context, repoName, commitHash, filePath, row string) ([]byte, int, error) {
	path := "/api/v1/repos/" + repoName + "/blame/" + commitHash + "/" + filePath
	if row != "" {
		path += "?row=" + url.QueryEscape(row)
	}
	return c.do(ctx, http.MethodGet, path, nil)
}
```

- [ ] **Step 2: Add handler to `dit.go`**

In `~/code/datahub-gateway/routers/api/v1/repo/dit.go`, add:

```go
func DatahubGetBlame(ctx *context.APIContext) {
	commitHash := ctx.Params(":commit")
	filePath := ctx.Params("*")
	row := ctx.FormString("row")
	data, status, err := dit.DefaultClient().GetBlame(ctx, ctx.Repo.Repository.Name, commitHash, filePath, row)
	proxyDatahubResponse(ctx, data, status, err)
}
```

- [ ] **Step 3: Register route in `api.go`**

In `~/code/datahub-gateway/routers/api/v1/api.go`, add to the dit routes group:

```go
m.Get("/blame/:commit/*", repo.DatahubGetBlame)
```

- [ ] **Step 4: Verify Go build**

Run: `cd ~/code/datahub-gateway && go build ./...`
Expected: Build succeeds

- [ ] **Step 5: Commit**

```bash
cd ~/code/datahub-gateway
git add modules/dit/client.go routers/api/v1/repo/dit.go routers/api/v1/api.go
git commit -m "feat: add blame proxy route to dit gateway"
```

---

### Task 5: Vue UI blame panel

**Files:**
- Modify: `~/code/datahub-gateway/web_src/js/components/DataRepoHome.vue`

- [ ] **Step 1: Add blame data fields and methods**

In the `data()` return object, add:

```js
blameData: null,
blameLoading: false,
blameError: null,
blameFile: null,       // which file is currently showing blame
rowHistoryData: null,
rowHistoryLoading: false,
```

In `methods`, add:

```js
async loadBlame(filePath) {
  this.blameFile = filePath;
  this.blameLoading = true;
  this.blameError = null;
  this.blameData = null;
  this.rowHistoryData = null;
  try {
    this.blameData = await ditFetch(
      this.owner, this.repo,
      `/blame/${this.commitHash}/${filePath}`,
    );
  } catch (e) {
    this.blameError = e.message;
  } finally {
    this.blameLoading = false;
  }
},
closeBlame() {
  this.blameData = null;
  this.blameFile = null;
  this.blameError = null;
  this.rowHistoryData = null;
},
async loadRowHistory(rowIndex) {
  this.rowHistoryLoading = true;
  try {
    this.rowHistoryData = await ditFetch(
      this.owner, this.repo,
      `/blame/${this.commitHash}/${this.blameFile}?row=${rowIndex}`,
    );
  } catch (e) {
    this.rowHistoryData = null;
  } finally {
    this.rowHistoryLoading = false;
  }
},
formatBlameDate(timestamp) {
  if (!timestamp) return '—';
  const d = new Date(timestamp * 1000);
  return d.toISOString().slice(0, 16).replace('T', ' ') + ' UTC';
},
```

- [ ] **Step 2: Add blame button to file table rows**

In the file table, add a "Blame" button/link for manifest-type files:

```html
<a v-if="entry.obj_type === 'manifest'" class="ui mini basic button"
   @click.prevent="loadBlame(entry.name)" title="Show blame">
  Blame
</a>
```

- [ ] **Step 3: Add blame panel template**

Below the file table, add a blame panel that shows when `blameData` is loaded:

```html
<!-- Blame panel -->
<div class="ui segment" v-if="blameFile">
  <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 1em;">
    <h4 class="ui header" style="margin: 0;">
      Blame: {{ blameFile }}
    </h4>
    <button class="ui mini icon button" @click="closeBlame" title="Close blame">
      <i class="close icon"></i>
    </button>
  </div>

  <div v-if="blameLoading" class="ui active centered inline loader"></div>
  <div v-else-if="blameError" class="ui small negative message">{{ blameError }}</div>
  <div v-else-if="blameData">
    <div class="ui small label" style="margin-bottom: 1em;">
      {{ blameData.summary.total_rows }} rows &middot;
      {{ blameData.summary.unique_commits }} commits &middot;
      {{ blameData.summary.unique_authors }} authors
    </div>

    <table class="ui very basic compact table">
      <thead>
        <tr>
          <th class="right aligned" style="width: 50px;">Row</th>
          <th style="width: 80px;">Commit</th>
          <th style="width: 100px;">Author</th>
          <th style="width: 160px;">Date</th>
          <th>Content</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="entry in blameData.entries" :key="entry.row_index"
            @click="loadRowHistory(entry.row_index)"
            style="cursor: pointer;"
            :class="{'active': rowHistoryData && rowHistoryData.row_index === entry.row_index}">
          <td class="right aligned">{{ entry.row_index }}</td>
          <td><code>{{ entry.commit_hash.slice(0, 7) }}</code></td>
          <td>{{ entry.author }}</td>
          <td>{{ formatBlameDate(entry.timestamp) }}</td>
          <td style="max-width: 400px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
            <code style="font-size: 0.85em;">{{ entry.content_preview }}</code>
          </td>
        </tr>
      </tbody>
    </table>

    <!-- Row history sub-panel -->
    <div v-if="rowHistoryLoading" class="ui active centered inline loader" style="margin-top: 1em;"></div>
    <div v-else-if="rowHistoryData" class="ui secondary segment" style="margin-top: 1em;">
      <h5 class="ui header">
        Row {{ rowHistoryData.row_index }} History
        <span v-if="rowHistoryData.query_fingerprint" class="ui mini label">
          qfp: {{ rowHistoryData.query_fingerprint.slice(0, 8) }}
        </span>
      </h5>
      <table class="ui very basic compact table">
        <thead>
          <tr>
            <th>Event</th>
            <th>Commit</th>
            <th>Author</th>
            <th>Date</th>
            <th>Content</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(evt, idx) in rowHistoryData.events" :key="idx">
            <td>
              <span class="ui mini label"
                    :class="{'green': evt.event === 'added', 'blue': evt.event === 'refresh', 'red': evt.event === 'removed'}">
                {{ evt.event }}
              </span>
            </td>
            <td><code>{{ evt.commit_hash.slice(0, 7) }}</code></td>
            <td>{{ evt.author }}</td>
            <td>{{ formatBlameDate(evt.timestamp) }}</td>
            <td style="max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
              <code style="font-size: 0.85em;">{{ evt.content_preview }}</code>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</div>
```

- [ ] **Step 4: Verify frontend build**

Run: `cd ~/code/datahub-gateway && npx webpack`
Expected: Build succeeds

- [ ] **Step 5: Commit**

```bash
cd ~/code/datahub-gateway
git add web_src/js/components/DataRepoHome.vue
git commit -m "feat: add blame panel with row history in data repo home view"
```

---

### Task 6: Final integration test + full suite

**Files:** None (test-only task)

- [ ] **Step 1: Run Python full test suite**

Run: `cd ~/code/dit && uv run pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 2: Run Go build**

Run: `cd ~/code/datahub-gateway && go build ./...`
Expected: Build succeeds

- [ ] **Step 3: Verify test count**

Expected: ~700+ tests passing (previous count was ~680, adding ~30 new blame tests)

- [ ] **Step 4: Final commit (if any fixups needed)**

Only if integration issues were found and fixed.
