# tests/test_cli_search.py
import json
import os
import time
from pathlib import Path

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
        lines = [line for line in result.stdout.splitlines() if "eval.jsonl" in line]
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
