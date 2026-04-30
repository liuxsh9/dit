# tests/test_robustness_merge_branch.py
"""Robustness tests for merge, branch, and checkout edge cases.

Stress-tests data integrity during branch operations including
three-way merges, fast-forwards, conflict handling, and checkout
materialization.
"""
import json
import os
from pathlib import Path

from typer.testing import CliRunner

from dit.cli.main import app

runner = CliRunner()


def _row(content: str, role: str = "user") -> str:
    """Build a single JSONL row with a unique user message."""
    return json.dumps({"messages": [{"role": role, "content": content}]})


def _multi_row(*contents: str) -> str:
    """Build a multi-row JSONL file from user messages."""
    return "\n".join(_row(c) for c in contents) + "\n"


def _init_and_commit(tmp_path: Path, files: dict[str, str] | None = None):
    """Init repo, write files, add, commit. Default: one data.jsonl."""
    os.chdir(tmp_path)
    runner.invoke(app, ["init"], catch_exceptions=False)
    if files is None:
        files = {"data.jsonl": _row("hello") + "\n"}
    for name, content in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    runner.invoke(app, ["add", "."], catch_exceptions=False)
    runner.invoke(app, ["commit", "-m", "initial"], catch_exceptions=False)


def _commit_file(tmp_path: Path, filename: str, content: str, msg: str):
    """Write a file, add, and commit."""
    p = tmp_path / filename
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    runner.invoke(app, ["add", "."], catch_exceptions=False)
    runner.invoke(app, ["commit", "-m", msg], catch_exceptions=False)


def _delete_and_commit(tmp_path: Path, filename: str, msg: str):
    """Delete a file, stage deletion, and commit."""
    p = tmp_path / filename
    if p.exists():
        p.unlink()
    runner.invoke(app, ["add", filename], catch_exceptions=False)
    runner.invoke(app, ["commit", "-m", msg], catch_exceptions=False)


def _read_head(tmp_path: Path) -> str:
    return (tmp_path / ".dit" / "HEAD").read_text().strip()


def _get_branch_hash(tmp_path: Path, branch: str) -> str:
    return (tmp_path / ".dit" / "refs" / "heads" / branch).read_text().strip()


class TestMergeNewFiles:
    """Merges involving files added on one or both branches."""

    def test_file_added_on_both_branches_different_content_conflicts(self, tmp_path):
        """Both branches add same filename with different content -> conflict."""
        _init_and_commit(tmp_path)

        runner.invoke(app, ["checkout", "-b", "feature"], catch_exceptions=False)
        _commit_file(tmp_path, "new.jsonl", _row("feature-content") + "\n", "feature add")

        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)
        _commit_file(tmp_path, "new.jsonl", _row("main-content") + "\n", "main add")

        result = runner.invoke(app, ["merge", "feature"])
        assert result.exit_code != 0
        assert "conflict" in result.output.lower()
        assert "both_added" in result.output

    def test_file_added_on_both_branches_same_content_merges(self, tmp_path):
        """Both branches add same filename with identical content -> clean merge."""
        _init_and_commit(tmp_path)

        runner.invoke(app, ["checkout", "-b", "feature"], catch_exceptions=False)
        _commit_file(tmp_path, "new.jsonl", _row("same-content") + "\n", "feature add")

        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)
        _commit_file(tmp_path, "new.jsonl", _row("same-content") + "\n", "main add")

        result = runner.invoke(app, ["merge", "feature"], catch_exceptions=False)
        assert result.exit_code == 0
        assert (tmp_path / "new.jsonl").exists()

    def test_file_added_on_one_branch_only(self, tmp_path):
        """Feature adds a new file, main doesn't touch it -> clean merge."""
        _init_and_commit(tmp_path)

        runner.invoke(app, ["checkout", "-b", "feature"], catch_exceptions=False)
        _commit_file(tmp_path, "feature_only.jsonl", _row("feature-data") + "\n", "add feature file")

        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)
        # Make a diverging commit on main so it's not a fast-forward
        _commit_file(tmp_path, "main_only.jsonl", _row("main-data") + "\n", "add main file")

        result = runner.invoke(app, ["merge", "feature"], catch_exceptions=False)
        assert result.exit_code == 0
        assert (tmp_path / "feature_only.jsonl").exists()
        assert (tmp_path / "main_only.jsonl").exists()
        assert "feature-data" in (tmp_path / "feature_only.jsonl").read_text()


