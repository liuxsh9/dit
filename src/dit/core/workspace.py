import json
from pathlib import Path

from dit.core.hash import canonical_json, row_hash, query_fingerprint
from dit.core.objects import Manifest, ManifestEntry
from dit.core.store import ObjectStore
from dit.utils.jsonl import read_rows


def find_jsonl_files(root: Path) -> list[Path]:
    results = []
    for p in sorted(root.rglob("*.jsonl")):
        if ".dit" in p.parts:
            continue
        results.append(p)
    return results


def find_all_files(root: Path) -> tuple[list[Path], list[Path]]:
    """Return (jsonl_files, blob_files) under root, excluding .dit/.

    jsonl_files: all *.jsonl paths
    blob_files: all other regular files
    """
    jsonl: list[Path] = []
    blobs: list[Path] = []
    for p in sorted(root.rglob("*")):
        if ".dit" in p.parts:
            continue
        if not p.is_file():
            continue
        if p.suffix == ".jsonl":
            jsonl.append(p)
        else:
            blobs.append(p)
    return jsonl, blobs


def build_blob_for_file(path: Path) -> bytes:
    """Read a non-JSONL file and return its raw content for blob storage."""
    return path.read_bytes()


def build_manifest_for_file(path: Path) -> tuple[Manifest, dict[str, bytes]]:
    """Build a Manifest for a JSONL file.

    Returns (manifest, row_data) where row_data maps row_hash -> canonical bytes.
    """
    entries = []
    row_data: dict[str, bytes] = {}
    for row in read_rows(path):
        canon = canonical_json(row)
        rh = row_hash(row)
        qfp = query_fingerprint(row)
        entries.append(ManifestEntry(row_hash=rh, query_fingerprint=qfp))
        row_data[rh] = canon
    return Manifest(entries=entries), row_data


def materialize_file(
    repo_root: Path, rel_path: str, manifest: Manifest, store: ObjectStore
) -> None:
    dest = repo_root / rel_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for entry in manifest.entries:
        data = store.read("rows", entry.row_hash)
        if data is None:
            raise KeyError(f"Row {entry.row_hash} not found in store")
        rows.append(json.loads(data))
    with open(dest, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
