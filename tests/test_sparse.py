"""Unit tests for dit.core.sparse module."""
from __future__ import annotations

from pathlib import Path

import pytest

from dit.core.sparse import is_sparse, load_sparse_paths, save_sparse_paths, is_in_sparse_set


class TestIsSparse:
    def test_not_sparse_when_no_file(self, tmp_path: Path) -> None:
        dot = tmp_path / ".dit"
        dot.mkdir()
        assert is_sparse(dot) is False

    def test_sparse_when_file_exists(self, tmp_path: Path) -> None:
        dot = tmp_path / ".dit"
        dot.mkdir()
        (dot / "sparse-checkout").write_text("")
        assert is_sparse(dot) is True

    def test_sparse_when_file_has_content(self, tmp_path: Path) -> None:
        dot = tmp_path / ".dit"
        dot.mkdir()
        (dot / "sparse-checkout").write_text("bug-fix/train.jsonl\n")
        assert is_sparse(dot) is True


class TestLoadSparsePaths:
    def test_returns_none_when_not_sparse(self, tmp_path: Path) -> None:
        dot = tmp_path / ".dit"
        dot.mkdir()
        assert load_sparse_paths(dot) is None

    def test_returns_empty_set_for_empty_file(self, tmp_path: Path) -> None:
        dot = tmp_path / ".dit"
        dot.mkdir()
        (dot / "sparse-checkout").write_text("")
        assert load_sparse_paths(dot) == set()

    def test_loads_file_paths(self, tmp_path: Path) -> None:
        dot = tmp_path / ".dit"
        dot.mkdir()
        (dot / "sparse-checkout").write_text("bug-fix/train.jsonl\ngeneral/eval.jsonl\n")
        assert load_sparse_paths(dot) == {"bug-fix/train.jsonl", "general/eval.jsonl"}

    def test_loads_directory_paths(self, tmp_path: Path) -> None:
        dot = tmp_path / ".dit"
        dot.mkdir()
        (dot / "sparse-checkout").write_text("bug-fix/\n")
        assert load_sparse_paths(dot) == {"bug-fix/"}

    def test_ignores_blank_lines_and_comments(self, tmp_path: Path) -> None:
        dot = tmp_path / ".dit"
        dot.mkdir()
        (dot / "sparse-checkout").write_text("# fetched files\nbug-fix/train.jsonl\n\n  \n")
        assert load_sparse_paths(dot) == {"bug-fix/train.jsonl"}

    def test_strips_whitespace(self, tmp_path: Path) -> None:
        dot = tmp_path / ".dit"
        dot.mkdir()
        (dot / "sparse-checkout").write_text("  bug-fix/train.jsonl  \n")
        assert load_sparse_paths(dot) == {"bug-fix/train.jsonl"}


class TestSaveSparsePaths:
    def test_roundtrip(self, tmp_path: Path) -> None:
        dot = tmp_path / ".dit"
        dot.mkdir()
        paths = {"bug-fix/train.jsonl", "general/"}
        save_sparse_paths(dot, paths)
        assert load_sparse_paths(dot) == paths

    def test_creates_file(self, tmp_path: Path) -> None:
        dot = tmp_path / ".dit"
        dot.mkdir()
        save_sparse_paths(dot, {"a.jsonl"})
        assert (dot / "sparse-checkout").exists()
        assert is_sparse(dot) is True

    def test_overwrites_existing(self, tmp_path: Path) -> None:
        dot = tmp_path / ".dit"
        dot.mkdir()
        save_sparse_paths(dot, {"old.jsonl"})
        save_sparse_paths(dot, {"new.jsonl"})
        assert load_sparse_paths(dot) == {"new.jsonl"}

    def test_sorted_output(self, tmp_path: Path) -> None:
        dot = tmp_path / ".dit"
        dot.mkdir()
        save_sparse_paths(dot, {"z.jsonl", "a.jsonl", "m.jsonl"})
        lines = (dot / "sparse-checkout").read_text().strip().split("\n")
        assert lines == ["a.jsonl", "m.jsonl", "z.jsonl"]

    def test_empty_set(self, tmp_path: Path) -> None:
        dot = tmp_path / ".dit"
        dot.mkdir()
        save_sparse_paths(dot, set())
        assert is_sparse(dot) is True
        assert load_sparse_paths(dot) == set()


class TestIsInSparseSet:
    def test_exact_file_match(self) -> None:
        paths = {"bug-fix/train.jsonl", "general/eval.jsonl"}
        assert is_in_sparse_set("bug-fix/train.jsonl", paths) is True
        assert is_in_sparse_set("general/eval.jsonl", paths) is True

    def test_no_match(self) -> None:
        paths = {"bug-fix/train.jsonl"}
        assert is_in_sparse_set("general/train.jsonl", paths) is False

    def test_directory_prefix_match(self) -> None:
        paths = {"bug-fix/"}
        assert is_in_sparse_set("bug-fix/train.jsonl", paths) is True
        assert is_in_sparse_set("bug-fix/eval.jsonl", paths) is True
        assert is_in_sparse_set("bug-fix/sub/deep.jsonl", paths) is True

    def test_directory_prefix_no_false_positive(self) -> None:
        paths = {"bug/"}
        assert is_in_sparse_set("bug-fix/train.jsonl", paths) is False

    def test_mixed_file_and_dir(self) -> None:
        paths = {"general/", "bug-fix/train.jsonl"}
        assert is_in_sparse_set("general/train.jsonl", paths) is True
        assert is_in_sparse_set("bug-fix/train.jsonl", paths) is True
        assert is_in_sparse_set("bug-fix/eval.jsonl", paths) is False

    def test_empty_sparse_set(self) -> None:
        assert is_in_sparse_set("anything.jsonl", set()) is False

    def test_root_level_file(self) -> None:
        paths = {"train.jsonl"}
        assert is_in_sparse_set("train.jsonl", paths) is True
        assert is_in_sparse_set("eval.jsonl", paths) is False