class TestMergeDeleteModify:
    """Merges where one branch deletes and the other modifies."""

    def test_delete_on_ours_modify_on_theirs_conflicts(self, tmp_path):
        """Main deletes file, feature modifies it -> modify_delete conflict."""
        _init_and_commit(tmp_path, {
            "contested.jsonl": _row("original") + "\n",
            "keep.jsonl": _row("keeper") + "\n",
        })

        runner.invoke(app, ["checkout", "-b", "feature"], catch_exceptions=False)
        _commit_file(tmp_path, "contested.jsonl",
                     _row("modified-by-feature") + "\n", "feature modify")

        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)
        _delete_and_commit(tmp_path, "contested.jsonl", "main delete")

        result = runner.invoke(app, ["merge", "feature"])
        assert result.exit_code != 0
        assert "modify_delete" in result.output

    def test_delete_on_theirs_modify_on_ours_conflicts(self, tmp_path):
        """Feature deletes file, main modifies it -> modify_delete conflict."""
        _init_and_commit(tmp_path, {
            "contested.jsonl": _row("original") + "\n",
            "keep.jsonl": _row("keeper") + "\n",
        })

        runner.invoke(app, ["checkout", "-b", "feature"], catch_exceptions=False)
        _delete_and_commit(tmp_path, "contested.jsonl", "feature delete")

        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)
        _commit_file(tmp_path, "contested.jsonl",
                     _row("modified-by-main") + "\n", "main modify")

        result = runner.invoke(app, ["merge", "feature"])
        assert result.exit_code != 0
        assert "modify_delete" in result.output


class TestMergeRowLevel:
    """Row-level merge behavior within a single file."""

    def test_different_rows_changed_auto_merges(self, tmp_path):
        """Same file, different rows changed on each branch -> auto-merge."""
        base_content = _multi_row("row-a", "row-b", "row-c")
        _init_and_commit(tmp_path, {"data.jsonl": base_content})

        runner.invoke(app, ["checkout", "-b", "feature"], catch_exceptions=False)
        # Feature adds a new row
        _commit_file(tmp_path, "data.jsonl",
                     _multi_row("row-a", "row-b", "row-c", "row-d-feature"), "feature add row")

        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)
        # Main adds a different new row
        _commit_file(tmp_path, "data.jsonl",
                     _multi_row("row-a", "row-b", "row-c", "row-e-main"), "main add row")

        result = runner.invoke(app, ["merge", "feature"], catch_exceptions=False)
        assert result.exit_code == 0
        content = (tmp_path / "data.jsonl").read_text()
        assert "row-d-feature" in content
        assert "row-e-main" in content

    def test_same_row_changed_both_branches_conflicts(self, tmp_path):
        """Same row refreshed on both branches with different content -> conflict."""
        _init_and_commit(tmp_path, {"data.jsonl": _row("hello") + "\n"})

        runner.invoke(app, ["checkout", "-b", "feature"], catch_exceptions=False)
        # Change the assistant response for the same user query
        row_f = json.dumps({"messages": [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "feature-answer"},
        ]})
        _commit_file(tmp_path, "data.jsonl", row_f + "\n", "feature refresh")

        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)
        row_m = json.dumps({"messages": [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "main-answer"},
        ]})
        _commit_file(tmp_path, "data.jsonl", row_m + "\n", "main refresh")

        result = runner.invoke(app, ["merge", "feature"])
        assert result.exit_code != 0
        assert "conflict" in result.output.lower()


