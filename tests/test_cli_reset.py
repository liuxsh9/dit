# tests/test_cli_reset.py
import json
import os
from pathlib import Path

from typer.testing import CliRunner

from dit.cli.main import app

runner = CliRunner()


def _init_and_commit(tmp_path: Path):
    os.chdir(tmp_path)
    runner.invoke(app, ["init"], catch_exceptions=False)
    (tmp_path / "data.jsonl").write_text(
        json.dumps({"messages": [{"role": "user", "content": "hello"}]}) + "\n"
    )
    runner.invoke(app, ["add", "."], catch_exceptions=False)
    runner.invoke(app, ["commit", "-m", "initial"], catch_exceptions=False)


class TestResetSoft:
    """dit reset (no --hard) only affects staging area."""

    def test_reset_clears_staging_area(self, tmp_path):
        _init_and_commit(tmp_path)
        # Stage a new file
        (tmp_path / "new.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "new"}]}) + "\n"
        )
        runner.invoke(app, ["add", "new.jsonl"], catch_exceptions=False)
        # Verify it's staged
        result = runner.invoke(app, ["status"], catch_exceptions=False)
        assert "new.jsonl" in result.output

        # Reset (no args) should clear staging
        result = runner.invoke(app, ["reset"], catch_exceptions=False)
        assert result.exit_code == 0

        # Staging area should be empty
        result = runner.invoke(app, ["status"], catch_exceptions=False)
        assert "Staged files" not in result.output

    def test_reset_does_not_affect_working_directory(self, tmp_path):
        _init_and_commit(tmp_path)
        (tmp_path / "new.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "new"}]}) + "\n"
        )
        runner.invoke(app, ["add", "new.jsonl"], catch_exceptions=False)

        runner.invoke(app, ["reset"], catch_exceptions=False)

        # File should still exist in working directory
        assert (tmp_path / "new.jsonl").exists()

    def test_reset_specific_path(self, tmp_path):
        _init_and_commit(tmp_path)
        (tmp_path / "a.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "a"}]}) + "\n"
        )
        (tmp_path / "b.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "b"}]}) + "\n"
        )
        runner.invoke(app, ["add", "a.jsonl", "b.jsonl"], catch_exceptions=False)

        # Reset only a.jsonl
        result = runner.invoke(app, ["reset", "a.jsonl"], catch_exceptions=False)
        assert result.exit_code == 0

        # b.jsonl should still be staged, a.jsonl should not
        result = runner.invoke(app, ["status"], catch_exceptions=False)
        assert "b.jsonl" in result.output
        # a.jsonl should appear as unstaged new file, not staged
        lines = result.output.split("\n")
        staged_section = False
        unstaged_section = False
        a_in_staged = False
        for line in lines:
            if "Staged files" in line:
                staged_section = True
                unstaged_section = False
            elif "Unstaged changes" in line:
                staged_section = False
                unstaged_section = True
            elif staged_section and "a.jsonl" in line:
                a_in_staged = True
        assert not a_in_staged

    def test_reset_multiple_paths(self, tmp_path):
        _init_and_commit(tmp_path)
        (tmp_path / "a.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "a"}]}) + "\n"
        )
        (tmp_path / "b.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "b"}]}) + "\n"
        )
        (tmp_path / "c.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "c"}]}) + "\n"
        )
        runner.invoke(app, ["add", "a.jsonl", "b.jsonl", "c.jsonl"], catch_exceptions=False)

        # Reset a and b
        result = runner.invoke(app, ["reset", "a.jsonl", "b.jsonl"], catch_exceptions=False)
        assert result.exit_code == 0

        result = runner.invoke(app, ["status"], catch_exceptions=False)
        assert "Staged files" in result.output
        assert "c.jsonl" in result.output

    def test_reset_empty_staging_is_noop(self, tmp_path):
        _init_and_commit(tmp_path)
        result = runner.invoke(app, ["reset"], catch_exceptions=False)
        assert result.exit_code == 0


class TestResetHard:
    """dit reset --hard restores working directory to HEAD."""

    def test_hard_reset_restores_modified_file(self, tmp_path):
        _init_and_commit(tmp_path)
        # Modify the committed file
        (tmp_path / "data.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "modified"}]}) + "\n"
        )
        runner.invoke(app, ["add", "data.jsonl"], catch_exceptions=False)

        result = runner.invoke(app, ["reset", "--hard"], catch_exceptions=False)
        assert result.exit_code == 0

        # File should be restored to HEAD content
        content = (tmp_path / "data.jsonl").read_text()
        assert "hello" in content
        assert "modified" not in content

    def test_hard_reset_removes_untracked_jsonl(self, tmp_path):
        _init_and_commit(tmp_path)
        (tmp_path / "extra.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "extra"}]}) + "\n"
        )
        runner.invoke(app, ["add", "extra.jsonl"], catch_exceptions=False)

        result = runner.invoke(app, ["reset", "--hard"], catch_exceptions=False)
        assert result.exit_code == 0

        # extra.jsonl should be removed (not in HEAD)
        assert not (tmp_path / "extra.jsonl").exists()

    def test_hard_reset_clears_staging(self, tmp_path):
        _init_and_commit(tmp_path)
        (tmp_path / "new.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "new"}]}) + "\n"
        )
        runner.invoke(app, ["add", "new.jsonl"], catch_exceptions=False)

        runner.invoke(app, ["reset", "--hard"], catch_exceptions=False)

        result = runner.invoke(app, ["status"], catch_exceptions=False)
        assert "Staged files" not in result.output

    def test_hard_reset_empty_repo(self, tmp_path):
        """In an empty repo (no commits), --hard should clear staging and remove JSONL files."""
        os.chdir(tmp_path)
        runner.invoke(app, ["init"], catch_exceptions=False)
        (tmp_path / "data.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "hello"}]}) + "\n"
        )
        runner.invoke(app, ["add", "data.jsonl"], catch_exceptions=False)

        result = runner.invoke(app, ["reset", "--hard"], catch_exceptions=False)
        assert result.exit_code == 0

        # Staging should be clear
        index_path = tmp_path / ".dit" / "index"
        if index_path.exists():
            assert json.loads(index_path.read_text()) == {}

        # JSONL files should be removed
        assert not (tmp_path / "data.jsonl").exists()

    def test_hard_reset_restores_deleted_file(self, tmp_path):
        _init_and_commit(tmp_path)
        # Delete the committed file
        (tmp_path / "data.jsonl").unlink()

        result = runner.invoke(app, ["reset", "--hard"], catch_exceptions=False)
        assert result.exit_code == 0

        # File should be restored
        assert (tmp_path / "data.jsonl").exists()
        content = (tmp_path / "data.jsonl").read_text()
        assert "hello" in content
