# Phase 0: Local `dit` — 本地单机版本控制 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a working local `dit init / add / commit / log / diff` flow that version-controls JSONL files with line-level tracking.

**Architecture:** Content-addressed object store (Row → Manifest → Tree → Commit → Ref) on the local filesystem, with RFC 8785 JSON canonicalization for deterministic hashing. CLI built with Typer. All objects are SHA-256 addressed, zstd-compressed, stored under `.datahub/objects/` with two-level directory sharding.

**Tech Stack:** Python 3.12+, uv, Typer, httpx (future), pyzstd, jcs (RFC 8785), pytest

---

## File Structure

```
datahub/
├── pyproject.toml                    # Package config, [project.scripts] dit = "dit.cli.main:app"
├── src/
│   └── dit/
│       ├── __init__.py               # Version string
│       ├── cli/
│       │   ├── __init__.py
│       │   └── main.py              # Typer app: init, add, commit, log, diff, status
│       ├── core/
│       │   ├── __init__.py
│       │   ├── hash.py              # RFC 8785 canonicalization + SHA-256
│       │   ├── objects.py           # Row, Manifest, Tree, Commit dataclasses + serialization
│       │   ├── store.py             # ObjectStore: read/write/exists on local filesystem
│       │   ├── index.py             # StagingIndex: tracks staged files (manifest hashes)
│       │   ├── refs.py              # RefStore: HEAD, branches
│       │   ├── workspace.py         # Scan working directory, detect changes vs HEAD
│       │   └── diff.py             # Diff two manifests, detect additions/removals/refreshes
│       └── utils/
│           ├── __init__.py
│           └── jsonl.py             # Read/write JSONL, iterate rows
└── tests/
    ├── conftest.py                   # Shared fixtures: tmp repo, sample JSONL
    ├── test_hash.py
    ├── test_objects.py
    ├── test_store.py
    ├── test_index.py
    ├── test_refs.py
    ├── test_workspace.py
    ├── test_diff.py
    └── test_cli.py
```

---

### Task 1: Project Scaffold

**Files:**
- Create: `pyproject.toml`
- Create: `src/dit/__init__.py`
- Create: `src/dit/cli/__init__.py`
- Create: `src/dit/cli/main.py`
- Create: `src/dit/core/__init__.py`
- Create: `src/dit/utils/__init__.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "dit"
version = "0.1.0"
description = "Git-like version control for LLM SFT training data"
requires-python = ">=3.12"
dependencies = [
    "typer>=0.15",
    "pyzstd>=0.16",
    "jcs>=0.2",
]

[project.scripts]
dit = "dit.cli.main:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/dit"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]

[dependency-groups]
dev = [
    "pytest>=8",
    "pytest-tmp-files>=0.0.2",
]
```

- [ ] **Step 2: Create package init files**

`src/dit/__init__.py`:
```python
__version__ = "0.1.0"
```

`src/dit/cli/__init__.py`: empty file

`src/dit/core/__init__.py`: empty file

`src/dit/utils/__init__.py`: empty file

- [ ] **Step 3: Create minimal CLI entry point**

`src/dit/cli/main.py`:
```python
import typer

app = typer.Typer(name="dit", help="Git-like version control for SFT training data.")


@app.command()
def version():
    """Print dit version."""
    from dit import __version__
    typer.echo(f"dit {__version__}")


if __name__ == "__main__":
    app()
```

- [ ] **Step 4: Create test conftest with fixtures**

`tests/conftest.py`:
```python
import json
import os
from pathlib import Path

import pytest


@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    """Create a temporary directory to act as a dit repository root."""
    return tmp_path


@pytest.fixture
def sample_conversation() -> dict:
    """A minimal OpenAI-format conversation for testing."""
    return {
        "messages": [
            {"role": "user", "content": "Implement an LRU cache in Python"},
            {"role": "assistant", "content": "Here's an LRU cache implementation..."},
        ]
    }


@pytest.fixture
def sample_jsonl(tmp_repo: Path, sample_conversation: dict) -> Path:
    """Create a sample JSONL file with 3 conversations."""
    convos = [
        sample_conversation,
        {
            "messages": [
                {"role": "user", "content": "Explain Python GIL"},
                {"role": "assistant", "content": "The GIL is..."},
            ]
        },
        {
            "messages": [
                {"role": "user", "content": "Write a bubble sort"},
                {"role": "assistant", "content": "def bubble_sort(arr):..."},
            ]
        },
    ]
    fp = tmp_repo / "coding.jsonl"
    with open(fp, "w") as f:
        for c in convos:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    return fp
```

- [ ] **Step 5: Install dependencies and verify**

```bash
cd /Users/lxs/code/datahub && uv sync
uv run dit version
```

Expected: `dit 0.1.0`

- [ ] **Step 6: Commit**

```bash
git init
git add pyproject.toml src/ tests/conftest.py
git commit -m "feat: project scaffold with CLI entry point"
```

---

### Task 2: JSON Canonicalization and Hashing (`hash.py`)

**Files:**
- Create: `src/dit/core/hash.py`
- Create: `tests/test_hash.py`

- [ ] **Step 1: Write failing tests**

`tests/test_hash.py`:
```python
from dit.core.hash import canonical_json, row_hash, query_fingerprint


class TestCanonicalJson:
    def test_key_order_irrelevant(self):
        a = {"b": 1, "a": 2}
        b = {"a": 2, "b": 1}
        assert canonical_json(a) == canonical_json(b)

    def test_whitespace_irrelevant(self):
        import json
        obj = {"messages": [{"role": "user", "content": "hello"}]}
        compact = json.dumps(obj, separators=(",", ":"))
        pretty = json.dumps(obj, indent=2)
        # Both parse to same object, canonical form should match
        assert canonical_json(json.loads(compact)) == canonical_json(json.loads(pretty))

    def test_unicode_normalized(self):
        obj = {"text": "café"}
        result = canonical_json(obj)
        assert isinstance(result, bytes)

    def test_deterministic(self):
        obj = {"messages": [{"role": "user", "content": "test"}]}
        assert canonical_json(obj) == canonical_json(obj)


class TestRowHash:
    def test_returns_hex_string_64_chars(self):
        obj = {"messages": [{"role": "user", "content": "hello"}]}
        h = row_hash(obj)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_same_content_same_hash(self):
        a = {"b": 1, "a": 2}
        b = {"a": 2, "b": 1}
        assert row_hash(a) == row_hash(b)

    def test_different_content_different_hash(self):
        a = {"messages": [{"role": "user", "content": "hello"}]}
        b = {"messages": [{"role": "user", "content": "world"}]}
        assert row_hash(a) != row_hash(b)


class TestQueryFingerprint:
    def test_extracts_user_content(self):
        conv = {
            "messages": [
                {"role": "user", "content": "Implement LRU cache"},
                {"role": "assistant", "content": "Here's the code..."},
            ]
        }
        fp = query_fingerprint(conv)
        assert len(fp) == 64

    def test_same_query_same_fingerprint(self):
        a = {
            "messages": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "response A"},
            ]
        }
        b = {
            "messages": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "response B"},
            ]
        }
        assert query_fingerprint(a) == query_fingerprint(b)

    def test_different_query_different_fingerprint(self):
        a = {
            "messages": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
            ]
        }
        b = {
            "messages": [
                {"role": "user", "content": "world"},
                {"role": "assistant", "content": "hi"},
            ]
        }
        assert query_fingerprint(a) != query_fingerprint(b)

    def test_no_user_message_returns_none(self):
        conv = {"messages": [{"role": "assistant", "content": "hi"}]}
        assert query_fingerprint(conv) is None

    def test_multi_turn_concatenates_user_messages(self):
        conv = {
            "messages": [
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "..."},
                {"role": "user", "content": "second"},
                {"role": "assistant", "content": "..."},
            ]
        }
        fp = query_fingerprint(conv)
        assert fp is not None
        # Should differ from single-turn
        single = {
            "messages": [
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "..."},
            ]
        }
        assert query_fingerprint(single) != fp
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_hash.py -v
```

