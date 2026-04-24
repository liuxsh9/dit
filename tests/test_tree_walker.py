# tests/test_tree_walker.py
import pytest
from dit.core.store import ObjectStore
from dit.core.tree_builder import build_nested_tree
from dit.core.tree_walker import flatten_tree, resolve_path


class TestFlattenTree:
    def test_flat_repo(self, tmp_path):
        store = ObjectStore(tmp_path / "objects")
        staged = {
            "a.jsonl": ("manifest", "a" * 64),
            "b.jsonl": ("manifest", "b" * 64),
        }
        tree_hash = build_nested_tree(store, staged)
        result = flatten_tree(store, tree_hash)
        assert result == {
            "a.jsonl": ("manifest", "a" * 64),
            "b.jsonl": ("manifest", "b" * 64),
        }

    def test_nested_repo(self, tmp_path):
        store = ObjectStore(tmp_path / "objects")
        staged = {
            "train/sft.jsonl": ("manifest", "a" * 64),
            "eval/bench.jsonl": ("manifest", "b" * 64),
            "README.md": ("blob", "c" * 64),
        }
        tree_hash = build_nested_tree(store, staged)
        result = flatten_tree(store, tree_hash)
        assert result == staged

    def test_deep_nesting(self, tmp_path):
        store = ObjectStore(tmp_path / "objects")
        staged = {"a/b/c.jsonl": ("manifest", "d" * 64)}
        tree_hash = build_nested_tree(store, staged)
        result = flatten_tree(store, tree_hash)
        assert result == staged

    def test_empty_tree(self, tmp_path):
        store = ObjectStore(tmp_path / "objects")
        tree_hash = build_nested_tree(store, {})
        result = flatten_tree(store, tree_hash)
        assert result == {}


class TestResolvePath:
    def test_root_path(self, tmp_path):
        store = ObjectStore(tmp_path / "objects")
        staged = {
            "train/sft.jsonl": ("manifest", "a" * 64),
            "README.md": ("blob", "c" * 64),
        }
        tree_hash = build_nested_tree(store, staged)
        entries = resolve_path(store, tree_hash, "")
        names = {e["name"] for e in entries}
        assert "train" in names
        assert "README.md" in names

    def test_subdir_path(self, tmp_path):
        store = ObjectStore(tmp_path / "objects")
        staged = {"train/sft.jsonl": ("manifest", "a" * 64)}
        tree_hash = build_nested_tree(store, staged)
        entries = resolve_path(store, tree_hash, "train")
        assert len(entries) == 1
        assert entries[0]["name"] == "sft.jsonl"
        assert entries[0]["obj_type"] == "manifest"

    def test_missing_path_returns_none(self, tmp_path):
        store = ObjectStore(tmp_path / "objects")
        staged = {"a.jsonl": ("manifest", "a" * 64)}
        tree_hash = build_nested_tree(store, staged)
        result = resolve_path(store, tree_hash, "nonexistent")
        assert result is None
