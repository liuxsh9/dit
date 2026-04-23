import hashlib
import json
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ManifestEntry:
    row_hash: str
    query_fingerprint: Optional[str]


@dataclass
class Manifest:
    entries: list[ManifestEntry]


@dataclass(frozen=True)
class TreeEntry:
    name: str
    obj_type: str  # "manifest" or "tree"
    obj_hash: str


@dataclass
class Tree:
    entries: list[TreeEntry]


@dataclass
class Commit:
    tree_hash: str
    parent_hashes: list[str]
    author: str
    message: str
    timestamp: int


def serialize_manifest(m: Manifest) -> bytes:
    data = {
        "type": "manifest",
        "entries": [
            {"row_hash": e.row_hash, "query_fingerprint": e.query_fingerprint}
            for e in m.entries
        ],
    }
    return json.dumps(data, separators=(",", ":"), sort_keys=True).encode("utf-8")


def deserialize_manifest(data: bytes) -> Manifest:
    obj = json.loads(data)
    entries = [
        ManifestEntry(
            row_hash=e["row_hash"],
            query_fingerprint=e.get("query_fingerprint"),
        )
        for e in obj["entries"]
    ]
    return Manifest(entries=entries)


def serialize_tree(t: Tree) -> bytes:
    sorted_entries = sorted(t.entries, key=lambda e: e.name)
    data = {
        "type": "tree",
        "entries": [
            {"name": e.name, "obj_type": e.obj_type, "obj_hash": e.obj_hash}
            for e in sorted_entries
        ],
    }
    return json.dumps(data, separators=(",", ":"), sort_keys=True).encode("utf-8")


def deserialize_tree(data: bytes) -> Tree:
    obj = json.loads(data)
    entries = [
        TreeEntry(name=e["name"], obj_type=e["obj_type"], obj_hash=e["obj_hash"])
        for e in obj["entries"]
    ]
    return Tree(entries=entries)


def serialize_commit(c: Commit) -> bytes:
    data = {
        "type": "commit",
        "tree_hash": c.tree_hash,
        "parent_hashes": c.parent_hashes,
        "author": c.author,
        "message": c.message,
        "timestamp": c.timestamp,
    }
    return json.dumps(data, separators=(",", ":"), sort_keys=True).encode("utf-8")


def deserialize_commit(data: bytes) -> Commit:
    obj = json.loads(data)
    return Commit(
        tree_hash=obj["tree_hash"],
        parent_hashes=obj["parent_hashes"],
        author=obj["author"],
        message=obj["message"],
        timestamp=obj["timestamp"],
    )


def object_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
