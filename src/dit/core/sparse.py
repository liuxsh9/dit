"""Sparse checkout configuration management."""
from __future__ import annotations


from pathlib import Path


def is_sparse(dot: Path) -> bool:
    return (dot / "sparse-checkout").exists()


def load_sparse_paths(dot: Path) -> set[str] | None:
    sc_file = dot / "sparse-checkout"
    if not sc_file.exists():
        return None
    paths: set[str] = set()
    for line in sc_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            paths.add(line)
    return paths


def save_sparse_paths(dot: Path, paths: set[str]) -> None:
    sc_file = dot / "sparse-checkout"
    sc_file.write_text("\n".join(sorted(paths)) + "\n" if paths else "")


def is_in_sparse_set(file_path: str, sparse_paths: set[str]) -> bool:
    if file_path in sparse_paths:
        return True
    for sp in sparse_paths:
        if sp.endswith("/") and file_path.startswith(sp):
            return True
    return False
