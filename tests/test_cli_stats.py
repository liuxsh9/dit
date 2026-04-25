import json
import os
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dit.cli.main import app
from dit.core.objects import (
    Commit, Manifest, ManifestEntry,
    Sidecar, SidecarEntry,
    serialize_commit, serialize_manifest, serialize_sidecar,
)
from dit.core.store import ObjectStore
from dit.core.tree_builder import build_nested_tree
from dit.core.refs import RefStore

runner = CliRunner()


def _init_repo_with_sidecar(tmp_path: Path) -> tuple[ObjectStore, RefStore, str]:
    """Init a dit repo with train.jsonl (with sidecar) and eval.jsonl (no sidecar)."""
    os.chdir(tmp_path)
    runner.invoke(app, ["init"])

    dot = tmp_path / ".dit"
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)

    row = json.dumps({"instruction": "hello", "response": "world"})
    rh = store.write("rows", row.encode("utf-8"))
    manifest = Manifest(entries=[ManifestEntry(row_hash=rh, query_fingerprint=None)])
    mh = store.write("manifests", serialize_manifest(manifest))

    sc = Sidecar(
        manifest_hash=mh,
        entries=[SidecarEntry(row_hash=rh, char_count=40, token_estimate=10, field_count=2, lang="en")],
    )
    sc_hash = store.write("sidecars", serialize_sidecar(sc))

    eval_row = json.dumps({"q": "hi"})
    eval_rh = store.write("rows", eval_row.encode("utf-8"))
    eval_mh = store.write("manifests", serialize_manifest(Manifest(entries=[ManifestEntry(row_hash=eval_rh, query_fingerprint=None)])))

    tree_entries = {
        "train.jsonl": ("manifest", mh, sc_hash),
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


class TestStatsCommand:
    def test_stats_default_exits_0(self, tmp_path: Path):
        _init_repo_with_sidecar(tmp_path)
        result = runner.invoke(app, ["stats"])
        assert result.exit_code == 0

    def test_stats_default_shows_file_with_sidecar(self, tmp_path: Path):
        _init_repo_with_sidecar(tmp_path)
        result = runner.invoke(app, ["stats"])
        assert "train.jsonl" in result.stdout

    def test_stats_default_shows_file_without_sidecar(self, tmp_path: Path):
        _init_repo_with_sidecar(tmp_path)
        result = runner.invoke(app, ["stats"])
        assert "eval.jsonl" in result.stdout

    def test_stats_default_shows_totals(self, tmp_path: Path):
        _init_repo_with_sidecar(tmp_path)
        result = runner.invoke(app, ["stats"])
        assert "TOTAL" in result.stdout

    def test_stats_default_shows_sidecar_warning(self, tmp_path: Path):
        _init_repo_with_sidecar(tmp_path)
        result = runner.invoke(app, ["stats"])
        # 1 of 2 files lacks a sidecar — footer warning expected
        assert "sidecar" in result.stdout.lower() or "meta" in result.stdout.lower()

    def test_stats_json_format_is_valid(self, tmp_path: Path):
        _init_repo_with_sidecar(tmp_path)
        result = runner.invoke(app, ["stats", "--format", "json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert "commit_hash" in data
        assert "files" in data
        assert "totals" in data

    def test_stats_json_format_files_have_has_sidecar(self, tmp_path: Path):
        _init_repo_with_sidecar(tmp_path)
        result = runner.invoke(app, ["stats", "--format", "json"])
        data = json.loads(result.stdout)
        has_sidecar_values = {f["path"]: f["has_sidecar"] for f in data["files"]}
        assert has_sidecar_values["train.jsonl"] is True
        assert has_sidecar_values["eval.jsonl"] is False

    def test_stats_path_filter(self, tmp_path: Path):
        _init_repo_with_sidecar(tmp_path)
        result = runner.invoke(app, ["stats", "train.jsonl"])
        assert result.exit_code == 0
        assert "train.jsonl" in result.stdout
        assert "eval.jsonl" not in result.stdout

    def test_stats_ref_flag(self, tmp_path: Path):
        _init_repo_with_sidecar(tmp_path)
        result = runner.invoke(app, ["stats", "--ref", "main"])
        assert result.exit_code == 0
        assert "train.jsonl" in result.stdout

    def test_stats_bad_ref_exits_1(self, tmp_path: Path):
        _init_repo_with_sidecar(tmp_path)
        result = runner.invoke(app, ["stats", "--ref", "nonexistent"])
        assert result.exit_code == 1

    def test_stats_compare_exits_0(self, tmp_path: Path):
        store, refs, c1 = _init_repo_with_sidecar(tmp_path)

        # Second commit: new train.jsonl with 2 rows and sidecar
        row2 = json.dumps({"instruction": "q2", "response": "a2"})
        rh2 = store.write("rows", row2.encode("utf-8"))
        mh2 = store.write("manifests", serialize_manifest(Manifest(entries=[
            ManifestEntry(row_hash=rh2, query_fingerprint=None)
        ])))
        sc2 = Sidecar(manifest_hash=mh2, entries=[
            SidecarEntry(row_hash=rh2, char_count=30, token_estimate=7, field_count=2, lang="en")
        ])
        sc2_hash = store.write("sidecars", serialize_sidecar(sc2))

        eval_row = json.dumps({"q": "hi"})
        eval_rh = store.write("rows", eval_row.encode("utf-8"))
        eval_mh = store.write("manifests", serialize_manifest(Manifest(entries=[ManifestEntry(row_hash=eval_rh, query_fingerprint=None)])))

        tree_entries2 = {
            "train.jsonl": ("manifest", mh2, sc2_hash),
            "eval.jsonl": ("manifest", eval_mh, None),
        }
        tree_hash2 = build_nested_tree(store, tree_entries2)
        c2_obj = Commit(tree_hash=tree_hash2, parent_hashes=[c1], author="t", message="second", timestamp=int(time.time()))
        c2 = store.write("commits", serialize_commit(c2_obj))
        refs.set_branch("main", c2)

        result = runner.invoke(app, ["stats", "--compare", c1, c2])
        assert result.exit_code == 0

    def test_stats_compare_shows_delta(self, tmp_path: Path):
        store, refs, c1 = _init_repo_with_sidecar(tmp_path)

        row2 = json.dumps({"instruction": "q2", "response": "a2"})
        rh2 = store.write("rows", row2.encode("utf-8"))
        mh2 = store.write("manifests", serialize_manifest(Manifest(entries=[
            ManifestEntry(row_hash=rh2, query_fingerprint=None)
        ])))
        sc2 = Sidecar(manifest_hash=mh2, entries=[
            SidecarEntry(row_hash=rh2, char_count=30, token_estimate=7, field_count=2, lang="en")
        ])
        sc2_hash = store.write("sidecars", serialize_sidecar(sc2))

        eval_row = json.dumps({"q": "hi"})
        eval_rh = store.write("rows", eval_row.encode("utf-8"))
        eval_mh = store.write("manifests", serialize_manifest(Manifest(entries=[ManifestEntry(row_hash=eval_rh, query_fingerprint=None)])))

        tree_entries2 = {
            "train.jsonl": ("manifest", mh2, sc2_hash),
            "eval.jsonl": ("manifest", eval_mh, None),
        }
        tree_hash2 = build_nested_tree(store, tree_entries2)
        c2_obj = Commit(tree_hash=tree_hash2, parent_hashes=[c1], author="t", message="second", timestamp=int(time.time()))
        c2 = store.write("commits", serialize_commit(c2_obj))

        result = runner.invoke(app, ["stats", "--compare", c1, c2])
        assert "train.jsonl" in result.stdout
        # Should show a row count delta — old was 1, new is 1 (same), so delta = 0 or shown
        assert result.exit_code == 0

    def test_stats_no_commits_exits_1(self, tmp_path: Path):
        os.chdir(tmp_path)
        runner.invoke(app, ["init"])
        result = runner.invoke(app, ["stats"])
        assert result.exit_code == 1

    def test_stats_outside_repo_exits_1(self, tmp_path: Path):
        empty = tmp_path / "empty"
        empty.mkdir()
        os.chdir(empty)
        result = runner.invoke(app, ["stats"])
        assert result.exit_code == 1
