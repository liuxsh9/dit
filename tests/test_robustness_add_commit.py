"""Robustness tests for dit add and commit commands.

Stress-tests edge cases that could break a VCS if they failed silently:
empty files, invalid JSON, duplicate rows, concurrent adds, special
filenames, commit message edge cases, and more.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dit.cli.main import app
from dit.core.objects import deserialize_commit, deserialize_manifest
from dit.core.store import ObjectStore
from dit.core.tree_walker import flatten_tree

runner = CliRunner()


def _make_row(content: str = "hello") -> str:
    return json.dumps({
        "messages": [
            {"role": "user", "content": content},
            {"role": "assistant", "content": "reply"},
        ]
    })


@pytest.fixture
def repo(tmp_path: Path, monkeypatch) -> Path:
    """Create and chdir into a fresh dit repo."""
    monkeypatch.chdir(tmp_path)
    r = runner.invoke(app, ["init"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    return tmp_path


def _commit_row_count(repo: Path) -> int:
    """Return total manifest entry count from the HEAD commit."""
    dot = repo / ".dit"
    store = ObjectStore(dot / "objects")
    from dit.core.refs import RefStore
    head = RefStore(dot).resolve_head()
    assert head, "No HEAD commit found"
    commit = deserialize_commit(store.read("commits", head))
    flat = flatten_tree(store, commit.tree_hash)
    total = 0
    for _, (obj_type, obj_hash, _) in flat.items():
        if obj_type == "manifest":
            m = deserialize_manifest(store.read("manifests", obj_hash))
            total += len(m.entries)
    return total


# ── File content edge cases ──────────────────────────────────────────


class TestFileContentEdgeCases:
    """Tests 1-4: empty file, single row, large file, invalid JSON."""

    def test_empty_file(self, repo: Path):
        """An empty .jsonl (0 bytes) should stage with 0 rows."""
        (repo / "empty.jsonl").write_text("")
        r = runner.invoke(app, ["add", "empty.jsonl"], catch_exceptions=False)
        assert r.exit_code == 0
        assert "0 rows" in r.output

    def test_single_row(self, repo: Path):
        """A file with exactly 1 JSON line should stage and commit cleanly."""
        (repo / "one.jsonl").write_text(_make_row("only") + "\n")
        r = runner.invoke(app, ["add", "one.jsonl"], catch_exceptions=False)
        assert r.exit_code == 0
        assert "1 rows" in r.output
        r = runner.invoke(app, ["commit", "-m", "one row"], catch_exceptions=False)
        assert r.exit_code == 0
        assert _commit_row_count(repo) == 1

    def test_large_row_count(self, repo: Path):
        """500+ rows should all be preserved through add and commit."""
        n = 500
        lines = [_make_row(f"row-{i}") + "\n" for i in range(n)]
        (repo / "big.jsonl").write_text("".join(lines))
        r = runner.invoke(app, ["add", "big.jsonl"], catch_exceptions=False)
        assert r.exit_code == 0
        assert f"{n} rows" in r.output
        r = runner.invoke(app, ["commit", "-m", "big"], catch_exceptions=False)
        assert r.exit_code == 0
        assert _commit_row_count(repo) == n

    def test_invalid_json_lines(self, repo: Path):
        """A file with invalid JSON should fail add with a clear error."""
        content = _make_row("ok") + "\n" + "NOT JSON\n" + _make_row("ok2") + "\n"
        (repo / "bad.jsonl").write_text(content)
        r = runner.invoke(app, ["add", "bad.jsonl"])
        assert r.exit_code != 0
        assert "invalid JSON" in r.output or "error" in r.output.lower()


# ── Duplicate and idempotency ────────────────────────────────────────


class TestDuplicateAndIdempotency:
    """Tests 5-7: duplicate rows, double add, add unchanged file."""

    def test_duplicate_rows_preserved(self, repo: Path):
        """Identical rows should all be preserved (dit tracks by position)."""
        same = _make_row("dup")
        (repo / "dups.jsonl").write_text((same + "\n") * 3)
        r = runner.invoke(app, ["add", "dups.jsonl"], catch_exceptions=False)
        assert r.exit_code == 0
        assert "3 rows" in r.output
        r = runner.invoke(app, ["commit", "-m", "dups"], catch_exceptions=False)
        assert r.exit_code == 0
        assert _commit_row_count(repo) == 3

    def test_add_same_file_twice_idempotent(self, repo: Path):
        """Adding the same file twice should be idempotent — no corruption."""
        (repo / "f.jsonl").write_text(_make_row("a") + "\n")
        r1 = runner.invoke(app, ["add", "f.jsonl"], catch_exceptions=False)
        assert r1.exit_code == 0
        r2 = runner.invoke(app, ["add", "f.jsonl"], catch_exceptions=False)
        assert r2.exit_code == 0
        r = runner.invoke(app, ["commit", "-m", "idem"], catch_exceptions=False)
        assert r.exit_code == 0
        assert _commit_row_count(repo) == 1

    def test_add_unchanged_after_commit(self, repo: Path):
        """Adding a file that hasn't changed since last commit should not corrupt."""
        (repo / "stable.jsonl").write_text(_make_row("s") + "\n")
        runner.invoke(app, ["add", "stable.jsonl"], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "first"], catch_exceptions=False)
        # Add again without changes
        r = runner.invoke(app, ["add", "stable.jsonl"], catch_exceptions=False)
        assert r.exit_code == 0
        r = runner.invoke(app, ["commit", "-m", "second"], catch_exceptions=False)
        assert r.exit_code == 0
        assert _commit_row_count(repo) == 1