Expected: ModuleNotFoundError or ImportError

- [ ] **Step 3: Implement `hash.py`**

`src/dit/core/hash.py`:
```python
import hashlib
from typing import Optional

import jcs


def canonical_json(obj: dict) -> bytes:
    """Serialize a dict to RFC 8785 canonical JSON bytes."""
    return jcs.canonicalize(obj)


def row_hash(obj: dict) -> str:
    """SHA-256 hash of the canonical JSON representation of a row."""
    return hashlib.sha256(canonical_json(obj)).hexdigest()


def query_fingerprint(conv: dict) -> Optional[str]:
    """Hash of all user-role message contents, for detecting response refreshes.

    Returns None if there are no user messages.
    """
    messages = conv.get("messages", [])
    user_contents = [
        m["content"] for m in messages
        if m.get("role") == "user" and "content" in m
    ]
    if not user_contents:
        return None
    combined = "\n".join(user_contents)
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_hash.py -v
```

Expected: All 10 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/dit/core/hash.py tests/test_hash.py
git commit -m "feat: JSON canonicalization and content hashing (RFC 8785 + SHA-256)"
```

---

### Task 3: JSONL Utilities (`jsonl.py`)

**Files:**
- Create: `src/dit/utils/jsonl.py`
- Create: `tests/test_jsonl.py`

- [ ] **Step 1: Write failing tests**

`tests/test_jsonl.py`:
```python
import json
from pathlib import Path

from dit.utils.jsonl import read_rows, write_rows


def test_read_rows(sample_jsonl: Path):
    rows = list(read_rows(sample_jsonl))
    assert len(rows) == 3
    assert rows[0]["messages"][0]["content"] == "Implement an LRU cache in Python"


def test_read_rows_preserves_order(tmp_path: Path):
    fp = tmp_path / "test.jsonl"
    data = [{"id": i} for i in range(100)]
    with open(fp, "w") as f:
        for d in data:
            f.write(json.dumps(d) + "\n")
    rows = list(read_rows(fp))
    assert [r["id"] for r in rows] == list(range(100))


def test_write_rows(tmp_path: Path):
    fp = tmp_path / "out.jsonl"
    data = [{"a": 1}, {"b": 2}]
    write_rows(fp, data)
    rows = list(read_rows(fp))
    assert rows == data


def test_read_rows_skips_blank_lines(tmp_path: Path):
    fp = tmp_path / "test.jsonl"
    fp.write_text('{"a":1}\n\n{"b":2}\n\n')
    rows = list(read_rows(fp))
    assert len(rows) == 2


def test_write_rows_no_trailing_newline_per_row(tmp_path: Path):
    fp = tmp_path / "out.jsonl"
    write_rows(fp, [{"x": 1}])
    content = fp.read_text()
    assert content.endswith("\n")
    assert content.count("\n") == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_jsonl.py -v
```

Expected: ImportError

- [ ] **Step 3: Implement `jsonl.py`**

`src/dit/utils/jsonl.py`:
```python
import json
from pathlib import Path
from typing import Iterator


def read_rows(path: Path) -> Iterator[dict]:
    """Yield parsed JSON objects from a JSONL file, skipping blank lines."""
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if stripped:
                yield json.loads(stripped)


def write_rows(path: Path, rows: list[dict]) -> None:
    """Write a list of dicts as JSONL."""
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_jsonl.py -v
```

Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/dit/utils/jsonl.py tests/test_jsonl.py
git commit -m "feat: JSONL read/write utilities"
```

---

### Task 4: Object Model Dataclasses (`objects.py`)

**Files:**
- Create: `src/dit/core/objects.py`
- Create: `tests/test_objects.py`

- [ ] **Step 1: Write failing tests**

`tests/test_objects.py`:
```python
import json
import time

from dit.core.objects import (
    ManifestEntry,
    Manifest,
    TreeEntry,
    Tree,
    Commit,
    serialize_manifest,
    deserialize_manifest,
    serialize_tree,
    deserialize_tree,
    serialize_commit,
    deserialize_commit,
    object_hash,
)


class TestManifest:
    def test_roundtrip(self):
        entries = [
            ManifestEntry(row_hash="aa" * 32, query_fingerprint="bb" * 32),
            ManifestEntry(row_hash="cc" * 32, query_fingerprint=None),
        ]
        m = Manifest(entries=entries)
        data = serialize_manifest(m)
        m2 = deserialize_manifest(data)
        assert len(m2.entries) == 2
        assert m2.entries[0].row_hash == "aa" * 32
        assert m2.entries[0].query_fingerprint == "bb" * 32
        assert m2.entries[1].query_fingerprint is None

    def test_preserves_order(self):
        hashes = [f"{i:064x}" for i in range(50)]
        entries = [ManifestEntry(row_hash=h, query_fingerprint=None) for h in hashes]
        m = Manifest(entries=entries)
        m2 = deserialize_manifest(serialize_manifest(m))
        assert [e.row_hash for e in m2.entries] == hashes

    def test_hash_deterministic(self):
        entries = [ManifestEntry(row_hash="aa" * 32, query_fingerprint=None)]
        m = Manifest(entries=entries)
        data = serialize_manifest(m)
        assert object_hash(data) == object_hash(data)


class TestTree:
    def test_roundtrip(self):
        t = Tree(entries=[
            TreeEntry(name="coding.jsonl", obj_type="manifest", obj_hash="aa" * 32),
            TreeEntry(name="subdir", obj_type="tree", obj_hash="bb" * 32),
        ])
        data = serialize_tree(t)
        t2 = deserialize_tree(data)
        assert len(t2.entries) == 2
        assert t2.entries[0].name == "coding.jsonl"
        assert t2.entries[0].obj_type == "manifest"
        assert t2.entries[1].obj_type == "tree"

    def test_sorted_by_name(self):
        t = Tree(entries=[
            TreeEntry(name="z.jsonl", obj_type="manifest", obj_hash="aa" * 32),
            TreeEntry(name="a.jsonl", obj_type="manifest", obj_hash="bb" * 32),
        ])
        data = serialize_tree(t)
        t2 = deserialize_tree(data)
        assert t2.entries[0].name == "a.jsonl"
        assert t2.entries[1].name == "z.jsonl"


class TestCommit:
    def test_roundtrip(self):
        c = Commit(
            tree_hash="aa" * 32,
            parent_hashes=[],
            author="zhangsan",
            message="initial commit",
            timestamp=1700000000,
        )
        data = serialize_commit(c)
        c2 = deserialize_commit(data)
        assert c2.tree_hash == "aa" * 32
        assert c2.parent_hashes == []
        assert c2.author == "zhangsan"
        assert c2.message == "initial commit"
        assert c2.timestamp == 1700000000

    def test_with_parent(self):
        c = Commit(
            tree_hash="aa" * 32,
            parent_hashes=["bb" * 32],
            author="lisi",
            message="second commit",
            timestamp=1700000001,
        )
        data = serialize_commit(c)
        c2 = deserialize_commit(data)
        assert c2.parent_hashes == ["bb" * 32]

    def test_hash_changes_with_content(self):
        c1 = Commit(tree_hash="aa" * 32, parent_hashes=[], author="a", message="m", timestamp=1)
        c2 = Commit(tree_hash="bb" * 32, parent_hashes=[], author="a", message="m", timestamp=1)
        assert object_hash(serialize_commit(c1)) != object_hash(serialize_commit(c2))
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_objects.py -v
```

