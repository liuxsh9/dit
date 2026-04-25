"""Tests for dit.core.gc — mark-and-sweep garbage collection."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from dit.core.hash import row_hash as compute_row_hash, query_fingerprint as compute_qfp
from dit.core.objects import (
    Commit,
    Manifest,
    ManifestEntry,
    Tree,
    TreeEntry,
    serialize_blob,
    serialize_commit,
    serialize_manifest,
    serialize_tree,
)
from dit.core.store import ObjectStore


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_store(tmp_path: Path) -> ObjectStore:
    return ObjectStore(tmp_path / "objects")


def _write_row(store: ObjectStore, content: dict) -> str:
    data = json.dumps(content, separators=(",", ":"), sort_keys=True).encode()
    return store.write("rows", data)


def _make_blob(store: ObjectStore, content: bytes) -> str:
    blob_bytes = serialize_blob(content)
    return store.write("blobs", blob_bytes)


def _make_commit(
    store: ObjectStore,
    files: dict[str, list[dict]],
    blobs: dict[str, bytes] | None = None,
    parent_hashes: list[str] | None = None,
    message: str = "test commit",
) -> str:
    """Create a commit whose tree contains manifest entries for each file in `files`
    and optional blob entries from `blobs`.

    Returns the commit hash.
    """
    tree_entries: list[TreeEntry] = []

    for path, rows in files.items():
        row_hashes = [_write_row(store, r) for r in rows]
        manifest = Manifest(
            entries=[ManifestEntry(row_hash=rh, query_fingerprint=None) for rh in row_hashes]
        )
        manifest_hash = store.write("manifests", serialize_manifest(manifest))
        tree_entries.append(TreeEntry(name=path, obj_type="manifest", obj_hash=manifest_hash))

    if blobs:
        for blob_path, blob_content in blobs.items():
            blob_hash = _make_blob(store, blob_content)
            tree_entries.append(TreeEntry(name=blob_path, obj_type="blob", obj_hash=blob_hash))

    tree = Tree(entries=tree_entries)
    tree_hash = store.write("trees", serialize_tree(tree))

    commit = Commit(
        tree_hash=tree_hash,
        parent_hashes=parent_hashes or [],
        author="tester",
        message=message,
        timestamp=int(time.time()),
    )
    return store.write("commits", serialize_commit(commit))


# ── TestCollectLiveSet ────────────────────────────────────────────────────────

class TestCollectLiveSet:
    def test_single_commit_all_objects_live(self, tmp_path: Path) -> None:
        from dit.core.gc import collect_live_set

        store = _make_store(tmp_path)
        commit_hash = _make_commit(store, {"data.jsonl": [{"a": 1}]})

        live = collect_live_set(store, [commit_hash])

        assert commit_hash in live["commits"]
        # tree, manifest, and row should all be live
        assert len(live["trees"]) == 1
        assert len(live["manifests"]) == 1
        assert len(live["rows"]) == 1

    def test_multiple_refs_union(self, tmp_path: Path) -> None:
        from dit.core.gc import collect_live_set

        store = _make_store(tmp_path)
        commit1 = _make_commit(store, {"a.jsonl": [{"x": 1}]}, message="c1")
        commit2 = _make_commit(store, {"b.jsonl": [{"y": 2}]}, message="c2")

        live = collect_live_set(store, [commit1, commit2])

        assert commit1 in live["commits"]
        assert commit2 in live["commits"]
        assert len(live["manifests"]) == 2
        assert len(live["rows"]) == 2

    def test_blob_in_live_set(self, tmp_path: Path) -> None:
        from dit.core.gc import collect_live_set

        store = _make_store(tmp_path)
        blob_content = b"raw binary data"
        commit_hash = _make_commit(
            store,
            files={},
            blobs={"file.bin": blob_content},
        )

        live = collect_live_set(store, [commit_hash])

        assert len(live["blobs"]) == 1

    def test_index_entries_manifests_protected(self, tmp_path: Path) -> None:
        from dit.core.gc import collect_live_set

        store = _make_store(tmp_path)

        # Create a manifest in the store (staging index points to it)
        row_h = _write_row(store, {"staged": True})
        manifest = Manifest(entries=[ManifestEntry(row_hash=row_h, query_fingerprint=None)])
        manifest_hash = store.write("manifests", serialize_manifest(manifest))

        # entries_typed() returns (obj_type, obj_hash)
        index_entries = {"staged.jsonl": ("manifest", manifest_hash)}

        live = collect_live_set(store, [], index_entries=index_entries)

        assert manifest_hash in live["manifests"]
        assert row_h in live["rows"]

    def test_index_entries_blobs_protected(self, tmp_path: Path) -> None:
        from dit.core.gc import collect_live_set

        store = _make_store(tmp_path)
        blob_hash = _make_blob(store, b"staged blob content")

        index_entries = {"attachment.bin": ("blob", blob_hash)}

        live = collect_live_set(store, [], index_entries=index_entries)

        assert blob_hash in live["blobs"]


# ── TestSweep ─────────────────────────────────────────────────────────────────

class TestSweep:
    def test_sweep_deletes_unreachable_old_objects(self, tmp_path: Path) -> None:
        from dit.core.gc import sweep

        store = _make_store(tmp_path)
        orphan_hash = _write_row(store, {"orphan": True})
        orphan_path = store._object_path("rows", orphan_hash)

        # Make the orphan appear old
        old_time = time.time() - 90000
        os.utime(orphan_path, (old_time, old_time))

        live_set: dict[str, set[str]] = {
            "commits": set(), "trees": set(), "manifests": set(),
            "rows": set(), "sidecars": set(), "blobs": set(),
        }
        result = sweep(store, live_set, grace_seconds=86400)

        assert result.total_deleted == 1
        assert result.deleted_counts["rows"] == 1
        assert not orphan_path.exists()

    def test_sweep_respects_grace_period(self, tmp_path: Path) -> None:
        from dit.core.gc import sweep

        store = _make_store(tmp_path)
        fresh_hash = _write_row(store, {"fresh": True})
        fresh_path = store._object_path("rows", fresh_hash)

        # mtime is recent (just written), well within grace period
        live_set: dict[str, set[str]] = {
            "commits": set(), "trees": set(), "manifests": set(),
            "rows": set(), "sidecars": set(), "blobs": set(),
        }
        result = sweep(store, live_set, grace_seconds=86400)

        assert result.total_deleted == 0
        assert fresh_path.exists()
        assert result.skipped_counts["rows"] >= 1

    def test_sweep_dry_run_no_delete(self, tmp_path: Path) -> None:
        from dit.core.gc import sweep

        store = _make_store(tmp_path)
        orphan_hash = _write_row(store, {"orphan": "dry"})
        orphan_path = store._object_path("rows", orphan_hash)

        old_time = time.time() - 90000
        os.utime(orphan_path, (old_time, old_time))

        live_set: dict[str, set[str]] = {
            "commits": set(), "trees": set(), "manifests": set(),
            "rows": set(), "sidecars": set(), "blobs": set(),
        }
        result = sweep(store, live_set, grace_seconds=86400, dry_run=True)

        assert result.total_deleted == 1
        # File should still exist — dry_run means count-only
        assert orphan_path.exists()

    def test_sweep_cleans_stale_tmp_files(self, tmp_path: Path) -> None:
        from dit.core.gc import sweep

        store = _make_store(tmp_path)
        # Ensure store root exists so we can create tmp dir
        store.root.mkdir(parents=True, exist_ok=True)
        tmp_dir = store.root / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)

        stale_tmp = tmp_dir / "stale-uuid-file"
        stale_tmp.write_bytes(b"partial write")
        old_time = time.time() - 90000
        os.utime(stale_tmp, (old_time, old_time))

        live_set: dict[str, set[str]] = {
            "commits": set(), "trees": set(), "manifests": set(),
            "rows": set(), "sidecars": set(), "blobs": set(),
        }
        result = sweep(store, live_set, grace_seconds=86400)

        assert result.tmp_deleted == 1
        assert not stale_tmp.exists()

    def test_sweep_preserves_fresh_tmp_files(self, tmp_path: Path) -> None:
        from dit.core.gc import sweep

        store = _make_store(tmp_path)
        store.root.mkdir(parents=True, exist_ok=True)
        tmp_dir = store.root / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)

        fresh_tmp = tmp_dir / "fresh-uuid-file"
        fresh_tmp.write_bytes(b"in-flight write")
        # mtime is now — well within grace period

        live_set: dict[str, set[str]] = {
            "commits": set(), "trees": set(), "manifests": set(),
            "rows": set(), "sidecars": set(), "blobs": set(),
        }
        result = sweep(store, live_set, grace_seconds=86400)

        assert result.tmp_deleted == 0
        assert fresh_tmp.exists()


# ── TestGC ────────────────────────────────────────────────────────────────────

class TestGC:
    def test_gc_end_to_end(self, tmp_path: Path) -> None:
        from dit.core.gc import gc

        store = _make_store(tmp_path)
        # Reachable commit
        live_commit = _make_commit(store, {"data.jsonl": [{"live": True}]})

        # Orphan row (not referenced by any commit)
        orphan_hash = _write_row(store, {"orphan": "end_to_end"})
        orphan_path = store._object_path("rows", orphan_hash)
        old_time = time.time() - 90000
        os.utime(orphan_path, (old_time, old_time))

        result = gc(store, ref_hashes=[live_commit], grace_seconds=86400)

        assert result.total_deleted >= 1
        assert not orphan_path.exists()

    def test_gc_with_no_orphans(self, tmp_path: Path) -> None:
        from dit.core.gc import gc

        store = _make_store(tmp_path)
        commit_hash = _make_commit(store, {"data.jsonl": [{"a": 1}, {"b": 2}]})

        result = gc(store, ref_hashes=[commit_hash], grace_seconds=86400)

        assert result.total_deleted == 0
        assert result.tmp_deleted == 0

    def test_gc_empty_repo(self, tmp_path: Path) -> None:
        from dit.core.gc import gc

        store = _make_store(tmp_path)
        # No objects written at all; should not raise

        result = gc(store, ref_hashes=[], grace_seconds=86400)

        assert result.total_deleted == 0
        assert result.total_scanned == 0
        assert result.errors == []
