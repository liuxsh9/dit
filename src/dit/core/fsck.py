"""Object store integrity verification."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

import pyzstd

from dit.core.objects import (
    deserialize_commit,
    deserialize_manifest,
    deserialize_tree,
)
from dit.core.store import ObjectStore

OBJ_TYPES = ["commits", "trees", "manifests", "rows", "sidecars", "blobs"]


@dataclass(frozen=True)
class FsckIssue:
    severity: str
    obj_type: str
    obj_hash: str
    message: str


@dataclass
class FsckResult:
    checked_objects: dict[str, int] = field(default_factory=lambda: {t: 0 for t in OBJ_TYPES})
    errors: list[FsckIssue] = field(default_factory=list)
    warnings: list[FsckIssue] = field(default_factory=list)
    total_checked: int = 0
    total_errors: int = 0
    total_warnings: int = 0


def _verify_hashes(store: ObjectStore, result: FsckResult) -> None:
    for obj_type in OBJ_TYPES:
        type_dir = store.root / obj_type
        if not type_dir.exists():
            continue
        for shard1 in sorted(type_dir.iterdir()):
            if not shard1.is_dir():
                continue
            for shard2 in sorted(shard1.iterdir()):
                if not shard2.is_dir():
                    continue
                for obj_file in sorted(shard2.iterdir()):
                    if not obj_file.is_file():
                        continue
                    if not re.fullmatch(r'[0-9a-f]{64}', obj_file.name):
                        continue
                    expected_hash = obj_file.name
                    result.checked_objects[obj_type] += 1
                    result.total_checked += 1

                    try:
                        compressed = obj_file.read_bytes()
                        data = pyzstd.decompress(compressed)
                    except Exception:
                        result.errors.append(FsckIssue(
                            severity="error",
                            obj_type=obj_type,
                            obj_hash=expected_hash,
                            message="corrupt object: decompression failed",
                        ))
                        result.total_errors += 1
                        continue

                    actual_hash = hashlib.sha256(data).hexdigest()
                    if actual_hash != expected_hash:
                        result.errors.append(FsckIssue(
                            severity="error",
                            obj_type=obj_type,
                            obj_hash=expected_hash,
                            message=f"hash mismatch: expected {expected_hash[:16]}..., got {actual_hash[:16]}...",
                        ))
                        result.total_errors += 1


def _verify_graph(store: ObjectStore, ref_hashes: list[str], result: FsckResult) -> None:
    visited_commits: set[str] = set()
    visited_trees: set[str] = set()
    visited_manifests: set[str] = set()

    def _check_commit(commit_hash: str) -> None:
        if commit_hash in visited_commits:
            return
        visited_commits.add(commit_hash)

        try:
            data = store.read("commits", commit_hash)
        except Exception:
            result.errors.append(FsckIssue(
                severity="error",
                obj_type="commits",
                obj_hash=commit_hash,
                message="corrupt commit object: decompression failed",
            ))
            result.total_errors += 1
            return
        if data is None:
            result.errors.append(FsckIssue(
                severity="error",
                obj_type="commits",
                obj_hash=commit_hash,
                message="missing commit object (dangling reference)",
            ))
            result.total_errors += 1
            return

        commit = deserialize_commit(data)

        for parent in commit.parent_hashes:
            _check_commit(parent)

        _check_tree(commit.tree_hash)

    def _check_tree(tree_hash: str) -> None:
        if tree_hash in visited_trees:
            return
        visited_trees.add(tree_hash)

        try:
            data = store.read("trees", tree_hash)
        except Exception:
            result.errors.append(FsckIssue(
                severity="error",
                obj_type="trees",
                obj_hash=tree_hash,
                message="corrupt tree object: decompression failed",
            ))
            result.total_errors += 1
            return
        if data is None:
            result.errors.append(FsckIssue(
                severity="error",
                obj_type="trees",
                obj_hash=tree_hash,
                message="missing tree object",
            ))
            result.total_errors += 1
            return

        tree = deserialize_tree(data)
        for entry in tree.entries:
            if entry.obj_type == "tree":
                _check_tree(entry.obj_hash)
            elif entry.obj_type == "manifest":
                _check_manifest(entry.obj_hash)
            elif entry.obj_type == "blob":
                try:
                    blob_data = store.read("blobs", entry.obj_hash)
                except Exception:
                    result.errors.append(FsckIssue(
                        severity="error",
                        obj_type="blobs",
                        obj_hash=entry.obj_hash,
                        message="corrupt blob object: decompression failed",
                    ))
                    result.total_errors += 1
                    blob_data = b""  # sentinel to skip missing check
                if blob_data is None:
                    result.errors.append(FsckIssue(
                        severity="error",
                        obj_type="blobs",
                        obj_hash=entry.obj_hash,
                        message="missing blob object",
                    ))
                    result.total_errors += 1

            if entry.sidecar_hash:
                try:
                    sidecar_data = store.read("sidecars", entry.sidecar_hash)
                except Exception:
                    result.errors.append(FsckIssue(
                        severity="error",
                        obj_type="sidecars",
                        obj_hash=entry.sidecar_hash,
                        message="corrupt sidecar object: decompression failed",
                    ))
                    result.total_errors += 1
                    sidecar_data = b""  # sentinel to skip missing check
                if sidecar_data is None:
                    result.errors.append(FsckIssue(
                        severity="error",
                        obj_type="sidecars",
                        obj_hash=entry.sidecar_hash,
                        message="missing sidecar object",
                    ))
                    result.total_errors += 1

    def _check_manifest(manifest_hash: str) -> None:
        if manifest_hash in visited_manifests:
            return
        visited_manifests.add(manifest_hash)

        try:
            data = store.read("manifests", manifest_hash)
        except Exception:
            result.errors.append(FsckIssue(
                severity="error",
                obj_type="manifests",
                obj_hash=manifest_hash,
                message="corrupt manifest object: decompression failed",
            ))
            result.total_errors += 1
            return
        if data is None:
            result.errors.append(FsckIssue(
                severity="error",
                obj_type="manifests",
                obj_hash=manifest_hash,
                message="missing manifest object",
            ))
            result.total_errors += 1
            return

        manifest = deserialize_manifest(data)
        for entry in manifest.entries:
            try:
                row_data = store.read("rows", entry.row_hash)
            except Exception:
                result.errors.append(FsckIssue(
                    severity="error",
                    obj_type="rows",
                    obj_hash=entry.row_hash,
                    message="corrupt row object: decompression failed",
                ))
                result.total_errors += 1
                continue
            if row_data is None:
                result.errors.append(FsckIssue(
                    severity="error",
                    obj_type="rows",
                    obj_hash=entry.row_hash,
                    message="missing row object",
                ))
                result.total_errors += 1

    for ref_hash in ref_hashes:
        _check_commit(ref_hash)


def fsck(
    store: ObjectStore,
    ref_hashes: list[str],
    check_hashes: bool = True,
    check_graph: bool = True,
) -> FsckResult:
    result = FsckResult()

    if check_hashes:
        _verify_hashes(store, result)

    if check_graph:
        _verify_graph(store, ref_hashes, result)

    return result