Expected: ImportError

- [ ] **Step 3: Implement `objects.py`**

`src/dit/core/objects.py`:
```python
import hashlib
import json
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ManifestEntry:
    row_hash: str
    query_fingerprint: Optional[str]


@dataclass
class Manifest:
    entries: list[ManifestEntry]


@dataclass(frozen=True)
class TreeEntry:
    name: str
    obj_type: str  # "manifest" or "tree"
    obj_hash: str


@dataclass
class Tree:
    entries: list[TreeEntry]


@dataclass
class Commit:
    tree_hash: str
    parent_hashes: list[str]
    author: str
    message: str
    timestamp: int


def serialize_manifest(m: Manifest) -> bytes:
    data = {
        "type": "manifest",
        "entries": [
            {"row_hash": e.row_hash, "query_fingerprint": e.query_fingerprint}
            for e in m.entries
        ],
    }
    return json.dumps(data, separators=(",", ":"), sort_keys=True).encode("utf-8")


def deserialize_manifest(data: bytes) -> Manifest:
    obj = json.loads(data)
    entries = [
        ManifestEntry(
            row_hash=e["row_hash"],
            query_fingerprint=e.get("query_fingerprint"),
        )
        for e in obj["entries"]
    ]
    return Manifest(entries=entries)


def serialize_tree(t: Tree) -> bytes:
    sorted_entries = sorted(t.entries, key=lambda e: e.name)
    data = {
        "type": "tree",
        "entries": [
            {"name": e.name, "obj_type": e.obj_type, "obj_hash": e.obj_hash}
            for e in sorted_entries
        ],
    }
    return json.dumps(data, separators=(",", ":"), sort_keys=True).encode("utf-8")


def deserialize_tree(data: bytes) -> Tree:
    obj = json.loads(data)
    entries = [
        TreeEntry(name=e["name"], obj_type=e["obj_type"], obj_hash=e["obj_hash"])
        for e in obj["entries"]
    ]
    return Tree(entries=entries)


def serialize_commit(c: Commit) -> bytes:
    data = {
        "type": "commit",
        "tree_hash": c.tree_hash,
        "parent_hashes": c.parent_hashes,
        "author": c.author,
        "message": c.message,
        "timestamp": c.timestamp,
    }
    return json.dumps(data, separators=(",", ":"), sort_keys=True).encode("utf-8")


def deserialize_commit(data: bytes) -> Commit:
    obj = json.loads(data)
    return Commit(
        tree_hash=obj["tree_hash"],
        parent_hashes=obj["parent_hashes"],
        author=obj["author"],
        message=obj["message"],
        timestamp=obj["timestamp"],
    )


def object_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_objects.py -v
```

Expected: All 8 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/dit/core/objects.py tests/test_objects.py
git commit -m "feat: object model dataclasses (Row, Manifest, Tree, Commit)"
```

---

### Task 5: Local Object Store (`store.py`)

**Files:**
- Create: `src/dit/core/store.py`
- Create: `tests/test_store.py`

- [ ] **Step 1: Write failing tests**

`tests/test_store.py`:
```python
from pathlib import Path

from dit.core.store import ObjectStore


class TestObjectStore:
    def test_write_and_read(self, tmp_repo: Path):
        store = ObjectStore(tmp_repo / ".datahub" / "objects")
        data = b"hello world"
        h = store.write("rows", data)
        assert len(h) == 64
        assert store.read("rows", h) == data

    def test_read_nonexistent_returns_none(self, tmp_repo: Path):
        store = ObjectStore(tmp_repo / ".datahub" / "objects")
        assert store.read("rows", "00" * 32) is None

    def test_exists(self, tmp_repo: Path):
        store = ObjectStore(tmp_repo / ".datahub" / "objects")
        data = b"test data"
        h = store.write("rows", data)
        assert store.exists("rows", h) is True
        assert store.exists("rows", "00" * 32) is False

    def test_two_level_sharding(self, tmp_repo: Path):
        store = ObjectStore(tmp_repo / ".datahub" / "objects")
        data = b"sharding test"
        h = store.write("rows", data)
        expected_path = store.root / "rows" / h[0:2] / h[2:4] / h
        assert expected_path.exists()

    def test_zstd_compression(self, tmp_repo: Path):
        store = ObjectStore(tmp_repo / ".datahub" / "objects")
        data = b"A" * 10000
        h = store.write("rows", data)
        raw_path = store.root / "rows" / h[0:2] / h[2:4] / h
        assert raw_path.stat().st_size < len(data)

    def test_idempotent_write(self, tmp_repo: Path):
        store = ObjectStore(tmp_repo / ".datahub" / "objects")
        data = b"same content"
        h1 = store.write("rows", data)
        h2 = store.write("rows", data)
        assert h1 == h2

    def test_different_types_independent(self, tmp_repo: Path):
        store = ObjectStore(tmp_repo / ".datahub" / "objects")
        data = b"some bytes"
        h = store.write("manifests", data)
        assert store.exists("manifests", h) is True
        assert store.exists("rows", h) is False

    def test_batch_exists(self, tmp_repo: Path):
        store = ObjectStore(tmp_repo / ".datahub" / "objects")
        h1 = store.write("rows", b"one")
        h2 = store.write("rows", b"two")
        missing = "00" * 32
        result = store.batch_exists("rows", [h1, h2, missing])
        assert result == {h1: True, h2: True, missing: False}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_store.py -v
```

Expected: ImportError

- [ ] **Step 3: Implement `store.py`**

`src/dit/core/store.py`:
```python
import hashlib
import os
import tempfile
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
            os.rename(tmp_path, dest)
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
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_store.py -v
```

Expected: All 8 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/dit/core/store.py tests/test_store.py
git commit -m "feat: local object store with zstd compression and two-level sharding"
```

---

### Task 6: Ref Store (`refs.py`)

**Files:**
- Create: `src/dit/core/refs.py`
- Create: `tests/test_refs.py`

- [ ] **Step 1: Write failing tests**

`tests/test_refs.py`:
```python
from pathlib import Path

from dit.core.refs import RefStore


class TestRefStore:
    def test_get_head_default_main(self, tmp_repo: Path):
        dot = tmp_repo / ".datahub"
        dot.mkdir()
        refs = RefStore(dot)
        refs.init()
        assert refs.get_head() == "ref:main"

    def test_get_set_branch(self, tmp_repo: Path):
        dot = tmp_repo / ".datahub"
        dot.mkdir()
        refs = RefStore(dot)
        refs.init()
        refs.set_branch("main", "aa" * 32)
        assert refs.get_branch("main") == "aa" * 32

    def test_get_nonexistent_branch_returns_none(self, tmp_repo: Path):
        dot = tmp_repo / ".datahub"
        dot.mkdir()
        refs = RefStore(dot)
        refs.init()
        assert refs.get_branch("nonexistent") is None

    def test_resolve_head_no_commits(self, tmp_repo: Path):
        dot = tmp_repo / ".datahub"
        dot.mkdir()
        refs = RefStore(dot)
        refs.init()
        assert refs.resolve_head() is None

    def test_resolve_head_with_commit(self, tmp_repo: Path):
        dot = tmp_repo / ".datahub"
        dot.mkdir()
        refs = RefStore(dot)
        refs.init()
        refs.set_branch("main", "cc" * 32)
        assert refs.resolve_head() == "cc" * 32

    def test_current_branch_name(self, tmp_repo: Path):
        dot = tmp_repo / ".datahub"
        dot.mkdir()
        refs = RefStore(dot)
        refs.init()
        assert refs.current_branch() == "main"

    def test_list_branches(self, tmp_repo: Path):
        dot = tmp_repo / ".datahub"
        dot.mkdir()
        refs = RefStore(dot)
        refs.init()
        refs.set_branch("main", "aa" * 32)
        refs.set_branch("dev", "bb" * 32)
        branches = refs.list_branches()
        assert set(branches.keys()) == {"main", "dev"}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_refs.py -v
```

