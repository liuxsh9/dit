import fcntl
import json
import time
from pathlib import Path


class StagingIndex:
    def __init__(self, path: Path):
        self._path = path
        self._lock_path = path.with_suffix(".lock")
        self._lock_timeout = 10  # seconds

    def _acquire_lock(self):
        """Acquire an exclusive file lock, with timeout."""
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_fd = open(self._lock_path, "w")
        deadline = time.monotonic() + self._lock_timeout
        while True:
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except (IOError, OSError):
                if time.monotonic() >= deadline:
                    self._lock_fd.close()
                    raise TimeoutError(
                        f"Could not acquire index lock ({self._lock_path}) "
                        f"within {self._lock_timeout}s. "
                        f"Another dit process may be running."
                    )
                time.sleep(0.05)

    def _release_lock(self):
        """Release the file lock."""
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            self._lock_fd.close()
        except Exception:
            pass

    def _load(self) -> dict[str, dict]:
        if not self._path.exists():
            return {}
        raw = json.loads(self._path.read_text())
        result = {}
        for k, v in raw.items():
            if isinstance(v, str):
                result[k] = {"hash": v, "type": "manifest"}
            else:
                result[k] = v
        return result

    def _write(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, sort_keys=True))

    def stage(self, rel_path: str, obj_hash: str, obj_type: str = "manifest") -> None:
        self._acquire_lock()
        try:
            data = self._load()
            data[rel_path] = {"hash": obj_hash, "type": obj_type}
            self._write(data)
        finally:
            self._release_lock()

    def stage_delete(self, rel_path: str) -> None:
        self._acquire_lock()
        try:
            data = self._load()
            data[rel_path] = {"hash": "", "type": "delete"}
            self._write(data)
        finally:
            self._release_lock()

    def entries(self) -> dict[str, str]:
        """Return rel_path → obj_hash (backward compat)."""
        return {k: v["hash"] for k, v in self._load().items()}

    def entries_typed(self) -> dict[str, tuple[str, str]]:
        """Return rel_path → (obj_type, obj_hash)."""
        return {k: (v["type"], v["hash"]) for k, v in self._load().items()}

    def unstage(self, rel_path: str) -> None:
        self._acquire_lock()
        try:
            data = self._load()
            data.pop(rel_path, None)
            self._write(data)
        finally:
            self._release_lock()

    def clear(self) -> None:
        self._acquire_lock()
        try:
            self._write({})
        finally:
            self._release_lock()
