# tests/test_chaos.py
"""Chaos and stress tests for dit.

Tries to break dit through unusual sequences, rapid state transitions,
edge cases with unusual inputs, and command ordering mistakes that real
users might hit accidentally.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from typer.testing import CliRunner

from dit.cli.main import app
from dit.core.objects import deserialize_commit, deserialize_manifest
from dit.core.store import ObjectStore
from dit.core.refs import RefStore
from dit.core.tree_walker import flatten_tree

runner = CliRunner()


# ── Helpers ─────────────────────────────────────────────────────────


def _row(content: str, role: str = "user") -> str:
    return json.dumps({"messages": [{"role": role, "content": content}]})


def _multi_row(*contents: str) -> str:
    return "\n".join(_row(c) for c in contents) + "\n"


def _init(tmp_path: Path):
    os.chdir(tmp_path)
    runner.invoke(app, ["init"], catch_exceptions=False)


def _init_and_commit(tmp_path: Path, files: dict[str, str] | None = None):
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
    p = tmp_path / filename
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    runner.invoke(app, ["add", "."], catch_exceptions=False)
    runner.invoke(app, ["commit", "-m", msg], catch_exceptions=False)


def _get_branch_hash(tmp_path: Path, branch: str) -> str:
    return (tmp_path / ".dit" / "refs" / "heads" / branch).read_text().strip()


def _head_flat(tmp_path: Path) -> dict:
    dot = tmp_path / ".dit"
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)
    head = refs.resolve_head()
    commit = deserialize_commit(store.read("commits", head))
    return flatten_tree(store, commit.tree_hash)


def _read_file(tmp_path: Path, rel: str) -> str:
    return (tmp_path / rel).read_text()


# ── Rapid state transitions ────────────────────────────────────────


class TestRapidStateTransitions:
    """Full lifecycle sequences that exercise many commands in order."""

    def test_full_lifecycle(self, tmp_path):
        """#1: init -> add -> commit -> branch -> checkout -> modify ->
        add -> commit -> checkout back -> merge. Verify data at every step."""
        os.chdir(tmp_path)
        r = runner.invoke(app, ["init"], catch_exceptions=False)
        assert r.exit_code == 0

        (tmp_path / "data.jsonl").write_text(_row("original") + "\n")
        r = runner.invoke(app, ["add", "."], catch_exceptions=False)
        assert r.exit_code == 0
        r = runner.invoke(app, ["commit", "-m", "initial"], catch_exceptions=False)
        assert r.exit_code == 0

        r = runner.invoke(app, ["checkout", "-b", "feature"], catch_exceptions=False)
        assert r.exit_code == 0

        (tmp_path / "data.jsonl").write_text(_row("modified-on-feature") + "\n")
        r = runner.invoke(app, ["add", "."], catch_exceptions=False)
        assert r.exit_code == 0
        r = runner.invoke(app, ["commit", "-m", "feature change"], catch_exceptions=False)
        assert r.exit_code == 0
        assert "modified-on-feature" in _read_file(tmp_path, "data.jsonl")

        r = runner.invoke(app, ["checkout", "main"], catch_exceptions=False)
        assert r.exit_code == 0
        assert "original" in _read_file(tmp_path, "data.jsonl")

        r = runner.invoke(app, ["merge", "feature"], catch_exceptions=False)
        assert r.exit_code == 0
        assert "modified-on-feature" in _read_file(tmp_path, "data.jsonl")

    def test_five_branches_merge_all(self, tmp_path):
        """#2: Create 5 branches from main, each adding a different file,
        merge all back one by one."""
        _init_and_commit(tmp_path)

        for i in range(5):
            runner.invoke(app, ["checkout", "main"], catch_exceptions=False)
            runner.invoke(app, ["checkout", "-b", f"feat-{i}"], catch_exceptions=False)
            _commit_file(tmp_path, f"feat{i}.jsonl",
                         _row(f"data-from-branch-{i}") + "\n", f"feat-{i}")

        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)
        for i in range(5):
            r = runner.invoke(app, ["merge", f"feat-{i}"], catch_exceptions=False)
            assert r.exit_code == 0, f"Merge feat-{i} failed: {r.output}"

        for i in range(5):
            assert (tmp_path / f"feat{i}.jsonl").exists()
            assert f"data-from-branch-{i}" in _read_file(tmp_path, f"feat{i}.jsonl")

    def test_add_delete_readd_lifecycle(self, tmp_path):
        """#3: Add -> commit -> delete -> commit -> re-add different content
        -> commit. Verify all 3 commits in log and final content."""
        _init(tmp_path)

        (tmp_path / "cycle.jsonl").write_text(_row("version-1") + "\n")
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "add v1"], catch_exceptions=False)

        (tmp_path / "cycle.jsonl").unlink()
        runner.invoke(app, ["add", "cycle.jsonl"], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "delete"], catch_exceptions=False)
        assert not (tmp_path / "cycle.jsonl").exists()

        (tmp_path / "cycle.jsonl").write_text(_row("version-2") + "\n")
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "add v2"], catch_exceptions=False)

        assert "version-2" in _read_file(tmp_path, "cycle.jsonl")
        assert "version-1" not in _read_file(tmp_path, "cycle.jsonl")

        r = runner.invoke(app, ["log", "--oneline"], catch_exceptions=False)
        assert r.exit_code == 0
        assert "add v1" in r.output
        assert "delete" in r.output
        assert "add v2" in r.output


# ── Interrupted / partial operations ───────────────────────────────


class TestPartialOperations:
    """Partial adds, conflict resolution, and checkout with staged changes."""

    def test_partial_add_commit(self, tmp_path):
        """#4: Add multiple files, commit only some by staging selectively."""
        _init(tmp_path)

        (tmp_path / "a.jsonl").write_text(_row("aaa") + "\n")
        (tmp_path / "b.jsonl").write_text(_row("bbb") + "\n")
        (tmp_path / "c.jsonl").write_text(_row("ccc") + "\n")

        # Stage only a and b
        runner.invoke(app, ["add", "a.jsonl", "b.jsonl"], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "partial"], catch_exceptions=False)

        flat = _head_flat(tmp_path)
        assert "a.jsonl" in flat
        assert "b.jsonl" in flat
        assert "c.jsonl" not in flat

        # c.jsonl should still exist on disk but not be committed
        assert (tmp_path / "c.jsonl").exists()

    def test_conflict_resolve_with_different_content(self, tmp_path):
        """#5: Trigger a conflict, resolve with completely different content,
        then merge --continue."""
        _init_and_commit(tmp_path, {"data.jsonl": _row("base") + "\n"})

        runner.invoke(app, ["checkout", "-b", "feature"], catch_exceptions=False)
        row_f = json.dumps({"messages": [
            {"role": "user", "content": "base"},
            {"role": "assistant", "content": "feature-answer"},
        ]})
        _commit_file(tmp_path, "data.jsonl", row_f + "\n", "feature change")

        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)
        row_m = json.dumps({"messages": [
            {"role": "user", "content": "base"},
            {"role": "assistant", "content": "main-answer"},
        ]})
        _commit_file(tmp_path, "data.jsonl", row_m + "\n", "main change")

        r = runner.invoke(app, ["merge", "feature"])
        assert r.exit_code != 0
        assert "conflict" in r.output.lower()

        # Resolve with completely different content
        resolution = _row("totally-new-resolution") + "\n"
        (tmp_path / "data.jsonl").write_text(resolution)
        runner.invoke(app, ["add", "data.jsonl"], catch_exceptions=False)
        r = runner.invoke(app, ["merge", "--continue"], catch_exceptions=False)
        assert r.exit_code == 0
        assert "totally-new-resolution" in _read_file(tmp_path, "data.jsonl")

    def test_checkout_with_staged_changes_fails(self, tmp_path):
        """#6: Checkout with staged changes should fail and preserve staging."""
        _init_and_commit(tmp_path)
        runner.invoke(app, ["branch", "other"], catch_exceptions=False)

        (tmp_path / "staged.jsonl").write_text(_row("staged-data") + "\n")
        runner.invoke(app, ["add", "staged.jsonl"], catch_exceptions=False)

        r = runner.invoke(app, ["checkout", "other"])
        assert r.exit_code != 0
        assert "staging area is not empty" in r.output.lower()

        # Verify we're still on main
        head = (tmp_path / ".dit" / "HEAD").read_text().strip()
        assert head == "ref:main"

        # Verify staging area still has the file
        r = runner.invoke(app, ["status"], catch_exceptions=False)
        assert "staged.jsonl" in r.output


