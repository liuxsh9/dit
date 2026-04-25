# tests/test_cli_merge.py
import json
import os
from pathlib import Path

from typer.testing import CliRunner

from dit.cli.main import app

runner = CliRunner()


def _init_and_commit(tmp_path: Path, filename: str = "data.jsonl", content: str | None = None):
    os.chdir(tmp_path)
    runner.invoke(app, ["init"], catch_exceptions=False)
    if content is None:
        content = json.dumps({"messages": [{"role": "user", "content": "hello"}]}) + "\n"
    (tmp_path / filename).write_text(content)
    runner.invoke(app, ["add", "."], catch_exceptions=False)
    runner.invoke(app, ["commit", "-m", "initial"], catch_exceptions=False)


class TestMergeFastForward:
    def test_fast_forward(self, tmp_path):
        _init_and_commit(tmp_path)
        runner.invoke(app, ["checkout", "-b", "feature"], catch_exceptions=False)
        (tmp_path / "data.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "updated"}]}) + "\n"
        )
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "feature change"], catch_exceptions=False)
        feature_hash = (tmp_path / ".dit" / "refs" / "heads" / "feature").read_text().strip()
        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)
        result = runner.invoke(app, ["merge", "feature"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "fast-forward" in result.output.lower()
        main_hash = (tmp_path / ".dit" / "refs" / "heads" / "main").read_text().strip()
        assert main_hash == feature_hash

    def test_already_up_to_date(self, tmp_path):
        _init_and_commit(tmp_path)
        runner.invoke(app, ["branch", "feature"], catch_exceptions=False)
        result = runner.invoke(app, ["merge", "feature"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "up to date" in result.output.lower()


class TestMergeThreeWay:
    def test_clean_three_way_merge(self, tmp_path):
        _init_and_commit(tmp_path)
        runner.invoke(app, ["checkout", "-b", "feature"], catch_exceptions=False)
        (tmp_path / "feature.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "feature data"}]}) + "\n"
        )
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "add feature file"], catch_exceptions=False)
        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)
        (tmp_path / "main.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "main data"}]}) + "\n"
        )
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "add main file"], catch_exceptions=False)
        result = runner.invoke(app, ["merge", "feature"], catch_exceptions=False)
        assert result.exit_code == 0
        assert (tmp_path / "feature.jsonl").exists()
        assert (tmp_path / "main.jsonl").exists()
        from dit.core.store import ObjectStore
        from dit.core.objects import deserialize_commit
        from dit.core.refs import RefStore
        dot = tmp_path / ".dit"
        store = ObjectStore(dot / "objects")
        refs = RefStore(dot)
        head_hash = refs.resolve_head()
        commit = deserialize_commit(store.read("commits", head_hash))
        assert len(commit.parent_hashes) == 2

    def test_merge_same_branch_fails(self, tmp_path):
        _init_and_commit(tmp_path)
        result = runner.invoke(app, ["merge", "main"])
        assert result.exit_code != 0

    def test_merge_nonexistent_branch_fails(self, tmp_path):
        _init_and_commit(tmp_path)
        result = runner.invoke(app, ["merge", "nope"])
        assert result.exit_code != 0

    def test_merge_with_staged_files_fails(self, tmp_path):
        _init_and_commit(tmp_path)
        runner.invoke(app, ["branch", "feature"], catch_exceptions=False)
        (tmp_path / "new.jsonl").write_text('{"x":1}\n')
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        result = runner.invoke(app, ["merge", "feature"])
        assert result.exit_code != 0


class TestMergeConflict:
    def test_conflict_creates_state_files(self, tmp_path):
        """Both branches modify the same row differently — conflict.
        Both branches change the assistant response for the same user query.
        Since query_fingerprint is derived from the user turn (same on both branches),
        and both produce different row_hashes, this triggers a "both_modified" conflict
        via the refresh detection path in merge_manifests."""
        _init_and_commit(tmp_path)
        runner.invoke(app, ["checkout", "-b", "feature"], catch_exceptions=False)
        (tmp_path / "data.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "feature answer"}]}) + "\n"
        )
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "feature refresh"], catch_exceptions=False)
        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)
        (tmp_path / "data.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "main answer"}]}) + "\n"
        )
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "main refresh"], catch_exceptions=False)
        result = runner.invoke(app, ["merge", "feature"])
        assert result.exit_code != 0
        assert "conflict" in result.output.lower()
        assert (tmp_path / ".dit" / "MERGE_HEAD").exists()
        assert (tmp_path / ".dit" / "MERGE_MSG").exists()
        assert (tmp_path / ".dit" / "conflicts.json").exists()

    def test_merge_abort(self, tmp_path):
        _init_and_commit(tmp_path)
        runner.invoke(app, ["checkout", "-b", "feature"], catch_exceptions=False)
        (tmp_path / "data.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "feature"}]}) + "\n"
        )
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "feature"], catch_exceptions=False)
        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)
        (tmp_path / "data.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "main"}]}) + "\n"
        )
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "main"], catch_exceptions=False)
        runner.invoke(app, ["merge", "feature"])
        result = runner.invoke(app, ["merge", "--abort"], catch_exceptions=False)
        assert result.exit_code == 0
        assert not (tmp_path / ".dit" / "MERGE_HEAD").exists()
        content = (tmp_path / "data.jsonl").read_text()
        assert "main" in content

    def test_merge_continue(self, tmp_path):
        _init_and_commit(tmp_path)
        runner.invoke(app, ["checkout", "-b", "feature"], catch_exceptions=False)
        (tmp_path / "data.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "feature"}]}) + "\n"
        )
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "feature"], catch_exceptions=False)
        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)
        (tmp_path / "data.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "main"}]}) + "\n"
        )
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "main"], catch_exceptions=False)
        runner.invoke(app, ["merge", "feature"])
        (tmp_path / "data.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "resolved"}]}) + "\n"
        )
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        result = runner.invoke(app, ["merge", "--continue"], catch_exceptions=False)
        assert result.exit_code == 0
        assert not (tmp_path / ".dit" / "MERGE_HEAD").exists()
        from dit.core.store import ObjectStore
        from dit.core.objects import deserialize_commit
        from dit.core.refs import RefStore
        dot = tmp_path / ".dit"
        store = ObjectStore(dot / "objects")
        refs = RefStore(dot)
        head_hash = refs.resolve_head()
        commit = deserialize_commit(store.read("commits", head_hash))
        assert len(commit.parent_hashes) == 2

    def test_abort_no_merge_fails(self, tmp_path):
        _init_and_commit(tmp_path)
        result = runner.invoke(app, ["merge", "--abort"])
        assert result.exit_code != 0

    def test_continue_no_merge_fails(self, tmp_path):
        _init_and_commit(tmp_path)
        result = runner.invoke(app, ["merge", "--continue"])
        assert result.exit_code != 0
