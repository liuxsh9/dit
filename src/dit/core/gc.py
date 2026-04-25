"""Garbage collection: mark-and-sweep for the object store."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from dit.core.store import ObjectStore
from dit.core.walker import walk_commit_objects
from dit.core.objects import deserialize_manifest


@dataclass
class GCResult:
    live_counts: dict[str, int] = field(default_factory=dict)
    deleted_counts: dict[str, int] = field(default_factory=dict)
    skipped_counts: dict[str, int] = field(default_factory=dict)
    total_scanned: int = 0
    total_deleted: int = 0
    tmp_deleted: int = 0
    errors: list[str] = field(default_factory=list)


OBJ_TYPES = ["commits", "trees", "manifests", "rows", "sidecars", "blobs"]


def collect_live_set(
    store: ObjectStore,
    ref_hashes: list[str],
    index_entries: dict[str, tuple[str, str]] | None = None,
) -> dict[str, set[str]]:
    live: dict[str, set[str]] = {t: set() for t in OBJ_TYPES}

    for ref_hash in ref_hashes:
        walked = walk_commit_objects(store, ref_hash)
        for obj_type in OBJ_TYPES:
            live[obj_type] |= walked.get(obj_type, set())

    if index_entries:
        for _path, (obj_type, obj_hash) in index_entries.items():
            if obj_type == "manifest":
                live["manifests"].add(obj_hash)
                m_data = store.read("manifests", obj_hash)
                if m_data is not None:
                    manifest = deserialize_manifest(m_data)
                    for entry in manifest.entries:
                        live["rows"].add(entry.row_hash)
            elif obj_type == "blob":
                live["blobs"].add(obj_hash)

    return live


def sweep(
    store: ObjectStore,
    live_set: dict[str, set[str]],
    grace_seconds: int = 86400,
    dry_run: bool = False,
) -> GCResult:
    result = GCResult()
    cutoff = time.time() - grace_seconds

    for obj_type in OBJ_TYPES:
        result.live_counts[obj_type] = len(live_set.get(obj_type, set()))
        result.deleted_counts[obj_type] = 0
        result.skipped_counts[obj_type] = 0

        type_dir = store.root / obj_type
        if not type_dir.exists():
            continue

        for shard1 in type_dir.iterdir():
            if not shard1.is_dir():
                continue
            for shard2 in shard1.iterdir():
                if not shard2.is_dir():
                    continue
                for obj_file in shard2.iterdir():
                    if not obj_file.is_file():
                        continue
                    result.total_scanned += 1
                    obj_hash = obj_file.name
                    if obj_hash in live_set.get(obj_type, set()):
                        continue
                    try:
                        mtime = obj_file.stat().st_mtime
                    except OSError:
                        continue
                    if mtime < cutoff:
                        result.deleted_counts[obj_type] += 1
                        result.total_deleted += 1
                        if not dry_run:
                            try:
                                obj_file.unlink()
                            except OSError as e:
                                result.errors.append(f"Failed to delete {obj_file}: {e}")
                    else:
                        result.skipped_counts[obj_type] += 1

    tmp_dir = store.root / "tmp"
    if tmp_dir.exists():
        for tmp_file in tmp_dir.iterdir():
            if not tmp_file.is_file():
                continue
            try:
                mtime = tmp_file.stat().st_mtime
            except OSError:
                continue
            if mtime < cutoff:
                result.tmp_deleted += 1
                if not dry_run:
                    try:
                        tmp_file.unlink()
                    except OSError as e:
                        result.errors.append(f"Failed to delete tmp {tmp_file}: {e}")

    return result


def gc(
    store: ObjectStore,
    ref_hashes: list[str],
    index_entries: dict[str, tuple[str, str]] | None = None,
    grace_seconds: int = 86400,
    dry_run: bool = False,
) -> GCResult:
    live = collect_live_set(store, ref_hashes, index_entries)
    return sweep(store, live, grace_seconds=grace_seconds, dry_run=dry_run)