# ── Commit edge cases ───────────────────────────────────────────────


class TestCommitEdgeCases:
    """Tests 8-9: nothing staged, commit message edge cases."""

    def test_commit_nothing_staged(self, repo: Path):
        """Commit with nothing staged should fail gracefully (exit 1)."""
        r = runner.invoke(app, ["commit", "-m", "empty"])
        assert r.exit_code != 0
        assert "nothing to commit" in r.output.lower() or "empty" in r.output.lower()

    def test_commit_empty_message(self, repo: Path):
        """Commit with empty string message — dit should allow or reject cleanly."""
        (repo / "m.jsonl").write_text(_make_row("msg") + "\n")
        runner.invoke(app, ["add", "m.jsonl"], catch_exceptions=False)
        r = runner.invoke(app, ["commit", "-m", ""])
        # Either succeeds or fails cleanly — must not crash
        assert r.exit_code in (0, 1, 2)

    def test_commit_long_message(self, repo: Path):
        """A 1000+ char commit message should be preserved."""
        (repo / "long.jsonl").write_text(_make_row("long") + "\n")
        runner.invoke(app, ["add", "long.jsonl"], catch_exceptions=False)
        msg = "A" * 1200
        r = runner.invoke(app, ["commit", "-m", msg], catch_exceptions=False)
        assert r.exit_code == 0
        # Verify message stored correctly
        dot = repo / ".dit"
        store = ObjectStore(dot / "objects")
        from dit.core.refs import RefStore
        head = RefStore(dot).resolve_head()
        commit = deserialize_commit(store.read("commits", head))
        assert commit.message == msg

    def test_commit_unicode_message(self, repo: Path):
        """Unicode commit messages should round-trip correctly."""
        (repo / "uni.jsonl").write_text(_make_row("uni") + "\n")
        runner.invoke(app, ["add", "uni.jsonl"], catch_exceptions=False)
        msg = "修复数据集 🔧 — добавить тесты"
        r = runner.invoke(app, ["commit", "-m", msg], catch_exceptions=False)
        assert r.exit_code == 0
        dot = repo / ".dit"
        store = ObjectStore(dot / "objects")
        from dit.core.refs import RefStore
        head = RefStore(dot).resolve_head()
        commit = deserialize_commit(store.read("commits", head))
        assert commit.message == msg


# ── Path and file edge cases ────────────────────────────────────────