# ── Unusual inputs ─────────────────────────────────────────────────


class TestUnusualInputs:
    """Edge-case file contents, names, and paths."""

    def test_empty_jsonl_file(self, tmp_path):
        """#7: File with 0 rows (empty / whitespace only)."""
        _init(tmp_path)

        (tmp_path / "empty.jsonl").write_text("")
        r = runner.invoke(app, ["add", "empty.jsonl"], catch_exceptions=False)
        assert r.exit_code == 0
        assert "0 rows" in r.output

        # Also test whitespace-only
        (tmp_path / "whitespace.jsonl").write_text("   \n\n  \n")
        r = runner.invoke(app, ["add", "whitespace.jsonl"], catch_exceptions=False)
        assert r.exit_code == 0
        assert "0 rows" in r.output

    def test_extremely_long_row(self, tmp_path):
        """#8: Single row with 100KB+ content — add, commit, verify round-trip."""
        _init(tmp_path)

        big_content = "x" * 100_000
        row = json.dumps({"messages": [
            {"role": "user", "content": big_content},
        ]})
        (tmp_path / "big.jsonl").write_text(row + "\n")
        r = runner.invoke(app, ["add", "big.jsonl"], catch_exceptions=False)
        assert r.exit_code == 0
        r = runner.invoke(app, ["commit", "-m", "big row"], catch_exceptions=False)
        assert r.exit_code == 0

        # Verify round-trip by checking out
        runner.invoke(app, ["checkout", "-b", "tmp"], catch_exceptions=False)
        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)
        recovered = _read_file(tmp_path, "big.jsonl")
        parsed = json.loads(recovered.strip())
        assert len(parsed["messages"][0]["content"]) == 100_000

    def test_filename_with_dots_dashes_underscores(self, tmp_path):
        """#9: Filename like my-data_v2.0.train.jsonl — full lifecycle."""
        _init(tmp_path)
        fname = "my-data_v2.0.train.jsonl"

        (tmp_path / fname).write_text(_row("special-name") + "\n")
        runner.invoke(app, ["add", fname], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "special name"], catch_exceptions=False)

        runner.invoke(app, ["checkout", "-b", "other"], catch_exceptions=False)
        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)

        assert (tmp_path / fname).exists()
        assert "special-name" in _read_file(tmp_path, fname)
        flat = _head_flat(tmp_path)
        assert fname in flat

    def test_deeply_nested_path(self, tmp_path):
        """#10: Deeply nested path a/b/c/d/e/f/g/data.jsonl."""
        _init(tmp_path)
        nested = "a/b/c/d/e/f/g/data.jsonl"
        p = tmp_path / nested
        p.parent.mkdir(parents=True)
        p.write_text(_row("deep") + "\n")

        runner.invoke(app, ["add", nested], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "deep"], catch_exceptions=False)

        # Checkout to another branch and back to verify materialization
        runner.invoke(app, ["checkout", "-b", "other"], catch_exceptions=False)
        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)

        assert (tmp_path / nested).exists()
        assert "deep" in _read_file(tmp_path, nested)


