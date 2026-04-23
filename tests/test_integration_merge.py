"""End-to-end integration test for the full merge workflow."""
import json
import os
from pathlib import Path

from typer.testing import CliRunner

from dit.cli.main import app
from dit.core.objects import deserialize_commit
from dit.core.refs import RefStore
from dit.core.store import ObjectStore

runner = CliRunner()


class TestFullMergeWorkflow:
    def test_branch_diverge_merge_resolve(self, tmp_path: Path):
        """Full workflow: init -> branch -> diverge -> merge -> resolve -> verify."""
        os.chdir(tmp_path)

        # Init and initial commit
        runner.invoke(app, ["init"], catch_exceptions=False)
        rows = [
            {"messages": [{"role": "user", "content": f"q{i}"}, {"role": "assistant", "content": f"a{i}"}]}
            for i in range(3)
        ]
        (tmp_path / "data.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "initial 3 rows"], catch_exceptions=False)

        # Create feature branch and add rows
        runner.invoke(app, ["checkout", "-b", "feature"], catch_exceptions=False)
        (tmp_path / "feature.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "feature q"}, {"role": "assistant", "content": "feature a"}]}) + "\n"
        )
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "add feature data"], catch_exceptions=False)

        # Back to main, add different file
        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)
        assert not (tmp_path / "feature.jsonl").exists()
        (tmp_path / "main-extra.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "main q"}, {"role": "assistant", "content": "main a"}]}) + "\n"
        )
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "add main data"], catch_exceptions=False)

        # Merge feature into main
        result = runner.invoke(app, ["merge", "feature"], catch_exceptions=False)
        assert result.exit_code == 0

        # Verify all files exist
        assert (tmp_path / "data.jsonl").exists()
        assert (tmp_path / "feature.jsonl").exists()
        assert (tmp_path / "main-extra.jsonl").exists()

        # Verify merge commit
        dot = tmp_path / ".datahub"
        store = ObjectStore(dot / "objects")
        refs = RefStore(dot)
        head_hash = refs.resolve_head()
        commit = deserialize_commit(store.read("commits", head_hash))
        assert len(commit.parent_hashes) == 2
        assert "merge" in commit.message.lower()

        # Verify log shows merge
        log_result = runner.invoke(app, ["log"], catch_exceptions=False)
        assert "merge" in log_result.output.lower()

    def test_tag_on_merge_commit(self, tmp_path: Path):
        """Create a tag on a merge commit."""
        os.chdir(tmp_path)
        runner.invoke(app, ["init"], catch_exceptions=False)
        (tmp_path / "data.jsonl").write_text('{"messages":[{"role":"user","content":"hi"}]}\n')
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "initial"], catch_exceptions=False)

        runner.invoke(app, ["checkout", "-b", "feature"], catch_exceptions=False)
        (tmp_path / "new.jsonl").write_text('{"messages":[{"role":"user","content":"new"}]}\n')
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "feature"], catch_exceptions=False)

        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)
        runner.invoke(app, ["merge", "feature"], catch_exceptions=False)

        result = runner.invoke(app, ["tag", "v1.0"], catch_exceptions=False)
        assert result.exit_code == 0

        result = runner.invoke(app, ["tag"], catch_exceptions=False)
        assert "v1.0" in result.output