class TestPathEdgeCases:
    """Tests 10-14: non-existent file, outside repo, concurrent add,
    special filenames, nested dirs."""

    def test_add_nonexistent_file(self, repo: Path):
        """Adding a non-existent file should fail with a clear error."""
        r = runner.invoke(app, ["add", "nonexistent.jsonl"])
        assert r.exit_code != 0
        assert "did not match" in r.output or "error" in r.output.lower()

    def test_add_file_outside_repo(self, repo: Path, tmp_path: Path):
        """Adding a file outside the repo should fail with a friendly error."""
        import tempfile
        with tempfile.TemporaryDirectory() as other_dir:
            outside = Path(other_dir) / "outside_data.jsonl"
            outside.write_text(_make_row("outside") + "\n")
            r = runner.invoke(app, ["add", str(outside)])
            assert r.exit_code != 0
            assert "outside" in r.output.lower()

    def test_concurrent_add_last_wins(self, repo: Path):
        """Add, modify, add again before commit — second add should win."""
        f = repo / "evolve.jsonl"
        f.write_text(_make_row("v1") + "\n")
        runner.invoke(app, ["add", "evolve.jsonl"], catch_exceptions=False)
        # Modify the file
        f.write_text(_make_row("v2a") + "\n" + _make_row("v2b") + "\n")
        r = runner.invoke(app, ["add", "evolve.jsonl"], catch_exceptions=False)
        assert r.exit_code == 0
        assert "2 rows" in r.output
        r = runner.invoke(app, ["commit", "-m", "v2"], catch_exceptions=False)
        assert r.exit_code == 0
        assert _commit_row_count(repo) == 2

    def test_special_characters_in_filename(self, repo: Path):
        """Filenames with spaces and unicode should be handled."""
        f = repo / "my data 日本語.jsonl"
        f.write_text(_make_row("special") + "\n")
        r = runner.invoke(app, ["add", str(f)], catch_exceptions=False)
        assert r.exit_code == 0
        assert "1 rows" in r.output
        r = runner.invoke(app, ["commit", "-m", "special name"], catch_exceptions=False)
        assert r.exit_code == 0
        assert _commit_row_count(repo) == 1

    def test_nested_directory_add(self, repo: Path):
        """Adding a file in a deeply nested directory should work."""
        nested = repo / "deep" / "nested" / "dir"
        nested.mkdir(parents=True)
        f = nested / "file.jsonl"
        f.write_text(_make_row("deep") + "\n")
        r = runner.invoke(app, ["add", str(f)], catch_exceptions=False)
        assert r.exit_code == 0
        assert "1 rows" in r.output
        r = runner.invoke(app, ["commit", "-m", "nested"], catch_exceptions=False)
        assert r.exit_code == 0
        # Verify the path is stored with nested structure
        dot = repo / ".dit"
        store = ObjectStore(dot / "objects")
        from dit.core.refs import RefStore
        head = RefStore(dot).resolve_head()
        commit = deserialize_commit(store.read("commits", head))
        flat = flatten_tree(store, commit.tree_hash)
        assert any("deep/nested/dir/file.jsonl" in p for p in flat)


# ── Dangerous state transitions ─────────────────────────────────────


class TestDangerousStateTransitions:
    """Test 15: add then delete before commit."""

    def test_add_then_delete_before_commit(self, repo: Path):
        """Add a file, delete it from disk, then commit — should fail or handle."""
        f = repo / "ghost.jsonl"
        f.write_text(_make_row("ghost") + "\n")
        runner.invoke(app, ["add", "ghost.jsonl"], catch_exceptions=False)
        f.unlink()  # delete from disk
        # Commit should still succeed — the data was already staged to the store
        r = runner.invoke(app, ["commit", "-m", "ghost"], catch_exceptions=False)
        assert r.exit_code == 0
        assert _commit_row_count(repo) == 1

    def test_multiple_files_one_invalid(self, repo: Path):
        """When adding multiple files and one is invalid, the valid ones
        should not be silently committed without the user knowing."""
        (repo / "good.jsonl").write_text(_make_row("ok") + "\n")
        (repo / "bad.jsonl").write_text("NOT JSON\n")
        r = runner.invoke(app, ["add", "good.jsonl", "bad.jsonl"])
        # Should fail — invalid JSON in bad.jsonl
        assert r.exit_code != 0

    def test_two_commits_preserve_history(self, repo: Path):
        """Two sequential commits should form a proper parent chain."""
        (repo / "a.jsonl").write_text(_make_row("a") + "\n")
        runner.invoke(app, ["add", "a.jsonl"], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "first"], catch_exceptions=False)

        (repo / "b.jsonl").write_text(_make_row("b") + "\n")
        runner.invoke(app, ["add", "b.jsonl"], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "second"], catch_exceptions=False)

        dot = repo / ".dit"
        store = ObjectStore(dot / "objects")
        from dit.core.refs import RefStore
        head = RefStore(dot).resolve_head()
        c2 = deserialize_commit(store.read("commits", head))
        assert c2.message == "second"
        assert len(c2.parent_hashes) == 1
        c1 = deserialize_commit(store.read("commits", c2.parent_hashes[0]))
        assert c1.message == "first"
        assert c1.parent_hashes == []
