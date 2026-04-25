"""Integration tests for GC: create repo, produce orphans, run GC, verify cleanup."""
import json
import os
import time
from pathlib import Path

import pytest

from dit.core.gc import gc, collect_live_set, sweep
from dit.core.hash import row_hash as compute_row_hash, query_fingerprint as compute_qfp
from dit.core.index import StagingIndex
from dit.core.objects import (
    Commit, Manifest, ManifestEntry, Tree, TreeEntry,
    serialize_manifest, serialize_tree, serialize_commit,
    serialize_blob,
    object_hash,
)
from dit.core.refs import RefStore
from dit.core.store import ObjectStore
from dit.core.walker import walk_commit_objects


# ── helpers ────────────────────────────────────────────────────────────────────

def _write_row(store: ObjectStore, content: dict) -> str:
    data = json.dumps(content, separators=(",", ":"), sort_keys=True).encode()
    return store.write("rows", data)


def _make_commit(
    store: ObjectStore,
    files: dict,
    blobs: dict | None = None,
    parent_hashes: list[str] | None = None,
    author: str = "alice",
) -> str:
    tree_entries = []
    for fname, rows in files.items():
        entries = []
        for row in rows:
            rh = compute_row_hash(row)
            _write_row(store, row)
            qfp = compute_qfp(row) if "messages" in row else None
            entries.append(ManifestEntry(row_hash=rh, query_fingerprint=qfp))
        manifest = Manifest(entries=entries)
        m_hash = store.write("manifests", serialize_manifest(manifest))
        tree_entries.append(TreeEntry(name=fname, obj_type="manifest", obj_hash=m_hash))
    if blobs:
        for bname, bdata in blobs.items():
            b_hash = store.write("blobs", serialize_blob(bdata))
            tree_entries.append(TreeEntry(name=bname, obj_type="blob", obj_hash=b_hash))
    tree = Tree(entries=tree_entries)
    t_hash = store.write("trees", serialize_tree(tree))
    c = Commit(
        tree_hash=t_hash,
        parent_hashes=parent_hashes or [],
        author=author,
        message="test",
        timestamp=int(time.time()),
    )
    return store.write("commits", serialize_commit(c))


# ── tests ──────────────────────────────────────────────────────────────────────

def test_branch_delete_then_gc(tmp_path: Path) -> None:
    """Objects reachable only from a deleted branch are swept after grace period."""
    dot = tmp_path / ".datahub"
    objects_root = tmp_path / "objects"

    refs = RefStore(dot)
    refs.init()
    store = ObjectStore(objects_root)

    # main branch commit
    main_hash = _make_commit(store, {"main.jsonl": [{"branch": "main"}]})
    refs.set_branch("main", main_hash)

    # feature branch commit (child of main)
    feature_hash = _make_commit(
        store, {"feature.jsonl": [{"branch": "feature"}]}, parent_hashes=[main_hash]
    )
    refs.set_branch("feature", feature_hash)

    # Both branches live — feature commit should be in live set
    live_before = collect_live_set(store, [main_hash, feature_hash])
    assert feature_hash in live_before["commits"]

    # Delete the feature branch
    refs.delete_branch("feature")

    # Now only main_hash is live — feature commit is unreachable
    live_after = collect_live_set(store, [main_hash])
    assert feature_hash not in live_after["commits"]

    # Age the feature commit so it passes the grace period
    feature_commit_path = store._object_path("commits", feature_hash)
    old_time = time.time() - 90000
    os.utime(feature_commit_path, (old_time, old_time))

    result = gc(store, [main_hash], grace_seconds=86400)
    assert result.total_deleted >= 1


def test_staging_index_protects_uncommitted(tmp_path: Path) -> None:
    """Objects staged in the index are NOT deleted even when no commits exist."""
    dot = tmp_path / ".datahub"
    objects_root = tmp_path / "objects"
    store = ObjectStore(objects_root)

    # Write a row + manifest manually (not via commit)
    row_content = {"staged_key": "staged_value"}
    rh = compute_row_hash(row_content)
    _write_row(store, row_content)
    manifest = Manifest(entries=[ManifestEntry(row_hash=rh, query_fingerprint=None)])
    m_hash = store.write("manifests", serialize_manifest(manifest))

    # Stage the manifest via StagingIndex
    index = StagingIndex(dot / "index")
    index.stage("staged.jsonl", m_hash, "manifest")

    # Load entries for GC
    index_entries = index.entries_typed()

    # Run GC with no commits and grace_seconds=0 (would delete anything old)
    result = gc(store, [], index_entries=index_entries, grace_seconds=0)

    # Manifest and row must still be present
    assert store.exists("manifests", m_hash)
    assert store.exists("rows", rh)
    assert result.total_deleted == 0


def test_walk_commit_objects_blobs_included_in_push_delta(tmp_path: Path) -> None:
    """Walker correctly separates blob objects between commits for push delta."""
    store = ObjectStore(tmp_path / "objects")

    shared_row = {"shared": True}
    rh = compute_row_hash(shared_row)
    _write_row(store, shared_row)
    manifest = Manifest(entries=[ManifestEntry(row_hash=rh, query_fingerprint=None)])
    m_hash = store.write("manifests", serialize_manifest(manifest))

    # c1: blob "v1"
    c1_hash = _make_commit(
        store,
        files={"data.jsonl": [shared_row]},
        blobs={"artifact.bin": b"version1"},
    )

    # c2: blob "v2", child of c1
    c2_hash = _make_commit(
        store,
        files={"data.jsonl": [shared_row]},
        blobs={"artifact.bin": b"version2"},
        parent_hashes=[c1_hash],
    )

    local = walk_commit_objects(store, c2_hash)
    remote = walk_commit_objects(store, c1_hash)

    # Objects only in c2 (not in c1) — push delta
    new_objects = {t: local[t] - remote[t] for t in local}

    # Only the v2 blob is new
    assert len(new_objects["blobs"]) == 1
    # Same row appears in both commits — no new rows
    assert len(new_objects["rows"]) == 0


def test_gc_with_blobs_preserves_reachable(tmp_path: Path) -> None:
    """Blobs reachable from a live commit are never deleted by GC."""
    store = ObjectStore(tmp_path / "objects")

    commit_hash = _make_commit(
        store,
        files={"data.jsonl": [{"x": 1}]},
        blobs={"image.bin": b"binary content"},
    )

    live = collect_live_set(store, [commit_hash])
    assert len(live["blobs"]) == 1
    blob_hash = next(iter(live["blobs"]))

    result = gc(store, [commit_hash], grace_seconds=0)

    assert store.exists("blobs", blob_hash)
    assert result.deleted_counts["blobs"] == 0


def test_stale_tmp_cleanup(tmp_path: Path) -> None:
    """GC removes stale tmp files while preserving fresh ones."""
    store = ObjectStore(tmp_path / "objects")

    # Create a real commit so the store root is initialised
    commit_hash = _make_commit(store, {"f.jsonl": [{"z": 9}]})

    tmp_dir = store.root / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    # Create 3 stale tmp files
    stale_paths = []
    for i in range(3):
        p = tmp_dir / f"stale-{i}"
        p.write_bytes(b"partial")
        old_time = time.time() - 90000
        os.utime(p, (old_time, old_time))
        stale_paths.append(p)

    # Create 1 fresh tmp file (mtime = now)
    fresh = tmp_dir / "fresh-0"
    fresh.write_bytes(b"in-flight")

    result = gc(store, [commit_hash], grace_seconds=86400)

    assert result.tmp_deleted == 3
    for p in stale_paths:
        assert not p.exists()
    assert fresh.exists()
