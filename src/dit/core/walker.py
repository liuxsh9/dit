from __future__ import annotations

from dit.core.objects import deserialize_commit, deserialize_manifest, deserialize_tree
from dit.core.store import ObjectStore


def walk_commit_objects(
    store: ObjectStore, commit_hash: str
) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {
        "commits": set(),
        "trees": set(),
        "manifests": set(),
        "rows": set(),
        "sidecars": set(),
        "blobs": set(),
    }
    stack: list[tuple[str, str]] = [("commit", commit_hash)]
    while stack:
        kind, h = stack.pop()
        if kind == "commit":
            if h in result["commits"]:
                continue
            result["commits"].add(h)
            data = store.read("commits", h)
            if data is None:
                continue
            commit = deserialize_commit(data)
            stack.append(("tree", commit.tree_hash))
            for parent in commit.parent_hashes:
                stack.append(("commit", parent))
        elif kind == "tree":
            if h in result["trees"]:
                continue
            result["trees"].add(h)
            data = store.read("trees", h)
            if data is None:
                continue
            tree = deserialize_tree(data)
            for entry in tree.entries:
                if entry.sidecar_hash:
                    result["sidecars"].add(entry.sidecar_hash)
                if entry.obj_type == "manifest":
                    stack.append(("manifest", entry.obj_hash))
                elif entry.obj_type == "tree":
                    stack.append(("tree", entry.obj_hash))
                elif entry.obj_type == "blob":
                    result["blobs"].add(entry.obj_hash)
        elif kind == "manifest":
            if h in result["manifests"]:
                continue
            result["manifests"].add(h)
            data = store.read("manifests", h)
            if data is None:
                continue
            manifest = deserialize_manifest(data)
            for entry in manifest.entries:
                result["rows"].add(entry.row_hash)
    return result


def is_ancestor(
    store: ObjectStore, ancestor_hash: str, descendant_hash: str
) -> bool:
    if ancestor_hash == descendant_hash:
        return True
    visited: set[str] = set()
    stack = [descendant_hash]
    while stack:
        current = stack.pop()
        if current in visited:
            continue
        visited.add(current)
        data = store.read("commits", current)
        if data is None:
            continue
        commit = deserialize_commit(data)
        for parent in commit.parent_hashes:
            if parent == ancestor_hash:
                return True
            stack.append(parent)
    return False
