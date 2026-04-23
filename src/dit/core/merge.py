# src/dit/core/merge.py
from __future__ import annotations

from dataclasses import dataclass, field

from dit.core.objects import (
    Manifest,
    ManifestEntry,
    deserialize_commit,
    deserialize_manifest,
    deserialize_tree,
    object_hash,
    serialize_manifest,
)
from dit.core.store import ObjectStore


@dataclass
class MergeConflict:
    file_path: str
    conflict_type: str  # "both_modified" | "modify_delete" | "both_added"
    base_entries: list[ManifestEntry] | None = None
    ours_entries: list[ManifestEntry] | None = None
    theirs_entries: list[ManifestEntry] | None = None


@dataclass
class MergeResult:
    merged_tree_entries: dict[str, str] = field(default_factory=dict)  # file_path -> manifest_hash
    conflicts: list[MergeConflict] = field(default_factory=list)


def _load_tree_manifests(store: ObjectStore, commit_hash: str) -> dict[str, str]:
    commit_data = store.read("commits", commit_hash)
    commit = deserialize_commit(commit_data)
    tree_data = store.read("trees", commit.tree_hash)
    tree = deserialize_tree(tree_data)
    return {e.name: e.obj_hash for e in tree.entries if e.obj_type == "manifest"}


def three_way_merge(
    store: ObjectStore,
    base_hash: str | None,
    ours_hash: str,
    theirs_hash: str,
) -> MergeResult:
    base_files = _load_tree_manifests(store, base_hash) if base_hash else {}
    ours_files = _load_tree_manifests(store, ours_hash)
    theirs_files = _load_tree_manifests(store, theirs_hash)

    all_paths = sorted(set(list(base_files.keys()) + list(ours_files.keys()) + list(theirs_files.keys())))
    result = MergeResult()

    for path in all_paths:
        base_mhash = base_files.get(path)
        ours_mhash = ours_files.get(path)
        theirs_mhash = theirs_files.get(path)

        if base_mhash is not None:
            # File existed in base
            if ours_mhash == base_mhash and theirs_mhash == base_mhash:
                result.merged_tree_entries[path] = base_mhash
            elif ours_mhash == base_mhash and theirs_mhash is not None:
                result.merged_tree_entries[path] = theirs_mhash
            elif theirs_mhash == base_mhash and ours_mhash is not None:
                result.merged_tree_entries[path] = ours_mhash
            elif ours_mhash is None and theirs_mhash == base_mhash:
                pass  # ours deleted, theirs unchanged -> delete
            elif theirs_mhash is None and ours_mhash == base_mhash:
                pass  # theirs deleted, ours unchanged -> delete
            elif ours_mhash is None and theirs_mhash is None:
                pass  # both deleted
            elif ours_mhash is None and theirs_mhash != base_mhash:
                # ours deleted, theirs modified -> conflict
                theirs_m = deserialize_manifest(store.read("manifests", theirs_mhash))
                base_m = deserialize_manifest(store.read("manifests", base_mhash))
                result.conflicts.append(MergeConflict(
                    file_path=path,
                    conflict_type="modify_delete",
                    base_entries=base_m.entries,
                    ours_entries=None,
                    theirs_entries=theirs_m.entries,
                ))
            elif theirs_mhash is None and ours_mhash != base_mhash:
                # theirs deleted, ours modified -> conflict
                ours_m = deserialize_manifest(store.read("manifests", ours_mhash))
                base_m = deserialize_manifest(store.read("manifests", base_mhash))
                result.conflicts.append(MergeConflict(
                    file_path=path,
                    conflict_type="modify_delete",
                    base_entries=base_m.entries,
                    ours_entries=ours_m.entries,
                    theirs_entries=None,
                ))
            elif ours_mhash == theirs_mhash:
                result.merged_tree_entries[path] = ours_mhash
            else:
                # Both modified differently -> row-level merge
                base_m = deserialize_manifest(store.read("manifests", base_mhash))
                ours_m = deserialize_manifest(store.read("manifests", ours_mhash))
                theirs_m = deserialize_manifest(store.read("manifests", theirs_mhash))
                merged_entries, conflicts = merge_manifests(base_m, ours_m, theirs_m, path)
                if conflicts:
                    result.conflicts.extend(conflicts)
                merged_manifest = Manifest(entries=merged_entries)
                merged_bytes = serialize_manifest(merged_manifest)
                merged_hash = store.write("manifests", merged_bytes)
                result.merged_tree_entries[path] = merged_hash
        else:
            # File NOT in base (new file)
            if ours_mhash is not None and theirs_mhash is None:
                result.merged_tree_entries[path] = ours_mhash
            elif ours_mhash is None and theirs_mhash is not None:
                result.merged_tree_entries[path] = theirs_mhash
            elif ours_mhash == theirs_mhash:
                result.merged_tree_entries[path] = ours_mhash
            else:
                # Both added different content -> conflict
                ours_m = deserialize_manifest(store.read("manifests", ours_mhash))
                theirs_m = deserialize_manifest(store.read("manifests", theirs_mhash))
                result.conflicts.append(MergeConflict(
                    file_path=path,
                    conflict_type="both_added",
                    base_entries=None,
                    ours_entries=ours_m.entries,
                    theirs_entries=theirs_m.entries,
                ))

    return result


def merge_manifests(
    base: Manifest,
    ours: Manifest,
    theirs: Manifest,
    file_path: str,
) -> tuple[list[ManifestEntry], list[MergeConflict]]:
    # Placeholder — implemented in Task 4
    raise NotImplementedError("merge_manifests not yet implemented")
