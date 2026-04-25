# src/dit/core/tree_builder.py
"""Build nested Tree objects from a flat staged map."""
from __future__ import annotations

from collections import defaultdict
from typing import Union

from dit.core.objects import Tree, TreeEntry, serialize_tree
from dit.core.store import ObjectStore

StagedValue = Union[tuple[str, str], tuple[str, str, str | None]]


def build_nested_tree(
    store: ObjectStore,
    staged: dict[str, StagedValue],
) -> str:
    return _build_subtree(store, staged, prefix="")


def _build_subtree(
    store: ObjectStore,
    staged: dict[str, StagedValue],
    prefix: str,
) -> str:
    direct: dict[str, StagedValue] = {}
    subdirs: dict[str, dict[str, StagedValue]] = defaultdict(dict)

    prefix_len = len(prefix)
    for path, value in staged.items():
        if not path.startswith(prefix):
            continue
        rest = path[prefix_len:]
        if "/" not in rest:
            direct[rest] = value
        else:
            subdir_name, sub_rest = rest.split("/", 1)
            subdirs[subdir_name][prefix + subdir_name + "/" + sub_rest] = value

    entries: list[TreeEntry] = []

    for name, value in direct.items():
        obj_type, obj_hash = value[0], value[1]
        sidecar_hash = value[2] if len(value) >= 3 else None
        entries.append(
            TreeEntry(name=name, obj_type=obj_type, obj_hash=obj_hash, sidecar_hash=sidecar_hash)
        )

    for subdir_name, sub_staged in subdirs.items():
        sub_tree_hash = _build_subtree(store, sub_staged, prefix=prefix + subdir_name + "/")
        entries.append(TreeEntry(name=subdir_name, obj_type="tree", obj_hash=sub_tree_hash))

    tree = Tree(entries=entries)
    tree_bytes = serialize_tree(tree)
    return store.write("trees", tree_bytes)
