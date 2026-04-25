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
