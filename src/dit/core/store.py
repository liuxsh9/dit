import hashlib
import os
import re
import uuid
from pathlib import Path

import pyzstd

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class ObjectStore:
    def __init__(self, root: Path):
        self.root = root

    def _object_path(self, obj_type: str, hash_hex: str) -> Path:
        if not _HASH_RE.match(hash_hex):
            raise ValueError("Invalid object hash: must be 64 lowercase hex characters")
        return self.root / obj_type / hash_hex[0:2] / hash_hex[2:4] / hash_hex

    def write(self, obj_type: str, data: bytes) -> str:
        hash_hex = hashlib.sha256(data).hexdigest()
        dest = self._object_path(obj_type, hash_hex)
        if dest.exists():
            return hash_hex
        dest.parent.mkdir(parents=True, exist_ok=True)
        compressed = pyzstd.compress(data)
        tmp_dir = self.root / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = tmp_dir / str(uuid.uuid4())
        try:
            tmp_path.write_bytes(compressed)
            os.replace(tmp_path, dest)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
        return hash_hex

    def read(self, obj_type: str, hash_hex: str) -> bytes | None:
        path = self._object_path(obj_type, hash_hex)
        if not path.exists():
            return None
        return pyzstd.decompress(path.read_bytes())

    def exists(self, obj_type: str, hash_hex: str) -> bool:
        return self._object_path(obj_type, hash_hex).exists()

    def batch_exists(self, obj_type: str, hashes: list[str]) -> dict[str, bool]:
        return {h: self.exists(obj_type, h) for h in hashes}

    def write_batch(self, obj_type: str, items: list[bytes]) -> list[str]:
        """Write multiple objects, optimizing directory creation.

        Returns list of hashes in the same order as *items*.
        Key optimizations over calling write() in a loop:
        - Deduplicates mkdir calls (many objects share the same 2-char prefix dirs)
        - Skips objects that already exist on disk (content-addressed = idempotent)
        - Creates all needed directories in one pass before writing any files
        """
        if not items:
            return []

        # Phase 1: compute hashes, collect entries that need writing
        all_hashes: list[str] = []          # one per input item, preserving order
        to_write: dict[str, bytes] = {}     # hash -> data (deduped)
        dirs_needed: set[Path] = set()

        for data in items:
            h = hashlib.sha256(data).hexdigest()
            all_hashes.append(h)
            if h in to_write:
                continue  # already queued for writing in this batch
            dest = self._object_path(obj_type, h)
            if dest.exists():
                continue  # already on disk
            to_write[h] = data
            dirs_needed.add(dest.parent)

        # Phase 2: create all directories at once
        for d in dirs_needed:
            d.mkdir(parents=True, exist_ok=True)

        # Ensure tmp dir exists once
        tmp_dir = self.root / "tmp"
        if to_write:
            tmp_dir.mkdir(parents=True, exist_ok=True)

        # Phase 3: write all files
        for h, data in to_write.items():
            dest = self._object_path(obj_type, h)
            if dest.exists():  # race-condition guard
                continue
            compressed = pyzstd.compress(data)
            tmp_path = tmp_dir / str(uuid.uuid4())
            try:
                tmp_path.write_bytes(compressed)
                os.replace(tmp_path, dest)
            except BaseException:
                tmp_path.unlink(missing_ok=True)
                raise

        return all_hashes