class TestFastForwardMerge:
    """Fast-forward merge preserves all data."""

    def test_ff_merge_preserves_all_rows(self, tmp_path):
        """After FF merge, every row from feature branch is present."""
        _init_and_commit(tmp_path, {"data.jsonl": _row("base-row") + "\n"})

        runner.invoke(app, ["checkout", "-b", "feature"], catch_exceptions=False)
        _commit_file(tmp_path, "data.jsonl",
                     _multi_row("base-row", "feature-row-1", "feature-row-2"),
                     "feature adds rows")
        _commit_file(tmp_path, "extra.jsonl", _row("extra-data") + "\n", "feature adds file")

        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)
        result = runner.invoke(app, ["merge", "feature"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "fast-forward" in result.output.lower()

        content = (tmp_path / "data.jsonl").read_text()
        assert "base-row" in content
        assert "feature-row-1" in content
        assert "feature-row-2" in content
        assert (tmp_path / "extra.jsonl").exists()
        assert "extra-data" in (tmp_path / "extra.jsonl").read_text()

        # Branch tips should match
        main_hash = _get_branch_hash(tmp_path, "main")
        feature_hash = _get_branch_hash(tmp_path, "feature")
        assert main_hash == feature_hash


class TestCheckoutMaterialization:
    """Checkout correctly materializes/removes files."""

    def test_checkout_to_branch_with_more_files(self, tmp_path):
        """Main has 1 file, feature has 3 -> checkout feature materializes all 3."""
        _init_and_commit(tmp_path, {"data.jsonl": _row("base") + "\n"})

        runner.invoke(app, ["checkout", "-b", "feature"], catch_exceptions=False)
        _commit_file(tmp_path, "extra1.jsonl", _row("extra1") + "\n", "add extra1")
        _commit_file(tmp_path, "extra2.jsonl", _row("extra2") + "\n", "add extra2")

        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)
        assert not (tmp_path / "extra1.jsonl").exists()
        assert not (tmp_path / "extra2.jsonl").exists()

        runner.invoke(app, ["checkout", "feature"], catch_exceptions=False)
        assert (tmp_path / "data.jsonl").exists()
        assert (tmp_path / "extra1.jsonl").exists()
        assert (tmp_path / "extra2.jsonl").exists()
        assert "extra1" in (tmp_path / "extra1.jsonl").read_text()
        assert "extra2" in (tmp_path / "extra2.jsonl").read_text()

    def test_checkout_to_branch_with_fewer_files(self, tmp_path):
        """Feature has 3 files, main has 1 -> checkout main removes extra 2."""
        _init_and_commit(tmp_path, {"data.jsonl": _row("base") + "\n"})

        runner.invoke(app, ["checkout", "-b", "feature"], catch_exceptions=False)
        _commit_file(tmp_path, "extra1.jsonl", _row("e1") + "\n", "add e1")
        _commit_file(tmp_path, "extra2.jsonl", _row("e2") + "\n", "add e2")
        assert (tmp_path / "extra1.jsonl").exists()
        assert (tmp_path / "extra2.jsonl").exists()

        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)
        assert (tmp_path / "data.jsonl").exists()
        assert not (tmp_path / "extra1.jsonl").exists()
        assert not (tmp_path / "extra2.jsonl").exists()


class TestBranchFromOlderCommit:
    """Branch creation from non-HEAD commits via ref manipulation."""

    def test_branch_from_older_commit(self, tmp_path):
        """Create a branch pointing at an older commit, checkout, verify state."""
        _init_and_commit(tmp_path, {"data.jsonl": _row("v1") + "\n"})
        initial_hash = _get_branch_hash(tmp_path, "main")

        _commit_file(tmp_path, "data.jsonl", _row("v2") + "\n", "second commit")
        second_hash = _get_branch_hash(tmp_path, "main")
        assert initial_hash != second_hash

        # Manually create a branch at the older commit
        from dit.core.refs import RefStore
        dot = tmp_path / ".dit"
        refs = RefStore(dot)
        refs.set_branch("old-branch", initial_hash)

        runner.invoke(app, ["checkout", "old-branch"], catch_exceptions=False)
        content = (tmp_path / "data.jsonl").read_text()
        assert "v1" in content
        assert "v2" not in content