Expected: ImportError

- [ ] **Step 3: Implement `refs.py`**

`src/dit/core/refs.py`:
```python
from pathlib import Path
from typing import Optional


class RefStore:
    def __init__(self, dot_datahub: Path):
        self.dot = dot_datahub
        self.head_file = dot_datahub / "HEAD"
        self.refs_dir = dot_datahub / "refs" / "heads"

    def init(self) -> None:
        self.refs_dir.mkdir(parents=True, exist_ok=True)
        if not self.head_file.exists():
            self.head_file.write_text("ref:main\n")

    def get_head(self) -> str:
        return self.head_file.read_text().strip()

    def current_branch(self) -> Optional[str]:
        head = self.get_head()
        if head.startswith("ref:"):
            return head[4:]
        return None

    def resolve_head(self) -> Optional[str]:
        head = self.get_head()
        if head.startswith("ref:"):
            return self.get_branch(head[4:])
        return head

    def get_branch(self, name: str) -> Optional[str]:
        path = self.refs_dir / name
        if not path.exists():
            return None
        return path.read_text().strip()

    def set_branch(self, name: str, commit_hash: str) -> None:
        path = self.refs_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(commit_hash + "\n")

    def list_branches(self) -> dict[str, str]:
        result = {}
        if self.refs_dir.exists():
            for f in self.refs_dir.iterdir():
                if f.is_file():
                    result[f.name] = f.read_text().strip()
        return result
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_refs.py -v
```

Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/dit/core/refs.py tests/test_refs.py
git commit -m "feat: ref store for HEAD and branch management"
```

---

### Task 7: Staging Index (`index.py`)

**Files:**
- Create: `src/dit/core/index.py`
- Create: `tests/test_index.py`

The staging index tracks which files have been `dit add`-ed. It stores a JSON mapping of `{relative_path: manifest_hash}`. On `dit add <file>`, the CLI reads the JSONL, computes row hashes, builds a Manifest, writes all objects to the store, and records the manifest hash in the index.

- [ ] **Step 1: Write failing tests**

`tests/test_index.py`:
```python
from pathlib import Path

from dit.core.index import StagingIndex


class TestStagingIndex:
    def test_empty_index(self, tmp_repo: Path):
        idx = StagingIndex(tmp_repo / ".datahub" / "index")
        assert idx.entries() == {}

    def test_stage_and_read(self, tmp_repo: Path):
        idx = StagingIndex(tmp_repo / ".datahub" / "index")
        idx.stage("coding.jsonl", "aa" * 32)
        entries = idx.entries()
        assert entries == {"coding.jsonl": "aa" * 32}

    def test_stage_overwrites(self, tmp_repo: Path):
        idx = StagingIndex(tmp_repo / ".datahub" / "index")
        idx.stage("coding.jsonl", "aa" * 32)
        idx.stage("coding.jsonl", "bb" * 32)
        assert idx.entries()["coding.jsonl"] == "bb" * 32

    def test_unstage(self, tmp_repo: Path):
        idx = StagingIndex(tmp_repo / ".datahub" / "index")
        idx.stage("coding.jsonl", "aa" * 32)
        idx.unstage("coding.jsonl")
        assert idx.entries() == {}

    def test_clear(self, tmp_repo: Path):
        idx = StagingIndex(tmp_repo / ".datahub" / "index")
        idx.stage("a.jsonl", "aa" * 32)
        idx.stage("b.jsonl", "bb" * 32)
        idx.clear()
        assert idx.entries() == {}

    def test_persistence(self, tmp_repo: Path):
        path = tmp_repo / ".datahub" / "index"
        idx1 = StagingIndex(path)
        idx1.stage("x.jsonl", "cc" * 32)
        idx2 = StagingIndex(path)
        assert idx2.entries() == {"x.jsonl": "cc" * 32}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_index.py -v
```

Expected: ImportError

- [ ] **Step 3: Implement `index.py`**

`src/dit/core/index.py`:
```python
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
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_index.py -v
```

Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/dit/core/index.py tests/test_index.py
git commit -m "feat: staging index for tracking added files"
```

---

### Task 8: Workspace Scanner (`workspace.py`)

**Files:**
- Create: `src/dit/core/workspace.py`
- Create: `tests/test_workspace.py`

The workspace scanner finds JSONL files in the working directory, computes their current manifest hashes, and compares against HEAD to detect changes.

- [ ] **Step 1: Write failing tests**

`tests/test_workspace.py`:
```python
import json
from pathlib import Path

from dit.core.workspace import find_jsonl_files, build_manifest_for_file
from dit.core.objects import deserialize_manifest


class TestFindJsonlFiles:
    def test_finds_jsonl(self, tmp_repo: Path):
        (tmp_repo / "a.jsonl").write_text('{"x":1}\n')
        (tmp_repo / "b.txt").write_text("not jsonl")
        (tmp_repo / "sub").mkdir()
        (tmp_repo / "sub" / "c.jsonl").write_text('{"y":2}\n')
        files = find_jsonl_files(tmp_repo)
        rel_paths = sorted(str(f.relative_to(tmp_repo)) for f in files)
        assert rel_paths == ["a.jsonl", "sub/c.jsonl"]

    def test_ignores_datahub_dir(self, tmp_repo: Path):
        (tmp_repo / ".datahub").mkdir()
        (tmp_repo / ".datahub" / "internal.jsonl").write_text('{"z":1}\n')
        (tmp_repo / "real.jsonl").write_text('{"w":1}\n')
        files = find_jsonl_files(tmp_repo)
        assert len(files) == 1
        assert files[0].name == "real.jsonl"


class TestBuildManifest:
    def test_builds_manifest(self, tmp_repo: Path):
        fp = tmp_repo / "test.jsonl"
        fp.write_text('{"a":1}\n{"b":2}\n')
        manifest, row_data = build_manifest_for_file(fp)
        assert len(manifest.entries) == 2
        assert len(row_data) == 2
        for entry in manifest.entries:
            assert len(entry.row_hash) == 64

    def test_preserves_row_order(self, tmp_repo: Path):
        fp = tmp_repo / "test.jsonl"
        rows = [{"id": i} for i in range(10)]
        fp.write_text("".join(json.dumps(r) + "\n" for r in rows))
        manifest, row_data = build_manifest_for_file(fp)
        # Hashes should follow original order
        assert len(manifest.entries) == 10

    def test_row_data_matches_entries(self, tmp_repo: Path):
        fp = tmp_repo / "test.jsonl"
        fp.write_text('{"x":1}\n{"y":2}\n')
        manifest, row_data = build_manifest_for_file(fp)
        for entry in manifest.entries:
            assert entry.row_hash in row_data
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_workspace.py -v
```

Expected: ImportError

- [ ] **Step 3: Implement `workspace.py`**

