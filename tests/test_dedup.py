"""Tests for dit.core.dedup module."""
import json
import time

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


def _make_commit(
    store: ObjectStore,
    files: dict[str, list[dict]],
    parent_hashes: list[str] | None = None,
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


def _conv(user_msg: str, asst_msg: str) -> dict:
    return {"messages": [{"role": "user", "content": user_msg}, {"role": "assistant", "content": asst_msg}]}


class TestDetectDuplicates:
    def test_no_duplicates(self, tmp_path):
        from dit.core.dedup import detect_duplicates

        store = ObjectStore(tmp_path / "objects")
        c = _make_commit(store, {"train.jsonl": [
            _conv("q1", "a1"),
            _conv("q2", "a2"),
        ]})

        result = detect_duplicates(store, c)
        assert result["summary"]["severity"] == "clean"
        assert result["summary"]["exact_dup_groups"] == 0
        assert result["summary"]["query_dup_groups"] == 0

    def test_exact_duplicates_same_file(self, tmp_path):
        from dit.core.dedup import detect_duplicates

        store = ObjectStore(tmp_path / "objects")
        row = _conv("q1", "a1")
        c = _make_commit(store, {"train.jsonl": [row, row, _conv("q2", "a2")]})

        result = detect_duplicates(store, c)
        assert result["summary"]["severity"] == "warning"
        assert result["summary"]["exact_dup_groups"] == 1
        assert result["summary"]["exact_dup_rows"] == 2

    def test_exact_duplicates_cross_file(self, tmp_path):
        from dit.core.dedup import detect_duplicates

        store = ObjectStore(tmp_path / "objects")
        row = _conv("q1", "a1")
        c = _make_commit(store, {
            "train.jsonl": [row],
            "eval.jsonl": [row],
        })

        result = detect_duplicates(store, c)
        assert result["summary"]["severity"] == "warning"
        assert result["summary"]["exact_dup_groups"] == 1
        assert len(result["exact_duplicates"][0]["occurrences"]) == 2
        files = {o["file"] for o in result["exact_duplicates"][0]["occurrences"]}
        assert files == {"train.jsonl", "eval.jsonl"}

    def test_query_duplicates_different_responses(self, tmp_path):
        from dit.core.dedup import detect_duplicates

        store = ObjectStore(tmp_path / "objects")
        c = _make_commit(store, {"train.jsonl": [
            _conv("same query", "response A"),
            _conv("same query", "response B"),
            _conv("different query", "response C"),
        ]})

        result = detect_duplicates(store, c)
        assert result["summary"]["severity"] == "info"
        assert result["summary"]["exact_dup_groups"] == 0
        assert result["summary"]["query_dup_groups"] == 1
        group = result["query_duplicates"][0]
        assert group["count"] == 2
        assert len(group["row_hashes"]) == 2

    def test_both_exact_and_query_duplicates(self, tmp_path):
        from dit.core.dedup import detect_duplicates

        store = ObjectStore(tmp_path / "objects")
        exact_row = _conv("q1", "a1")
        c = _make_commit(store, {"train.jsonl": [
            exact_row,
            exact_row,
            _conv("q2", "response X"),
            _conv("q2", "response Y"),
        ]})

        result = detect_duplicates(store, c)
        assert result["summary"]["severity"] == "warning"
        assert result["summary"]["exact_dup_groups"] == 1
        assert result["summary"]["query_dup_groups"] == 1

    def test_path_prefix_filter(self, tmp_path):
        from dit.core.dedup import detect_duplicates

        store = ObjectStore(tmp_path / "objects")
        row = _conv("q1", "a1")
        c = _make_commit(store, {
            "train.jsonl": [row],
            "eval.jsonl": [row],
        })

        result = detect_duplicates(store, c, path_prefix="train")
        assert result["summary"]["total_files"] == 1
        assert result["summary"]["exact_dup_groups"] == 0

    def test_commit_not_found(self, tmp_path):
        import pytest
        from dit.core.dedup import detect_duplicates

        store = ObjectStore(tmp_path / "objects")
        with pytest.raises(FileNotFoundError):
            detect_duplicates(store, "0" * 64)

    def test_content_preview_in_occurrences(self, tmp_path):
        from dit.core.dedup import detect_duplicates

        store = ObjectStore(tmp_path / "objects")
        row = _conv("q1", "a1")
        c = _make_commit(store, {"train.jsonl": [row, row]})

        result = detect_duplicates(store, c)
        occ = result["exact_duplicates"][0]["occurrences"][0]
        assert "content_preview" in occ
        assert len(occ["content_preview"]) <= 63

    def test_summary_counts(self, tmp_path):
        from dit.core.dedup import detect_duplicates

        store = ObjectStore(tmp_path / "objects")
        c = _make_commit(store, {
            "a.jsonl": [_conv("q1", "a1"), _conv("q2", "a2")],
            "b.jsonl": [_conv("q3", "a3")],
        })

        result = detect_duplicates(store, c)
        assert result["summary"]["total_rows"] == 3
        assert result["summary"]["total_files"] == 2

    def test_no_query_fingerprint_rows(self, tmp_path):
        from dit.core.dedup import detect_duplicates

        store = ObjectStore(tmp_path / "objects")
        row_no_qfp = {"data": "no messages field"}
        c = _make_commit(store, {"data.jsonl": [row_no_qfp, row_no_qfp]})

        result = detect_duplicates(store, c)
        assert result["summary"]["exact_dup_groups"] == 1
        assert result["summary"]["query_dup_groups"] == 0
