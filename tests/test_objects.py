import json
import time

from dit.core.objects import (
    ManifestEntry,
    Manifest,
    SidecarEntry,
    Sidecar,
    TreeEntry,
    Tree,
    Commit,
    serialize_manifest,
    deserialize_manifest,
    serialize_sidecar,
    deserialize_sidecar,
    serialize_tree,
    deserialize_tree,
    serialize_commit,
    deserialize_commit,
    serialize_blob,
    deserialize_blob,
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


class TestBlob:
    def test_roundtrip_text(self):
        content = b"# README\n\nThis is a data repository.\n"
        data = serialize_blob(content)
        assert deserialize_blob(data) == content

    def test_roundtrip_binary(self):
        content = bytes(range(256))
        data = serialize_blob(content)
        assert deserialize_blob(data) == content

    def test_hash_deterministic(self):
        content = b"hello world"
        data1 = serialize_blob(content)
        data2 = serialize_blob(content)
        assert object_hash(data1) == object_hash(data2)

    def test_different_content_different_hash(self):
        data1 = serialize_blob(b"foo")
        data2 = serialize_blob(b"bar")
        assert object_hash(data1) != object_hash(data2)


class TestSidecar:
    def _make_entry(self, row_hash="aa" * 32, char_count=100, token_estimate=25, field_count=3, lang="en"):
        return SidecarEntry(
            row_hash=row_hash,
            char_count=char_count,
            token_estimate=token_estimate,
            field_count=field_count,
            lang=lang,
        )

    def test_roundtrip_basic(self):
        entry = self._make_entry()
        s = Sidecar(manifest_hash="bb" * 32, entries=[entry])
        data = serialize_sidecar(s)
        s2 = deserialize_sidecar(data)
        assert s2.manifest_hash == "bb" * 32
        assert len(s2.entries) == 1
        e = s2.entries[0]
        assert e.row_hash == "aa" * 32
        assert e.char_count == 100
        assert e.token_estimate == 25
        assert e.field_count == 3
        assert e.lang == "en"

    def test_roundtrip_lang_none(self):
        entry = self._make_entry(lang=None)
        s = Sidecar(manifest_hash="cc" * 32, entries=[entry])
        data = serialize_sidecar(s)
        s2 = deserialize_sidecar(data)
        assert s2.entries[0].lang is None

    def test_roundtrip_empty_entries(self):
        s = Sidecar(manifest_hash="dd" * 32, entries=[])
        data = serialize_sidecar(s)
        s2 = deserialize_sidecar(data)
        assert s2.manifest_hash == "dd" * 32
        assert s2.entries == []

    def test_serialize_type_field(self):
        s = Sidecar(manifest_hash="ee" * 32, entries=[])
        data = serialize_sidecar(s)
        obj = json.loads(data)
        assert obj["type"] == "sidecar"

    def test_serialize_deterministic(self):
        entry = self._make_entry()
        s = Sidecar(manifest_hash="ff" * 32, entries=[entry])
        assert serialize_sidecar(s) == serialize_sidecar(s)

    def test_serialize_entry_key_order(self):
        entry = self._make_entry()
        s = Sidecar(manifest_hash="11" * 32, entries=[entry])
        data = serialize_sidecar(s)
        obj = json.loads(data)
        keys = list(obj["entries"][0].keys())
        assert keys == sorted(keys)


class TestTreeEntryWithSidecar:
    def test_sidecar_hash_defaults_to_none(self):
        e = TreeEntry(name="data.jsonl", obj_type="manifest", obj_hash="aa" * 32)
        assert e.sidecar_hash is None

    def test_sidecar_hash_can_be_set(self):
        e = TreeEntry(name="data.jsonl", obj_type="manifest", obj_hash="aa" * 32, sidecar_hash="bb" * 32)
        assert e.sidecar_hash == "bb" * 32

    def test_tree_entry_frozen(self):
        e = TreeEntry(name="data.jsonl", obj_type="manifest", obj_hash="aa" * 32)
        try:
            e.sidecar_hash = "cc" * 32  # type: ignore[misc]
            assert False, "should have raised"
        except Exception:
            pass


class TestSerializeTreeSidecar:
    def test_sidecar_hash_omitted_when_none(self):
        t = Tree(entries=[
            TreeEntry(name="data.jsonl", obj_type="manifest", obj_hash="aa" * 32),
        ])
        data = serialize_tree(t)
        obj = json.loads(data)
        assert "sidecar_hash" not in obj["entries"][0]

    def test_sidecar_hash_included_when_set(self):
        t = Tree(entries=[
            TreeEntry(name="data.jsonl", obj_type="manifest", obj_hash="aa" * 32, sidecar_hash="bb" * 32),
        ])
        data = serialize_tree(t)
        obj = json.loads(data)
        assert obj["entries"][0]["sidecar_hash"] == "bb" * 32

    def test_mixed_entries_sidecar_selectively_included(self):
        t = Tree(entries=[
            TreeEntry(name="a.jsonl", obj_type="manifest", obj_hash="aa" * 32, sidecar_hash="cc" * 32),
            TreeEntry(name="b.jsonl", obj_type="manifest", obj_hash="bb" * 32),
        ])
        data = serialize_tree(t)
        obj = json.loads(data)
        entries = {e["name"]: e for e in obj["entries"]}
        assert "sidecar_hash" in entries["a.jsonl"]
        assert "sidecar_hash" not in entries["b.jsonl"]