`src/dit/core/workspace.py`:
```python
from pathlib import Path

from dit.core.hash import canonical_json, row_hash, query_fingerprint
from dit.core.objects import Manifest, ManifestEntry
from dit.utils.jsonl import read_rows


def find_jsonl_files(root: Path) -> list[Path]:
    results = []
    for p in sorted(root.rglob("*.jsonl")):
        if ".datahub" in p.parts:
            continue
        results.append(p)
    return results


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
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_workspace.py -v
```

Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/dit/core/workspace.py tests/test_workspace.py
git commit -m "feat: workspace scanner to find JSONL files and build manifests"
```

---

### Task 9: Diff Engine (`diff.py`)

**Files:**
- Create: `src/dit/core/diff.py`
- Create: `tests/test_diff.py`

- [ ] **Step 1: Write failing tests**

`tests/test_diff.py`:
```python
from dit.core.objects import Manifest, ManifestEntry
from dit.core.diff import diff_manifests, DiffResult


def _entry(row_hash: str, qfp: str | None = None) -> ManifestEntry:
    return ManifestEntry(row_hash=row_hash.ljust(64, "0"), query_fingerprint=qfp)


class TestDiffManifests:
    def test_identical(self):
        m = Manifest(entries=[_entry("aa"), _entry("bb")])
        result = diff_manifests(m, m)
        assert result.added == []
        assert result.removed == []
        assert result.refreshed == []

    def test_added_rows(self):
        old = Manifest(entries=[_entry("aa")])
        new = Manifest(entries=[_entry("aa"), _entry("bb")])
        result = diff_manifests(old, new)
        assert len(result.added) == 1
        assert result.added[0].row_hash == "bb".ljust(64, "0")
        assert result.removed == []

    def test_removed_rows(self):
        old = Manifest(entries=[_entry("aa"), _entry("bb")])
        new = Manifest(entries=[_entry("aa")])
        result = diff_manifests(old, new)
        assert len(result.removed) == 1
        assert result.removed[0].row_hash == "bb".ljust(64, "0")

    def test_refreshed_detection(self):
        qfp = "qfp_same".ljust(64, "0")
        old = Manifest(entries=[_entry("aa", qfp)])
        new = Manifest(entries=[_entry("bb", qfp)])
        result = diff_manifests(old, new)
        assert len(result.refreshed) == 1
        assert result.refreshed[0] == (
            "aa".ljust(64, "0"),
            "bb".ljust(64, "0"),
            qfp,
        )
        # Refreshed rows should not appear in added/removed
        assert result.added == []
        assert result.removed == []

    def test_mixed_changes(self):
        qfp = "shared_qfp".ljust(64, "0")
        old = Manifest(entries=[
            _entry("keep"),
            _entry("remove_me"),
            _entry("old_resp", qfp),
        ])
        new = Manifest(entries=[
            _entry("keep"),
            _entry("brand_new"),
            _entry("new_resp", qfp),
        ])
        result = diff_manifests(old, new)
        assert len(result.refreshed) == 1
        assert len(result.added) == 1
        assert result.added[0].row_hash == "brand_new".ljust(64, "0")
        assert len(result.removed) == 1
        assert result.removed[0].row_hash == "remove_me".ljust(64, "0")

    def test_summary(self):
        old = Manifest(entries=[_entry("aa"), _entry("bb")])
        new = Manifest(entries=[_entry("aa"), _entry("cc"), _entry("dd")])
        result = diff_manifests(old, new)
        s = result.summary()
        assert "+2" in s
        assert "-1" in s
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_diff.py -v
```

Expected: ImportError

- [ ] **Step 3: Implement `diff.py`**

`src/dit/core/diff.py`:
```python
from dataclasses import dataclass, field

from dit.core.objects import Manifest, ManifestEntry


@dataclass
class DiffResult:
    added: list[ManifestEntry] = field(default_factory=list)
    removed: list[ManifestEntry] = field(default_factory=list)
    refreshed: list[tuple[str, str, str]] = field(default_factory=list)

    def summary(self) -> str:
        parts = []
        if self.added:
            parts.append(f"+{len(self.added)}")
        if self.removed:
            parts.append(f"-{len(self.removed)}")
        if self.refreshed:
            parts.append(f"~{len(self.refreshed)} refreshed")
        return ", ".join(parts) if parts else "no changes"


def diff_manifests(old: Manifest, new: Manifest) -> DiffResult:
    old_hashes = {e.row_hash for e in old.entries}
    new_hashes = {e.row_hash for e in new.entries}

    raw_removed = [e for e in old.entries if e.row_hash not in new_hashes]
    raw_added = [e for e in new.entries if e.row_hash not in old_hashes]

    # Detect refreshes: same query_fingerprint, different row_hash
    old_by_qfp: dict[str, ManifestEntry] = {}
    for e in raw_removed:
        if e.query_fingerprint:
            old_by_qfp[e.query_fingerprint] = e

    refreshed: list[tuple[str, str, str]] = []
    refreshed_old_hashes: set[str] = set()
    refreshed_new_hashes: set[str] = set()

    for e in raw_added:
        if e.query_fingerprint and e.query_fingerprint in old_by_qfp:
            old_entry = old_by_qfp[e.query_fingerprint]
            refreshed.append((old_entry.row_hash, e.row_hash, e.query_fingerprint))
            refreshed_old_hashes.add(old_entry.row_hash)
            refreshed_new_hashes.add(e.row_hash)

    added = [e for e in raw_added if e.row_hash not in refreshed_new_hashes]
    removed = [e for e in raw_removed if e.row_hash not in refreshed_old_hashes]

    return DiffResult(added=added, removed=removed, refreshed=refreshed)
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_diff.py -v
```

Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/dit/core/diff.py tests/test_diff.py
git commit -m "feat: diff engine with response-refresh detection"
```

---

### Task 10: CLI — `dit init`

**Files:**
- Modify: `src/dit/cli/main.py`
- Create: `tests/test_cli.py`

- [ ] **Step 1: Write failing test**

`tests/test_cli.py`:
```python
import os
from pathlib import Path

from typer.testing import CliRunner

from dit.cli.main import app

runner = CliRunner()


class TestInit:
    def test_init_creates_datahub_dir(self, tmp_path: Path):
        os.chdir(tmp_path)
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0
        assert (tmp_path / ".datahub").is_dir()
        assert (tmp_path / ".datahub" / "HEAD").exists()
        assert (tmp_path / ".datahub" / "refs" / "heads").is_dir()
        assert (tmp_path / ".datahub" / "objects").is_dir()

    def test_init_already_exists(self, tmp_path: Path):
        os.chdir(tmp_path)
        runner.invoke(app, ["init"])
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0
        assert "already" in result.stdout.lower() or "initialized" in result.stdout.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_cli.py::TestInit -v
```

Expected: FAIL (no `init` command)

- [ ] **Step 3: Implement `init` command**

Replace the entire `src/dit/cli/main.py`:
```python
from pathlib import Path

import typer

from dit.core.refs import RefStore
from dit.core.store import ObjectStore

app = typer.Typer(name="dit", help="Git-like version control for SFT training data.")


def find_repo_root() -> Path:
    cwd = Path.cwd()
    p = cwd
    while True:
        if (p / ".datahub").is_dir():
            return p
        if p.parent == p:
            break
        p = p.parent
    typer.echo("fatal: not a dit repository", err=True)
    raise typer.Exit(1)


def get_dot(repo_root: Path) -> Path:
    return repo_root / ".datahub"


@app.command()
def version():
    """Print dit version."""
    from dit import __version__
    typer.echo(f"dit {__version__}")


@app.command()
def init():
    """Initialize a new dit repository in the current directory."""
    cwd = Path.cwd()
    dot = cwd / ".datahub"
    if dot.exists():
        typer.echo(f"Already initialized dit repository in {cwd}")
        return
    dot.mkdir()
    (dot / "objects").mkdir()
    RefStore(dot).init()
    typer.echo(f"Initialized empty dit repository in {cwd}")


if __name__ == "__main__":
    app()
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_cli.py::TestInit -v
```

