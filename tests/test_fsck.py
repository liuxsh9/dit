"""Tests for dit.core.fsck module."""
import hashlib
import json
import time

import pyzstd
import pytest

from dit.core.objects import (
    Commit, Manifest, ManifestEntry, Tree, TreeEntry,
    serialize_manifest, serialize_tree, serialize_commit,
    object_hash,
)
from dit.core.store import ObjectStore
from dit.core.hash import row_hash as compute_row_hash, query_fingerprint as compute_qfp


def _write_row(store: ObjectStore, content: dict) -> str:
    data = json.dumps(content, separators=(",", ":"), sort_keys=True).encode()
    return store.write("rows", data)


def _conv(user_msg: str, asst_msg: str) -> dict:
    return {"messages": [{"role": "user", "content": user_msg}, {"role": "assistant", "content": asst_msg}]}


def _make_commit(store: ObjectStore, files: dict[str, list[dict]], parent_hashes=None) -> str:
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
    tree = Tree(entries=tree_entries)
    t_hash = store.write("trees", serialize_tree(tree))
    c = Commit(
        tree_hash=t_hash,
        parent_hashes=parent_hashes or [],
        author="alice",
        message="test",
        timestamp=int(time.time()),
    )
    return store.write("commits", serialize_commit(c))


class TestFsck:
    def test_clean_store(self, tmp_path):
        from dit.core.fsck import fsck

        store = ObjectStore(tmp_path / "objects")
        c = _make_commit(store, {"train.jsonl": [_conv("q1", "a1"), _conv("q2", "a2")]})

        result = fsck(store, [c])
        assert result.total_errors == 0
        assert result.total_warnings == 0
        assert result.total_checked > 0
        assert result.checked_objects["commits"] >= 1
        assert result.checked_objects["rows"] >= 2

    def test_hash_mismatch_detected(self, tmp_path):
        from dit.core.fsck import fsck

        store = ObjectStore(tmp_path / "objects")
        c = _make_commit(store, {"train.jsonl": [_conv("q1", "a1")]})

        row_hash = compute_row_hash(_conv("q1", "a1"))
        obj_path = store._object_path("rows", row_hash)
        obj_path.write_bytes(pyzstd.compress(b"corrupted data"))

        result = fsck(store, [c])
        assert result.total_errors >= 1
        error_msgs = [e.message for e in result.errors]
        assert any("hash mismatch" in m for m in error_msgs)

    def test_corrupt_object_detected(self, tmp_path):
        from dit.core.fsck import fsck

        store = ObjectStore(tmp_path / "objects")
        c = _make_commit(store, {"train.jsonl": [_conv("q1", "a1")]})

        row_hash = compute_row_hash(_conv("q1", "a1"))
        obj_path = store._object_path("rows", row_hash)
        obj_path.write_bytes(b"not valid zstd data")

        result = fsck(store, [c])
        assert result.total_errors >= 1
        error_msgs = [e.message for e in result.errors]
        assert any("corrupt" in m.lower() or "decompression" in m.lower() for m in error_msgs)

    def test_missing_object_in_graph(self, tmp_path):
        from dit.core.fsck import fsck

        store = ObjectStore(tmp_path / "objects")
        c = _make_commit(store, {"train.jsonl": [_conv("q1", "a1")]})

        row_hash = compute_row_hash(_conv("q1", "a1"))
        obj_path = store._object_path("rows", row_hash)
        obj_path.unlink()

        result = fsck(store, [c], check_hashes=False)
        assert result.total_errors >= 1
        error_msgs = [e.message for e in result.errors]
        assert any("missing" in m.lower() for m in error_msgs)

    def test_dangling_ref(self, tmp_path):
        from dit.core.fsck import fsck

        store = ObjectStore(tmp_path / "objects")
        fake_hash = "a" * 64

        result = fsck(store, [fake_hash])
        assert result.total_errors >= 1
        error_msgs = [e.message for e in result.errors]
        assert any("dangling" in m.lower() or "missing" in m.lower() for m in error_msgs)

    def test_skip_hash_check(self, tmp_path):
        from dit.core.fsck import fsck

        store = ObjectStore(tmp_path / "objects")
        c = _make_commit(store, {"train.jsonl": [_conv("q1", "a1")]})

        result = fsck(store, [c], check_hashes=False)
        assert result.total_errors == 0

    def test_skip_graph_check(self, tmp_path):
        from dit.core.fsck import fsck

        store = ObjectStore(tmp_path / "objects")
        c = _make_commit(store, {"train.jsonl": [_conv("q1", "a1")]})

        result = fsck(store, [c], check_graph=False)
        assert result.total_errors == 0
        assert result.total_checked > 0

    def test_result_counts_match(self, tmp_path):
        from dit.core.fsck import fsck

        store = ObjectStore(tmp_path / "objects")
        c = _make_commit(store, {
            "a.jsonl": [_conv("q1", "a1")],
            "b.jsonl": [_conv("q2", "a2")],
        })

        result = fsck(store, [c])
        total_from_dict = sum(result.checked_objects.values())
        assert result.total_checked == total_from_dict

    def test_multiple_refs(self, tmp_path):
        from dit.core.fsck import fsck

        store = ObjectStore(tmp_path / "objects")
        c1 = _make_commit(store, {"a.jsonl": [_conv("q1", "a1")]})
        c2 = _make_commit(store, {"b.jsonl": [_conv("q2", "a2")]})

        result = fsck(store, [c1, c2])
        assert result.total_errors == 0
        assert result.checked_objects["commits"] >= 2
