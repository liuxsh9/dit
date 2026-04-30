# tests/test_cli_checkout_force.py
"""Tests for checkout --force and switch --force flags."""
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


def _make_dirty(tmp_path: Path):
    """Modify data.jsonl to create uncommitted changes."""
    (tmp_path / "data.jsonl").write_text(
        json.dumps({"messages": [{"role": "user", "content": "dirty"}]}) + "\n"
    )


def _stage_file(tmp_path: Path, name: str = "staged.jsonl"):
    """Create and stage a new file."""
    (tmp_path / name).write_text(
        json.dumps({"messages": [{"role": "user", "content": "staged"}]}) + "\n"
    )
    runner.invoke(app, ["add", name], catch_exceptions=False)


class TestCheckoutForce:
    def test_force_switches_with_uncommitted_changes(self, tmp_path):
        _init_and_commit(tmp_path)
        runner.invoke(app, ["branch", "feature"], catch_exceptions=False)
        _make_dirty(tmp_path)

        result = runner.invoke(app, ["checkout", "--force", "feature"], catch_exceptions=False)

        assert result.exit_code == 0
        assert (tmp_path / ".dit" / "HEAD").read_text().strip() == "ref:feature"

    def test_short_flag_f_works(self, tmp_path):
        _init_and_commit(tmp_path)
        runner.invoke(app, ["branch", "feature"], catch_exceptions=False)
        _make_dirty(tmp_path)

        result = runner.invoke(app, ["checkout", "-f", "feature"], catch_exceptions=False)

        assert result.exit_code == 0
        assert (tmp_path / ".dit" / "HEAD").read_text().strip() == "ref:feature"

    def test_force_clears_index_and_switches(self, tmp_path):
        _init_and_commit(tmp_path)
        runner.invoke(app, ["branch", "feature"], catch_exceptions=False)
        _stage_file(tmp_path)

        result = runner.invoke(app, ["checkout", "--force", "feature"], catch_exceptions=False)

        assert result.exit_code == 0
        assert (tmp_path / ".dit" / "HEAD").read_text().strip() == "ref:feature"
        # Index should be cleared after force checkout (no staged changes section)
        status_result = runner.invoke(app, ["status"], catch_exceptions=False)
        assert "Staged changes" not in status_result.output

    def test_without_force_refuses_uncommitted(self, tmp_path):
        """Existing behavior preserved: checkout without --force refuses with dirty state."""
        _init_and_commit(tmp_path)
        runner.invoke(app, ["branch", "feature"], catch_exceptions=False)
        _make_dirty(tmp_path)

        result = runner.invoke(app, ["checkout", "feature"])

        assert result.exit_code != 0
        assert "uncommitted" in result.output.lower()

    def test_error_message_mentions_force(self, tmp_path):
        _init_and_commit(tmp_path)
        runner.invoke(app, ["branch", "feature"], catch_exceptions=False)
        _make_dirty(tmp_path)

        result = runner.invoke(app, ["checkout", "feature"])

        assert result.exit_code != 0
        assert "--force" in result.output

    def test_staged_error_message_mentions_force(self, tmp_path):
        _init_and_commit(tmp_path)
        runner.invoke(app, ["branch", "feature"], catch_exceptions=False)
        _stage_file(tmp_path)

        result = runner.invoke(app, ["checkout", "feature"])

        assert result.exit_code != 0
        assert "--force" in result.output
        assert "staging area" in result.output.lower()

    def test_force_create_branch_with_dirty_state(self, tmp_path):
        """checkout --force -b newbranch creates and switches even with dirty state.

        Note: -b returns early before the force guards, so -b always works
        regardless of dirty state. --force is silently accepted but not needed.
        """
        _init_and_commit(tmp_path)
        _make_dirty(tmp_path)

        result = runner.invoke(
            app, ["checkout", "--force", "-b", "newbranch"], catch_exceptions=False
        )

        assert result.exit_code == 0
        assert (tmp_path / ".dit" / "HEAD").read_text().strip() == "ref:newbranch"

    def test_force_checkout_working_dir_matches_target(self, tmp_path):
        """After force checkout, working directory content matches target branch."""
        _init_and_commit(tmp_path)
        # Create feature branch with different content
        runner.invoke(app, ["checkout", "-b", "feature"], catch_exceptions=False)
        (tmp_path / "data.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "feature content"}]}) + "\n"
        )
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "feature change"], catch_exceptions=False)

        # Go back to main, dirty the working dir, then force checkout feature
        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)
        _make_dirty(tmp_path)

        runner.invoke(app, ["checkout", "--force", "feature"], catch_exceptions=False)

        content = (tmp_path / "data.jsonl").read_text()
        assert "feature content" in content
        assert "dirty" not in content


