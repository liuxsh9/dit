import hashlib
import os
import uuid
from pathlib import Path

import pyzstd


class ObjectStore:
    def __init__(self, root: Path):
        self.root = root

    def _object_path(self, obj_type: str, hash_hex: str) -> Path:
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
