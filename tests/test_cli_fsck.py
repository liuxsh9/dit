"""Tests for dit fsck CLI command."""
import json
import time
from pathlib import Path

import pyzstd
from typer.testing import CliRunner

from dit.cli.main import app
from dit.core.objects import (
    Commit, Manifest, ManifestEntry, Tree, TreeEntry,
    serialize_manifest, serialize_tree, serialize_commit,
)
from dit.core.refs import RefStore
from dit.core.store import ObjectStore
from dit.core.hash import row_hash as compute_row_hash, query_fingerprint as compute_qfp

runner = CliRunner()


def _setup_repo(tmp_path: Path) -> tuple[Path, ObjectStore, RefStore]:
    dot = tmp_path / ".dit"
    dot.mkdir()
    (dot / "objects").mkdir()
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)
    refs.init()
    return dot, store, refs


def _write_row(store, content):
    data = json.dumps(content, separators=(",", ":"), sort_keys=True).encode()
    return store.write("rows", data)


def _conv(user_msg, asst_msg):
    return {"messages": [{"role": "user", "content": user_msg}, {"role": "assistant", "content": asst_msg}]}


def _make_commit(store, files, parent_hashes=None):
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


class TestFsckCommand:
    def test_clean_repo(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot, store, refs = _setup_repo(tmp_path)
        c = _make_commit(store, {"train.jsonl": [_conv("q1", "a1")]})
        refs.set_branch("main", c)

        result = runner.invoke(app, ["fsck"])
        assert result.exit_code == 0
        assert "No issues" in result.output or "0 error" in result.output

    def test_corrupt_object_exit_code(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot, store, refs = _setup_repo(tmp_path)
        c = _make_commit(store, {"train.jsonl": [_conv("q1", "a1")]})
        refs.set_branch("main", c)

        row_hash = compute_row_hash(_conv("q1", "a1"))
        obj_path = store._object_path("rows", row_hash)
        obj_path.write_bytes(pyzstd.compress(b"corrupted"))

        result = runner.invoke(app, ["fsck"])
        assert result.exit_code == 1

    def test_json_format(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot, store, refs = _setup_repo(tmp_path)
        c = _make_commit(store, {"train.jsonl": [_conv("q1", "a1")]})
        refs.set_branch("main", c)

        result = runner.invoke(app, ["fsck", "--format", "json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "checked_objects" in data
        assert "errors" in data
        assert "warnings" in data
        assert "total_checked" in data

    def test_no_hash_check_flag(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot, store, refs = _setup_repo(tmp_path)
        c = _make_commit(store, {"train.jsonl": [_conv("q1", "a1")]})
        refs.set_branch("main", c)

        row_hash = compute_row_hash(_conv("q1", "a1"))
        obj_path = store._object_path("rows", row_hash)
        obj_path.write_bytes(pyzstd.compress(b"corrupted"))

        result = runner.invoke(app, ["fsck", "--no-hash-check"])
        assert result.exit_code == 0

    def test_no_commits_repo(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot, store, refs = _setup_repo(tmp_path)

        result = runner.invoke(app, ["fsck"])
        assert result.exit_code == 0
