# tests/test_robustness_status_reset.py
"""Robustness tests for status, diff, reset, and fsck edge cases.

Focus: scenarios where incorrect behavior could mislead users or cause data loss.
"""
import json
import os
from pathlib import Path

from typer.testing import CliRunner

from dit.cli.main import app

runner = CliRunner()

ROW_HELLO = json.dumps({"messages": [{"role": "user", "content": "hello"}]})
ROW_WORLD = json.dumps({"messages": [{"role": "user", "content": "world"}]})
ROW_NEW = json.dumps({"messages": [{"role": "user", "content": "new"}]})
ROW_MODIFIED = json.dumps(
    {"messages": [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]}
)
ROW_REFRESHED = json.dumps(
    {"messages": [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hey"}]}
)


def _init(tmp_path: Path):
    os.chdir(tmp_path)
    runner.invoke(app, ["init"], catch_exceptions=False)


def _init_and_commit(tmp_path: Path, files: dict[str, str] | None = None):
    os.chdir(tmp_path)
    runner.invoke(app, ["init"], catch_exceptions=False)
    if files is None:
        files = {"data.jsonl": ROW_HELLO + "\n"}
    for name, content in files.items():
        (tmp_path / name).write_text(content)
    runner.invoke(app, ["add", "."], catch_exceptions=False)
    runner.invoke(app, ["commit", "-m", "initial"], catch_exceptions=False)


class TestStatusEdgeCases:
    """Status command under unusual working-directory states."""

    def test_status_empty_repo_no_commits(self, tmp_path):
        """dit status with no commits should show clean or 'no commits yet'."""
        _init(tmp_path)
        result = runner.invoke(app, ["status"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "clean" in result.output.lower() or "nothing to commit" in result.output.lower()

    def test_status_shows_staged_files(self, tmp_path):
        """After add but before commit, status should show staged files."""
        _init(tmp_path)
        (tmp_path / "data.jsonl").write_text(ROW_HELLO + "\n")
        runner.invoke(app, ["add", "data.jsonl"], catch_exceptions=False)
        result = runner.invoke(app, ["status"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "Staged files" in result.output
        assert "data.jsonl" in result.output

    def test_status_modified_file_shows_unstaged(self, tmp_path):
        """Modify a committed file without staging -- should show as modified."""
        _init_and_commit(tmp_path)
        (tmp_path / "data.jsonl").write_text(ROW_MODIFIED + "\n")
        result = runner.invoke(app, ["status"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "modified" in result.output.lower()
        assert "data.jsonl" in result.output

    def test_status_deleted_file_shows_deleted(self, tmp_path):
        """Delete a committed file -- should show as deleted."""
        _init_and_commit(tmp_path)
        (tmp_path / "data.jsonl").unlink()
        result = runner.invoke(app, ["status"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "deleted" in result.output.lower()
        assert "data.jsonl" in result.output

    def test_status_new_untracked_file(self, tmp_path):
        """Add a .jsonl file without dit add -- should show as new file."""
        _init_and_commit(tmp_path)
        (tmp_path / "extra.jsonl").write_text(ROW_NEW + "\n")
        result = runner.invoke(app, ["status"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "new file" in result.output.lower()
        assert "extra.jsonl" in result.output

    def test_status_after_reset_shows_modified(self, tmp_path):
        """Reset a staged file, status should show it as modified (unstaged)."""
        _init_and_commit(tmp_path)
        (tmp_path / "data.jsonl").write_text(ROW_MODIFIED + "\n")
        runner.invoke(app, ["add", "data.jsonl"], catch_exceptions=False)
        runner.invoke(app, ["reset", "data.jsonl"], catch_exceptions=False)
        result = runner.invoke(app, ["status"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "Staged files" not in result.output
        assert "modified" in result.output.lower()
        assert "data.jsonl" in result.output


class TestDiffEdgeCases:
    """Diff command under various change scenarios."""

    def test_diff_no_changes(self, tmp_path):
        """Diff with no changes should show nothing."""
        _init_and_commit(tmp_path)
        result = runner.invoke(app, ["diff"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "No changes" in result.output

    def test_diff_added_rows(self, tmp_path):
        """Add rows to a file, diff should show them."""
        _init_and_commit(tmp_path)
        (tmp_path / "data.jsonl").write_text(ROW_HELLO + "\n" + ROW_WORLD + "\n")
        result = runner.invoke(app, ["diff"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "data.jsonl" in result.output
        assert "+1" in result.output or "2 rows" in result.output

    def test_diff_removed_rows(self, tmp_path):
        """Remove rows from a file, diff should show them."""
        _init_and_commit(
            tmp_path,
            {"data.jsonl": ROW_HELLO + "\n" + ROW_WORLD + "\n"},
        )
        (tmp_path / "data.jsonl").write_text(ROW_HELLO + "\n")
        result = runner.invoke(app, ["diff"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "data.jsonl" in result.output
        assert "-1" in result.output or "1 rows" in result.output

    def test_diff_refreshed_rows(self, tmp_path):
        """Change assistant response for same user query -- should show as refreshed."""
        _init_and_commit(tmp_path, {"data.jsonl": ROW_MODIFIED + "\n"})
        (tmp_path / "data.jsonl").write_text(ROW_REFRESHED + "\n")
        result = runner.invoke(app, ["diff"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "data.jsonl" in result.output
        assert "refreshed" in result.output.lower()

    def test_diff_new_file(self, tmp_path):
        """Diff should show a new file that was not in HEAD."""
        _init_and_commit(tmp_path)
        (tmp_path / "extra.jsonl").write_text(ROW_NEW + "\n")
        result = runner.invoke(app, ["diff"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "extra.jsonl" in result.output
        assert "new file" in result.output.lower()

    def test_diff_deleted_file(self, tmp_path):
        """Diff should show a deleted file."""
        _init_and_commit(tmp_path)
        (tmp_path / "data.jsonl").unlink()
        result = runner.invoke(app, ["diff"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "data.jsonl" in result.output
        assert "deleted" in result.output.lower()

    def test_diff_after_partial_staging(self, tmp_path):
        """Modify file, add it, modify again -- diff should show the second modification."""
        _init_and_commit(tmp_path)
        # First modification + stage
        (tmp_path / "data.jsonl").write_text(ROW_MODIFIED + "\n")
        runner.invoke(app, ["add", "data.jsonl"], catch_exceptions=False)
        # Second modification (not staged)
        (tmp_path / "data.jsonl").write_text(ROW_MODIFIED + "\n" + ROW_WORLD + "\n")
        result = runner.invoke(app, ["diff"], catch_exceptions=False)
        assert result.exit_code == 0
        # diff compares working dir to HEAD, so should show all changes
        assert "data.jsonl" in result.output


class TestResetRobustness:
    """Reset command edge cases that could cause data loss."""

    def test_soft_reset_then_readd(self, tmp_path):
        """Reset a staged file, modify it, re-add -- should stage the new version."""
        _init_and_commit(tmp_path)
        (tmp_path / "data.jsonl").write_text(ROW_MODIFIED + "\n")
        runner.invoke(app, ["add", "data.jsonl"], catch_exceptions=False)
        runner.invoke(app, ["reset", "data.jsonl"], catch_exceptions=False)
        # Modify again
        (tmp_path / "data.jsonl").write_text(ROW_WORLD + "\n")
        runner.invoke(app, ["add", "data.jsonl"], catch_exceptions=False)
        result = runner.invoke(app, ["status"], catch_exceptions=False)
        assert "Staged files" in result.output
        assert "data.jsonl" in result.output

    def test_hard_reset_restores_multiple_files(self, tmp_path):
        """Modify 3 files, reset --hard, all should be restored."""
        files = {
            "a.jsonl": ROW_HELLO + "\n",
            "b.jsonl": ROW_WORLD + "\n",
            "c.jsonl": ROW_NEW + "\n",
        }
        _init_and_commit(tmp_path, files)
        # Modify all three
        for name in files:
            (tmp_path / name).write_text(ROW_MODIFIED + "\n")
        result = runner.invoke(app, ["reset", "--hard"], catch_exceptions=False)
        assert result.exit_code == 0
        # All files should be restored to original content
        assert "hello" in (tmp_path / "a.jsonl").read_text()
        assert "world" in (tmp_path / "b.jsonl").read_text()
        assert "new" in (tmp_path / "c.jsonl").read_text()

    def test_hard_reset_removes_new_staged_file(self, tmp_path):
        """Add a new file, stage it, reset --hard -- new file should be removed."""
        _init_and_commit(tmp_path)
        (tmp_path / "extra.jsonl").write_text(ROW_NEW + "\n")
        runner.invoke(app, ["add", "extra.jsonl"], catch_exceptions=False)
        result = runner.invoke(app, ["reset", "--hard"], catch_exceptions=False)
        assert result.exit_code == 0
        assert not (tmp_path / "extra.jsonl").exists()
        # Original file should still be there
        assert (tmp_path / "data.jsonl").exists()
        assert "hello" in (tmp_path / "data.jsonl").read_text()


class TestFsckRobustness:
    """Fsck integrity checks on healthy and corrupted repos."""

    def test_fsck_healthy_repo(self, tmp_path):
        """Fsck on a healthy repo should pass with no errors."""
        _init_and_commit(tmp_path)
        # fsck uses raise typer.Exit(), so don't use catch_exceptions=False
        result = runner.invoke(app, ["fsck"])
        assert result.exit_code == 0
        assert "No issues found" in result.output

    def test_fsck_detects_corruption(self, tmp_path):
        """Manually corrupt a row object file, fsck should detect it.

        NOTE: corrupting a commit object causes an unhandled ZstdError in
        fsck's _verify_graph -> store.read, which is arguably a dit bug --
        the graph walker should handle decompression failures gracefully
        like _verify_hashes does. We corrupt a row object here to test the
        detection path that does work.
        """
        _init_and_commit(tmp_path)
        rows_dir = tmp_path / ".dit" / "objects" / "rows"
        corrupted = False
        for shard1 in sorted(rows_dir.iterdir()):
            if not shard1.is_dir():
                continue
            for shard2 in sorted(shard1.iterdir()):
                if not shard2.is_dir():
                    continue
                for obj_file in sorted(shard2.iterdir()):
                    if obj_file.is_file():
                        obj_file.write_bytes(b"corrupted data")
                        corrupted = True
                        break
                if corrupted:
                    break
            if corrupted:
                break
        assert corrupted, "Could not find a row object file to corrupt"
        result = runner.invoke(app, ["fsck"])
        assert result.exit_code != 0
        output_lower = result.output.lower()
        assert "error" in output_lower or "corrupt" in output_lower

    def test_fsck_detects_corrupt_commit(self, tmp_path):
        """Corrupt a commit object file, fsck should report the error without crashing.

        This verifies that _verify_graph handles decompression errors in commit
        objects gracefully, rather than letting ZstdError propagate.
        """
        _init_and_commit(tmp_path)
        commits_dir = tmp_path / ".dit" / "objects" / "commits"
        corrupted = False
        for shard1 in sorted(commits_dir.iterdir()):
            if not shard1.is_dir():
                continue
            for shard2 in sorted(shard1.iterdir()):
                if not shard2.is_dir():
                    continue
                for obj_file in sorted(shard2.iterdir()):
                    if obj_file.is_file():
                        obj_file.write_bytes(b"corrupted data")
                        corrupted = True
                        break
                if corrupted:
                    break
            if corrupted:
                break
        assert corrupted, "Could not find a commit object file to corrupt"
        result = runner.invoke(app, ["fsck"])
        assert result.exit_code != 0
        output_lower = result.output.lower()
        assert "error" in output_lower or "corrupt" in output_lower
