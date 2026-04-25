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
    }
    _walk_commit(store, commit_hash, result)
    return result


def _walk_commit(
    store: ObjectStore, commit_hash: str, result: dict[str, set[str]]
) -> None:
    if commit_hash in result["commits"]:
        return
    result["commits"].add(commit_hash)
    commit_data = store.read("commits", commit_hash)
    if commit_data is None:
        return
    commit = deserialize_commit(commit_data)
    _walk_tree(store, commit.tree_hash, result)
    for parent_hash in commit.parent_hashes:
        _walk_commit(store, parent_hash, result)


def _walk_tree(
    store: ObjectStore, tree_hash: str, result: dict[str, set[str]]
) -> None:
    if tree_hash in result["trees"]:
        return
    result["trees"].add(tree_hash)
    tree_data = store.read("trees", tree_hash)
    if tree_data is None:
        return
    tree = deserialize_tree(tree_data)
    for entry in tree.entries:
        if entry.sidecar_hash:
            result["sidecars"].add(entry.sidecar_hash)
        if entry.obj_type == "manifest":
            _walk_manifest(store, entry.obj_hash, result)
        elif entry.obj_type == "tree":
            _walk_tree(store, entry.obj_hash, result)


def _walk_manifest(
    store: ObjectStore, manifest_hash: str, result: dict[str, set[str]]
) -> None:
    if manifest_hash in result["manifests"]:
        return
    result["manifests"].add(manifest_hash)
    manifest_data = store.read("manifests", manifest_hash)
    if manifest_data is None:
        return
    manifest = deserialize_manifest(manifest_data)
    for entry in manifest.entries:
        result["rows"].add(entry.row_hash)


def is_ancestor(
    store: ObjectStore, ancestor_hash: str, descendant_hash: str
) -> bool:
    if ancestor_hash == descendant_hash:
        return True
    visited: set[str] = set()
    return _is_ancestor_dfs(store, ancestor_hash, descendant_hash, visited)


def _is_ancestor_dfs(
    store: ObjectStore,
    ancestor_hash: str,
    current_hash: str,
    visited: set[str],
) -> bool:
    if current_hash in visited:
        return False
    visited.add(current_hash)
    commit_data = store.read("commits", current_hash)
    if commit_data is None:
        return False
    commit = deserialize_commit(commit_data)
    for parent_hash in commit.parent_hashes:
        if parent_hash == ancestor_hash:
            return True
        if _is_ancestor_dfs(store, ancestor_hash, parent_hash, visited):
            return True
    return False
