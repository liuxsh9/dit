"""Tests for dit blame CLI command."""
import json
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
from dit.core.hash import row_hash as compute_row_hash

runner = CliRunner()


def _init_repo(tmp_path: Path):
    dot = tmp_path / ".dit"
    dot.mkdir()
    (dot / "objects").mkdir()
    RefStore(dot).init()
    return dot


def _write_row(store, content):
    data = json.dumps(content, separators=(",", ":"), sort_keys=True).encode()
    return store.write("rows", data), compute_row_hash(content)


def _make_commit(store, files, parent_hashes=None, author="alice", msg="c", ts=None):
    tree_entries = []
    for fname, rows in files.items():
        entries = []
        for row in rows:
            rh = compute_row_hash(row)
            data = json.dumps(row, separators=(",", ":"), sort_keys=True).encode()
            store.write("rows", data)
            entries.append(ManifestEntry(row_hash=rh, query_fingerprint=None))
        manifest = Manifest(entries=entries)
        m_bytes = serialize_manifest(manifest)
        m_hash = store.write("manifests", m_bytes)
        tree_entries.append(TreeEntry(name=fname, obj_type="manifest", obj_hash=m_hash))
    tree = Tree(entries=tree_entries)
    t_bytes = serialize_tree(tree)
    t_hash = store.write("trees", t_bytes)
    c = Commit(
        tree_hash=t_hash,
        parent_hashes=parent_hashes or [],
        author=author, message=msg, timestamp=ts or int(time.time()),
    )
    return store.write("commits", serialize_commit(c))


class TestBlameCommand:

    def test_blame_table_output(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot = _init_repo(tmp_path)
        store = ObjectStore(dot / "objects")
        refs = RefStore(dot)

        row_a = {"text": "hello", "label": "pos"}
        row_b = {"text": "world", "label": "neg"}
        c1 = _make_commit(store, {"train.jsonl": [row_a]}, author="alice", ts=1000)
        c2 = _make_commit(store, {"train.jsonl": [row_a, row_b]}, parent_hashes=[c1], author="bob", ts=2000)
        refs.set_branch("main", c2)

        result = runner.invoke(app, ["blame", "train.jsonl"])
        assert result.exit_code == 0
        assert "alice" in result.output
        assert "bob" in result.output
        assert "train.jsonl" in result.output

    def test_blame_json_output(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot = _init_repo(tmp_path)
        store = ObjectStore(dot / "objects")
        refs = RefStore(dot)

        row = {"text": "test", "label": "x"}
        c1 = _make_commit(store, {"data.jsonl": [row]}, author="alice", ts=1000)
        refs.set_branch("main", c1)

        result = runner.invoke(app, ["blame", "data.jsonl", "--format", "json"])
        assert result.exit_code == 0
        body = json.loads(result.output)
        assert body["file"] == "data.jsonl"
        assert len(body["entries"]) == 1

    def test_blame_with_ref(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot = _init_repo(tmp_path)
        store = ObjectStore(dot / "objects")
        refs = RefStore(dot)

        row = {"text": "test", "label": "x"}
        c1 = _make_commit(store, {"f.jsonl": [row]}, ts=1000)
        refs.set_branch("main", c1)
        refs.set_branch("dev", c1)

        result = runner.invoke(app, ["blame", "f.jsonl", "--ref", "dev"])
        assert result.exit_code == 0

    def test_blame_file_not_found(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot = _init_repo(tmp_path)
        store = ObjectStore(dot / "objects")
        refs = RefStore(dot)

        c1 = _make_commit(store, {"other.jsonl": [{"a": 1}]}, ts=1000)
        refs.set_branch("main", c1)

        result = runner.invoke(app, ["blame", "missing.jsonl"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower() or "fatal" in result.output.lower()

    def test_blame_row_history(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot = _init_repo(tmp_path)
        store = ObjectStore(dot / "objects")
        refs = RefStore(dot)

        row = {"text": "hello", "label": "pos"}
        c1 = _make_commit(store, {"train.jsonl": [row]}, author="alice", ts=1000)
        refs.set_branch("main", c1)

        result = runner.invoke(app, ["blame", "train.jsonl", "--row", "0"])
        assert result.exit_code == 0
        assert "added" in result.output.lower()

    def test_blame_row_history_json(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot = _init_repo(tmp_path)
        store = ObjectStore(dot / "objects")
        refs = RefStore(dot)

        row = {"text": "hello", "label": "pos"}
        c1 = _make_commit(store, {"train.jsonl": [row]}, author="alice", ts=1000)
        refs.set_branch("main", c1)

        result = runner.invoke(app, ["blame", "train.jsonl", "--row", "0", "--format", "json"])
        assert result.exit_code == 0
        body = json.loads(result.output)
        assert body["row_index"] == 0

    def test_blame_no_commits(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _init_repo(tmp_path)

        result = runner.invoke(app, ["blame", "train.jsonl"])
        assert result.exit_code == 1

    def test_blame_summary_line(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot = _init_repo(tmp_path)
        store = ObjectStore(dot / "objects")
        refs = RefStore(dot)

        row_a = {"text": "a", "label": "1"}
        row_b = {"text": "b", "label": "2"}
        c1 = _make_commit(store, {"f.jsonl": [row_a]}, author="alice", ts=1000)
        c2 = _make_commit(store, {"f.jsonl": [row_a, row_b]}, parent_hashes=[c1], author="bob", ts=2000)
        refs.set_branch("main", c2)

        result = runner.invoke(app, ["blame", "f.jsonl"])
        assert result.exit_code == 0
        assert "2 rows" in result.output
        assert "2 commits" in result.output
        assert "2 authors" in result.output