# ── Data integrity under stress ────────────────────────────────────


class TestDataIntegrity:
    """Stress tests for data correctness."""

    def test_modify_every_row(self, tmp_path):
        """#11: Modify every row in a 100-row file, commit, verify all changed."""
        _init(tmp_path)

        original = _multi_row(*(f"original-{i}" for i in range(100)))
        (tmp_path / "data.jsonl").write_text(original)
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "100 rows"], catch_exceptions=False)

        modified = _multi_row(*(f"modified-{i}" for i in range(100)))
        (tmp_path / "data.jsonl").write_text(modified)
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "all modified"], catch_exceptions=False)

        content = _read_file(tmp_path, "data.jsonl")
        for i in range(100):
            assert f"modified-{i}" in content
            assert f"original-{i}" not in content

    def test_add_20_files_at_once(self, tmp_path):
        """#12: Add 20 files in a single dit add call, commit, verify all."""
        _init(tmp_path)

        fnames = [f"file_{i:02d}.jsonl" for i in range(20)]
        for fname in fnames:
            (tmp_path / fname).write_text(_row(f"content-{fname}") + "\n")

        r = runner.invoke(app, ["add"] + fnames, catch_exceptions=False)
        assert r.exit_code == 0
        r = runner.invoke(app, ["commit", "-m", "20 files"], catch_exceptions=False)
        assert r.exit_code == 0

        flat = _head_flat(tmp_path)
        for fname in fnames:
            assert fname in flat, f"{fname} missing from commit tree"

    def test_delete_half_the_files(self, tmp_path):
        """#13: Delete half the files, commit, verify deleted gone and rest intact."""
        files = {f"f{i}.jsonl": _row(f"data-{i}") + "\n" for i in range(10)}
        _init_and_commit(tmp_path, files)

        to_delete = [f"f{i}.jsonl" for i in range(5)]
        to_keep = [f"f{i}.jsonl" for i in range(5, 10)]

        for fname in to_delete:
            (tmp_path / fname).unlink()
        for fname in to_delete:
            runner.invoke(app, ["add", fname], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "delete half"], catch_exceptions=False)

        flat = _head_flat(tmp_path)
        for fname in to_delete:
            assert fname not in flat
        for fname in to_keep:
            assert fname in flat
            assert f"data-{int(fname[1])}" in _read_file(tmp_path, fname)

    def test_corrupt_row_object_checkout_fails(self, tmp_path):
        """#14: Manually corrupt a row object, then checkout — should fail
        gracefully, not silently produce wrong data.

        We need two branches with *different* content for data.jsonl so that
        checkout is forced to re-materialize the file (the optimization skips
        files whose manifest hash hasn't changed).
        """
        _init_and_commit(tmp_path, {"data.jsonl": _row("main-v1") + "\n"})

        # Create a second branch with different content
        runner.invoke(app, ["checkout", "-b", "other"], catch_exceptions=False)
        _commit_file(tmp_path, "data.jsonl", _row("other-content") + "\n", "other")

        # Go back to main and corrupt the row object for main's data.jsonl
        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)
        dot = tmp_path / ".dit"
        store = ObjectStore(dot / "objects")
        refs = RefStore(dot)
        head = refs.resolve_head()
        commit = deserialize_commit(store.read("commits", head))
        flat = flatten_tree(store, commit.tree_hash)

        for path, (obj_type, obj_hash, _sc) in flat.items():
            if obj_type == "manifest":
                m = deserialize_manifest(store.read("manifests", obj_hash))
                row_hash = m.entries[0].row_hash
                break

        # Delete the row object file
        row_path = store._object_path("rows", row_hash)
        row_path.unlink()

        # Now checkout to other (which has different data.jsonl), then back
        # to main — this forces materialization of main's data.jsonl whose
        # row object is now missing.
        runner.invoke(app, ["checkout", "other"], catch_exceptions=False)
        r = runner.invoke(app, ["checkout", "main"])
        # BUG NOTE: checkout raises KeyError("Row <hash> not found in store")
        # which typer catches as exit code 1. This is acceptable — the key
        # thing is it does NOT silently succeed with wrong data.
        assert r.exit_code != 0