class TestSwitchForce:
    def test_force_switches_with_uncommitted_changes(self, tmp_path):
        _init_and_commit(tmp_path)
        runner.invoke(app, ["branch", "feature"], catch_exceptions=False)
        _make_dirty(tmp_path)

        result = runner.invoke(app, ["switch", "--force", "feature"], catch_exceptions=False)

        assert result.exit_code == 0
        assert (tmp_path / ".dit" / "HEAD").read_text().strip() == "ref:feature"

    def test_short_flag_f_works(self, tmp_path):
        _init_and_commit(tmp_path)
        runner.invoke(app, ["branch", "feature"], catch_exceptions=False)
        _make_dirty(tmp_path)

        result = runner.invoke(app, ["switch", "-f", "feature"], catch_exceptions=False)

        assert result.exit_code == 0
        assert (tmp_path / ".dit" / "HEAD").read_text().strip() == "ref:feature"

    def test_force_clears_index_and_switches(self, tmp_path):
        _init_and_commit(tmp_path)
        runner.invoke(app, ["branch", "feature"], catch_exceptions=False)
        _stage_file(tmp_path)

        result = runner.invoke(app, ["switch", "--force", "feature"], catch_exceptions=False)

        assert result.exit_code == 0
        assert (tmp_path / ".dit" / "HEAD").read_text().strip() == "ref:feature"

    def test_without_force_refuses_uncommitted(self, tmp_path):
        _init_and_commit(tmp_path)
        runner.invoke(app, ["branch", "feature"], catch_exceptions=False)
        _make_dirty(tmp_path)

        result = runner.invoke(app, ["switch", "feature"])

        assert result.exit_code != 0
        assert "uncommitted" in result.output.lower()

    def test_error_message_mentions_force(self, tmp_path):
        _init_and_commit(tmp_path)
        runner.invoke(app, ["branch", "feature"], catch_exceptions=False)
        _make_dirty(tmp_path)

        result = runner.invoke(app, ["switch", "feature"])

        assert result.exit_code != 0
        assert "--force" in result.output

    def test_force_switch_working_dir_matches_target(self, tmp_path):
        """After force switch, working directory content matches target branch."""
        _init_and_commit(tmp_path)
        runner.invoke(app, ["checkout", "-b", "feature"], catch_exceptions=False)
        (tmp_path / "data.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "feature content"}]}) + "\n"
        )
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "feature change"], catch_exceptions=False)

        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)
        _make_dirty(tmp_path)

        runner.invoke(app, ["switch", "--force", "feature"], catch_exceptions=False)

        content = (tmp_path / "data.jsonl").read_text()
        assert "feature content" in content
        assert "dirty" not in content


class TestMonkeyRapidSwitching:
    """Monkey test: rapidly force-switch between branches with different content."""

    def test_rapid_force_checkout_preserves_integrity(self, tmp_path):
        """Create 3 branches with distinct content, force-switch 12 times,
        verify correct content and HEAD after every switch."""
        _init_and_commit(tmp_path)

        # Branch "alpha" with unique content
        runner.invoke(app, ["checkout", "-b", "alpha"], catch_exceptions=False)
        (tmp_path / "data.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "alpha data"}]}) + "\n"
        )
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "alpha commit"], catch_exceptions=False)

        # Branch "beta" from main with different content
        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)
        runner.invoke(app, ["checkout", "-b", "beta"], catch_exceptions=False)
        (tmp_path / "data.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "beta data"}]}) + "\n"
        )
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "beta commit"], catch_exceptions=False)

        # Map branch name -> expected content substring
        expected = {
            "main": "hello",
            "alpha": "alpha data",
            "beta": "beta data",
        }

        # Rapid switching sequence: 12 switches alternating commands
        sequence = [
            ("checkout", "alpha"),
            ("switch", "beta"),
            ("checkout", "main"),
            ("switch", "alpha"),
            ("checkout", "beta"),
            ("switch", "main"),
            ("checkout", "alpha"),
            ("checkout", "main"),
            ("switch", "beta"),
            ("switch", "alpha"),
            ("checkout", "beta"),
            ("switch", "main"),
        ]

        for i, (cmd, branch) in enumerate(sequence):
            # Dirty the working dir before each switch to exercise --force
            (tmp_path / "data.jsonl").write_text(
                json.dumps({"messages": [{"role": "user", "content": f"noise-{i}"}]}) + "\n"
            )

            result = runner.invoke(
                app, [cmd, "--force", branch], catch_exceptions=False
            )

            assert result.exit_code == 0, (
                f"Switch {i} ({cmd} {branch}) failed: {result.output}"
            )

            # Verify HEAD
            head = (tmp_path / ".dit" / "HEAD").read_text().strip()
            assert head == f"ref:{branch}", (
                f"Switch {i}: HEAD is {head}, expected ref:{branch}"
            )

            # Verify file content matches target branch
            content = (tmp_path / "data.jsonl").read_text()
            assert expected[branch] in content, (
                f"Switch {i} ({cmd} -> {branch}): expected '{expected[branch]}' "
                f"in content but got: {content[:120]}"
            )

    def test_rapid_force_switch_with_staged_entries(self, tmp_path):
        """Force-switch with staged entries each time -- index must be cleared."""
        _init_and_commit(tmp_path)

        runner.invoke(app, ["checkout", "-b", "feature"], catch_exceptions=False)
        (tmp_path / "data.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "feature data"}]}) + "\n"
        )
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "feature commit"], catch_exceptions=False)

        branches = ["main", "feature"]
        for i in range(10):
            target = branches[i % 2]
            # Stage a throwaway file before each switch
            throwaway = tmp_path / "throwaway.jsonl"
            throwaway.write_text(
                json.dumps({"messages": [{"role": "user", "content": f"throw-{i}"}]}) + "\n"
            )
            runner.invoke(app, ["add", "throwaway.jsonl"], catch_exceptions=False)

            result = runner.invoke(
                app, ["switch", "--force", target], catch_exceptions=False
            )
            assert result.exit_code == 0, (
                f"Iteration {i} switch to {target} failed: {result.output}"
            )
            assert (tmp_path / ".dit" / "HEAD").read_text().strip() == f"ref:{target}"
