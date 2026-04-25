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
