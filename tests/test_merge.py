# tests/test_merge.py
"""Tests for three-way merge algorithm."""
import time

from dit.core.merge import (
    MergeConflict,
    MergeResult,
    three_way_merge,
    merge_manifests,
)
from dit.core.objects import (
    Commit,
    Manifest,
    ManifestEntry,
    Tree,
    TreeEntry,
    object_hash,
    serialize_commit,
    serialize_manifest,
    serialize_tree,
)
from dit.core.store import ObjectStore


def _write_manifest(store: ObjectStore, entries: list[ManifestEntry]) -> str:
    m = Manifest(entries=entries)
    data = serialize_manifest(m)
    return store.write("manifests", data)


def _write_tree(store: ObjectStore, file_entries: dict[str, str]) -> str:
    entries = [
        TreeEntry(name=name, obj_type="manifest", obj_hash=mhash)
        for name, mhash in file_entries.items()
    ]
    tree = Tree(entries=entries)
    data = serialize_tree(tree)
    return store.write("trees", data)


def _write_commit(store: ObjectStore, tree_hash: str, parents: list[str]) -> str:
    c = Commit(
        tree_hash=tree_hash,
        parent_hashes=parents,
        author="test",
        message="test",
        timestamp=int(time.time()),
    )
    data = serialize_commit(c)
    return store.write("commits", data)


