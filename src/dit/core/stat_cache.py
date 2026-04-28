"""File stat cache to avoid re-parsing unchanged JSONL files."""
import json
from pathlib import Path


class StatCache:
    """Cache mapping rel_path -> (mtime_ns, size, manifest_hash).

    Used by status/diff to skip re-hashing files that haven't changed on disk.
    """

    def __init__(self, path: Path):
        self._path = path

    def _load(self) -> dict[str, dict]:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}

    def _save(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, sort_keys=True))

    def get_manifest_hash(self, rel_path: str, file_path: Path) -> str | None:
        """Return cached manifest_hash if file stat matches, else None."""
        data = self._load()
        entry = data.get(rel_path)
        if entry is None:
            return None
        try:
            stat = file_path.stat()
        except OSError:
            return None
        if stat.st_mtime_ns == entry.get("mtime_ns") and stat.st_size == entry.get("size"):
            return entry.get("manifest_hash")
        return None

    def update(self, rel_path: str, file_path: Path, manifest_hash: str) -> None:
        """Update cache entry for a file."""
        data = self._load()
        try:
            stat = file_path.stat()
        except OSError:
            return
        data[rel_path] = {
            "mtime_ns": stat.st_mtime_ns,
            "size": stat.st_size,
            "manifest_hash": manifest_hash,
        }
        self._save(data)

    def invalidate(self, rel_path: str) -> None:
        """Remove a file from the cache."""
        data = self._load()
        if rel_path in data:
            del data[rel_path]
            self._save(data)

    def clear(self) -> None:
        """Clear the entire cache."""
        self._save({})
