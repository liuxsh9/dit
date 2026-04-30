"""Tests for `dit diff` with ref arguments (branch, tag, commit hash comparisons)."""
from __future__ import annotations

import json
import os
from pathlib import Path

from typer.testing import CliRunner

from dit.cli.main import app, _resolve_ref
from dit.core.refs import RefStore

runner = CliRunner()


def _row(content: str) -> str:
    """Build a single JSONL row with the given assistant content."""
    return json.dumps({"messages": [{"role": "user", "content": content}]}) + "\n"


def _row_pair(question: str, answer: str) -> str:
    """Build a JSONL row with user question + assistant answer (enables refresh detection)."""
    return json.dumps({
        "messages": [
            {"role": "user", "content": question},
            {"role": "assistant", "content": answer},
        ]
    }) + "\n"


def _init_repo(tmp_path: Path):
    """Initialize a dit repo and chdir into it."""
    os.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0


def _commit_file(tmp_path: Path, filename: str, content: str, message: str) -> str:
    """Write content to a file, add, commit, and return the commit hash."""
    (tmp_path / filename).write_text(content)
    result = runner.invoke(app, ["add", "."])
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["commit", "-m", message])
    assert result.exit_code == 0, result.output
    return (tmp_path / ".dit" / "refs" / "heads" / "main").read_text().strip()


# ---------------------------------------------------------------------------
# Basic: dit diff (no args) still works
# ---------------------------------------------------------------------------
class TestDiffNoArgs:
    def test_no_args_shows_working_dir_vs_head(self, tmp_path: Path):
        _init_repo(tmp_path)
        _commit_file(tmp_path, "data.jsonl", _row("hello"), "initial")
        (tmp_path / "data.jsonl").write_text(_row("world"))
        result = runner.invoke(app, ["diff"])
        assert result.exit_code == 0
        assert "data.jsonl" in result.output

    def test_no_args_no_changes(self, tmp_path: Path):
        _init_repo(tmp_path)
        _commit_file(tmp_path, "data.jsonl", _row("hello"), "initial")
        result = runner.invoke(app, ["diff"])
        assert result.exit_code == 0
        assert "no changes" in result.output.lower()


