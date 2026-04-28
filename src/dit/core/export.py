# src/dit/core/export.py
"""Export files from a commit to a local directory."""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from dit.core.objects import deserialize_commit, deserialize_manifest, deserialize_sidecar
from dit.core.store import ObjectStore
from dit.core.tree_walker import flatten_tree


def export_commit(
    store: ObjectStore,
    commit_hash: str,
    output_dir: Path,
    *,
    file_filter: str | None = None,
    fmt: str = "jsonl",
    include_meta: bool = False,
) -> list[dict]:
    """Export files from a commit to output_dir.

    Returns list of dicts: [{"path": "train.jsonl", "rows": 1500, "bytes": 12345}, ...]
    """
    commit_data = store.read("commits", commit_hash)
    if commit_data is None:
        raise FileNotFoundError(f"Commit {commit_hash[:8]} not found in store")

    commit = deserialize_commit(commit_data)
    flat = flatten_tree(store, commit.tree_hash)

    if file_filter is not None:
        clean_filter = file_filter.lstrip("/")
        if clean_filter not in flat:
            raise FileNotFoundError(f"'{file_filter}' not found in commit {commit_hash[:8]}")

    report: list[dict] = []

    for path, (obj_type, obj_hash, sidecar_hash) in sorted(flat.items()):
        if obj_type != "manifest":
            continue
        if file_filter is not None and path != file_filter.lstrip("/"):
            continue

        manifest_data = store.read("manifests", obj_hash)
        if manifest_data is None:
            raise FileNotFoundError(f"Manifest {obj_hash[:8]} missing from store")

        manifest = deserialize_manifest(manifest_data)

        dest = output_dir / path
        if fmt == "csv":
            dest = dest.with_suffix(".csv")
        dest.parent.mkdir(parents=True, exist_ok=True)

        if fmt == "jsonl":
            total_bytes = _write_jsonl(store, manifest, dest)
        elif fmt == "csv":
            total_bytes = _write_csv(store, manifest, dest)
        else:
            raise ValueError(f"Unknown format '{fmt}'. Expected 'jsonl' or 'csv'.")

        report_path = path
        if fmt == "csv":
            report_path = str(Path(path).with_suffix(".csv"))
        row_count = len(manifest.entries)
        report.append({"path": report_path, "rows": row_count, "bytes": total_bytes})

        if include_meta and sidecar_hash is not None:
            _write_meta(store, path, obj_hash, sidecar_hash, output_dir)

    return report


def _write_jsonl(store: ObjectStore, manifest, dest: Path) -> int:
    total = 0
    with dest.open("wb") as fh:
        for entry in manifest.entries:
            row_bytes = store.read("rows", entry.row_hash)
            if row_bytes is None:
                raise FileNotFoundError(f"Row {entry.row_hash[:8]} missing from store")
            fh.write(row_bytes)
            fh.write(b"\n")
            total += len(row_bytes) + 1
    return total


def _write_csv(store: ObjectStore, manifest, dest: Path) -> int:
    rows: list[dict] = []
    all_keys: list[str] = []
    seen_keys: set[str] = set()

    for entry in manifest.entries:
        row_bytes = store.read("rows", entry.row_hash)
        if row_bytes is None:
            raise FileNotFoundError(f"Row {entry.row_hash[:8]} missing from store")
        parsed = json.loads(row_bytes)
        if isinstance(parsed, dict):
            for k in parsed:
                if k not in seen_keys:
                    seen_keys.add(k)
                    all_keys.append(k)
        rows.append(parsed)

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=sorted(all_keys), extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        if not isinstance(row, dict):
            row = {"value": json.dumps(row)}
        flat_row = {
            k: json.dumps(v) if isinstance(v, (dict, list)) else v
            for k, v in row.items()
        }
        writer.writerow(flat_row)

    content = buf.getvalue().encode("utf-8")
    dest.write_bytes(content)
    return len(content)


def _write_meta(
    store: ObjectStore,
    path: str,
    manifest_hash: str,
    sidecar_hash: str,
    output_dir: Path,
) -> None:
    from dit.core.sidecar import sidecar_summary

    sc_data = store.read("sidecars", sidecar_hash)
    if sc_data is None:
        return

    sidecar = deserialize_sidecar(sc_data)
    summary = sidecar_summary(sidecar)
    meta = {
        "file": path,
        "manifest_hash": manifest_hash,
        "sidecar_hash": sidecar_hash,
        **summary,
    }
    meta_dest = output_dir / (path + ".meta.json")
    meta_dest.parent.mkdir(parents=True, exist_ok=True)
    meta_dest.write_text(json.dumps(meta, indent=2))
