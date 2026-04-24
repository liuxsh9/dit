import json
from pathlib import Path


class StagingIndex:
    def __init__(self, path: Path):
        self._path = path

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
        data = self._load()
        data[rel_path] = {"hash": obj_hash, "type": obj_type}
        self._write(data)

    def entries(self) -> dict[str, str]:
        """Return rel_path → obj_hash (backward compat)."""
        return {k: v["hash"] for k, v in self._load().items()}

    def entries_typed(self) -> dict[str, tuple[str, str]]:
        """Return rel_path → (obj_type, obj_hash)."""
        return {k: (v["type"], v["hash"]) for k, v in self._load().items()}

    def unstage(self, rel_path: str) -> None:
        data = self._load()
        data.pop(rel_path, None)
        self._write(data)

    def clear(self) -> None:
        self._write({})
