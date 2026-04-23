import json
import time

from dit.core.objects import (
    ManifestEntry,
    Manifest,
    TreeEntry,
    Tree,
    Commit,
    serialize_manifest,
    deserialize_manifest,
    serialize_tree,
    deserialize_tree,
    serialize_commit,
    deserialize_commit,
    object_hash,
)


class TestManifest:
    def test_roundtrip(self):
        entries = [
            ManifestEntry(row_hash="aa" * 32, query_fingerprint="bb" * 32),
            ManifestEntry(row_hash="cc" * 32, query_fingerprint=None),
        ]
        m = Manifest(entries=entries)
        data = serialize_manifest(m)
        m2 = deserialize_manifest(data)
        assert len(m2.entries) == 2
        assert m2.entries[0].row_hash == "aa" * 32
        assert m2.entries[0].query_fingerprint == "bb" * 32
        assert m2.entries[1].query_fingerprint is None

    def test_preserves_order(self):
        hashes = [f"{i:064x}" for i in range(50)]
        entries = [ManifestEntry(row_hash=h, query_fingerprint=None) for h in hashes]
        m = Manifest(entries=entries)
        m2 = deserialize_manifest(serialize_manifest(m))
        assert [e.row_hash for e in m2.entries] == hashes

    def test_hash_deterministic(self):
        entries = [ManifestEntry(row_hash="aa" * 32, query_fingerprint=None)]
        m = Manifest(entries=entries)
        data = serialize_manifest(m)
        assert object_hash(data) == object_hash(data)


class TestTree:
    def test_roundtrip(self):
        t = Tree(entries=[
            TreeEntry(name="coding.jsonl", obj_type="manifest", obj_hash="aa" * 32),
            TreeEntry(name="subdir", obj_type="tree", obj_hash="bb" * 32),
        ])
        data = serialize_tree(t)
        t2 = deserialize_tree(data)
        assert len(t2.entries) == 2
        assert t2.entries[0].name == "coding.jsonl"
        assert t2.entries[0].obj_type == "manifest"
        assert t2.entries[1].obj_type == "tree"

    def test_sorted_by_name(self):
        t = Tree(entries=[
            TreeEntry(name="z.jsonl", obj_type="manifest", obj_hash="aa" * 32),
            TreeEntry(name="a.jsonl", obj_type="manifest", obj_hash="bb" * 32),
        ])
        data = serialize_tree(t)
        t2 = deserialize_tree(data)
        assert t2.entries[0].name == "a.jsonl"
        assert t2.entries[1].name == "z.jsonl"


class TestCommit:
    def test_roundtrip(self):
        c = Commit(
            tree_hash="aa" * 32,
            parent_hashes=[],
            author="zhangsan",
            message="initial commit",
            timestamp=1700000000,
        )
        data = serialize_commit(c)
        c2 = deserialize_commit(data)
        assert c2.tree_hash == "aa" * 32
        assert c2.parent_hashes == []
        assert c2.author == "zhangsan"
        assert c2.message == "initial commit"
        assert c2.timestamp == 1700000000

    def test_with_parent(self):
        c = Commit(
            tree_hash="aa" * 32,
            parent_hashes=["bb" * 32],
            author="lisi",
            message="second commit",
            timestamp=1700000001,
        )
        data = serialize_commit(c)
        c2 = deserialize_commit(data)
        assert c2.parent_hashes == ["bb" * 32]

    def test_hash_changes_with_content(self):
        c1 = Commit(tree_hash="aa" * 32, parent_hashes=[], author="a", message="m", timestamp=1)
        c2 = Commit(tree_hash="bb" * 32, parent_hashes=[], author="a", message="m", timestamp=1)
        assert object_hash(serialize_commit(c1)) != object_hash(serialize_commit(c2))
