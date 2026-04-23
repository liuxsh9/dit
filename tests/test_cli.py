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


class TestCommit:
    def _setup_staged(self, tmp_path: Path):
        os.chdir(tmp_path)
        runner.invoke(app, ["init"])
        fp = tmp_path / "data.jsonl"
        fp.write_text('{"messages":[{"role":"user","content":"test"}]}\n')
        runner.invoke(app, ["add", "data.jsonl"])

    def test_commit_creates_commit(self, tmp_path: Path):
        self._setup_staged(tmp_path)
        result = runner.invoke(app, ["commit", "-m", "initial"])
        assert result.exit_code == 0
        assert "initial" in result.stdout

        head_ref = (tmp_path / ".datahub" / "refs" / "heads" / "main").read_text().strip()
        assert len(head_ref) == 64

    def test_commit_clears_index(self, tmp_path: Path):
        self._setup_staged(tmp_path)
        runner.invoke(app, ["commit", "-m", "first"])
        idx = json.loads((tmp_path / ".datahub" / "index").read_text())
        assert idx == {}

    def test_commit_nothing_staged(self, tmp_path: Path):
        os.chdir(tmp_path)
        runner.invoke(app, ["init"])
        result = runner.invoke(app, ["commit", "-m", "empty"])
        assert result.exit_code != 0

    def test_second_commit_has_parent(self, tmp_path: Path):
        self._setup_staged(tmp_path)
        runner.invoke(app, ["commit", "-m", "first"])
        first_hash = (tmp_path / ".datahub" / "refs" / "heads" / "main").read_text().strip()

        (tmp_path / "data.jsonl").write_text('{"messages":[{"role":"user","content":"updated"}]}\n')
        runner.invoke(app, ["add", "data.jsonl"])
        runner.invoke(app, ["commit", "-m", "second"])
        second_hash = (tmp_path / ".datahub" / "refs" / "heads" / "main").read_text().strip()

        assert first_hash != second_hash
        from dit.core.store import ObjectStore
        from dit.core.objects import deserialize_commit
        store = ObjectStore(tmp_path / ".datahub" / "objects")
        commit_data = store.read("commits", second_hash)
        commit_obj = deserialize_commit(commit_data)
        assert commit_obj.parent_hashes == [first_hash]


class TestLog:
    def test_log_shows_commits(self, tmp_path: Path):
        os.chdir(tmp_path)
        runner.invoke(app, ["init"])
        (tmp_path / "a.jsonl").write_text('{"x":1}\n')
        runner.invoke(app, ["add", "."])
        runner.invoke(app, ["commit", "-m", "first commit"])
        (tmp_path / "a.jsonl").write_text('{"x":1}\n{"y":2}\n')
        runner.invoke(app, ["add", "."])
        runner.invoke(app, ["commit", "-m", "second commit"])

        result = runner.invoke(app, ["log"])
        assert result.exit_code == 0
        assert "second commit" in result.stdout
        assert "first commit" in result.stdout
        assert result.stdout.index("second") < result.stdout.index("first")

    def test_log_empty_repo(self, tmp_path: Path):
        os.chdir(tmp_path)
        runner.invoke(app, ["init"])
        result = runner.invoke(app, ["log"])
        assert result.exit_code == 0
        assert "no commits" in result.stdout.lower()