class TestMergeBackAndForth:
    """Merge in both directions between branches."""

    def test_merge_feature_to_main_then_main_to_feature(self, tmp_path):
        """Merge feature->main, then main->feature should be clean."""
        _init_and_commit(tmp_path, {"data.jsonl": _row("base") + "\n"})

        runner.invoke(app, ["checkout", "-b", "feature"], catch_exceptions=False)
        _commit_file(tmp_path, "feature.jsonl", _row("feat") + "\n", "feature work")

        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)
        _commit_file(tmp_path, "main.jsonl", _row("main-work") + "\n", "main work")

        # Merge feature -> main
        result = runner.invoke(app, ["merge", "feature"], catch_exceptions=False)
        assert result.exit_code == 0

        # Now merge main -> feature (should fast-forward since feature is ancestor)
        runner.invoke(app, ["checkout", "feature"], catch_exceptions=False)
        result = runner.invoke(app, ["merge", "main"], catch_exceptions=False)
        assert result.exit_code == 0

        # Both branches should now have all files
        assert (tmp_path / "data.jsonl").exists()
        assert (tmp_path / "feature.jsonl").exists()
        assert (tmp_path / "main.jsonl").exists()

        # Tips should match
        main_hash = _get_branch_hash(tmp_path, "main")
        feature_hash = _get_branch_hash(tmp_path, "feature")
        assert main_hash == feature_hash


class TestMultipleSequentialMerges:
    """Merge multiple branches one by one into main."""

    def test_three_branches_merged_sequentially(self, tmp_path):
        """Create 3 feature branches, merge each into main one by one."""
        _init_and_commit(tmp_path, {"data.jsonl": _row("base") + "\n"})

        # Create 3 branches from initial commit
        for i in range(1, 4):
            runner.invoke(app, ["checkout", "main"], catch_exceptions=False)
            runner.invoke(app, ["checkout", "-b", f"feat-{i}"], catch_exceptions=False)
            _commit_file(tmp_path, f"feat{i}.jsonl",
                         _row(f"feature-{i}-data") + "\n", f"feat-{i} work")

        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)

        # Merge each branch
        for i in range(1, 4):
            result = runner.invoke(app, ["merge", f"feat-{i}"], catch_exceptions=False)
            assert result.exit_code == 0, f"Merge feat-{i} failed: {result.output}"

        # All feature files should exist
        for i in range(1, 4):
            assert (tmp_path / f"feat{i}.jsonl").exists()
            assert f"feature-{i}-data" in (tmp_path / f"feat{i}.jsonl").read_text()


class TestMergeAbortCleanup:
    """Abort a conflicting merge and verify clean state."""

    def test_abort_restores_working_directory(self, tmp_path):
        """After merge --abort, working directory matches pre-merge state."""
        _init_and_commit(tmp_path, {"data.jsonl": _row("hello") + "\n"})

        runner.invoke(app, ["checkout", "-b", "feature"], catch_exceptions=False)
        row_f = json.dumps({"messages": [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "feature"},
        ]})
        _commit_file(tmp_path, "data.jsonl", row_f + "\n", "feature change")

        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)
        row_m = json.dumps({"messages": [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "main"},
        ]})
        _commit_file(tmp_path, "data.jsonl", row_m + "\n", "main change")

        pre_merge_hash = _get_branch_hash(tmp_path, "main")

        # Trigger conflict
        result = runner.invoke(app, ["merge", "feature"])
        assert result.exit_code != 0

        # Abort
        result = runner.invoke(app, ["merge", "--abort"], catch_exceptions=False)
        assert result.exit_code == 0

        # Verify clean state
        assert not (tmp_path / ".dit" / "MERGE_HEAD").exists()
        assert not (tmp_path / ".dit" / "MERGE_MSG").exists()
        assert not (tmp_path / ".dit" / "conflicts.json").exists()
        # Compare semantically: dit re-serializes JSON with canonical key order,
        # so raw string comparison won't match the original hand-written JSON.
        post_abort_rows = [json.loads(line) for line in (tmp_path / "data.jsonl").read_text().strip().splitlines()]
        assert len(post_abort_rows) == 1
        assert post_abort_rows[0]["messages"][1]["content"] == "main"
        assert _get_branch_hash(tmp_path, "main") == pre_merge_hash


