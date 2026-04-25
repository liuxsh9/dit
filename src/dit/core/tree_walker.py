# src/dit/core/tree_walker.py
"""Walk nested Tree objects to produce flat path maps or resolve sub-paths."""
from __future__ import annotations

from dit.core.objects import TreeEntry, deserialize_tree
from dit.core.store import ObjectStore


def flatten_tree(
    store: ObjectStore,
    tree_hash: str,
    prefix: str = "",
) -> dict[str, tuple[str, str, str | None]]:
    """Recursively expand a Tree into a flat map of path → (obj_type, obj_hash, sidecar_hash).

    Tree-type entries are descended recursively; manifest and blob entries are
    included as leaves with their full relative path.
    """
    data = store.read("trees", tree_hash)
    if data is None:
        return {}
    tree = deserialize_tree(data)
    result: dict[str, tuple[str, str, str | None]] = {}
    for entry in tree.entries:
        full_path = f"{prefix}{entry.name}" if not prefix else f"{prefix}/{entry.name}"
        if entry.obj_type == "tree":
            result.update(flatten_tree(store, entry.obj_hash, prefix=full_path))
        else:
            result[full_path] = (entry.obj_type, entry.obj_hash, entry.sidecar_hash)
    return result


def resolve_path(
    store: ObjectStore,
    tree_hash: str,
    path: str,
) -> list[dict] | None:
    """Navigate a nested tree to the given path and return its directory listing.

    Returns list of entry dicts with keys: name, obj_type, obj_hash, sidecar_hash.
    Returns None if the path does not exist or points to a non-tree entry.
    """
    data = store.read("trees", tree_hash)
    if data is None:
        return None

    if path == "" or path == ".":
        tree = deserialize_tree(data)
        return [
            {"name": e.name, "obj_type": e.obj_type, "obj_hash": e.obj_hash, "sidecar_hash": e.sidecar_hash}
            for e in tree.entries
        ]

    parts = path.strip("/").split("/")
    current_hash = tree_hash
    for i, part in enumerate(parts):
        node_data = store.read("trees", current_hash)
        if node_data is None:
            return None
        node = deserialize_tree(node_data)
        found: TreeEntry | None = None
        for entry in node.entries:
            if entry.name == part:
                found = entry
                break
        if found is None:
            return None
        if i == len(parts) - 1:
            if found.obj_type != "tree":
                return None
            leaf_data = store.read("trees", found.obj_hash)
            if leaf_data is None:
                return None
            leaf = deserialize_tree(leaf_data)
            return [
                {"name": e.name, "obj_type": e.obj_type, "obj_hash": e.obj_hash, "sidecar_hash": e.sidecar_hash}
                for e in leaf.entries
            ]
        else:
            if found.obj_type != "tree":
                return None
            current_hash = found.obj_hash
    return None
