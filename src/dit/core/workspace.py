import json
import hashlib
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

    Returns (manifest, row_data) where row_data maps:
    - row_hash -> canonical row bytes
    - raw_row_hash -> original line bytes including trailing newline
    """
    entries = []
    row_data: dict[str, bytes] = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            row = json.loads(stripped)
            canon = canonical_json(row)
            rh = row_hash(row)
            qfp = query_fingerprint(row)
            raw_bytes = line.encode("utf-8")
            raw_row_hash = hashlib.sha256(raw_bytes).hexdigest()
            entries.append(
                ManifestEntry(
                    row_hash=rh,
                    query_fingerprint=qfp,
                    raw_row_hash=raw_row_hash,
                )
            )
            row_data[rh] = canon
            row_data[raw_row_hash] = raw_bytes
    return Manifest(entries=entries), row_data


def materialize_file(
    repo_root: Path, rel_path: str, manifest: Manifest, store: ObjectStore
) -> None:
    dest = repo_root / rel_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for entry in manifest.entries:
        if entry.raw_row_hash is not None:
            raw_data = store.read("row_text", entry.raw_row_hash)
            if raw_data is not None:
                rows.append(raw_data)
                continue

        data = store.read("rows", entry.row_hash)
        if data is None:
            raise KeyError(f"Row {entry.row_hash} not found in store")
        rows.append(json.dumps(json.loads(data), ensure_ascii=False).encode("utf-8") + b"\n")
    with open(dest, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(row.decode("utf-8"))
