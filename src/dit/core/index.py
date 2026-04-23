import json
from pathlib import Path


class StagingIndex:
    def __init__(self, path: Path):
        self._path = path

    def _read(self) -> dict[str, str]:
        if not self._path.exists():
            return {}
        return json.loads(self._path.read_text())

    def _write(self, data: dict[str, str]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, sort_keys=True))

    def entries(self) -> dict[str, str]:
        return self._read()

    def stage(self, rel_path: str, manifest_hash: str) -> None:
        data = self._read()
        data[rel_path] = manifest_hash
        self._write(data)

    def unstage(self, rel_path: str) -> None:
        data = self._read()
        data.pop(rel_path, None)
        self._write(data)

    def clear(self) -> None:
        self._write({})