# ---------------------------------------------------------------------------
# Two-ref mode: dit diff <ref1> <ref2>
# ---------------------------------------------------------------------------
class TestDiffTwoRefs:
    def test_diff_two_branches(self, tmp_path: Path):
        _init_repo(tmp_path)
        _commit_file(tmp_path, "data.jsonl", _row("v1"), "first")
        runner.invoke(app, ["branch", "branch-a"])

        _commit_file(tmp_path, "data.jsonl", _row("v1") + _row("v2"), "second")
        runner.invoke(app, ["branch", "branch-b"])

        result = runner.invoke(app, ["diff", "branch-a", "branch-b"])
        assert result.exit_code == 0
        assert "data.jsonl" in result.output
        assert "+1" in result.output

    def test_diff_two_commit_hashes(self, tmp_path: Path):
        _init_repo(tmp_path)
        h1 = _commit_file(tmp_path, "data.jsonl", _row("v1"), "first")
        h2 = _commit_file(tmp_path, "data.jsonl", _row("v1") + _row("v2"), "second")

        result = runner.invoke(app, ["diff", h1, h2])
        assert result.exit_code == 0
        assert "data.jsonl" in result.output
        assert "+1" in result.output

    def test_diff_two_tags(self, tmp_path: Path):
        _init_repo(tmp_path)
        _commit_file(tmp_path, "data.jsonl", _row("v1"), "first")
        runner.invoke(app, ["tag", "v1.0"])

        _commit_file(tmp_path, "data.jsonl", _row("v1") + _row("v2"), "second")
        runner.invoke(app, ["tag", "v2.0"])

        result = runner.invoke(app, ["diff", "v1.0", "v2.0"])
        assert result.exit_code == 0
        assert "data.jsonl" in result.output

    def test_diff_branch_vs_tag(self, tmp_path: Path):
        _init_repo(tmp_path)
        _commit_file(tmp_path, "data.jsonl", _row("v1"), "first")
        runner.invoke(app, ["branch", "old-branch"])

        _commit_file(tmp_path, "data.jsonl", _row("v1") + _row("v2"), "second")
        runner.invoke(app, ["tag", "new-tag"])

        result = runner.invoke(app, ["diff", "old-branch", "new-tag"])
        assert result.exit_code == 0
        assert "data.jsonl" in result.output

    def test_diff_identical_commits_no_changes(self, tmp_path: Path):
        _init_repo(tmp_path)
        h1 = _commit_file(tmp_path, "data.jsonl", _row("same"), "only commit")

        result = runner.invoke(app, ["diff", h1, h1])
        assert result.exit_code == 0
        assert "no changes" in result.output.lower()

    def test_diff_identical_branches_no_changes(self, tmp_path: Path):
        _init_repo(tmp_path)
        _commit_file(tmp_path, "data.jsonl", _row("same"), "only commit")
        runner.invoke(app, ["branch", "copy"])

        result = runner.invoke(app, ["diff", "main", "copy"])
        assert result.exit_code == 0
        assert "no changes" in result.output.lower()

    def test_diff_added_file(self, tmp_path: Path):
        """Commit 1 has file A, commit 2 adds file B."""
        _init_repo(tmp_path)
        h1 = _commit_file(tmp_path, "a.jsonl", _row("a"), "add a")
        (tmp_path / "b.jsonl").write_text(_row("b"))
        runner.invoke(app, ["add", "."])
        result = runner.invoke(app, ["commit", "-m", "add b"])
        assert result.exit_code == 0
        h2 = (tmp_path / ".dit" / "refs" / "heads" / "main").read_text().strip()

        result = runner.invoke(app, ["diff", h1, h2])
        assert result.exit_code == 0
        assert "b.jsonl" in result.output
        assert "new file" in result.output.lower()

    def test_diff_removed_file(self, tmp_path: Path):
        """Commit 1 has files A+B, commit 2 deletes B."""
        _init_repo(tmp_path)
        (tmp_path / "a.jsonl").write_text(_row("a"))
        (tmp_path / "b.jsonl").write_text(_row("b"))
        runner.invoke(app, ["add", "."])
        runner.invoke(app, ["commit", "-m", "add both"])
        h1 = (tmp_path / ".dit" / "refs" / "heads" / "main").read_text().strip()

        (tmp_path / "b.jsonl").unlink()
        runner.invoke(app, ["add", "b.jsonl"])
        runner.invoke(app, ["commit", "-m", "delete b"])
        h2 = (tmp_path / ".dit" / "refs" / "heads" / "main").read_text().strip()

        result = runner.invoke(app, ["diff", h1, h2])
        assert result.exit_code == 0
        assert "b.jsonl" in result.output
        assert "deleted" in result.output.lower()

    def test_diff_modified_rows_refreshed(self, tmp_path: Path):
        """Detect refreshed rows between two commits (same question, different answer)."""
        _init_repo(tmp_path)
        h1 = _commit_file(
            tmp_path, "data.jsonl",
            _row_pair("implement LRU", "old answer"),
            "v1",
        )
        h2 = _commit_file(
            tmp_path, "data.jsonl",
            _row_pair("implement LRU", "new answer"),
            "v2",
        )

        result = runner.invoke(app, ["diff", h1, h2])
        assert result.exit_code == 0
        assert "refresh" in result.output.lower()

    def test_diff_reversed_direction(self, tmp_path: Path):
        """diff ref1 ref2 vs diff ref2 ref1 should show opposite changes."""
        _init_repo(tmp_path)
        h1 = _commit_file(tmp_path, "data.jsonl", _row("v1"), "first")
        h2 = _commit_file(tmp_path, "data.jsonl", _row("v1") + _row("v2"), "second")

        fwd = runner.invoke(app, ["diff", h1, h2])
        rev = runner.invoke(app, ["diff", h2, h1])
        assert fwd.exit_code == 0
        assert rev.exit_code == 0
        # Forward: +1 row added; Reverse: -1 row removed
        assert "+1" in fwd.output
        assert "-1" in rev.output


# ---------------------------------------------------------------------------
# One-ref mode: dit diff <ref> (ref vs working directory)
# ---------------------------------------------------------------------------
class TestDiffOneRef:
    def test_diff_ref_vs_working_dir(self, tmp_path: Path):
        _init_repo(tmp_path)
        h1 = _commit_file(tmp_path, "data.jsonl", _row("v1"), "first")
        # Modify working directory without committing
        (tmp_path / "data.jsonl").write_text(_row("v1") + _row("v2"))

        result = runner.invoke(app, ["diff", h1])
        assert result.exit_code == 0
        assert "data.jsonl" in result.output
        assert "+1" in result.output

    def test_diff_branch_vs_working_dir(self, tmp_path: Path):
        _init_repo(tmp_path)
        _commit_file(tmp_path, "data.jsonl", _row("v1"), "first")
        runner.invoke(app, ["branch", "snap"])
        # Advance main
        _commit_file(tmp_path, "data.jsonl", _row("v1") + _row("v2"), "second")
        # Modify working dir further
        (tmp_path / "data.jsonl").write_text(_row("v1") + _row("v2") + _row("v3"))

        result = runner.invoke(app, ["diff", "snap"])
        assert result.exit_code == 0
        assert "data.jsonl" in result.output
        # snap has 1 row, working dir has 3 rows => +2
        assert "+2" in result.output

    def test_diff_ref_vs_working_dir_no_changes(self, tmp_path: Path):
        """If working dir matches the ref, show no changes."""
        _init_repo(tmp_path)
        _commit_file(tmp_path, "data.jsonl", _row("same"), "first")

        result = runner.invoke(app, ["diff", "main"])
        assert result.exit_code == 0
        assert "no changes" in result.output.lower()


