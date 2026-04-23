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
    base_hashes = {e.row_hash for e in base.entries}
    ours_hashes = {e.row_hash for e in ours.entries}
    theirs_hashes = {e.row_hash for e in theirs.entries}

    # Index by query_fingerprint for refresh detection
    base_by_qfp: dict[str, ManifestEntry] = {}
    for e in base.entries:
        if e.query_fingerprint:
            base_by_qfp[e.query_fingerprint] = e

    ours_by_qfp: dict[str, ManifestEntry] = {}
    for e in ours.entries:
        if e.query_fingerprint:
            ours_by_qfp[e.query_fingerprint] = e

    theirs_by_qfp: dict[str, ManifestEntry] = {}
    for e in theirs.entries:
        if e.query_fingerprint:
            theirs_by_qfp[e.query_fingerprint] = e

    # Detect refreshes: base qfp present in ours/theirs but with different row_hash
    ours_refreshed: dict[str, ManifestEntry] = {}   # qfp -> new entry in ours
    theirs_refreshed: dict[str, ManifestEntry] = {}  # qfp -> new entry in theirs

    for qfp, base_entry in base_by_qfp.items():
        if base_entry.row_hash not in ours_hashes and qfp in ours_by_qfp:
            ours_refreshed[qfp] = ours_by_qfp[qfp]
        if base_entry.row_hash not in theirs_hashes and qfp in theirs_by_qfp:
            theirs_refreshed[qfp] = theirs_by_qfp[qfp]

    conflicts: list[MergeConflict] = []
    # Track which row_hashes are consumed by refresh conflict resolution
    conflict_ours_hashes: set[str] = set()
    conflict_theirs_hashes: set[str] = set()

    # Resolve refresh conflicts
    refresh_resolved: dict[str, ManifestEntry] = {}  # qfp -> winning entry
    for qfp in set(list(ours_refreshed.keys()) + list(theirs_refreshed.keys())):
        o = ours_refreshed.get(qfp)
        t = theirs_refreshed.get(qfp)
        if o and t:
            if o.row_hash == t.row_hash:
                refresh_resolved[qfp] = o
            else:
                conflicts.append(MergeConflict(
                    file_path=file_path,
                    conflict_type="both_modified",
                    base_entries=[base_by_qfp[qfp]],
                    ours_entries=[o],
                    theirs_entries=[t],
                ))
                conflict_ours_hashes.add(o.row_hash)
                conflict_theirs_hashes.add(t.row_hash)
        elif o:
            refresh_resolved[qfp] = o
        elif t:
            refresh_resolved[qfp] = t

    # Determine which base rows are deleted
    deleted_base_hashes: set[str] = set()
    for e in base.entries:
        in_ours = e.row_hash in ours_hashes or (e.query_fingerprint and e.query_fingerprint in ours_refreshed)
        in_theirs = e.row_hash in theirs_hashes or (e.query_fingerprint and e.query_fingerprint in theirs_refreshed)
        if not in_ours or not in_theirs:
            deleted_base_hashes.add(e.row_hash)

    # Collect theirs-only new rows (not in base, not in ours)
    theirs_only_new: list[ManifestEntry] = []
    ours_all_hashes = ours_hashes | conflict_ours_hashes
    theirs_refreshed_hashes = {e.row_hash for e in theirs_refreshed.values()}
    for e in theirs.entries:
        if e.row_hash not in base_hashes and e.row_hash not in ours_all_hashes and e.row_hash not in conflict_theirs_hashes and e.row_hash not in theirs_refreshed_hashes:
            theirs_only_new.append(e)

    # Build merged result: ours as skeleton
    merged: list[ManifestEntry] = []
    seen_hashes: set[str] = set()

    for e in ours.entries:
        if e.row_hash in conflict_ours_hashes:
            continue

        # Check if this is a refresh
        if e.query_fingerprint and e.query_fingerprint in refresh_resolved:
            resolved = refresh_resolved[e.query_fingerprint]
            if resolved.row_hash not in seen_hashes:
                merged.append(resolved)
                seen_hashes.add(resolved.row_hash)
            continue

        # Check if this row was deleted by theirs
        if e.row_hash in base_hashes and e.row_hash not in theirs_hashes:
            if not (e.query_fingerprint and e.query_fingerprint in theirs_refreshed):
                continue  # theirs deleted this row

        if e.row_hash in deleted_base_hashes:
            continue

        if e.row_hash not in seen_hashes:
            merged.append(e)
            seen_hashes.add(e.row_hash)

    # Append theirs-only new rows
    for e in theirs_only_new:
        if e.row_hash not in seen_hashes:
            merged.append(e)
            seen_hashes.add(e.row_hash)

    return merged, conflicts
