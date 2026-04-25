# tests/test_cli_cherry_pick.py
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


class TestCherryPick:
    def test_clean_cherry_pick(self, tmp_path):
        _init_and_commit(tmp_path)
        runner.invoke(app, ["checkout", "-b", "feature"], catch_exceptions=False)
        (tmp_path / "feature.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "feature data"}]}) + "\n"
        )
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "add feature file"], catch_exceptions=False)
        feature_hash = (tmp_path / ".dit" / "refs" / "heads" / "feature").read_text().strip()
        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)
        result = runner.invoke(app, ["cherry-pick", feature_hash], catch_exceptions=False)
        assert result.exit_code == 0
        assert (tmp_path / "feature.jsonl").exists()
        from dit.core.store import ObjectStore
        from dit.core.objects import deserialize_commit
        from dit.core.refs import RefStore
        dot = tmp_path / ".dit"
        store = ObjectStore(dot / "objects")
        refs = RefStore(dot)
        head_hash = refs.resolve_head()
        commit = deserialize_commit(store.read("commits", head_hash))
        assert len(commit.parent_hashes) == 1
        assert "cherry-pick" in commit.message.lower()

    def test_cherry_pick_accepts_abbreviated_hash(self, tmp_path):
        _init_and_commit(tmp_path)
        runner.invoke(app, ["checkout", "-b", "feature"], catch_exceptions=False)
        (tmp_path / "feature.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "feature data"}]}) + "\n"
        )
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "add feature file"], catch_exceptions=False)
        feature_hash = (tmp_path / ".dit" / "refs" / "heads" / "feature").read_text().strip()
        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)

        result = runner.invoke(app, ["cherry-pick", feature_hash[:8]])

        assert result.exit_code == 0
        assert (tmp_path / "feature.jsonl").exists()

    def test_cherry_pick_conflict(self, tmp_path):
        _init_and_commit(tmp_path)
        runner.invoke(app, ["checkout", "-b", "feature"], catch_exceptions=False)
        (tmp_path / "data.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "feature"}]}) + "\n"
        )
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "feature change"], catch_exceptions=False)
        feature_hash = (tmp_path / ".dit" / "refs" / "heads" / "feature").read_text().strip()
        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)
        (tmp_path / "data.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "main"}]}) + "\n"
        )
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "main change"], catch_exceptions=False)
        result = runner.invoke(app, ["cherry-pick", feature_hash])
        assert result.exit_code != 0
        assert (tmp_path / ".dit" / "CHERRY_PICK_HEAD").exists()
        assert not (tmp_path / ".dit" / "MERGE_HEAD").exists()
        assert "main" in (tmp_path / "data.jsonl").read_text()

    def test_cherry_pick_continue(self, tmp_path):
        _init_and_commit(tmp_path)
        runner.invoke(app, ["checkout", "-b", "feature"], catch_exceptions=False)
        (tmp_path / "data.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "feature"}]}) + "\n"
        )
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "feature change"], catch_exceptions=False)
        feature_hash = (tmp_path / ".dit" / "refs" / "heads" / "feature").read_text().strip()
        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)
        (tmp_path / "data.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "main"}]}) + "\n"
        )
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "main change"], catch_exceptions=False)
        runner.invoke(app, ["cherry-pick", feature_hash])
        (tmp_path / "data.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "resolved"}]}) + "\n"
        )
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        result = runner.invoke(app, ["cherry-pick", "--continue"], catch_exceptions=False)
        assert result.exit_code == 0
        assert not (tmp_path / ".dit" / "CHERRY_PICK_HEAD").exists()
        from dit.core.store import ObjectStore
        from dit.core.objects import deserialize_commit
        from dit.core.refs import RefStore
        dot = tmp_path / ".dit"
        store = ObjectStore(dot / "objects")
        refs = RefStore(dot)
        head_hash = refs.resolve_head()
        commit = deserialize_commit(store.read("commits", head_hash))
        assert len(commit.parent_hashes) == 1

    def test_cherry_pick_abort(self, tmp_path):
        _init_and_commit(tmp_path)
        runner.invoke(app, ["checkout", "-b", "feature"], catch_exceptions=False)
        (tmp_path / "data.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "feature"}]}) + "\n"
        )
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "feature change"], catch_exceptions=False)
        feature_hash = (tmp_path / ".dit" / "refs" / "heads" / "feature").read_text().strip()
        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)
        (tmp_path / "data.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "main"}]}) + "\n"
        )
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "main change"], catch_exceptions=False)
        runner.invoke(app, ["cherry-pick", feature_hash])
        result = runner.invoke(app, ["cherry-pick", "--abort"], catch_exceptions=False)
        assert result.exit_code == 0
        assert not (tmp_path / ".dit" / "CHERRY_PICK_HEAD").exists()
        content = (tmp_path / "data.jsonl").read_text()
        assert "main" in content

    def test_cherry_pick_invalid_hash_fails(self, tmp_path):
        _init_and_commit(tmp_path)
        result = runner.invoke(app, ["cherry-pick", "0" * 64])
        assert result.exit_code != 0

    def test_merge_and_cherry_pick_mutually_exclusive(self, tmp_path):
        _init_and_commit(tmp_path)
        (tmp_path / ".dit" / "MERGE_HEAD").write_text("a" * 64 + "\n")
        result = runner.invoke(app, ["cherry-pick", "b" * 64])
        assert result.exit_code != 0
        assert "merge" in result.output.lower()
        (tmp_path / ".dit" / "MERGE_HEAD").unlink()