# ---------------------------------------------------------------------------
# Error handling: bad refs
# ---------------------------------------------------------------------------
class TestDiffBadRefs:
    def test_bad_single_ref(self, tmp_path: Path):
        _init_repo(tmp_path)
        _commit_file(tmp_path, "data.jsonl", _row("v1"), "first")

        result = runner.invoke(app, ["diff", "nonexistent-ref"])
        assert result.exit_code != 0
        assert "bad revision" in result.output.lower()
        assert "nonexistent-ref" in result.output

    def test_bad_first_ref_of_two(self, tmp_path: Path):
        _init_repo(tmp_path)
        h1 = _commit_file(tmp_path, "data.jsonl", _row("v1"), "first")

        result = runner.invoke(app, ["diff", "bad-ref", h1])
        assert result.exit_code != 0
        assert "bad revision" in result.output.lower()
        assert "bad-ref" in result.output

    def test_bad_second_ref_of_two(self, tmp_path: Path):
        _init_repo(tmp_path)
        h1 = _commit_file(tmp_path, "data.jsonl", _row("v1"), "first")

        result = runner.invoke(app, ["diff", h1, "bad-ref"])
        assert result.exit_code != 0
        assert "bad revision" in result.output.lower()
        assert "bad-ref" in result.output

    def test_bad_both_refs(self, tmp_path: Path):
        _init_repo(tmp_path)
        _commit_file(tmp_path, "data.jsonl", _row("v1"), "first")

        result = runner.invoke(app, ["diff", "nope1", "nope2"])
        assert result.exit_code != 0
        assert "bad revision" in result.output.lower()
        # Should fail on the first bad ref
        assert "nope1" in result.output


# ---------------------------------------------------------------------------
# _resolve_ref unit tests
# ---------------------------------------------------------------------------
class TestResolveRef:
    def test_resolve_branch(self, tmp_path: Path):
        _init_repo(tmp_path)
        h = _commit_file(tmp_path, "data.jsonl", _row("v1"), "first")
        runner.invoke(app, ["branch", "feature"])
        dot = tmp_path / ".dit"
        assert _resolve_ref(dot, "feature") == h

    def test_resolve_tag(self, tmp_path: Path):
        _init_repo(tmp_path)
        h = _commit_file(tmp_path, "data.jsonl", _row("v1"), "first")
        runner.invoke(app, ["tag", "v1.0"])
        dot = tmp_path / ".dit"
        assert _resolve_ref(dot, "v1.0") == h

    def test_resolve_full_commit_hash(self, tmp_path: Path):
        _init_repo(tmp_path)
        h = _commit_file(tmp_path, "data.jsonl", _row("v1"), "first")
        dot = tmp_path / ".dit"
        assert _resolve_ref(dot, h) == h

    def test_resolve_abbreviated_commit_hash(self, tmp_path: Path):
        _init_repo(tmp_path)
        h = _commit_file(tmp_path, "data.jsonl", _row("v1"), "first")
        dot = tmp_path / ".dit"
        # Use first 8 chars as abbreviation
        resolved = _resolve_ref(dot, h[:8])
        assert resolved == h

    def test_resolve_branch_takes_priority_over_tag(self, tmp_path: Path):
        """If a name matches both a branch and a tag, branch wins."""
        _init_repo(tmp_path)
        h1 = _commit_file(tmp_path, "data.jsonl", _row("v1"), "first")
        # Create tag "ambiguous" pointing at h1
        runner.invoke(app, ["tag", "ambiguous"])
        # Advance main and create branch "ambiguous" pointing at h2
        h2 = _commit_file(tmp_path, "data.jsonl", _row("v2"), "second")
        dot = tmp_path / ".dit"
        refs = RefStore(dot)
        refs.set_branch("ambiguous", h2)

        resolved = _resolve_ref(dot, "ambiguous")
        # Branch should win (h2), not tag (h1)
        assert resolved == h2

    def test_resolve_nonexistent_returns_none(self, tmp_path: Path):
        _init_repo(tmp_path)
        _commit_file(tmp_path, "data.jsonl", _row("v1"), "first")
        dot = tmp_path / ".dit"
        assert _resolve_ref(dot, "does-not-exist") is None


