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


@dataclass(frozen=True)
class SidecarEntry:
    row_hash: str
    char_count: int
    token_estimate: int
    field_count: int
    lang: Optional[str]


@dataclass(frozen=True)
class Sidecar:
    manifest_hash: str
    entries: list[SidecarEntry]


def serialize_sidecar(s: Sidecar) -> bytes:
    data = {
        "type": "sidecar",
        "manifest_hash": s.manifest_hash,
        "entries": [
            {
                "char_count": e.char_count,
                "field_count": e.field_count,
                "lang": e.lang,
                "row_hash": e.row_hash,
                "token_estimate": e.token_estimate,
            }
            for e in s.entries
        ],
    }
    return json.dumps(data, separators=(",", ":"), sort_keys=True).encode("utf-8")


def deserialize_sidecar(data: bytes) -> Sidecar:
    obj = json.loads(data)
    entries = [
        SidecarEntry(
            row_hash=e["row_hash"],
            char_count=e["char_count"],
            token_estimate=e["token_estimate"],
            field_count=e["field_count"],
            lang=e.get("lang"),
        )
        for e in obj["entries"]
    ]
    return Sidecar(manifest_hash=obj["manifest_hash"], entries=entries)


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


def serialize_blob(content: bytes) -> bytes:
    """Wrap raw blob content for storage in the object store.

    Uses a length-prefixed envelope: 8-byte big-endian length + raw bytes.
    """
    import struct
    return struct.pack(">Q", len(content)) + content


def deserialize_blob(data: bytes) -> bytes:
    """Extract raw blob content from store envelope."""
    import struct
    if len(data) < 8:
        raise ValueError("Blob data too short to contain length prefix")
    (length,) = struct.unpack(">Q", data[:8])
    payload = data[8:]
    if len(payload) != length:
        raise ValueError(f"Blob length mismatch: expected {length}, got {len(payload)}")
    return payload


def object_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
