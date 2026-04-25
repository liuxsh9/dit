# tests/test_cli_tag.py
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


class TestTag:
    def test_create_tag(self, tmp_path):
        _init_and_commit(tmp_path)
        result = runner.invoke(app, ["tag", "v1.0"], catch_exceptions=False)
        assert result.exit_code == 0
        assert (tmp_path / ".dit" / "refs" / "tags" / "v1.0").exists()

    def test_list_tags(self, tmp_path):
        _init_and_commit(tmp_path)
        runner.invoke(app, ["tag", "v1.0"], catch_exceptions=False)
        runner.invoke(app, ["tag", "v2.0"], catch_exceptions=False)
        result = runner.invoke(app, ["tag"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "v1.0" in result.output
        assert "v2.0" in result.output

    def test_list_tags_empty(self, tmp_path):
        _init_and_commit(tmp_path)
        result = runner.invoke(app, ["tag"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "no tags" in result.output.lower()

    def test_delete_tag(self, tmp_path):
        _init_and_commit(tmp_path)
        runner.invoke(app, ["tag", "v1.0"], catch_exceptions=False)
        result = runner.invoke(app, ["tag", "-d", "v1.0"], catch_exceptions=False)
        assert result.exit_code == 0
        assert not (tmp_path / ".dit" / "refs" / "tags" / "v1.0").exists()

    def test_create_existing_tag_fails(self, tmp_path):
        _init_and_commit(tmp_path)
        runner.invoke(app, ["tag", "v1.0"], catch_exceptions=False)
        result = runner.invoke(app, ["tag", "v1.0"])
        assert result.exit_code != 0

    def test_delete_nonexistent_tag_fails(self, tmp_path):
        _init_and_commit(tmp_path)
        result = runner.invoke(app, ["tag", "-d", "nope"])
        assert result.exit_code != 0

    def test_tag_before_any_commits_fails(self, tmp_path):
        os.chdir(tmp_path)
        runner.invoke(app, ["init"], catch_exceptions=False)
        result = runner.invoke(app, ["tag", "v1.0"])
        assert result.exit_code != 0