# ---------------------------------------------------------------------------
# Stress: multiple branches with different content
# ---------------------------------------------------------------------------
class TestDiffStressMultipleBranches:
    def test_five_branches_pairwise_diffs(self, tmp_path: Path):
        """Create 5 branches with different row counts, diff various pairs."""
        _init_repo(tmp_path)

        branches = {}
        for i in range(1, 6):
            content = "".join(_row(f"row-{j}") for j in range(i))
            _commit_file(tmp_path, "data.jsonl", content, f"commit-{i}")
            bname = f"b{i}"
            runner.invoke(app, ["branch", bname])
            branches[bname] = i  # number of rows

        # Diff b1 (1 row) vs b5 (5 rows) => +4
        result = runner.invoke(app, ["diff", "b1", "b5"])
        assert result.exit_code == 0
        assert "+4" in result.output

        # Diff b5 (5 rows) vs b1 (1 row) => -4
        result = runner.invoke(app, ["diff", "b5", "b1"])
        assert result.exit_code == 0
        assert "-4" in result.output

        # Diff b2 (2 rows) vs b4 (4 rows) => +2
        result = runner.invoke(app, ["diff", "b2", "b4"])
        assert result.exit_code == 0
        assert "+2" in result.output

        # Diff b3 vs b3 => no changes
        result = runner.invoke(app, ["diff", "b3", "b3"])
        assert result.exit_code == 0
        assert "no changes" in result.output.lower()

    def test_branches_with_different_files(self, tmp_path: Path):
        """Branches that add/remove different files."""
        _init_repo(tmp_path)

        # Commit 1: only a.jsonl
        (tmp_path / "a.jsonl").write_text(_row("a-data"))
        runner.invoke(app, ["add", "."])
        runner.invoke(app, ["commit", "-m", "add a"])
        runner.invoke(app, ["branch", "only-a"])

        # Commit 2: a.jsonl + b.jsonl
        (tmp_path / "b.jsonl").write_text(_row("b-data"))
        runner.invoke(app, ["add", "."])
        runner.invoke(app, ["commit", "-m", "add b"])
        runner.invoke(app, ["branch", "a-and-b"])

        # Commit 3: a.jsonl + b.jsonl + c.jsonl
        (tmp_path / "c.jsonl").write_text(_row("c-data"))
        runner.invoke(app, ["add", "."])
        runner.invoke(app, ["commit", "-m", "add c"])
        runner.invoke(app, ["branch", "a-b-c"])

        # Diff only-a vs a-b-c: b.jsonl and c.jsonl are new
        result = runner.invoke(app, ["diff", "only-a", "a-b-c"])
        assert result.exit_code == 0
        assert "b.jsonl" in result.output
        assert "c.jsonl" in result.output
        assert "new file" in result.output.lower()

        # Diff a-b-c vs only-a: b.jsonl and c.jsonl are deleted
        result = runner.invoke(app, ["diff", "a-b-c", "only-a"])
        assert result.exit_code == 0
        assert "b.jsonl" in result.output
        assert "c.jsonl" in result.output
        assert "deleted" in result.output.lower()

    def test_abbreviated_hash_in_diff(self, tmp_path: Path):
        """Use abbreviated commit hashes (8 chars) in diff."""
        _init_repo(tmp_path)
        h1 = _commit_file(tmp_path, "data.jsonl", _row("v1"), "first")
        h2 = _commit_file(tmp_path, "data.jsonl", _row("v1") + _row("v2"), "second")

        result = runner.invoke(app, ["diff", h1[:8], h2[:8]])
        assert result.exit_code == 0
        assert "data.jsonl" in result.output
        assert "+1" in result.output

    def test_mixed_ref_types_in_diff(self, tmp_path: Path):
        """Use a tag for ref1 and an abbreviated hash for ref2."""
        _init_repo(tmp_path)
        _commit_file(tmp_path, "data.jsonl", _row("v1"), "first")
        runner.invoke(app, ["tag", "start"])

        h2 = _commit_file(tmp_path, "data.jsonl", _row("v1") + _row("v2") + _row("v3"), "second")

        result = runner.invoke(app, ["diff", "start", h2[:8]])
        assert result.exit_code == 0
        assert "+2" in result.output
