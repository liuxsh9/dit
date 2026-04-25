# tests/test_cli_validate.py
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


def _init_repo_with_rows(
    tmp_path: Path,
    rows_by_file: dict[str, list[str]] | None = None,
    rules_yaml: str | None = None,
) -> tuple[ObjectStore, RefStore, str]:
    """Init a dit repo. If rows_by_file is None, uses two default files."""
    os.chdir(tmp_path)
    runner.invoke(app, ["init"])

    dot = tmp_path / ".datahub"
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)

    if rows_by_file is None:
        rows_by_file = {
            "train.jsonl": [
                json.dumps({"instruction": "hello", "response": "world"}),
                json.dumps({"instruction": "foo", "response": "bar"}),
            ],
            "eval.jsonl": [
                json.dumps({"instruction": "test", "response": "ok"}),
            ],
        }

    # Write rules file alongside repo root (where .datahub lives)
    if rules_yaml is not None:
        (tmp_path / ".ditvalidate.yaml").write_text(rules_yaml)

    tree_entries: dict[str, tuple] = {}
    for filename, rows in rows_by_file.items():
        entries = []
        for r in rows:
            rh = store.write("rows", r.encode("utf-8"))
            entries.append(ManifestEntry(row_hash=rh, query_fingerprint=None))
        manifest = Manifest(entries=entries)
        mh = store.write("manifests", serialize_manifest(manifest))
        tree_entries[filename] = ("manifest", mh, None)

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


class TestValidateCommand:
    def test_no_rules_exits_0(self, tmp_path: Path):
        _init_repo_with_rows(tmp_path)
        result = runner.invoke(app, ["validate"])
        assert result.exit_code == 0

    def test_pass_output_contains_pass(self, tmp_path: Path):
        _init_repo_with_rows(tmp_path)
        result = runner.invoke(app, ["validate"])
        assert "PASS" in result.stdout

    def test_pass_output_shows_checked_rows(self, tmp_path: Path):
        _init_repo_with_rows(tmp_path)
        result = runner.invoke(app, ["validate"])
        assert "3" in result.stdout  # 2 train + 1 eval

    def test_fail_exits_1_on_violation(self, tmp_path: Path):
        _init_repo_with_rows(
            tmp_path,
            rules_yaml="required_fields:\n  - missing_field\n",
        )
        result = runner.invoke(app, ["validate"])
        assert result.exit_code == 1

    def test_fail_output_contains_fail(self, tmp_path: Path):
        _init_repo_with_rows(
            tmp_path,
            rules_yaml="required_fields:\n  - missing_field\n",
        )
        result = runner.invoke(app, ["validate"])
        assert "FAIL" in result.stdout

    def test_fail_output_shows_violation_table(self, tmp_path: Path):
        _init_repo_with_rows(
            tmp_path,
            rows_by_file={
                "train.jsonl": [
                    json.dumps({"instruction": "hi"}),  # missing response
                ],
            },
            rules_yaml="required_fields:\n  - instruction\n  - response\n",
        )
        result = runner.invoke(app, ["validate"])
        assert "train.jsonl" in result.stdout
        assert "required_fields" in result.stdout
        assert "response" in result.stdout

    def test_json_format_pass(self, tmp_path: Path):
        _init_repo_with_rows(tmp_path)
        result = runner.invoke(app, ["validate", "--format", "json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["status"] == "pass"
        assert data["violations"] == []
        assert "checked_rows" in data

    def test_json_format_fail(self, tmp_path: Path):
        _init_repo_with_rows(
            tmp_path,
            rules_yaml="required_fields:\n  - missing_field\n",
        )
        result = runner.invoke(app, ["validate", "--format", "json"])
        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert data["status"] == "fail"
        assert len(data["violations"]) > 0

    def test_json_violation_has_required_keys(self, tmp_path: Path):
        _init_repo_with_rows(
            tmp_path,
            rules_yaml="required_fields:\n  - missing_field\n",
        )
        result = runner.invoke(app, ["validate", "--format", "json"])
        data = json.loads(result.stdout)
        v = data["violations"][0]
        assert "file" in v
        assert "row_index" in v
        assert "row_hash" in v
        assert "rule" in v
        assert "detail" in v

    def test_ref_option_accepts_branch(self, tmp_path: Path):
        _init_repo_with_rows(tmp_path)
        result = runner.invoke(app, ["validate", "--ref", "main"])
        assert result.exit_code == 0

    def test_ref_bad_branch_exits_1(self, tmp_path: Path):
        _init_repo_with_rows(tmp_path)
        result = runner.invoke(app, ["validate", "--ref", "no-such-branch"])
        assert result.exit_code == 1
        assert "not found" in result.stdout.lower() or "not found" in (result.stderr or "").lower()

    def test_forbidden_keyword_violation(self, tmp_path: Path):
        _init_repo_with_rows(
            tmp_path,
            rows_by_file={
                "train.jsonl": [
                    json.dumps({"instruction": "Tell me about OpenAI", "response": "ok"}),
                ],
            },
            rules_yaml='forbidden_keywords:\n  - "OpenAI"\n',
        )
        result = runner.invoke(app, ["validate"])
        assert result.exit_code == 1
        assert "forbidden_keywords" in result.stdout
