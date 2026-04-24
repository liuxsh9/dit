# tests/test_tree_builder.py
import pytest
from pathlib import Path

from dit.core.store import ObjectStore
from dit.core.tree_builder import build_nested_tree
from dit.core.objects import deserialize_tree, deserialize_manifest


class TestBuildNestedTree:
    def test_flat_files(self, tmp_path):
        """Single-level files produce a flat root tree."""
        store = ObjectStore(tmp_path / "objects")
        # staged: rel_path -> (obj_type, obj_hash)
        staged = {
            "a.jsonl": ("manifest", "a" * 64),
            "b.jsonl": ("manifest", "b" * 64),
        }
        tree_hash = build_nested_tree(store, staged)
        data = store.read("trees", tree_hash)
        assert data is not None
        tree = deserialize_tree(data)
        names = {e.name for e in tree.entries}
        assert names == {"a.jsonl", "b.jsonl"}
        assert all(e.obj_type == "manifest" for e in tree.entries)

    def test_nested_files(self, tmp_path):
        """Files in subdirectories produce nested trees."""
        store = ObjectStore(tmp_path / "objects")
        staged = {
            "train/sft.jsonl": ("manifest", "a" * 64),
            "eval/bench.jsonl": ("manifest", "b" * 64),
            "README.md": ("blob", "c" * 64),
        }
        tree_hash = build_nested_tree(store, staged)
        data = store.read("trees", tree_hash)
        root = deserialize_tree(data)
        entry_map = {e.name: e for e in root.entries}
        assert "README.md" in entry_map
        assert entry_map["README.md"].obj_type == "blob"
        assert "train" in entry_map
        assert entry_map["train"].obj_type == "tree"
        assert "eval" in entry_map
        assert entry_map["eval"].obj_type == "tree"

        train_data = store.read("trees", entry_map["train"].obj_hash)
        assert train_data is not None
        train_tree = deserialize_tree(train_data)
        assert len(train_tree.entries) == 1
        assert train_tree.entries[0].name == "sft.jsonl"
        assert train_tree.entries[0].obj_type == "manifest"

    def test_deep_nesting(self, tmp_path):
        """Three-level nesting resolves correctly."""
        store = ObjectStore(tmp_path / "objects")
        staged = {
            "a/b/c.jsonl": ("manifest", "d" * 64),
        }
        tree_hash = build_nested_tree(store, staged)
        root = deserialize_tree(store.read("trees", tree_hash))
        assert len(root.entries) == 1
        assert root.entries[0].name == "a"
        assert root.entries[0].obj_type == "tree"

        a_tree = deserialize_tree(store.read("trees", root.entries[0].obj_hash))
        assert a_tree.entries[0].name == "b"
        assert a_tree.entries[0].obj_type == "tree"

        b_tree = deserialize_tree(store.read("trees", a_tree.entries[0].obj_hash))
        assert b_tree.entries[0].name == "c.jsonl"

    def test_deterministic_hash(self, tmp_path):
        """Same staged inputs always produce the same tree hash."""
        store = ObjectStore(tmp_path / "objects")
        staged = {
            "x.jsonl": ("manifest", "e" * 64),
            "sub/y.jsonl": ("manifest", "f" * 64),
        }
        h1 = build_nested_tree(store, staged)
        h2 = build_nested_tree(store, staged)
        assert h1 == h2

    def test_empty_staged(self, tmp_path):
        """Empty staged map produces an empty root tree."""
        store = ObjectStore(tmp_path / "objects")
        tree_hash = build_nested_tree(store, {})
        root = deserialize_tree(store.read("trees", tree_hash))
        assert root.entries == []
