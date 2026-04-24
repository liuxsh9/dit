# src/dit/core/tree_builder.py
"""Build nested Tree objects from a flat staged map."""
from __future__ import annotations

from collections import defaultdict

from dit.core.objects import Tree, TreeEntry, serialize_tree
from dit.core.store import ObjectStore


def build_nested_tree(
    store: ObjectStore,
    staged: dict[str, tuple[str, str]],
) -> str:
    """Recursively build nested Tree objects and write them to store.

    Args:
        store: ObjectStore to write tree objects into.
        staged: Flat map of POSIX-relative path → (obj_type, obj_hash).
                obj_type must be "manifest" or "blob".

    Returns:
        SHA-256 hex hash of the root Tree object.
    """
    return _build_subtree(store, staged, prefix="")


def _build_subtree(
    store: ObjectStore,
    staged: dict[str, tuple[str, str]],
    prefix: str,
) -> str:
    direct: dict[str, tuple[str, str]] = {}
    subdirs: dict[str, dict[str, tuple[str, str]]] = defaultdict(dict)

    prefix_len = len(prefix)
    for path, (obj_type, obj_hash) in staged.items():
        if not path.startswith(prefix):
            continue
        rest = path[prefix_len:]
        if "/" not in rest:
            direct[rest] = (obj_type, obj_hash)
        else:
            subdir_name, sub_rest = rest.split("/", 1)
            subdirs[subdir_name][prefix + subdir_name + "/" + sub_rest] = (obj_type, obj_hash)

    entries: list[TreeEntry] = []

    for name, (obj_type, obj_hash) in direct.items():
        entries.append(TreeEntry(name=name, obj_type=obj_type, obj_hash=obj_hash))

    for subdir_name, sub_staged in subdirs.items():
        sub_tree_hash = _build_subtree(store, sub_staged, prefix=prefix + subdir_name + "/")
        entries.append(TreeEntry(name=subdir_name, obj_type="tree", obj_hash=sub_tree_hash))

    tree = Tree(entries=entries)
    tree_bytes = serialize_tree(tree)
    return store.write("trees", tree_bytes)