Expected: All 2 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/dit/cli/main.py tests/test_cli.py
git commit -m "feat: dit init command"
```

---

### Task 11: CLI — `dit add`

**Files:**
- Modify: `src/dit/cli/main.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_cli.py`:
```python
import json


class TestAdd:
    def test_add_single_file(self, tmp_path: Path):
        os.chdir(tmp_path)
        runner.invoke(app, ["init"])
        fp = tmp_path / "coding.jsonl"
        fp.write_text(json.dumps({"messages": [{"role": "user", "content": "hi"}]}) + "\n")
        result = runner.invoke(app, ["add", "coding.jsonl"])
        assert result.exit_code == 0

        idx_path = tmp_path / ".datahub" / "index"
        assert idx_path.exists()
        idx = json.loads(idx_path.read_text())
        assert "coding.jsonl" in idx

    def test_add_dot(self, tmp_path: Path):
        os.chdir(tmp_path)
        runner.invoke(app, ["init"])
        (tmp_path / "a.jsonl").write_text('{"x":1}\n')
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "b.jsonl").write_text('{"y":2}\n')
        result = runner.invoke(app, ["add", "."])
        assert result.exit_code == 0
        idx = json.loads((tmp_path / ".datahub" / "index").read_text())
        assert "a.jsonl" in idx
        assert "sub/b.jsonl" in idx

    def test_add_nonexistent_file(self, tmp_path: Path):
        os.chdir(tmp_path)
        runner.invoke(app, ["init"])
        result = runner.invoke(app, ["add", "nope.jsonl"])
        assert result.exit_code != 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_cli.py::TestAdd -v
```

Expected: FAIL

- [ ] **Step 3: Implement `add` command**

Add to `src/dit/cli/main.py` (after the `init` command):
```python
from dit.core.index import StagingIndex
from dit.core.workspace import find_jsonl_files, build_manifest_for_file
from dit.core.objects import serialize_manifest


@app.command()
def add(paths: list[str] = typer.Argument(..., help="Files or directories to stage")):
    """Stage JSONL files for the next commit."""
    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    index = StagingIndex(dot / "index")

    for path_str in paths:
        target = Path(path_str).resolve()
        if path_str == ".":
            files = find_jsonl_files(repo_root)
        elif target.is_dir():
            files = find_jsonl_files(target)
        elif target.is_file() and target.suffix == ".jsonl":
            files = [target]
        else:
            typer.echo(f"fatal: pathspec '{path_str}' did not match any jsonl files", err=True)
            raise typer.Exit(1)

        for fp in files:
            manifest, row_data = build_manifest_for_file(fp)
            for rh, data in row_data.items():
                store.write("rows", data)
            manifest_bytes = serialize_manifest(manifest)
            manifest_hash = store.write("manifests", manifest_bytes)
            rel = str(fp.relative_to(repo_root))
            index.stage(rel, manifest_hash)
            typer.echo(f"  staged {rel} ({len(manifest.entries)} rows)")
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_cli.py::TestAdd -v
```

Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/dit/cli/main.py tests/test_cli.py
git commit -m "feat: dit add command — stage JSONL files with row-level hashing"
```

---

### Task 12: CLI — `dit commit`

**Files:**
- Modify: `src/dit/cli/main.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_cli.py`:
```python
class TestCommit:
    def _setup_staged(self, tmp_path: Path):
        os.chdir(tmp_path)
        runner.invoke(app, ["init"])
        fp = tmp_path / "data.jsonl"
        fp.write_text('{"messages":[{"role":"user","content":"test"}]}\n')
        runner.invoke(app, ["add", "data.jsonl"])

    def test_commit_creates_commit(self, tmp_path: Path):
        self._setup_staged(tmp_path)
        result = runner.invoke(app, ["commit", "-m", "initial"])
        assert result.exit_code == 0
        assert "initial" in result.stdout

        head_ref = (tmp_path / ".datahub" / "refs" / "heads" / "main").read_text().strip()
        assert len(head_ref) == 64

    def test_commit_clears_index(self, tmp_path: Path):
        self._setup_staged(tmp_path)
        runner.invoke(app, ["commit", "-m", "first"])
        idx = json.loads((tmp_path / ".datahub" / "index").read_text())
        assert idx == {}

    def test_commit_nothing_staged(self, tmp_path: Path):
        os.chdir(tmp_path)
        runner.invoke(app, ["init"])
        result = runner.invoke(app, ["commit", "-m", "empty"])
        assert result.exit_code != 0

    def test_second_commit_has_parent(self, tmp_path: Path):
        self._setup_staged(tmp_path)
        runner.invoke(app, ["commit", "-m", "first"])
        first_hash = (tmp_path / ".datahub" / "refs" / "heads" / "main").read_text().strip()

        (tmp_path / "data.jsonl").write_text('{"messages":[{"role":"user","content":"updated"}]}\n')
        runner.invoke(app, ["add", "data.jsonl"])
        runner.invoke(app, ["commit", "-m", "second"])
        second_hash = (tmp_path / ".datahub" / "refs" / "heads" / "main").read_text().strip()

        assert first_hash != second_hash
        # Verify parent by reading the commit object
        from dit.core.store import ObjectStore
        from dit.core.objects import deserialize_commit
        store = ObjectStore(tmp_path / ".datahub" / "objects")
        commit_data = store.read("commits", second_hash)
        commit = deserialize_commit(commit_data)
        assert commit.parent_hashes == [first_hash]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_cli.py::TestCommit -v
```

Expected: FAIL

- [ ] **Step 3: Implement `commit` command**

Add to `src/dit/cli/main.py`:
```python
import time

from dit.core.objects import (
    Tree,
    TreeEntry,
    Commit,
    serialize_tree,
    serialize_commit,
    object_hash,
)


@app.command()
def commit(message: str = typer.Option(..., "-m", help="Commit message")):
    """Create a commit from staged files."""
    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    index = StagingIndex(dot / "index")
    refs = RefStore(dot)

    staged = index.entries()
    if not staged:
        typer.echo("nothing to commit (staging area is empty)", err=True)
        raise typer.Exit(1)

    # Build tree from staged manifests + any existing tree entries from HEAD
    head_commit_hash = refs.resolve_head()
    existing_tree_entries: dict[str, TreeEntry] = {}
    if head_commit_hash:
        from dit.core.objects import deserialize_commit, deserialize_tree
        commit_data = store.read("commits", head_commit_hash)
        old_commit = deserialize_commit(commit_data)
        tree_data = store.read("trees", old_commit.tree_hash)
        old_tree = deserialize_tree(tree_data)
        for e in old_tree.entries:
            existing_tree_entries[e.name] = e

    # Merge staged files into tree
    for rel_path, manifest_hash in staged.items():
        existing_tree_entries[rel_path] = TreeEntry(
            name=rel_path, obj_type="manifest", obj_hash=manifest_hash
        )

    tree = Tree(entries=list(existing_tree_entries.values()))
    tree_bytes = serialize_tree(tree)
    tree_hash = store.write("trees", tree_bytes)

    parent_hashes = [head_commit_hash] if head_commit_hash else []
    c = Commit(
        tree_hash=tree_hash,
        parent_hashes=parent_hashes,
        author=_get_author(),
        message=message,
        timestamp=int(time.time()),
    )
    commit_bytes = serialize_commit(c)
    commit_hash = store.write("commits", commit_bytes)

    branch = refs.current_branch()
    refs.set_branch(branch, commit_hash)
    index.clear()
    typer.echo(f"[{branch} {commit_hash[:8]}] {message}")


def _get_author() -> str:
    import os
    return os.environ.get("DIT_AUTHOR", os.environ.get("USER", "unknown"))
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_cli.py::TestCommit -v
```

Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/dit/cli/main.py tests/test_cli.py
git commit -m "feat: dit commit command — create tree + commit objects"
```

---

### Task 13: CLI — `dit log`

**Files:**
- Modify: `src/dit/cli/main.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_cli.py`:
```python
class TestLog:
    def test_log_shows_commits(self, tmp_path: Path):
        os.chdir(tmp_path)
        runner.invoke(app, ["init"])
        (tmp_path / "a.jsonl").write_text('{"x":1}\n')
        runner.invoke(app, ["add", "."])
        runner.invoke(app, ["commit", "-m", "first commit"])
        (tmp_path / "a.jsonl").write_text('{"x":1}\n{"y":2}\n')
        runner.invoke(app, ["add", "."])
        runner.invoke(app, ["commit", "-m", "second commit"])

        result = runner.invoke(app, ["log"])
        assert result.exit_code == 0
        assert "second commit" in result.stdout
        assert "first commit" in result.stdout
        # Most recent first
        assert result.stdout.index("second") < result.stdout.index("first")

    def test_log_empty_repo(self, tmp_path: Path):
        os.chdir(tmp_path)
        runner.invoke(app, ["init"])
        result = runner.invoke(app, ["log"])
        assert result.exit_code == 0
        assert "no commits" in result.stdout.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_cli.py::TestLog -v
