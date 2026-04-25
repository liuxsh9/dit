# tests/test_cli_branch.py
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


class TestBranch:
    def test_list_branches(self, tmp_path):
        _init_and_commit(tmp_path)
        result = runner.invoke(app, ["branch"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "main" in result.output

    def test_list_marks_current(self, tmp_path):
        _init_and_commit(tmp_path)
        result = runner.invoke(app, ["branch"], catch_exceptions=False)
        assert "* main" in result.output

    def test_create_branch(self, tmp_path):
        _init_and_commit(tmp_path)
        result = runner.invoke(app, ["branch", "feature"], catch_exceptions=False)
        assert result.exit_code == 0
        result = runner.invoke(app, ["branch"], catch_exceptions=False)
        assert "feature" in result.output
        assert "* main" in result.output

    def test_create_existing_branch_fails(self, tmp_path):
        _init_and_commit(tmp_path)
        runner.invoke(app, ["branch", "feature"], catch_exceptions=False)
        result = runner.invoke(app, ["branch", "feature"])
        assert result.exit_code != 0

    def test_delete_branch(self, tmp_path):
        _init_and_commit(tmp_path)
        runner.invoke(app, ["branch", "feature"], catch_exceptions=False)
        result = runner.invoke(app, ["branch", "-d", "feature"], catch_exceptions=False)
        assert result.exit_code == 0
        result = runner.invoke(app, ["branch"], catch_exceptions=False)
        assert "feature" not in result.output

    def test_delete_current_branch_fails(self, tmp_path):
        _init_and_commit(tmp_path)
        result = runner.invoke(app, ["branch", "-d", "main"])
        assert result.exit_code != 0

    def test_delete_nonexistent_branch_fails(self, tmp_path):
        _init_and_commit(tmp_path)
        result = runner.invoke(app, ["branch", "-d", "nope"])
        assert result.exit_code != 0


class TestCheckout:
    def test_checkout_existing_branch(self, tmp_path):
        _init_and_commit(tmp_path)
        runner.invoke(app, ["branch", "feature"], catch_exceptions=False)
        result = runner.invoke(app, ["checkout", "feature"], catch_exceptions=False)
        assert result.exit_code == 0
        head = (tmp_path / ".dit" / "HEAD").read_text().strip()
        assert head == "ref:feature"

    def test_checkout_creates_new_branch(self, tmp_path):
        _init_and_commit(tmp_path)
        result = runner.invoke(app, ["checkout", "-b", "feature"], catch_exceptions=False)
        assert result.exit_code == 0
        head = (tmp_path / ".dit" / "HEAD").read_text().strip()
        assert head == "ref:feature"

    def test_checkout_nonexistent_branch_fails(self, tmp_path):
        _init_and_commit(tmp_path)
        result = runner.invoke(app, ["checkout", "nope"])
        assert result.exit_code != 0

    def test_checkout_b_existing_branch_fails(self, tmp_path):
        _init_and_commit(tmp_path)
        runner.invoke(app, ["branch", "feature"], catch_exceptions=False)
        result = runner.invoke(app, ["checkout", "-b", "feature"])
        assert result.exit_code != 0

    def test_checkout_materializes_files(self, tmp_path):
        _init_and_commit(tmp_path)
        runner.invoke(app, ["checkout", "-b", "feature"], catch_exceptions=False)
        (tmp_path / "data.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "new content"}]}) + "\n"
        )
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "change on feature"], catch_exceptions=False)
        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)
        content = (tmp_path / "data.jsonl").read_text()
        assert "hello" in content
        assert "new content" not in content

    def test_checkout_with_uncommitted_changes_fails(self, tmp_path):
        _init_and_commit(tmp_path)
        runner.invoke(app, ["branch", "feature"], catch_exceptions=False)
        (tmp_path / "data.jsonl").write_text('{"messages":[{"role":"user","content":"dirty"}]}\n')
        result = runner.invoke(app, ["checkout", "feature"])
        assert result.exit_code != 0
        assert "uncommitted" in result.output.lower()

    def test_checkout_removes_files_not_in_target(self, tmp_path):
        _init_and_commit(tmp_path)
        runner.invoke(app, ["checkout", "-b", "feature"], catch_exceptions=False)
        (tmp_path / "extra.jsonl").write_text('{"x":1}\n')
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "add extra"], catch_exceptions=False)
        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)
        assert not (tmp_path / "extra.jsonl").exists()


class TestSwitch:
    def test_switch_to_existing_branch(self, tmp_path):
        _init_and_commit(tmp_path)
        runner.invoke(app, ["branch", "feature"], catch_exceptions=False)
        result = runner.invoke(app, ["switch", "feature"], catch_exceptions=False)
        assert result.exit_code == 0
        head = (tmp_path / ".dit" / "HEAD").read_text().strip()
        assert head == "ref:feature"

    def test_switch_nonexistent_fails(self, tmp_path):
        _init_and_commit(tmp_path)
        result = runner.invoke(app, ["switch", "nope"])
        assert result.exit_code != 0
