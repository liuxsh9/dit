"""Tests for status/diff/add sparse awareness."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dit.cli.main import app
from dit.core.sparse import save_sparse_paths

runner = CliRunner()


def _init_repo_with_files(tmp_path: Path, monkeypatch) -> Path:
    """Create a repo with bug-fix/train.jsonl and general/eval.jsonl, committed."""
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    runner.invoke(app, ["init"], catch_exceptions=False)

    (repo / "bug-fix").mkdir()
    (repo / "bug-fix" / "train.jsonl").write_text(
        json.dumps({"messages": [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}]}) + "\n"
    )
    (repo / "general").mkdir()
    (repo / "general" / "eval.jsonl").write_text(
        json.dumps({"messages": [{"role": "user", "content": "q2"}, {"role": "assistant", "content": "a2"}]}) + "\n"
    )

    runner.invoke(app, ["add", "bug-fix/train.jsonl"], catch_exceptions=False)
    runner.invoke(app, ["add", "general/eval.jsonl"], catch_exceptions=False)
    runner.invoke(app, ["commit", "-m", "init"], catch_exceptions=False)
    return repo


class TestStatusSparse:
    def test_status_no_false_deleted_in_sparse(self, tmp_path: Path, monkeypatch) -> None:
        """In sparse mode, files not in sparse set should NOT appear as deleted."""
        repo = _init_repo_with_files(tmp_path, monkeypatch)
        dot = repo / ".dit"
        save_sparse_paths(dot, {"bug-fix/train.jsonl"})
        # Remove general/eval.jsonl to simulate sparse (not fetched)
        (repo / "general" / "eval.jsonl").unlink()

        result = runner.invoke(app, ["status"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        assert "deleted" not in result.output
        assert "general/eval.jsonl" not in result.output

    def test_status_shows_sparse_indicator(self, tmp_path: Path, monkeypatch) -> None:
        repo = _init_repo_with_files(tmp_path, monkeypatch)
        dot = repo / ".dit"
        save_sparse_paths(dot, {"bug-fix/train.jsonl"})
        (repo / "general" / "eval.jsonl").unlink()

        result = runner.invoke(app, ["status"], catch_exceptions=False)
        assert "sparse" in result.output.lower()

    def test_status_still_shows_real_delete_in_sparse(self, tmp_path: Path, monkeypatch) -> None:
        """If a file IS in sparse set but was deleted, it should show as deleted."""
        repo = _init_repo_with_files(tmp_path, monkeypatch)
        dot = repo / ".dit"
        save_sparse_paths(dot, {"bug-fix/train.jsonl", "general/eval.jsonl"})
        # Delete a file that IS in the sparse set
        (repo / "general" / "eval.jsonl").unlink()

        result = runner.invoke(app, ["status"], catch_exceptions=False)
        assert "general/eval.jsonl" in result.output

    def test_status_non_sparse_unchanged(self, tmp_path: Path, monkeypatch) -> None:
        """Without sparse-checkout file, status works as before."""
        repo = _init_repo_with_files(tmp_path, monkeypatch)
        (repo / "general" / "eval.jsonl").unlink()

        result = runner.invoke(app, ["status"], catch_exceptions=False)
        assert "general/eval.jsonl" in result.output
        assert "sparse" not in result.output.lower()


class TestDiffSparse:
    def test_diff_skips_unfetched_files(self, tmp_path: Path, monkeypatch) -> None:
        repo = _init_repo_with_files(tmp_path, monkeypatch)
        dot = repo / ".dit"
        save_sparse_paths(dot, {"bug-fix/train.jsonl"})
        (repo / "general" / "eval.jsonl").unlink()

        result = runner.invoke(app, ["diff"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        assert "general/eval.jsonl" not in result.output

    def test_diff_non_sparse_unchanged(self, tmp_path: Path, monkeypatch) -> None:
        repo = _init_repo_with_files(tmp_path, monkeypatch)
        (repo / "general" / "eval.jsonl").unlink()

        result = runner.invoke(app, ["diff"], catch_exceptions=False)
        assert "general/eval.jsonl" in result.output


class TestAddSparse:
    def test_add_unfetched_file_gives_hint(self, tmp_path: Path, monkeypatch) -> None:
        """Trying to add a path that doesn't exist in a sparse repo should hint about sparse-checkout."""
        repo = _init_repo_with_files(tmp_path, monkeypatch)
        dot = repo / ".dit"
        save_sparse_paths(dot, {"bug-fix/train.jsonl"})
        (repo / "general" / "eval.jsonl").unlink()

        result = runner.invoke(app, ["add", "general/eval.jsonl"])
        assert result.exit_code != 0
        assert "sparse-checkout" in result.output.lower() or "not checked out" in result.output.lower()
