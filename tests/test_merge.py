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


class TestMergeManifests:
    """Row-level three-way merge tests."""

    def _e(self, rh: str, qfp: str | None = None) -> ManifestEntry:
        return ManifestEntry(row_hash=rh, query_fingerprint=qfp)

    def test_all_same(self):
        """Base/ours/theirs identical — no changes."""
        base = Manifest(entries=[self._e("a1", "q1"), self._e("a2", "q2")])
        ours = Manifest(entries=[self._e("a1", "q1"), self._e("a2", "q2")])
        theirs = Manifest(entries=[self._e("a1", "q1"), self._e("a2", "q2")])
        merged, conflicts = merge_manifests(base, ours, theirs, "f.jsonl")
        assert conflicts == []
        assert [e.row_hash for e in merged] == ["a1", "a2"]

    def test_ours_adds_row(self):
        """Ours adds a new row — included in merged."""
        base = Manifest(entries=[self._e("a1", "q1")])
        ours = Manifest(entries=[self._e("a1", "q1"), self._e("a2", "q2")])
        theirs = Manifest(entries=[self._e("a1", "q1")])
        merged, conflicts = merge_manifests(base, ours, theirs, "f.jsonl")
        assert conflicts == []
        assert len(merged) == 2
        hashes = [e.row_hash for e in merged]
        assert "a1" in hashes and "a2" in hashes

    def test_theirs_adds_row(self):
        """Theirs adds a new row — appended to end."""
        base = Manifest(entries=[self._e("a1", "q1")])
        ours = Manifest(entries=[self._e("a1", "q1")])
        theirs = Manifest(entries=[self._e("a1", "q1"), self._e("b1", "q2")])
        merged, conflicts = merge_manifests(base, ours, theirs, "f.jsonl")
        assert conflicts == []
        assert len(merged) == 2
        assert merged[0].row_hash == "a1"
        assert merged[1].row_hash == "b1"

    def test_ours_deletes_row(self):
        """Ours deletes a row — removed from merged."""
        base = Manifest(entries=[self._e("a1", "q1"), self._e("a2", "q2")])
        ours = Manifest(entries=[self._e("a1", "q1")])
        theirs = Manifest(entries=[self._e("a1", "q1"), self._e("a2", "q2")])
        merged, conflicts = merge_manifests(base, ours, theirs, "f.jsonl")
        assert conflicts == []
        assert [e.row_hash for e in merged] == ["a1"]

    def test_theirs_deletes_row(self):
        """Theirs deletes a row — removed from merged."""
        base = Manifest(entries=[self._e("a1", "q1"), self._e("a2", "q2")])
        ours = Manifest(entries=[self._e("a1", "q1"), self._e("a2", "q2")])
        theirs = Manifest(entries=[self._e("a1", "q1")])
        merged, conflicts = merge_manifests(base, ours, theirs, "f.jsonl")
        assert conflicts == []
        assert [e.row_hash for e in merged] == ["a1"]

    def test_ours_refreshes_row(self):
        """Ours refreshes a row (same qfp, different row_hash) — take ours."""
        base = Manifest(entries=[self._e("old_rh", "q1")])
        ours = Manifest(entries=[self._e("new_rh_ours", "q1")])
        theirs = Manifest(entries=[self._e("old_rh", "q1")])
        merged, conflicts = merge_manifests(base, ours, theirs, "f.jsonl")
        assert conflicts == []
        assert merged[0].row_hash == "new_rh_ours"

    def test_theirs_refreshes_row(self):
        """Theirs refreshes a row — take theirs."""
        base = Manifest(entries=[self._e("old_rh", "q1")])
        ours = Manifest(entries=[self._e("old_rh", "q1")])
        theirs = Manifest(entries=[self._e("new_rh_theirs", "q1")])
        merged, conflicts = merge_manifests(base, ours, theirs, "f.jsonl")
        assert conflicts == []
        assert merged[0].row_hash == "new_rh_theirs"

    def test_both_refresh_same_result(self):
        """Both refresh same row to same result — no conflict."""
        base = Manifest(entries=[self._e("old_rh", "q1")])
        ours = Manifest(entries=[self._e("new_rh", "q1")])
        theirs = Manifest(entries=[self._e("new_rh", "q1")])
        merged, conflicts = merge_manifests(base, ours, theirs, "f.jsonl")
        assert conflicts == []
        assert merged[0].row_hash == "new_rh"

    def test_both_refresh_different_result_conflict(self):
        """Both refresh same row to different results — conflict."""
        base = Manifest(entries=[self._e("old_rh", "q1")])
        ours = Manifest(entries=[self._e("ours_rh", "q1")])
        theirs = Manifest(entries=[self._e("theirs_rh", "q1")])
        merged, conflicts = merge_manifests(base, ours, theirs, "f.jsonl")
        assert len(conflicts) == 1
        assert conflicts[0].conflict_type == "both_modified"
        assert conflicts[0].file_path == "f.jsonl"

    def test_both_add_same_new_row(self):
        """Both add the same new row — keep one copy."""
        base = Manifest(entries=[])
        ours = Manifest(entries=[self._e("new_rh", "q1")])
        theirs = Manifest(entries=[self._e("new_rh", "q1")])
        merged, conflicts = merge_manifests(base, ours, theirs, "f.jsonl")
        assert conflicts == []
        assert len(merged) == 1
        assert merged[0].row_hash == "new_rh"

    def test_row_ordering_ours_skeleton(self):
        """Ours order is used as skeleton, theirs additions appended."""
        base = Manifest(entries=[self._e("a1", "q1"), self._e("a2", "q2")])
        ours = Manifest(entries=[self._e("a2", "q2"), self._e("a1", "q1"), self._e("a3", "q3")])
        theirs = Manifest(entries=[self._e("a1", "q1"), self._e("a2", "q2"), self._e("b1", "q4")])
        merged, conflicts = merge_manifests(base, ours, theirs, "f.jsonl")
        assert conflicts == []
        hashes = [e.row_hash for e in merged]
        assert hashes == ["a2", "a1", "a3", "b1"]

    def test_complex_mixed_operations(self):
        """Mix of add, delete, refresh across both sides."""
        base = Manifest(entries=[
            self._e("r1", "q1"),
            self._e("r2", "q2"),
            self._e("r3", "q3"),
        ])
        ours = Manifest(entries=[
            self._e("r1", "q1"),       # keep
            self._e("r2_new", "q2"),    # refresh r2
            # r3 deleted
            self._e("r4", "q4"),        # new
        ])
        theirs = Manifest(entries=[
            self._e("r1", "q1"),        # keep
            self._e("r2", "q2"),        # unchanged
            self._e("r3", "q3"),        # unchanged
            self._e("r5", "q5"),        # new
        ])
        merged, conflicts = merge_manifests(base, ours, theirs, "f.jsonl")
        assert conflicts == []
        hashes = [e.row_hash for e in merged]
        assert "r1" in hashes
        assert "r2_new" in hashes  # ours refresh wins
        assert "r3" not in hashes  # ours deleted
        assert "r4" in hashes      # ours new
        assert "r5" in hashes      # theirs new

    def test_row_without_query_fingerprint(self):
        """Rows with qfp=None: treated as independent, no refresh detection."""
        base = Manifest(entries=[self._e("r1", None)])
        ours = Manifest(entries=[self._e("r1", None)])
        theirs = Manifest(entries=[self._e("r2", None)])
        merged, conflicts = merge_manifests(base, ours, theirs, "f.jsonl")
        assert conflicts == []
        # r1 deleted by theirs (not in theirs), r2 added by theirs
        assert len(merged) == 1
        assert merged[0].row_hash == "r2"

    def test_pure_reorder_no_conflict(self):
        """Ours reorders rows, theirs unchanged — ours order preserved."""
        base = Manifest(entries=[self._e("a1", "q1"), self._e("a2", "q2"), self._e("a3", "q3")])
        ours = Manifest(entries=[self._e("a3", "q3"), self._e("a1", "q1"), self._e("a2", "q2")])
        theirs = Manifest(entries=[self._e("a1", "q1"), self._e("a2", "q2"), self._e("a3", "q3")])
        merged, conflicts = merge_manifests(base, ours, theirs, "f.jsonl")
        assert conflicts == []
        assert [e.row_hash for e in merged] == ["a3", "a1", "a2"]
