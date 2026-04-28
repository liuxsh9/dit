import json
from pathlib import Path
from typing import Iterator


def read_rows(path: Path) -> Iterator[dict]:
    """Yield parsed JSON objects from a JSONL file, skipping blank lines."""
    with open(path, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            stripped = line.strip()
            if stripped:
                try:
                    yield json.loads(stripped)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"{path.name} line {lineno}: invalid JSON: {exc}"
                    ) from None


def write_rows(path: Path, rows: list[dict]) -> None:
    """Write a list of dicts as JSONL."""
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