# ── Command ordering edge cases ────────────────────────────────────


class TestCommandOrdering:
    """Commands run in wrong order or without prerequisites."""

    def test_status_before_init(self, tmp_path):
        """#15: dit status in a non-repo directory should fail gracefully."""
        os.chdir(tmp_path)
        r = runner.invoke(app, ["status"])
        assert r.exit_code != 0
        assert "not a dit repository" in r.output.lower()

    def test_commit_before_add(self, tmp_path):
        """#16: dit commit with nothing staged should fail."""
        _init(tmp_path)
        r = runner.invoke(app, ["commit", "-m", "nothing"])
        assert r.exit_code != 0

    def test_push_without_remote(self, tmp_path):
        """#17: dit push without a configured remote should fail with clear error."""
        _init_and_commit(tmp_path)
        r = runner.invoke(app, ["push"])
        assert r.exit_code != 0
        assert "remote" in r.output.lower()

    def test_diff_with_no_commits(self, tmp_path):
        """#18: dit diff with no commits — should handle gracefully."""
        _init(tmp_path)
        (tmp_path / "data.jsonl").write_text(_row("new") + "\n")
        r = runner.invoke(app, ["diff"])
        # Should succeed and show the new file, or show nothing — not crash
        assert r.exit_code == 0

    def test_log_with_no_commits(self, tmp_path):
        """#19: dit log with no commits — should handle gracefully."""
        _init(tmp_path)
        r = runner.invoke(app, ["log"])
        # Should succeed with empty output or a message — not crash
        assert r.exit_code == 0

    def test_double_init(self, tmp_path):
        """#20: dit init twice — should be idempotent or warn, not crash."""
        os.chdir(tmp_path)
        r1 = runner.invoke(app, ["init"], catch_exceptions=False)
        assert r1.exit_code == 0
        r2 = runner.invoke(app, ["init"], catch_exceptions=False)
        assert r2.exit_code == 0
        assert "already" in r2.output.lower()
        # Repo should still be functional
        (tmp_path / "data.jsonl").write_text(_row("after-double-init") + "\n")
        r = runner.invoke(app, ["add", "."], catch_exceptions=False)
        assert r.exit_code == 0