class TestFileLevelMerge:
    """Tests for tree-level three-way merge dispatch (per-file decisions)."""

    def test_both_same_as_base(self, tmp_path):
        """File unchanged in both — keep as-is."""
        store = ObjectStore(tmp_path / "objects")
        entry = ManifestEntry(row_hash="a" * 64, query_fingerprint="q1")
        mhash = _write_manifest(store, [entry])
        base_tree = _write_tree(store, {"f.jsonl": mhash})
        base = _write_commit(store, base_tree, [])
        ours = _write_commit(store, base_tree, [base])
        theirs = _write_commit(store, base_tree, [base])
        result = three_way_merge(store, base, ours, theirs)
        assert result.conflicts == []
        assert "f.jsonl" in result.merged_tree_entries

    def test_ours_modified_theirs_same(self, tmp_path):
        """File modified only in ours — take ours."""
        store = ObjectStore(tmp_path / "objects")
        base_e = ManifestEntry(row_hash="a" * 64, query_fingerprint="q1")
        ours_e = ManifestEntry(row_hash="b" * 64, query_fingerprint="q1")
        base_mhash = _write_manifest(store, [base_e])
        ours_mhash = _write_manifest(store, [ours_e])
        base_tree = _write_tree(store, {"f.jsonl": base_mhash})
        ours_tree = _write_tree(store, {"f.jsonl": ours_mhash})
        base_c = _write_commit(store, base_tree, [])
        ours_c = _write_commit(store, ours_tree, [base_c])
        theirs_c = _write_commit(store, base_tree, [base_c])
        result = three_way_merge(store, base_c, ours_c, theirs_c)
        assert result.conflicts == []
        assert result.merged_tree_entries["f.jsonl"] == ours_mhash

    def test_theirs_modified_ours_same(self, tmp_path):
        """File modified only in theirs — take theirs."""
        store = ObjectStore(tmp_path / "objects")
        base_e = ManifestEntry(row_hash="a" * 64, query_fingerprint="q1")
        theirs_e = ManifestEntry(row_hash="c" * 64, query_fingerprint="q1")
        base_mhash = _write_manifest(store, [base_e])
        theirs_mhash = _write_manifest(store, [theirs_e])
        base_tree = _write_tree(store, {"f.jsonl": base_mhash})
        theirs_tree = _write_tree(store, {"f.jsonl": theirs_mhash})
        base_c = _write_commit(store, base_tree, [])
        ours_c = _write_commit(store, base_tree, [base_c])
        theirs_c = _write_commit(store, theirs_tree, [base_c])
        result = three_way_merge(store, base_c, ours_c, theirs_c)
        assert result.conflicts == []
        assert result.merged_tree_entries["f.jsonl"] == theirs_mhash

    def test_ours_deleted_theirs_same(self, tmp_path):
        """File deleted in ours, unchanged in theirs — delete."""
        store = ObjectStore(tmp_path / "objects")
        base_e = ManifestEntry(row_hash="a" * 64, query_fingerprint="q1")
        base_mhash = _write_manifest(store, [base_e])
        base_tree = _write_tree(store, {"f.jsonl": base_mhash})
        ours_tree = _write_tree(store, {})
        base_c = _write_commit(store, base_tree, [])
        ours_c = _write_commit(store, ours_tree, [base_c])
        theirs_c = _write_commit(store, base_tree, [base_c])
        result = three_way_merge(store, base_c, ours_c, theirs_c)
        assert result.conflicts == []
        assert "f.jsonl" not in result.merged_tree_entries

    def test_theirs_deleted_ours_same(self, tmp_path):
        """File deleted in theirs, unchanged in ours — delete."""
        store = ObjectStore(tmp_path / "objects")
        base_e = ManifestEntry(row_hash="a" * 64, query_fingerprint="q1")
        base_mhash = _write_manifest(store, [base_e])
        base_tree = _write_tree(store, {"f.jsonl": base_mhash})
        theirs_tree = _write_tree(store, {})
        base_c = _write_commit(store, base_tree, [])
        ours_c = _write_commit(store, base_tree, [base_c])
        theirs_c = _write_commit(store, theirs_tree, [base_c])
        result = three_way_merge(store, base_c, ours_c, theirs_c)
        assert result.conflicts == []
        assert "f.jsonl" not in result.merged_tree_entries

    def test_both_deleted(self, tmp_path):
        """File deleted in both — delete."""
        store = ObjectStore(tmp_path / "objects")
        base_e = ManifestEntry(row_hash="a" * 64, query_fingerprint="q1")
        base_mhash = _write_manifest(store, [base_e])
        base_tree = _write_tree(store, {"f.jsonl": base_mhash})
        empty_tree = _write_tree(store, {})
        base_c = _write_commit(store, base_tree, [])
        ours_c = _write_commit(store, empty_tree, [base_c])
        theirs_c = _write_commit(store, empty_tree, [base_c])
        result = three_way_merge(store, base_c, ours_c, theirs_c)
        assert result.conflicts == []
        assert "f.jsonl" not in result.merged_tree_entries

    def test_ours_new_file(self, tmp_path):
        """New file only in ours — add."""
        store = ObjectStore(tmp_path / "objects")
        new_e = ManifestEntry(row_hash="a" * 64, query_fingerprint="q1")
        new_mhash = _write_manifest(store, [new_e])
        base_tree = _write_tree(store, {})
        ours_tree = _write_tree(store, {"new.jsonl": new_mhash})
        base_c = _write_commit(store, base_tree, [])
        ours_c = _write_commit(store, ours_tree, [base_c])
        theirs_c = _write_commit(store, base_tree, [base_c])
        result = three_way_merge(store, base_c, ours_c, theirs_c)
        assert result.conflicts == []
        assert result.merged_tree_entries["new.jsonl"] == new_mhash

    def test_theirs_new_file(self, tmp_path):
        """New file only in theirs — add."""
        store = ObjectStore(tmp_path / "objects")
        new_e = ManifestEntry(row_hash="a" * 64, query_fingerprint="q1")
        new_mhash = _write_manifest(store, [new_e])
        base_tree = _write_tree(store, {})
        theirs_tree = _write_tree(store, {"new.jsonl": new_mhash})
        base_c = _write_commit(store, base_tree, [])
        ours_c = _write_commit(store, base_tree, [base_c])
        theirs_c = _write_commit(store, theirs_tree, [base_c])
        result = three_way_merge(store, base_c, ours_c, theirs_c)
        assert result.conflicts == []
        assert result.merged_tree_entries["new.jsonl"] == new_mhash

    def test_both_add_same_new_file(self, tmp_path):
        """Both add the same new file with same content — keep one."""
        store = ObjectStore(tmp_path / "objects")
        new_e = ManifestEntry(row_hash="a" * 64, query_fingerprint="q1")
        new_mhash = _write_manifest(store, [new_e])
        base_tree = _write_tree(store, {})
        both_tree = _write_tree(store, {"new.jsonl": new_mhash})
        base_c = _write_commit(store, base_tree, [])
        ours_c = _write_commit(store, both_tree, [base_c])
        theirs_c = _write_commit(store, both_tree, [base_c])
        result = three_way_merge(store, base_c, ours_c, theirs_c)
        assert result.conflicts == []
        assert result.merged_tree_entries["new.jsonl"] == new_mhash

    def test_modify_delete_conflict(self, tmp_path):
        """Ours deletes, theirs modifies — conflict."""
        store = ObjectStore(tmp_path / "objects")
        base_e = ManifestEntry(row_hash="a" * 64, query_fingerprint="q1")
        theirs_e = ManifestEntry(row_hash="b" * 64, query_fingerprint="q1")
        base_mhash = _write_manifest(store, [base_e])
        theirs_mhash = _write_manifest(store, [theirs_e])
        base_tree = _write_tree(store, {"f.jsonl": base_mhash})
        ours_tree = _write_tree(store, {})
        theirs_tree = _write_tree(store, {"f.jsonl": theirs_mhash})
        base_c = _write_commit(store, base_tree, [])
        ours_c = _write_commit(store, ours_tree, [base_c])
        theirs_c = _write_commit(store, theirs_tree, [base_c])
        result = three_way_merge(store, base_c, ours_c, theirs_c)
        assert len(result.conflicts) == 1
        assert result.conflicts[0].file_path == "f.jsonl"
        assert result.conflicts[0].conflict_type == "modify_delete"

    def test_both_add_different_new_file_conflict(self, tmp_path):
        """Both add same filename with different content — conflict."""
        store = ObjectStore(tmp_path / "objects")
        ours_e = ManifestEntry(row_hash="a" * 64, query_fingerprint="q1")
        theirs_e = ManifestEntry(row_hash="b" * 64, query_fingerprint="q2")
        ours_mhash = _write_manifest(store, [ours_e])
        theirs_mhash = _write_manifest(store, [theirs_e])
        base_tree = _write_tree(store, {})
        ours_tree = _write_tree(store, {"new.jsonl": ours_mhash})
        theirs_tree = _write_tree(store, {"new.jsonl": theirs_mhash})
        base_c = _write_commit(store, base_tree, [])
        ours_c = _write_commit(store, ours_tree, [base_c])
        theirs_c = _write_commit(store, theirs_tree, [base_c])
        result = three_way_merge(store, base_c, ours_c, theirs_c)
        assert len(result.conflicts) == 1
        assert result.conflicts[0].file_path == "new.jsonl"
        assert result.conflicts[0].conflict_type == "both_added"
