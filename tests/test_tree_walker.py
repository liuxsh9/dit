# tests/test_tree_walker.py
from dit.core.store import ObjectStore
from dit.core.tree_builder import build_nested_tree
from dit.core.tree_walker import flatten_tree, resolve_path
from dit.core.objects import Tree, TreeEntry, serialize_tree


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
            "a.jsonl": ("manifest", "a" * 64, None),
            "b.jsonl": ("manifest", "b" * 64, None),
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
        assert result == {
            "train/sft.jsonl": ("manifest", "a" * 64, None),
            "eval/bench.jsonl": ("manifest", "b" * 64, None),
            "README.md": ("blob", "c" * 64, None),
        }

    def test_deep_nesting(self, tmp_path):
        store = ObjectStore(tmp_path / "objects")
        staged = {"a/b/c.jsonl": ("manifest", "d" * 64)}
        tree_hash = build_nested_tree(store, staged)
        result = flatten_tree(store, tree_hash)
        assert result == {"a/b/c.jsonl": ("manifest", "d" * 64, None)}

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


def _make_store(tmp_path):
    return ObjectStore(tmp_path / "objects")


def _store_tree(store, entries):
    tree = Tree(entries=entries)
    data = serialize_tree(tree)
    return store.write("trees", data)


class TestFlattenTreeSidecar:
    def test_returns_3tuple_with_sidecar_hash(self, tmp_path):
        store = _make_store(tmp_path)
        sidecar_hash = "sc" * 32
        tree_hash = _store_tree(store, [
            TreeEntry(name="train.jsonl", obj_type="manifest", obj_hash="aa" * 32, sidecar_hash=sidecar_hash),
        ])
        result = flatten_tree(store, tree_hash)
        assert "train.jsonl" in result
        obj_type, obj_hash, sc_hash = result["train.jsonl"]
        assert obj_type == "manifest"
        assert obj_hash == "aa" * 32
        assert sc_hash == sidecar_hash

    def test_returns_none_sidecar_when_not_set(self, tmp_path):
        store = _make_store(tmp_path)
        tree_hash = _store_tree(store, [
            TreeEntry(name="eval.jsonl", obj_type="manifest", obj_hash="bb" * 32),
        ])
        result = flatten_tree(store, tree_hash)
        obj_type, obj_hash, sc_hash = result["eval.jsonl"]
        assert sc_hash is None

    def test_nested_tree_preserves_sidecar_hash(self, tmp_path):
        store = _make_store(tmp_path)
        sidecar_hash = "33" * 32
        inner_tree_hash = _store_tree(store, [
            TreeEntry(name="deep.jsonl", obj_type="manifest", obj_hash="44" * 32, sidecar_hash=sidecar_hash),
        ])
        root_hash = _store_tree(store, [
            TreeEntry(name="subdir", obj_type="tree", obj_hash=inner_tree_hash),
        ])
        result = flatten_tree(store, root_hash)
        assert "subdir/deep.jsonl" in result
        _, _, sc_hash = result["subdir/deep.jsonl"]
        assert sc_hash == sidecar_hash

    def test_mixed_entries_correct_sidecar(self, tmp_path):
        store = _make_store(tmp_path)
        sc = "55" * 32
        tree_hash = _store_tree(store, [
            TreeEntry(name="with.jsonl", obj_type="manifest", obj_hash="66" * 32, sidecar_hash=sc),
            TreeEntry(name="without.jsonl", obj_type="manifest", obj_hash="77" * 32),
        ])
        result = flatten_tree(store, tree_hash)
        _, _, sc1 = result["with.jsonl"]
        _, _, sc2 = result["without.jsonl"]
        assert sc1 == sc
        assert sc2 is None

    def test_return_type_is_3tuple(self, tmp_path):
        store = _make_store(tmp_path)
        tree_hash = _store_tree(store, [
            TreeEntry(name="x.jsonl", obj_type="manifest", obj_hash="88" * 32),
        ])
        result = flatten_tree(store, tree_hash)
        value = result["x.jsonl"]
        assert len(value) == 3
