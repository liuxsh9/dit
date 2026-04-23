import os
from pathlib import Path

from typer.testing import CliRunner

from dit.cli.main import app

runner = CliRunner()


class TestInit:
    def test_init_creates_datahub_dir(self, tmp_path: Path):
        os.chdir(tmp_path)
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0
        assert (tmp_path / ".datahub").is_dir()
        assert (tmp_path / ".datahub" / "HEAD").exists()
        assert (tmp_path / ".datahub" / "refs" / "heads").is_dir()
        assert (tmp_path / ".datahub" / "objects").is_dir()

    def test_init_already_exists(self, tmp_path: Path):
        os.chdir(tmp_path)
        runner.invoke(app, ["init"])
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0
        assert "already" in result.stdout.lower() or "initialized" in result.stdout.lower()


import json


class TestAdd:
    def test_add_single_file(self, tmp_path: Path):
        os.chdir(tmp_path)
        runner.invoke(app, ["init"])
        fp = tmp_path / "coding.jsonl"
        fp.write_text(json.dumps({"messages": [{"role": "user", "content": "hi"}]}) + "\n")
        result = runner.invoke(app, ["add", "coding.jsonl"])
        assert result.exit_code == 0

        idx_path = tmp_path / ".datahub" / "index"
        assert idx_path.exists()
        idx = json.loads(idx_path.read_text())
        assert "coding.jsonl" in idx

    def test_add_dot(self, tmp_path: Path):
        os.chdir(tmp_path)
        runner.invoke(app, ["init"])
        (tmp_path / "a.jsonl").write_text('{"x":1}\n')
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "b.jsonl").write_text('{"y":2}\n')
        result = runner.invoke(app, ["add", "."])
        assert result.exit_code == 0
        idx = json.loads((tmp_path / ".datahub" / "index").read_text())
        assert "a.jsonl" in idx
        assert "sub/b.jsonl" in idx

    def test_add_nonexistent_file(self, tmp_path: Path):
        os.chdir(tmp_path)
        runner.invoke(app, ["init"])
        result = runner.invoke(app, ["add", "nope.jsonl"])
        assert result.exit_code != 0