class TestNestedDirectoryMerge:
    """Merge with files in subdirectories."""

    def test_nested_dir_changes_on_both_branches(self, tmp_path):
        """Both branches add files in subdirectories -> clean merge.

        BUG: _load_tree_manifests in merge.py only reads top-level tree entries,
        ignoring nested tree objects (subdirectories). Files in subdirectories
        are silently dropped during three-way merge. This test documents the
        current (broken) behavior. When fixed, flip the assertions below.
        """
        _init_and_commit(tmp_path, {"data.jsonl": _row("root") + "\n"})

        runner.invoke(app, ["checkout", "-b", "feature"], catch_exceptions=False)
        _commit_file(tmp_path, "sub/feature.jsonl",
                     _row("nested-feature") + "\n", "feature nested")

        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)
        _commit_file(tmp_path, "sub/main.jsonl",
                     _row("nested-main") + "\n", "main nested")

        result = runner.invoke(app, ["merge", "feature"], catch_exceptions=False)
        assert result.exit_code == 0
        # After fix: nested files from both branches are preserved in the merge
        assert (tmp_path / "sub" / "feature.jsonl").exists()
        assert (tmp_path / "sub" / "main.jsonl").exists()

    def test_checkout_removes_nested_dirs(self, tmp_path):
        """Checkout to branch without nested files removes them."""
        _init_and_commit(tmp_path, {"data.jsonl": _row("root") + "\n"})

        runner.invoke(app, ["checkout", "-b", "feature"], catch_exceptions=False)
        _commit_file(tmp_path, "deep/nested/file.jsonl",
                     _row("deep") + "\n", "add deep file")
        assert (tmp_path / "deep" / "nested" / "file.jsonl").exists()

        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)
        assert not (tmp_path / "deep" / "nested" / "file.jsonl").exists()


class TestCherryPickThenMerge:
    """Cherry-pick a commit, then merge the source branch."""

    def test_cherry_pick_then_merge_handles_duplicates(self, tmp_path):
        """Cherry-pick a commit from feature, then merge feature -> should be clean."""
        _init_and_commit(tmp_path, {"data.jsonl": _row("base") + "\n"})

        runner.invoke(app, ["checkout", "-b", "feature"], catch_exceptions=False)
        _commit_file(tmp_path, "picked.jsonl",
                     _row("cherry-data") + "\n", "to be picked")
        feature_hash = _get_branch_hash(tmp_path, "feature")

        # Add another commit on feature so merge isn't trivial
        _commit_file(tmp_path, "extra.jsonl",
                     _row("extra-data") + "\n", "extra feature work")

        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)
        # Make main diverge so merge is three-way
        _commit_file(tmp_path, "main_work.jsonl",
                     _row("main-data") + "\n", "main work")

        # Cherry-pick the first feature commit
        result = runner.invoke(app, ["cherry-pick", feature_hash], catch_exceptions=False)
        assert result.exit_code == 0
        assert (tmp_path / "picked.jsonl").exists()

        # Now merge feature branch (which includes the cherry-picked commit)
        result = runner.invoke(app, ["merge", "feature"], catch_exceptions=False)
        assert result.exit_code == 0
        # All files should be present
        assert (tmp_path / "picked.jsonl").exists()
        assert (tmp_path / "extra.jsonl").exists()
        assert (tmp_path / "main_work.jsonl").exists()
