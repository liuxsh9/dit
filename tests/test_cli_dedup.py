"""Tests for dit dedup CLI command."""
import json
import os
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

runner = CliRunner()


def _setup_repo(tmp_path: Path) -> tuple[Path, ObjectStore, RefStore]:
    dot = tmp_path / ".datahub"
    dot.mkdir()
    (dot / "objects").mkdir()
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)
    refs.init()
    return dot, store, refs


def _write_row(store: ObjectStore, content: dict) -> str:
    data = json.dumps(content, separators=(",", ":"), sort_keys=True).encode()
    return store.write("rows", data)


def _conv(user_msg: str, asst_msg: str) -> dict:
    return {"messages": [{"role": "user", "content": user_msg}, {"role": "assistant", "content": asst_msg}]}


def _make_commit(store: ObjectStore, files: dict[str, list[dict]], parent_hashes=None) -> str:
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
        m_hash = store.write("manifests", serialize_manifest(manifest))
        tree_entries.append(TreeEntry(name=fname, obj_type="manifest", obj_hash=m_hash))
    tree = Tree(entries=tree_entries)
    t_hash = store.write("trees", serialize_tree(tree))
    c = Commit(tree_hash=t_hash, parent_hashes=parent_hashes or [], author="alice", message="test", timestamp=int(time.time()))
    return store.write("commits", serialize_commit(c))


class TestDedupCommand:
    def test_clean_table(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot, store, refs = _setup_repo(tmp_path)
        c = _make_commit(store, {"train.jsonl": [_conv("q1", "a1"), _conv("q2", "a2")]})
        refs.set_branch("main", c)

        result = runner.invoke(app, ["dedup"])
        assert result.exit_code == 0
        assert "No duplicates" in result.output or "clean" in result.output.lower()

    def test_exact_dup_warning_exit_code(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot, store, refs = _setup_repo(tmp_path)
        row = _conv("q1", "a1")
        c = _make_commit(store, {"train.jsonl": [row, row]})
        refs.set_branch("main", c)

        result = runner.invoke(app, ["dedup"])
        assert result.exit_code == 1
        assert "EXACT" in result.output or "exact" in result.output.lower() or "WARNING" in result.output

    def test_query_dup_info_exit_code_zero(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot, store, refs = _setup_repo(tmp_path)
        c = _make_commit(store, {"train.jsonl": [
            _conv("same q", "resp A"),
            _conv("same q", "resp B"),
        ]})
        refs.set_branch("main", c)

        result = runner.invoke(app, ["dedup"])
        assert result.exit_code == 0
        assert "QUERY" in result.output or "query" in result.output.lower()

    def test_json_format(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot, store, refs = _setup_repo(tmp_path)
        row = _conv("q1", "a1")
        c = _make_commit(store, {"train.jsonl": [row, row]})
        refs.set_branch("main", c)

        result = runner.invoke(app, ["dedup", "--format", "json"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert "exact_duplicates" in data
        assert "query_duplicates" in data
        assert "summary" in data

    def test_ref_option(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot, store, refs = _setup_repo(tmp_path)
        c = _make_commit(store, {"train.jsonl": [_conv("q1", "a1")]})
        refs.set_branch("dev", c)

        result = runner.invoke(app, ["dedup", "--ref", "dev"])
        assert result.exit_code == 0

    def test_exact_only_flag(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot, store, refs = _setup_repo(tmp_path)
        row = _conv("q1", "a1")
        c = _make_commit(store, {"train.jsonl": [
            row, row,
            _conv("q2", "resp A"),
            _conv("q2", "resp B"),
        ]})
        refs.set_branch("main", c)

        result = runner.invoke(app, ["dedup", "--exact-only", "--format", "json"])
        data = json.loads(result.output)
        assert len(data["exact_duplicates"]) >= 1
        assert len(data["query_duplicates"]) == 0

    def test_query_only_flag(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot, store, refs = _setup_repo(tmp_path)
        row = _conv("q1", "a1")
        c = _make_commit(store, {"train.jsonl": [
            row, row,
            _conv("q2", "resp A"),
            _conv("q2", "resp B"),
        ]})
        refs.set_branch("main", c)

        result = runner.invoke(app, ["dedup", "--query-only", "--format", "json"])
        data = json.loads(result.output)
        assert len(data["exact_duplicates"]) == 0
        assert len(data["query_duplicates"]) >= 1
