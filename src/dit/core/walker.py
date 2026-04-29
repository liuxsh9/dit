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


def walk_commit_objects_since(
    store: ObjectStore, head_hash: str, stop_at: str | None
) -> dict[str, set[str]]:
    """Walk object graph from head_hash, stopping at stop_at commit.

    Only collects objects from commits AFTER stop_at. When a commit
    is stop_at or is already known to be reachable from stop_at,
    we skip it AND skip entering its tree.

    This means we only collect tree/manifest/row hashes from NEW
    commits (between stop_at and head_hash), not the entire history.

    When stop_at is None, falls back to full walk_commit_objects behavior.
    """
    if stop_at is None:
        return walk_commit_objects(store, head_hash)

    result: dict[str, set[str]] = {
        "commits": set(),
        "trees": set(),
        "manifests": set(),
        "rows": set(),
        "sidecars": set(),
        "blobs": set(),
    }

    # BFS/DFS over commits, stopping when we hit stop_at
    commit_stack: list[str] = [head_hash]
    visited_commits: set[str] = set()
    new_commits: set[str] = set()

    while commit_stack:
        h = commit_stack.pop()
        if h in visited_commits:
            continue
        visited_commits.add(h)

        # If this commit is the stop point, don't collect it or its tree
        if h == stop_at:
            continue

        new_commits.add(h)
        result["commits"].add(h)

        data = store.read("commits", h)
        if data is None:
            continue
        commit = deserialize_commit(data)

        # Queue parent commits for traversal (they may also be new)
        for parent in commit.parent_hashes:
            if parent not in visited_commits:
                commit_stack.append(parent)

    # Now walk trees/manifests only for new commits
    tree_stack: list[tuple[str, str]] = []
    for ch in new_commits:
        data = store.read("commits", ch)
        if data is None:
            continue
        commit = deserialize_commit(data)
        tree_stack.append(("tree", commit.tree_hash))

    while tree_stack:
        kind, h = tree_stack.pop()
        if kind == "tree":
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
                    tree_stack.append(("manifest", entry.obj_hash))
                elif entry.obj_type == "tree":
                    tree_stack.append(("tree", entry.obj_hash))
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