```

Expected: FAIL

- [ ] **Step 3: Implement `log` command**

Add to `src/dit/cli/main.py`:
```python
from datetime import datetime, timezone
from dit.core.objects import deserialize_commit


@app.command()
def log():
    """Show commit history."""
    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)

    commit_hash = refs.resolve_head()
    if not commit_hash:
        typer.echo("No commits yet.")
        return

    while commit_hash:
        data = store.read("commits", commit_hash)
        c = deserialize_commit(data)
        ts = datetime.fromtimestamp(c.timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        typer.echo(f"commit {commit_hash}")
        typer.echo(f"Author: {c.author}")
        typer.echo(f"Date:   {ts}")
        typer.echo(f"\n    {c.message}\n")
        commit_hash = c.parent_hashes[0] if c.parent_hashes else None
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_cli.py::TestLog -v
```

Expected: All 2 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/dit/cli/main.py tests/test_cli.py
git commit -m "feat: dit log command — walk commit chain"
```

---

### Task 14: CLI — `dit diff`

**Files:**
- Modify: `src/dit/cli/main.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_cli.py`:
```python
class TestDiff:
    def test_diff_shows_changes(self, tmp_path: Path):
        os.chdir(tmp_path)
        runner.invoke(app, ["init"])
        (tmp_path / "data.jsonl").write_text('{"messages":[{"role":"user","content":"hello"}]}\n')
        runner.invoke(app, ["add", "."])
        runner.invoke(app, ["commit", "-m", "initial"])

        # Modify: remove old row, add new row
        (tmp_path / "data.jsonl").write_text('{"messages":[{"role":"user","content":"world"}]}\n')
        result = runner.invoke(app, ["diff"])
        assert result.exit_code == 0
        assert "+1" in result.stdout
        assert "-1" in result.stdout

    def test_diff_no_changes(self, tmp_path: Path):
        os.chdir(tmp_path)
        runner.invoke(app, ["init"])
        (tmp_path / "data.jsonl").write_text('{"x":1}\n')
        runner.invoke(app, ["add", "."])
        runner.invoke(app, ["commit", "-m", "initial"])
        result = runner.invoke(app, ["diff"])
        assert result.exit_code == 0
        assert "no changes" in result.stdout.lower()

    def test_diff_new_file(self, tmp_path: Path):
        os.chdir(tmp_path)
        runner.invoke(app, ["init"])
        (tmp_path / "old.jsonl").write_text('{"x":1}\n')
        runner.invoke(app, ["add", "."])
        runner.invoke(app, ["commit", "-m", "initial"])
        (tmp_path / "new.jsonl").write_text('{"y":2}\n')
        result = runner.invoke(app, ["diff"])
        assert result.exit_code == 0
        assert "new.jsonl" in result.stdout

    def test_diff_detects_refresh(self, tmp_path: Path):
        os.chdir(tmp_path)
        runner.invoke(app, ["init"])
        row = {"messages": [
            {"role": "user", "content": "implement LRU"},
            {"role": "assistant", "content": "old response"},
        ]}
        (tmp_path / "data.jsonl").write_text(json.dumps(row) + "\n")
        runner.invoke(app, ["add", "."])
        runner.invoke(app, ["commit", "-m", "initial"])

        row["messages"][1]["content"] = "new response"
        (tmp_path / "data.jsonl").write_text(json.dumps(row) + "\n")
        result = runner.invoke(app, ["diff"])
        assert result.exit_code == 0
        assert "refresh" in result.stdout.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_cli.py::TestDiff -v
```

Expected: FAIL

- [ ] **Step 3: Implement `diff` command**

Add to `src/dit/cli/main.py`:
```python
from dit.core.diff import diff_manifests
from dit.core.objects import deserialize_tree, deserialize_manifest, Manifest


@app.command()
def diff():
    """Show changes between working directory and HEAD."""
    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)

    # Build current working directory state
    current_files: dict[str, Manifest] = {}
    for fp in find_jsonl_files(repo_root):
        rel = str(fp.relative_to(repo_root))
        manifest, _ = build_manifest_for_file(fp)
        current_files[rel] = manifest

    # Load HEAD state
    head_files: dict[str, Manifest] = {}
    head_hash = refs.resolve_head()
    if head_hash:
        commit_data = store.read("commits", head_hash)
        head_commit = deserialize_commit(commit_data)
        tree_data = store.read("trees", head_commit.tree_hash)
        tree = deserialize_tree(tree_data)
        for entry in tree.entries:
            if entry.obj_type == "manifest":
                m_data = store.read("manifests", entry.obj_hash)
                head_files[entry.name] = deserialize_manifest(m_data)

    all_files = sorted(set(list(current_files.keys()) + list(head_files.keys())))
    any_changes = False

    for rel in all_files:
        old_m = head_files.get(rel, Manifest(entries=[]))
        new_m = current_files.get(rel, Manifest(entries=[]))
        result = diff_manifests(old_m, new_m)

        if not result.added and not result.removed and not result.refreshed:
            continue

        any_changes = True
        old_count = len(old_m.entries)
        new_count = len(new_m.entries)

        if rel not in head_files:
            typer.echo(f"{rel}: new file ({new_count} rows)")
        elif rel not in current_files:
            typer.echo(f"{rel}: deleted ({old_count} rows)")
        else:
            typer.echo(f"{rel}: {old_count} → {new_count} rows ({result.summary()})")

        if result.refreshed:
            typer.echo(f"  Likely refreshed: {len(result.refreshed)} rows")

    if not any_changes:
        typer.echo("No changes.")
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_cli.py::TestDiff -v
```

Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/dit/cli/main.py tests/test_cli.py
git commit -m "feat: dit diff command — working directory vs HEAD with refresh detection"
```

---

### Task 15: CLI — `dit status`

**Files:**
- Modify: `src/dit/cli/main.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_cli.py`:
```python
class TestStatus:
    def test_status_clean(self, tmp_path: Path):
        os.chdir(tmp_path)
        runner.invoke(app, ["init"])
        (tmp_path / "a.jsonl").write_text('{"x":1}\n')
        runner.invoke(app, ["add", "."])
        runner.invoke(app, ["commit", "-m", "init"])
        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        assert "clean" in result.stdout.lower() or "nothing" in result.stdout.lower()

    def test_status_staged_files(self, tmp_path: Path):
        os.chdir(tmp_path)
        runner.invoke(app, ["init"])
        (tmp_path / "a.jsonl").write_text('{"x":1}\n')
        runner.invoke(app, ["add", "."])
        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        assert "staged" in result.stdout.lower()
        assert "a.jsonl" in result.stdout

    def test_status_modified_file(self, tmp_path: Path):
        os.chdir(tmp_path)
        runner.invoke(app, ["init"])
        (tmp_path / "a.jsonl").write_text('{"x":1}\n')
        runner.invoke(app, ["add", "."])
        runner.invoke(app, ["commit", "-m", "init"])
        (tmp_path / "a.jsonl").write_text('{"x":1}\n{"y":2}\n')
        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        assert "modified" in result.stdout.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_cli.py::TestStatus -v
```

Expected: FAIL

- [ ] **Step 3: Implement `status` command**

Add to `src/dit/cli/main.py`:
```python
@app.command()
def status():
    """Show working directory status."""
    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    index = StagingIndex(dot / "index")
    refs = RefStore(dot)

    branch = refs.current_branch() or "HEAD"
    typer.echo(f"On branch {branch}")

    staged = index.entries()
    if staged:
        typer.echo("\nStaged files:")
        for rel in sorted(staged.keys()):
            typer.echo(f"  {rel}")

    # Load HEAD manifests
    head_manifests: dict[str, str] = {}
    head_hash = refs.resolve_head()
    if head_hash:
        commit_data = store.read("commits", head_hash)
        head_commit = deserialize_commit(commit_data)
        tree_data = store.read("trees", head_commit.tree_hash)
        tree = deserialize_tree(tree_data)
        for entry in tree.entries:
            if entry.obj_type == "manifest":
                head_manifests[entry.name] = entry.obj_hash

    # Compare working directory to HEAD
    current_files = find_jsonl_files(repo_root)
    current_rels = {str(f.relative_to(repo_root)) for f in current_files}
    head_rels = set(head_manifests.keys())

    modified = []
    new_files = []
    deleted = sorted(head_rels - current_rels)

    for fp in current_files:
        rel = str(fp.relative_to(repo_root))
        if rel not in head_manifests:
            new_files.append(rel)
        else:
            manifest, _ = build_manifest_for_file(fp)
            from dit.core.objects import serialize_manifest as _ser, object_hash as _oh
            current_hash = _oh(_ser(manifest))
            if current_hash != head_manifests[rel]:
                modified.append(rel)

    has_changes = modified or new_files or deleted
    if not staged and not has_changes:
        typer.echo("\nNothing to commit, working directory clean.")
        return

    if modified or new_files or deleted:
        typer.echo("\nUnstaged changes:")
        for rel in sorted(modified):
            typer.echo(f"  modified: {rel}")
        for rel in sorted(new_files):
            typer.echo(f"  new file: {rel}")
        for rel in deleted:
            typer.echo(f"  deleted:  {rel}")
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_cli.py::TestStatus -v
```

Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/dit/cli/main.py tests/test_cli.py
git commit -m "feat: dit status command — show staged and unstaged changes"
```

---

### Task 16: End-to-End Integration Test

**Files:**
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write the integration test**

Append to `tests/test_cli.py`:
```python
class TestEndToEnd:
    def test_full_workflow(self, tmp_path: Path):
        """init → add → commit → modify → diff → add → commit → log"""
        os.chdir(tmp_path)

        # Init
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0

        # Create data
        rows = [
            {"messages": [{"role": "user", "content": f"question {i}"}, {"role": "assistant", "content": f"answer {i}"}]}
            for i in range(5)
        ]
        (tmp_path / "data.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))

        # Add + commit
        result = runner.invoke(app, ["add", "."])
        assert result.exit_code == 0
        result = runner.invoke(app, ["commit", "-m", "add 5 conversations"])
        assert result.exit_code == 0

        # Status should be clean
        result = runner.invoke(app, ["status"])
        assert "clean" in result.stdout.lower() or "nothing" in result.stdout.lower()

        # Modify: remove 2 rows, add 1, refresh 1 response
        modified_rows = [
            rows[0],
            rows[1],
            # rows[2] and rows[3] removed
            {"messages": [{"role": "user", "content": rows[4]["messages"][0]["content"]}, {"role": "assistant", "content": "refreshed answer 4"}]},
            {"messages": [{"role": "user", "content": "brand new question"}, {"role": "assistant", "content": "new answer"}]},
        ]
        (tmp_path / "data.jsonl").write_text("".join(json.dumps(r) + "\n" for r in modified_rows))

        # Diff should detect changes
        result = runner.invoke(app, ["diff"])
        assert result.exit_code == 0
        assert "data.jsonl" in result.stdout

        # Add + commit
        runner.invoke(app, ["add", "."])
        result = runner.invoke(app, ["commit", "-m", "remove 2, add 1, refresh 1"])
        assert result.exit_code == 0

        # Log should show 2 commits
        result = runner.invoke(app, ["log"])
        assert "add 5 conversations" in result.stdout
        assert "remove 2, add 1, refresh 1" in result.stdout

    def test_multi_file_workflow(self, tmp_path: Path):
        """Test multiple JSONL files in subdirectories."""
        os.chdir(tmp_path)
        runner.invoke(app, ["init"])

        (tmp_path / "feature-impl").mkdir()
        (tmp_path / "feature-impl" / "coding.jsonl").write_text('{"messages":[{"role":"user","content":"code q"}]}\n')
        (tmp_path / "bug-fix").mkdir()
        (tmp_path / "bug-fix" / "fixes.jsonl").write_text('{"messages":[{"role":"user","content":"fix q"}]}\n')

        runner.invoke(app, ["add", "."])
        result = runner.invoke(app, ["commit", "-m", "multi-dir data"])
        assert result.exit_code == 0

        result = runner.invoke(app, ["status"])
        assert "clean" in result.stdout.lower() or "nothing" in result.stdout.lower()
```

- [ ] **Step 2: Run all tests**

```bash
uv run pytest tests/ -v
```

Expected: All tests PASS (should be ~35+ tests total)

- [ ] **Step 3: Commit**

```bash
git add tests/test_cli.py
git commit -m "test: end-to-end integration tests for full dit workflow"
```

---

### Task 17: Final Cleanup and Full Test Run

**Files:**
- No new files — verify everything works together

- [ ] **Step 1: Run full test suite**

```bash
uv run pytest tests/ -v --tb=short
```

Expected: All tests PASS

- [ ] **Step 2: Verify CLI works interactively**

```bash
cd /tmp && mkdir dit-test && cd dit-test
uv run --project /Users/lxs/code/datahub dit init
echo '{"messages":[{"role":"user","content":"hello"},{"role":"assistant","content":"world"}]}' > test.jsonl
uv run --project /Users/lxs/code/datahub dit add .
uv run --project /Users/lxs/code/datahub dit commit -m "test commit"
uv run --project /Users/lxs/code/datahub dit log
uv run --project /Users/lxs/code/datahub dit status
echo '{"messages":[{"role":"user","content":"goodbye"},{"role":"assistant","content":"bye"}]}' >> test.jsonl
uv run --project /Users/lxs/code/datahub dit diff
cd /Users/lxs/code/datahub
rm -rf /tmp/dit-test
```

Expected: All commands produce expected output — log shows 1 commit, status clean after commit, diff shows +1 row.

- [ ] **Step 3: Final commit with any fixups**

```bash
git add -A
git commit -m "chore: final cleanup for Phase 0"
```
