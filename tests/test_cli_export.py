import json
import os
import time
from pathlib import Path

import pytest
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


def _init_repo(tmp_path: Path) -> tuple[ObjectStore, RefStore, str]:
    os.chdir(tmp_path)
    runner.invoke(app, ["init"])

    dot = tmp_path / ".dit"
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)

    row = json.dumps({"messages": [{"role": "user", "content": "hello"}]})
    rh = store.write("rows", row.encode("utf-8"))

    manifest = Manifest(entries=[ManifestEntry(row_hash=rh, query_fingerprint=None)])
    mh = store.write("manifests", serialize_manifest(manifest))

    tree_entries = {"train.jsonl": ("manifest", mh, None)}
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


class TestExportCommand:
    def test_export_creates_output_file(self, tmp_path: Path):
        _init_repo(tmp_path)
        out = tmp_path / "exported"
        out.mkdir()

        result = runner.invoke(app, ["export", "--output", str(out)])
        assert result.exit_code == 0
        assert (out / "train.jsonl").exists()

    def test_export_output_contains_summary(self, tmp_path: Path):
        _init_repo(tmp_path)
        out = tmp_path / "exported"
        out.mkdir()

        result = runner.invoke(app, ["export", "--output", str(out)])
        assert result.exit_code == 0
        assert "train.jsonl" in result.stdout

    def test_export_file_filter(self, tmp_path: Path):
        _init_repo(tmp_path)
        out = tmp_path / "exported"
        out.mkdir()

        result = runner.invoke(app, ["export", "--file", "train.jsonl", "--output", str(out)])
        assert result.exit_code == 0
        assert (out / "train.jsonl").exists()

    def test_export_missing_file_filter_exits_1(self, tmp_path: Path):
        _init_repo(tmp_path)
        out = tmp_path / "exported"
        out.mkdir()

        result = runner.invoke(app, ["export", "--file", "missing.jsonl", "--output", str(out)])
        assert result.exit_code == 1

    def test_export_no_commits_exits_1(self, tmp_path: Path):
        os.chdir(tmp_path)
        runner.invoke(app, ["init"])
        out = tmp_path / "exported"
        out.mkdir()

        result = runner.invoke(app, ["export", "--output", str(out)])
        assert result.exit_code == 1

    def test_export_csv_format(self, tmp_path: Path):
        _init_repo(tmp_path)
        out = tmp_path / "exported"
        out.mkdir()

        result = runner.invoke(app, ["export", "--format", "csv", "--output", str(out)])
        assert result.exit_code == 0
        assert (out / "train.jsonl").exists()
        content = (out / "train.jsonl").read_text()
        assert "messages" in content

    def test_export_ref_flag(self, tmp_path: Path):
        _init_repo(tmp_path)
        out = tmp_path / "exported"
        out.mkdir()

        result = runner.invoke(app, ["export", "--ref", "main", "--output", str(out)])
        assert result.exit_code == 0
        assert (out / "train.jsonl").exists()

    def test_export_outside_repo_exits_1(self, tmp_path: Path):
        empty = tmp_path / "empty"
        empty.mkdir()
        os.chdir(empty)
        out = tmp_path / "exported"
        out.mkdir()

        result = runner.invoke(app, ["export", "--output", str(out)])
        assert result.exit_code == 1
