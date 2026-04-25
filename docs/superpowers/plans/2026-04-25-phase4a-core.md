# Phase 4A-Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add sidecar data model, serialization, computation, and walker/tree_builder support to datahub-core.

**Architecture:** Content-addressable sidecar objects with backward-compatible TreeEntry extension.

**Tech Stack:** Python 3.12, pytest, pyzstd

---

## Overview

This plan covers 10 tasks in TDD order. Each task is independently completable. Tasks 1–5 modify `objects.py` only. Tasks 6–7 create the new `sidecar.py`. Tasks 8–10 extend `tree_builder.py`, `walker.py`, and `tree_walker.py`.

**Run all tests at the end:**
```
pytest tests/ -v
```

---

### Task 1: SidecarEntry + Sidecar dataclasses, serialize_sidecar, deserialize_sidecar

**Files:**
- Modify: `src/dit/core/objects.py`
- Test: `tests/test_objects.py`

- [ ] **Step 1: Write the failing test**

Add this class to `tests/test_objects.py`:

```python
from dit.core.objects import (
    SidecarEntry,
    Sidecar,
    serialize_sidecar,
    deserialize_sidecar,
)

class TestSidecar:
    def test_roundtrip_basic(self):
        entry = SidecarEntry(
            row_hash="ab" * 32,
            char_count=120,
            token_estimate=30,
            field_count=4,
            lang="en",
        )
        s = Sidecar(manifest_hash="cd" * 32, entries=[entry])
        data = serialize_sidecar(s)
        s2 = deserialize_sidecar(data)
        assert s2.manifest_hash == "cd" * 32
        assert len(s2.entries) == 1
        e2 = s2.entries[0]
        assert e2.row_hash == "ab" * 32
        assert e2.char_count == 120
        assert e2.token_estimate == 30
        assert e2.field_count == 4
        assert e2.lang == "en"

    def test_roundtrip_lang_none(self):
        entry = SidecarEntry(
            row_hash="ff" * 32,
            char_count=5,
            token_estimate=1,
            field_count=1,
            lang=None,
        )
        s = Sidecar(manifest_hash="ee" * 32, entries=[entry])
        data = serialize_sidecar(s)
        s2 = deserialize_sidecar(data)
        assert s2.entries[0].lang is None

    def test_roundtrip_empty_entries(self):
        s = Sidecar(manifest_hash="aa" * 32, entries=[])
        data = serialize_sidecar(s)
        s2 = deserialize_sidecar(data)
        assert s2.manifest_hash == "aa" * 32
        assert s2.entries == []

    def test_serialize_type_field(self):
        import json
        s = Sidecar(manifest_hash="bb" * 32, entries=[])
        data = serialize_sidecar(s)
        obj = json.loads(data)
        assert obj["type"] == "sidecar"

    def test_serialize_deterministic(self):
        entry = SidecarEntry(
            row_hash="cc" * 32,
            char_count=100,
            token_estimate=25,
            field_count=3,
            lang="zh",
        )
        s = Sidecar(manifest_hash="dd" * 32, entries=[entry])
        assert serialize_sidecar(s) == serialize_sidecar(s)

    def test_serialize_entry_key_order(self):
        """Entry keys must be sorted: char_count, field_count, lang, row_hash, token_estimate."""
        import json
        entry = SidecarEntry(
            row_hash="11" * 32,
            char_count=50,
            token_estimate=12,
            field_count=2,
            lang="ru",
        )
        s = Sidecar(manifest_hash="22" * 32, entries=[entry])
        data = serialize_sidecar(s)
        # Verify it decodes correctly and keys are present
        obj = json.loads(data)
        e = obj["entries"][0]
        assert set(e.keys()) == {"char_count", "field_count", "lang", "row_hash", "token_estimate"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_objects.py::TestSidecar -v`

Expected: FAIL with `ImportError: cannot import name 'SidecarEntry'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/dit/core/objects.py` (after the existing `Tree` dataclass, before `serialize_manifest`):

```python
@dataclass(frozen=True)
class SidecarEntry:
    row_hash: str
    char_count: int
    token_estimate: int
    field_count: int
    lang: Optional[str]


@dataclass(frozen=True)
class Sidecar:
    manifest_hash: str
    entries: list[SidecarEntry]


def serialize_sidecar(s: Sidecar) -> bytes:
    data = {
        "type": "sidecar",
        "manifest_hash": s.manifest_hash,
        "entries": [
            {
                "char_count": e.char_count,
                "field_count": e.field_count,
                "lang": e.lang,
                "row_hash": e.row_hash,
                "token_estimate": e.token_estimate,
            }
            for e in s.entries
        ],
    }
    return json.dumps(data, separators=(",", ":"), sort_keys=True).encode("utf-8")


def deserialize_sidecar(data: bytes) -> Sidecar:
    obj = json.loads(data)
    entries = [
        SidecarEntry(
            row_hash=e["row_hash"],
            char_count=e["char_count"],
            token_estimate=e["token_estimate"],
            field_count=e["field_count"],
            lang=e.get("lang"),
        )
        for e in obj["entries"]
    ]
    return Sidecar(manifest_hash=obj["manifest_hash"], entries=entries)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_objects.py::TestSidecar -v`

Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```
git add src/dit/core/objects.py tests/test_objects.py
git commit -m "feat: add SidecarEntry/Sidecar dataclasses and serialize/deserialize_sidecar"
```

---

### Task 2: TreeEntry sidecar_hash optional field

**Files:**
- Modify: `src/dit/core/objects.py`
- Test: `tests/test_objects.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_objects.py`:

```python
class TestTreeEntryWithSidecar:
    def test_sidecar_hash_defaults_to_none(self):
        entry = TreeEntry(name="data.jsonl", obj_type="manifest", obj_hash="aa" * 32)
        assert entry.sidecar_hash is None

    def test_sidecar_hash_can_be_set(self):
        entry = TreeEntry(
            name="data.jsonl",
            obj_type="manifest",
            obj_hash="aa" * 32,
            sidecar_hash="bb" * 32,
        )
        assert entry.sidecar_hash == "bb" * 32

    def test_tree_entry_frozen(self):
        """TreeEntry must remain frozen (immutable)."""
        import pytest
        entry = TreeEntry(name="x.jsonl", obj_type="manifest", obj_hash="cc" * 32)
        with pytest.raises((AttributeError, TypeError)):
            entry.sidecar_hash = "dd" * 32
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_objects.py::TestTreeEntryWithSidecar -v`

Expected: FAIL with `TypeError: TreeEntry.__init__() got an unexpected keyword argument 'sidecar_hash'`

- [ ] **Step 3: Write minimal implementation**

Replace the existing `TreeEntry` dataclass in `src/dit/core/objects.py`:

```python
@dataclass(frozen=True)
class TreeEntry:
    name: str
    obj_type: str  # "manifest" or "tree"
    obj_hash: str
    sidecar_hash: Optional[str] = None  # present only when obj_type == "manifest"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_objects.py::TestTreeEntryWithSidecar tests/test_objects.py::TestTree -v`

Expected: PASS (all tree tests still pass, new tests pass)

- [ ] **Step 5: Commit**

```
git add src/dit/core/objects.py tests/test_objects.py
git commit -m "feat: add optional sidecar_hash field to TreeEntry"
```

---

### Task 3: serialize_tree backward-compatible extension (omit None, include when present)

**Files:**
- Modify: `src/dit/core/objects.py`
- Test: `tests/test_objects.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_objects.py`:

```python
class TestSerializeTreeSidecar:
    def test_sidecar_hash_omitted_when_none(self):
        """serialize_tree must NOT emit sidecar_hash key when it is None."""
        import json
        t = Tree(entries=[
            TreeEntry(name="a.jsonl", obj_type="manifest", obj_hash="aa" * 32, sidecar_hash=None),
        ])
        data = serialize_tree(t)
        obj = json.loads(data)
        entry = obj["entries"][0]
        assert "sidecar_hash" not in entry

    def test_sidecar_hash_included_when_set(self):
        """serialize_tree MUST include sidecar_hash when it is not None."""
        import json
        t = Tree(entries=[
            TreeEntry(
                name="a.jsonl",
                obj_type="manifest",
                obj_hash="aa" * 32,
                sidecar_hash="bb" * 32,
            ),
        ])
        data = serialize_tree(t)
        obj = json.loads(data)
        entry = obj["entries"][0]
        assert "sidecar_hash" in entry
        assert entry["sidecar_hash"] == "bb" * 32

    def test_mixed_entries_sidecar_selectively_included(self):
        """Only entries with non-None sidecar_hash get the key."""
        import json
        t = Tree(entries=[
            TreeEntry(name="a.jsonl", obj_type="manifest", obj_hash="aa" * 32, sidecar_hash="cc" * 32),
            TreeEntry(name="b.jsonl", obj_type="manifest", obj_hash="bb" * 32, sidecar_hash=None),
        ])
        data = serialize_tree(t)
        obj = json.loads(data)
        by_name = {e["name"]: e for e in obj["entries"]}
        assert "sidecar_hash" in by_name["a.jsonl"]
        assert "sidecar_hash" not in by_name["b.jsonl"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_objects.py::TestSerializeTreeSidecar -v`

Expected: FAIL — current `serialize_tree` does not handle `sidecar_hash` at all, so `test_sidecar_hash_included_when_set` fails (key missing) and `test_sidecar_hash_omitted_when_none` may pass by accident. Run to confirm actual failure.

- [ ] **Step 3: Write minimal implementation**

Replace the `serialize_tree` function in `src/dit/core/objects.py`:

```python
def serialize_tree(t: Tree) -> bytes:
    sorted_entries = sorted(t.entries, key=lambda e: e.name)
    entry_list = []
    for e in sorted_entries:
        entry_dict = {"name": e.name, "obj_hash": e.obj_hash, "obj_type": e.obj_type}
        if e.sidecar_hash is not None:
            entry_dict["sidecar_hash"] = e.sidecar_hash
        entry_list.append(entry_dict)
    data = {
        "type": "tree",
        "entries": entry_list,
    }
    return json.dumps(data, separators=(",", ":"), sort_keys=True).encode("utf-8")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_objects.py::TestSerializeTreeSidecar tests/test_objects.py::TestTree -v`

Expected: PASS

- [ ] **Step 5: Commit**

```
git add src/dit/core/objects.py tests/test_objects.py
git commit -m "feat: extend serialize_tree to conditionally include sidecar_hash"
```

---

### Task 4: deserialize_tree extension (use .get with default None)

**Files:**
- Modify: `src/dit/core/objects.py`
- Test: `tests/test_objects.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_objects.py`:

```python
class TestDeserializeTreeSidecar:
    def test_deserialize_with_sidecar_hash(self):
        """deserialize_tree reads sidecar_hash when present."""
        import json
        raw = json.dumps({
            "type": "tree",
            "entries": [
                {
                    "name": "data.jsonl",
                    "obj_type": "manifest",
                    "obj_hash": "aa" * 32,
                    "sidecar_hash": "bb" * 32,
                }
            ],
        }, separators=(",", ":"), sort_keys=True).encode("utf-8")
        tree = deserialize_tree(raw)
        assert tree.entries[0].sidecar_hash == "bb" * 32

    def test_deserialize_without_sidecar_hash_defaults_none(self):
        """deserialize_tree defaults sidecar_hash=None when key is absent (old format)."""
        import json
        raw = json.dumps({
            "type": "tree",
            "entries": [
                {
                    "name": "data.jsonl",
                    "obj_type": "manifest",
                    "obj_hash": "cc" * 32,
                }
            ],
        }, separators=(",", ":"), sort_keys=True).encode("utf-8")
        tree = deserialize_tree(raw)
        assert tree.entries[0].sidecar_hash is None

    def test_roundtrip_with_sidecar_hash(self):
        """Full roundtrip preserves sidecar_hash."""
        t = Tree(entries=[
            TreeEntry(
                name="train.jsonl",
                obj_type="manifest",
                obj_hash="dd" * 32,
                sidecar_hash="ee" * 32,
            )
        ])
        data = serialize_tree(t)
        t2 = deserialize_tree(data)
        assert t2.entries[0].sidecar_hash == "ee" * 32
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_objects.py::TestDeserializeTreeSidecar -v`

Expected: FAIL — current `deserialize_tree` does not pass `sidecar_hash` to `TreeEntry()`, so `test_deserialize_with_sidecar_hash` raises `TypeError` or the field is missing.

- [ ] **Step 3: Write minimal implementation**

Replace the `deserialize_tree` function in `src/dit/core/objects.py`:

```python
def deserialize_tree(data: bytes) -> Tree:
    obj = json.loads(data)
    entries = [
        TreeEntry(
            name=e["name"],
            obj_type=e["obj_type"],
            obj_hash=e["obj_hash"],
            sidecar_hash=e.get("sidecar_hash"),
        )
        for e in obj["entries"]
    ]
    return Tree(entries=entries)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_objects.py::TestDeserializeTreeSidecar tests/test_objects.py::TestTree -v`

Expected: PASS

- [ ] **Step 5: Commit**

```
git add src/dit/core/objects.py tests/test_objects.py
git commit -m "feat: extend deserialize_tree to read optional sidecar_hash"
```

---

### Task 5: Hash stability — serialize_tree(deserialize_tree(old_bytes)) == old_bytes

**Files:**
- Test only: `tests/test_objects.py`

This task adds no code. It verifies the backward-compat invariant: a tree serialized without sidecar_hash round-trips to byte-identical output.

- [ ] **Step 1: Write the test**

Add to `tests/test_objects.py`:

```python
class TestTreeHashStability:
    def test_old_format_roundtrip_byte_identical(self):
        """A tree serialized in old format (no sidecar_hash) must round-trip
        to the exact same bytes after deserialize_tree -> serialize_tree.
        This guarantees hash stability for existing stored trees."""
        import json
        # Manually construct what the old serialize_tree would have produced
        old_bytes = json.dumps(
            {
                "type": "tree",
                "entries": [
                    {"name": "eval.jsonl", "obj_hash": "bb" * 32, "obj_type": "manifest"},
                    {"name": "train.jsonl", "obj_hash": "aa" * 32, "obj_type": "manifest"},
                ],
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

        tree = deserialize_tree(old_bytes)
        new_bytes = serialize_tree(tree)
        assert new_bytes == old_bytes, (
            "serialize_tree(deserialize_tree(old_bytes)) must be byte-identical to old_bytes. "
            f"old={old_bytes!r}\nnew={new_bytes!r}"
        )

    def test_old_format_hash_unchanged(self):
        """The SHA-256 hash of an old tree must not change after round-trip."""
        import json
        import hashlib
        old_bytes = json.dumps(
            {
                "type": "tree",
                "entries": [
                    {"name": "data.jsonl", "obj_hash": "cc" * 32, "obj_type": "manifest"},
                ],
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        old_hash = hashlib.sha256(old_bytes).hexdigest()

        new_bytes = serialize_tree(deserialize_tree(old_bytes))
        new_hash = hashlib.sha256(new_bytes).hexdigest()
        assert old_hash == new_hash

    def test_new_format_with_sidecar_hash_stable(self):
        """A tree with sidecar_hash set also round-trips stably."""
        t = Tree(entries=[
            TreeEntry(name="a.jsonl", obj_type="manifest", obj_hash="11" * 32, sidecar_hash="22" * 32),
        ])
        original_bytes = serialize_tree(t)
        roundtrip_bytes = serialize_tree(deserialize_tree(original_bytes))
        assert original_bytes == roundtrip_bytes
```

- [ ] **Step 2: Run test to verify it passes immediately**

Run: `pytest tests/test_objects.py::TestTreeHashStability -v`

Expected: PASS (this validates Tasks 3+4 are correct; if it fails, debug serialize_tree key ordering)

- [ ] **Step 3: No implementation changes needed**

The test is purely a guard. If it fails, review that `serialize_tree` uses `sort_keys=True` and that the entry dict contains exactly `{"name", "obj_hash", "obj_type"}` when `sidecar_hash is None`.

- [ ] **Step 4: Run full objects test suite**

Run: `pytest tests/test_objects.py -v`

Expected: All tests PASS

- [ ] **Step 5: Commit**

```
git add tests/test_objects.py
git commit -m "test: add hash stability guard for backward-compatible TreeEntry serialization"
```

---

### Task 6: detect_lang heuristic in new sidecar.py

**Files:**
- Create: `src/dit/core/sidecar.py`
- Test: `tests/test_sidecar.py` (new file)

- [ ] **Step 1: Write the failing test**

Create `tests/test_sidecar.py`:

```python
"""Tests for sidecar computation: detect_lang and compute_sidecar."""
from __future__ import annotations

import json
import pytest

from dit.core.sidecar import detect_lang


class TestDetectLang:
    def test_english_text(self):
        parsed = {"text": "This is a long English sentence with many words."}
        assert detect_lang(parsed) == "en"

    def test_chinese_text(self):
        parsed = {"text": "这是一段中文文字，用于测试语言检测功能。"}
        assert detect_lang(parsed) == "zh"

    def test_russian_text(self):
        parsed = {"text": "Это длинный русский текст для проверки определения языка."}
        assert detect_lang(parsed) == "ru"

    def test_arabic_text(self):
        parsed = {"text": "هذا نص عربي طويل لاختبار الكشف عن اللغة والتعرف عليها."}
        assert detect_lang(parsed) == "ar"

    def test_short_string_returns_none(self):
        """Strings shorter than 10 chars return None — too short to detect."""
        parsed = {"text": "Hi"}
        assert detect_lang(parsed) is None

    def test_exactly_9_chars_returns_none(self):
        parsed = {"text": "123456789"}
        assert detect_lang(parsed) is None

    def test_exactly_10_chars_detects_lang(self):
        parsed = {"text": "1234567890"}
        # 10 chars, ASCII → "en"
        assert detect_lang(parsed) == "en"

    def test_nested_dict_finds_longest(self):
        """Recursively finds the longest string across nested structures."""
        parsed = {
            "short": "hi",
            "nested": {"inner": "这是一段足够长的中文文本用于语言识别"},
        }
        assert detect_lang(parsed) == "zh"

    def test_list_values_searched(self):
        """String values inside lists are also searched."""
        parsed = {"items": ["short", "Это достаточно длинный русский текст для теста"]}
        assert detect_lang(parsed) == "ru"

    def test_no_string_values_returns_none(self):
        """If no string values exist, return None."""
        parsed = {"count": 42, "flag": True}
        assert detect_lang(parsed) is None

    def test_uses_longest_string_not_first(self):
        """When multiple strings exist, the longest one determines the language."""
        parsed = {
            "short_zh": "中文",
            "long_en": "This is a sufficiently long English string for detection.",
        }
        # long_en is longer — should detect "en"
        assert detect_lang(parsed) == "en"

    def test_empty_dict_returns_none(self):
        assert detect_lang({}) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_sidecar.py::TestDetectLang -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'dit.core.sidecar'`

- [ ] **Step 3: Write minimal implementation**

Create `src/dit/core/sidecar.py`:

```python
# src/dit/core/sidecar.py
"""Sidecar computation: per-row metadata (char count, tokens, fields, language)."""
from __future__ import annotations

from typing import Optional

from dit.core.objects import Sidecar, SidecarEntry
from dit.core.store import ObjectStore


def detect_lang(parsed_json: dict) -> Optional[str]:
    """Detect the dominant language of the longest string value in parsed_json.

    Searches recursively through dicts and lists for string values.
    Returns:
        "zh"  — CJK characters detected
        "ru"  — Cyrillic characters detected
        "ar"  — Arabic characters detected
        "en"  — default for ASCII / Latin text
        None  — longest string is shorter than 10 characters, or no strings found
    """
    longest = _find_longest_string(parsed_json)
    if longest is None or len(longest) < 10:
        return None

    for ch in longest:
        cp = ord(ch)
        # CJK Unified Ideographs and common CJK extensions
        if (
            0x4E00 <= cp <= 0x9FFF   # CJK Unified Ideographs
            or 0x3400 <= cp <= 0x4DBF  # CJK Extension A
            or 0x20000 <= cp <= 0x2A6DF  # CJK Extension B
            or 0x3000 <= cp <= 0x303F  # CJK Symbols and Punctuation
            or 0xFF00 <= cp <= 0xFFEF  # Halfwidth/Fullwidth forms
        ):
            return "zh"
        # Cyrillic
        if 0x0400 <= cp <= 0x04FF:
            return "ru"
        # Arabic
        if 0x0600 <= cp <= 0x06FF:
            return "ar"

    return "en"


def _find_longest_string(value: object) -> Optional[str]:
    """Recursively find the longest string value in a nested dict/list structure."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        longest: Optional[str] = None
        for v in value.values():
            candidate = _find_longest_string(v)
            if candidate is not None:
                if longest is None or len(candidate) > len(longest):
                    longest = candidate
        return longest
    if isinstance(value, list):
        longest = None
        for item in value:
            candidate = _find_longest_string(item)
            if candidate is not None:
                if longest is None or len(candidate) > len(longest):
                    longest = candidate
        return longest
    return None


def compute_sidecar(store: ObjectStore, manifest_hash: str) -> Sidecar:
    """Read a manifest, load each row, compute per-row metadata, return Sidecar.

    Raises ValueError if the manifest cannot be read from the store.
    Returns Sidecar with empty entries if manifest has 0 entries.
    """
    from dit.core.objects import deserialize_manifest
    import json

    manifest_data = store.read("manifests", manifest_hash)
    if manifest_data is None:
        raise ValueError(f"Manifest not found in store: {manifest_hash}")
    manifest = deserialize_manifest(manifest_data)

    entries: list[SidecarEntry] = []
    for me in manifest.entries:
        row_bytes = store.read("rows", me.row_hash)
        if row_bytes is None:
            raise ValueError(f"Row not found in store: {me.row_hash}")
        row_text = row_bytes.decode("utf-8")
        parsed = json.loads(row_text)
        char_count = len(row_text)
        token_estimate = char_count // 4
        field_count = len(parsed) if isinstance(parsed, dict) else 0
        lang = detect_lang(parsed) if isinstance(parsed, dict) else None
        entries.append(
            SidecarEntry(
                row_hash=me.row_hash,
                char_count=char_count,
                token_estimate=token_estimate,
                field_count=field_count,
                lang=lang,
            )
        )
    return Sidecar(manifest_hash=manifest_hash, entries=entries)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_sidecar.py::TestDetectLang -v`

Expected: PASS (12 tests)

- [ ] **Step 5: Commit**

```
git add src/dit/core/sidecar.py tests/test_sidecar.py
git commit -m "feat: add detect_lang heuristic in sidecar.py (CJK/Cyrillic/Arabic/en)"
```

---

### Task 7: compute_sidecar function

**Files:**
- Modify: `src/dit/core/sidecar.py` (already written in Task 6)
- Test: `tests/test_sidecar.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_sidecar.py`:

```python
import json
from pathlib import Path

from dit.core.objects import (
    Manifest,
    ManifestEntry,
    Sidecar,
    serialize_manifest,
)
from dit.core.store import ObjectStore
from dit.core.sidecar import compute_sidecar


def _write_row(store: ObjectStore, content: dict) -> str:
    """Write a JSON row to the store and return its hash."""
    data = json.dumps(content, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return store.write("rows", data)


def _write_manifest(store: ObjectStore, row_hashes: list[str]) -> str:
    """Write a manifest referencing the given row hashes and return its hash."""
    entries = [ManifestEntry(row_hash=rh, query_fingerprint=None) for rh in row_hashes]
    manifest = Manifest(entries=entries)
    data = serialize_manifest(manifest)
    return store.write("manifests", data)


class TestComputeSidecar:
    def test_basic_english_row(self, tmp_path):
        store = ObjectStore(tmp_path / "objects")
        row = {"text": "This is a sufficiently long English sentence."}
        row_hash = _write_row(store, row)
        manifest_hash = _write_manifest(store, [row_hash])

        sidecar = compute_sidecar(store, manifest_hash)

        assert sidecar.manifest_hash == manifest_hash
        assert len(sidecar.entries) == 1
        e = sidecar.entries[0]
        assert e.row_hash == row_hash
        row_text = json.dumps(row, separators=(",", ":"), sort_keys=True)
        assert e.char_count == len(row_text)
        assert e.token_estimate == len(row_text) // 4
        assert e.field_count == 1
        assert e.lang == "en"

    def test_chinese_row_detected(self, tmp_path):
        store = ObjectStore(tmp_path / "objects")
        row = {"text": "这是一段中文文字，用于测试语言检测功能。"}
        row_hash = _write_row(store, row)
        manifest_hash = _write_manifest(store, [row_hash])

        sidecar = compute_sidecar(store, manifest_hash)
        assert sidecar.entries[0].lang == "zh"

    def test_empty_manifest_returns_empty_sidecar(self, tmp_path):
        store = ObjectStore(tmp_path / "objects")
        manifest_hash = _write_manifest(store, [])

        sidecar = compute_sidecar(store, manifest_hash)
        assert sidecar.manifest_hash == manifest_hash
        assert sidecar.entries == []

    def test_multiple_rows(self, tmp_path):
        store = ObjectStore(tmp_path / "objects")
        rows = [
            {"instruction": "What is Python?", "response": "A programming language."},
            {"instruction": "What is Go?", "response": "A compiled language by Google."},
        ]
        row_hashes = [_write_row(store, r) for r in rows]
        manifest_hash = _write_manifest(store, row_hashes)

        sidecar = compute_sidecar(store, manifest_hash)
        assert len(sidecar.entries) == 2
        assert sidecar.entries[0].row_hash == row_hashes[0]
        assert sidecar.entries[1].row_hash == row_hashes[1]
        assert sidecar.entries[0].field_count == 2
        assert sidecar.entries[1].field_count == 2

    def test_field_count_matches_top_level_keys(self, tmp_path):
        store = ObjectStore(tmp_path / "objects")
        row = {"a": 1, "b": 2, "c": 3, "d": 4, "e": 5}
        row_hash = _write_row(store, row)
        manifest_hash = _write_manifest(store, [row_hash])

        sidecar = compute_sidecar(store, manifest_hash)
        assert sidecar.entries[0].field_count == 5

    def test_missing_manifest_raises(self, tmp_path):
        store = ObjectStore(tmp_path / "objects")
        with pytest.raises(ValueError, match="Manifest not found"):
            compute_sidecar(store, "ff" * 32)

    def test_sidecar_is_deterministic(self, tmp_path):
        """Same manifest always produces the same sidecar bytes."""
        store = ObjectStore(tmp_path / "objects")
        row = {"text": "Deterministic content for testing purposes."}
        row_hash = _write_row(store, row)
        manifest_hash = _write_manifest(store, [row_hash])

        sidecar1 = compute_sidecar(store, manifest_hash)
        sidecar2 = compute_sidecar(store, manifest_hash)

        from dit.core.objects import serialize_sidecar
        assert serialize_sidecar(sidecar1) == serialize_sidecar(sidecar2)

    def test_sidecar_entry_order_matches_manifest(self, tmp_path):
        """Sidecar entries must be in the same order as manifest entries."""
        store = ObjectStore(tmp_path / "objects")
        rows = [{"id": i, "text": f"Row number {i} with some text content."} for i in range(5)]
        row_hashes = [_write_row(store, r) for r in rows]
        manifest_hash = _write_manifest(store, row_hashes)

        sidecar = compute_sidecar(store, manifest_hash)
        for i, entry in enumerate(sidecar.entries):
            assert entry.row_hash == row_hashes[i]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_sidecar.py::TestComputeSidecar -v`

Expected: FAIL — `compute_sidecar` was written in Task 6 but `_write_row` uses `json.dumps` with specific args while the row reader in `compute_sidecar` decodes directly; most tests should fail with import or logic errors. Run to see actual failures.

Note: If Task 6 implementation is already correct, some tests may pass immediately. That is acceptable — confirm all 8 pass after any needed fixes.

- [ ] **Step 3: Verify implementation is complete**

The `compute_sidecar` function was written in Task 6. Verify the function body in `src/dit/core/sidecar.py` matches:

```python
def compute_sidecar(store: ObjectStore, manifest_hash: str) -> Sidecar:
    from dit.core.objects import deserialize_manifest
    import json

    manifest_data = store.read("manifests", manifest_hash)
    if manifest_data is None:
        raise ValueError(f"Manifest not found in store: {manifest_hash}")
    manifest = deserialize_manifest(manifest_data)

    entries: list[SidecarEntry] = []
    for me in manifest.entries:
        row_bytes = store.read("rows", me.row_hash)
        if row_bytes is None:
            raise ValueError(f"Row not found in store: {me.row_hash}")
        row_text = row_bytes.decode("utf-8")
        parsed = json.loads(row_text)
        char_count = len(row_text)
        token_estimate = char_count // 4
        field_count = len(parsed) if isinstance(parsed, dict) else 0
        lang = detect_lang(parsed) if isinstance(parsed, dict) else None
        entries.append(
            SidecarEntry(
                row_hash=me.row_hash,
                char_count=char_count,
                token_estimate=token_estimate,
                field_count=field_count,
                lang=lang,
            )
        )
    return Sidecar(manifest_hash=manifest_hash, entries=entries)
```

If it doesn't match, apply the correction now.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_sidecar.py -v`

Expected: PASS (all 20 tests: 12 detect_lang + 8 compute_sidecar)

- [ ] **Step 5: Commit**

```
git add src/dit/core/sidecar.py tests/test_sidecar.py
git commit -m "feat: compute_sidecar reads manifest rows and produces per-row metadata"
```

---

### Task 8: tree_builder 3-tuple extension (sidecar_hash passthrough)

**Files:**
- Modify: `src/dit/core/tree_builder.py`
- Test: `tests/test_tree_builder.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_tree_builder.py`:

```python
from dit.core.objects import deserialize_tree


class TestBuildNestedTreeSidecar:
    def test_3tuple_with_sidecar_hash(self, tmp_path):
        """3-tuple (obj_type, obj_hash, sidecar_hash) passes sidecar_hash to TreeEntry."""
        store = ObjectStore(tmp_path / "objects")
        sidecar_hash = "sc" * 32
        staged = {
            "train.jsonl": ("manifest", "aa" * 32, sidecar_hash),
        }
        tree_hash = build_nested_tree(store, staged)
        data = store.read("trees", tree_hash)
        tree = deserialize_tree(data)
        assert len(tree.entries) == 1
        assert tree.entries[0].name == "train.jsonl"
        assert tree.entries[0].sidecar_hash == sidecar_hash

    def test_2tuple_sidecar_hash_is_none(self, tmp_path):
        """2-tuple (obj_type, obj_hash) produces sidecar_hash=None (backward compat)."""
        store = ObjectStore(tmp_path / "objects")
        staged = {
            "eval.jsonl": ("manifest", "bb" * 32),
        }
        tree_hash = build_nested_tree(store, staged)
        data = store.read("trees", tree_hash)
        tree = deserialize_tree(data)
        assert tree.entries[0].sidecar_hash is None

    def test_3tuple_none_sidecar_hash(self, tmp_path):
        """3-tuple with None sidecar_hash produces sidecar_hash=None in TreeEntry."""
        store = ObjectStore(tmp_path / "objects")
        staged = {
            "data.jsonl": ("manifest", "cc" * 32, None),
        }
        tree_hash = build_nested_tree(store, staged)
        data = store.read("trees", tree_hash)
        tree = deserialize_tree(data)
        assert tree.entries[0].sidecar_hash is None

    def test_mixed_2tuple_and_3tuple(self, tmp_path):
        """Mixed 2-tuple and 3-tuple inputs are both handled correctly."""
        store = ObjectStore(tmp_path / "objects")
        sidecar_hash = "dd" * 32
        staged = {
            "with_sidecar.jsonl": ("manifest", "ee" * 32, sidecar_hash),
            "without_sidecar.jsonl": ("manifest", "ff" * 32),
        }
        tree_hash = build_nested_tree(store, staged)
        data = store.read("trees", tree_hash)
        tree = deserialize_tree(data)
        by_name = {e.name: e for e in tree.entries}
        assert by_name["with_sidecar.jsonl"].sidecar_hash == sidecar_hash
        assert by_name["without_sidecar.jsonl"].sidecar_hash is None

    def test_3tuple_in_nested_directory(self, tmp_path):
        """3-tuple sidecar_hash is preserved through nested tree building."""
        store = ObjectStore(tmp_path / "objects")
        sidecar_hash = "11" * 32
        staged = {
            "subdir/deep.jsonl": ("manifest", "22" * 32, sidecar_hash),
        }
        tree_hash = build_nested_tree(store, staged)
        root = deserialize_tree(store.read("trees", tree_hash))
        subdir_entry = next(e for e in root.entries if e.name == "subdir")
        subdir_tree = deserialize_tree(store.read("trees", subdir_entry.obj_hash))
        assert subdir_tree.entries[0].sidecar_hash == sidecar_hash
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_tree_builder.py::TestBuildNestedTreeSidecar -v`

Expected: FAIL — `build_nested_tree` only handles 2-tuples; passing a 3-tuple raises `ValueError: too many values to unpack`

- [ ] **Step 3: Write minimal implementation**

Replace `src/dit/core/tree_builder.py` entirely:

```python
# src/dit/core/tree_builder.py
"""Build nested Tree objects from a flat staged map."""
from __future__ import annotations

from collections import defaultdict
from typing import Union

from dit.core.objects import Tree, TreeEntry, serialize_tree
from dit.core.store import ObjectStore

# Staged value: either (obj_type, obj_hash) or (obj_type, obj_hash, sidecar_hash | None)
StagedValue = Union[tuple[str, str], tuple[str, str, str | None]]


def build_nested_tree(
    store: ObjectStore,
    staged: dict[str, StagedValue],
) -> str:
    """Recursively build nested Tree objects and write them to store.

    Args:
        store: ObjectStore to write tree objects into.
        staged: Flat map of POSIX-relative path → (obj_type, obj_hash) or
                (obj_type, obj_hash, sidecar_hash). 2-tuples default sidecar_hash=None.

    Returns:
        SHA-256 hex hash of the root Tree object.
    """
    return _build_subtree(store, staged, prefix="")


def _build_subtree(
    store: ObjectStore,
    staged: dict[str, StagedValue],
    prefix: str,
) -> str:
    direct: dict[str, StagedValue] = {}
    subdirs: dict[str, dict[str, StagedValue]] = defaultdict(dict)

    prefix_len = len(prefix)
    for path, value in staged.items():
        if not path.startswith(prefix):
            continue
        rest = path[prefix_len:]
        if "/" not in rest:
            direct[rest] = value
        else:
            subdir_name, sub_rest = rest.split("/", 1)
            subdirs[subdir_name][prefix + subdir_name + "/" + sub_rest] = value

    entries: list[TreeEntry] = []

    for name, value in direct.items():
        obj_type, obj_hash = value[0], value[1]
        sidecar_hash = value[2] if len(value) >= 3 else None
        entries.append(
            TreeEntry(name=name, obj_type=obj_type, obj_hash=obj_hash, sidecar_hash=sidecar_hash)
        )

    for subdir_name, sub_staged in subdirs.items():
        sub_tree_hash = _build_subtree(store, sub_staged, prefix=prefix + subdir_name + "/")
        entries.append(TreeEntry(name=subdir_name, obj_type="tree", obj_hash=sub_tree_hash))

    tree = Tree(entries=entries)
    tree_bytes = serialize_tree(tree)
    return store.write("trees", tree_bytes)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_tree_builder.py -v`

Expected: PASS (all original tests + 5 new sidecar tests)

- [ ] **Step 5: Commit**

```
git add src/dit/core/tree_builder.py tests/test_tree_builder.py
git commit -m "feat: extend build_nested_tree to accept optional sidecar_hash in 3-tuple"
```

---

### Task 9: walker "sidecars" set extension

**Files:**
- Modify: `src/dit/core/walker.py`
- Test: `tests/test_walker.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_walker.py`:

```python
class TestWalkSidecars:
    def test_sidecar_hash_collected(self, tmp_path: Path) -> None:
        """When a TreeEntry has sidecar_hash set, it is included in result['sidecars']."""
        store = _make_store(tmp_path)
        row_hash = _store_row(store, '{"text":"hello world example"}')
        manifest_hash = _store_manifest(store, [row_hash])
        sidecar_hash = "sc" * 32  # fake sidecar hash, doesn't need to exist in store
        tree_hash = _store_tree(store, [
            TreeEntry(name="data.jsonl", obj_type="manifest", obj_hash=manifest_hash, sidecar_hash=sidecar_hash)
        ])
        commit_hash = _store_commit(store, tree_hash, [])

        result = walk_commit_objects(store, commit_hash)
        assert "sidecars" in result
        assert sidecar_hash in result["sidecars"]

    def test_no_sidecar_hash_not_in_result(self, tmp_path: Path) -> None:
        """TreeEntries with sidecar_hash=None contribute nothing to result['sidecars']."""
        store = _make_store(tmp_path)
        row_hash = _store_row(store, '{"a":1}')
        manifest_hash = _store_manifest(store, [row_hash])
        tree_hash = _store_tree(store, [
            TreeEntry(name="data.jsonl", obj_type="manifest", obj_hash=manifest_hash)
        ])
        commit_hash = _store_commit(store, tree_hash, [])

        result = walk_commit_objects(store, commit_hash)
        assert result.get("sidecars", set()) == set()

    def test_multiple_sidecar_hashes_all_collected(self, tmp_path: Path) -> None:
        """Multiple manifests with sidecar hashes all get collected."""
        store = _make_store(tmp_path)
        sc1 = "11" * 32
        sc2 = "22" * 32
        m1 = _store_manifest(store, [_store_row(store, '{"x":1}')])
        m2 = _store_manifest(store, [_store_row(store, '{"y":2}')])
        tree_hash = _store_tree(store, [
            TreeEntry(name="a.jsonl", obj_type="manifest", obj_hash=m1, sidecar_hash=sc1),
            TreeEntry(name="b.jsonl", obj_type="manifest", obj_hash=m2, sidecar_hash=sc2),
        ])
        commit_hash = _store_commit(store, tree_hash, [])

        result = walk_commit_objects(store, commit_hash)
        assert sc1 in result["sidecars"]
        assert sc2 in result["sidecars"]

    def test_sidecars_key_present_in_result_dict(self, tmp_path: Path) -> None:
        """walk_commit_objects always returns a 'sidecars' key, even if empty."""
        store = _make_store(tmp_path)
        tree_hash = _store_tree(store, [])
        commit_hash = _store_commit(store, tree_hash, [])

        result = walk_commit_objects(store, commit_hash)
        assert "sidecars" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_walker.py::TestWalkSidecars -v`

Expected: FAIL — `result` dict does not have "sidecars" key; `KeyError: 'sidecars'`

- [ ] **Step 3: Write minimal implementation**

Replace `src/dit/core/walker.py`:

```python
from __future__ import annotations

from dit.core.objects import deserialize_commit, deserialize_manifest, deserialize_tree
from dit.core.store import ObjectStore


def walk_commit_objects(
    store: ObjectStore, commit_hash: str
) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {
        "commits": set(),
        "trees": set(),
        "manifests": set(),
        "rows": set(),
        "sidecars": set(),
    }
    _walk_commit(store, commit_hash, result)
    return result


def _walk_commit(
    store: ObjectStore, commit_hash: str, result: dict[str, set[str]]
) -> None:
    if commit_hash in result["commits"]:
        return
    result["commits"].add(commit_hash)

    commit_data = store.read("commits", commit_hash)
    if commit_data is None:
        return
    commit = deserialize_commit(commit_data)

    _walk_tree(store, commit.tree_hash, result)

    for parent_hash in commit.parent_hashes:
        _walk_commit(store, parent_hash, result)


def _walk_tree(
    store: ObjectStore, tree_hash: str, result: dict[str, set[str]]
) -> None:
    if tree_hash in result["trees"]:
        return
    result["trees"].add(tree_hash)

    tree_data = store.read("trees", tree_hash)
    if tree_data is None:
        return
    tree = deserialize_tree(tree_data)

    for entry in tree.entries:
        if entry.sidecar_hash:
            result["sidecars"].add(entry.sidecar_hash)
        if entry.obj_type == "manifest":
            _walk_manifest(store, entry.obj_hash, result)
        elif entry.obj_type == "tree":
            _walk_tree(store, entry.obj_hash, result)


def _walk_manifest(
    store: ObjectStore, manifest_hash: str, result: dict[str, set[str]]
) -> None:
    if manifest_hash in result["manifests"]:
        return
    result["manifests"].add(manifest_hash)

    manifest_data = store.read("manifests", manifest_hash)
    if manifest_data is None:
        return
    manifest = deserialize_manifest(manifest_data)

    for entry in manifest.entries:
        result["rows"].add(entry.row_hash)


def is_ancestor(
    store: ObjectStore, ancestor_hash: str, descendant_hash: str
) -> bool:
    if ancestor_hash == descendant_hash:
        return True
    visited: set[str] = set()
    return _is_ancestor_dfs(store, ancestor_hash, descendant_hash, visited)


def _is_ancestor_dfs(
    store: ObjectStore,
    ancestor_hash: str,
    current_hash: str,
    visited: set[str],
) -> bool:
    if current_hash in visited:
        return False
    visited.add(current_hash)

    commit_data = store.read("commits", current_hash)
    if commit_data is None:
        return False
    commit = deserialize_commit(commit_data)

    for parent_hash in commit.parent_hashes:
        if parent_hash == ancestor_hash:
            return True
        if _is_ancestor_dfs(store, ancestor_hash, parent_hash, visited):
            return True
    return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_walker.py -v`

Expected: PASS (all original tests + 4 new sidecar tests)

- [ ] **Step 5: Commit**

```
git add src/dit/core/walker.py tests/test_walker.py
git commit -m "feat: extend walk_commit_objects to collect sidecar_hash entries"
```

---

### Task 10: tree_walker flatten_tree extension to return sidecar_hash

**Files:**
- Modify: `src/dit/core/tree_walker.py`
- Test: `tests/test_tree_walker.py`

- [ ] **Step 1: Write the failing test**

Check if `tests/test_tree_walker.py` exists. If not, create it. Add:

```python
# tests/test_tree_walker.py
"""Tests for tree_walker: flatten_tree and resolve_path."""
from __future__ import annotations

import pytest
from pathlib import Path

from dit.core.objects import Tree, TreeEntry, serialize_tree
from dit.core.store import ObjectStore
from dit.core.tree_walker import flatten_tree


def _make_store(tmp_path: Path) -> ObjectStore:
    return ObjectStore(tmp_path / "objects")


def _store_tree(store: ObjectStore, entries: list[TreeEntry]) -> str:
    tree = Tree(entries=entries)
    data = serialize_tree(tree)
    return store.write("trees", data)


class TestFlattenTreeSidecar:
    def test_returns_3tuple_with_sidecar_hash(self, tmp_path):
        """flatten_tree returns (obj_type, obj_hash, sidecar_hash) for each leaf."""
        store = _make_store(tmp_path)
        sidecar_hash = "sc" * 32
        tree_hash = _store_tree(store, [
            TreeEntry(name="train.jsonl", obj_type="manifest", obj_hash="aa" * 32, sidecar_hash=sidecar_hash),
        ])
        result = flatten_tree(store, tree_hash)
        assert "train.jsonl" in result
        obj_type, obj_hash, sc_hash = result["train.jsonl"]
        assert obj_type == "manifest"
        assert obj_hash == "aa" * 32
        assert sc_hash == sidecar_hash

    def test_returns_none_sidecar_when_not_set(self, tmp_path):
        """flatten_tree returns None as sidecar_hash when TreeEntry has none."""
        store = _make_store(tmp_path)
        tree_hash = _store_tree(store, [
            TreeEntry(name="eval.jsonl", obj_type="manifest", obj_hash="bb" * 32),
        ])
        result = flatten_tree(store, tree_hash)
        obj_type, obj_hash, sc_hash = result["eval.jsonl"]
        assert sc_hash is None

    def test_nested_tree_preserves_sidecar_hash(self, tmp_path):
        """sidecar_hash is preserved through nested tree traversal."""
        store = _make_store(tmp_path)
        sidecar_hash = "33" * 32
        inner_tree_hash = _store_tree(store, [
            TreeEntry(name="deep.jsonl", obj_type="manifest", obj_hash="44" * 32, sidecar_hash=sidecar_hash),
        ])
        root_hash = _store_tree(store, [
            TreeEntry(name="subdir", obj_type="tree", obj_hash=inner_tree_hash),
        ])
        result = flatten_tree(store, root_hash)
        assert "subdir/deep.jsonl" in result
        _, _, sc_hash = result["subdir/deep.jsonl"]
        assert sc_hash == sidecar_hash

    def test_mixed_entries_correct_sidecar(self, tmp_path):
        """Mixed entries with and without sidecar_hash are both handled."""
        store = _make_store(tmp_path)
        sc = "55" * 32
        tree_hash = _store_tree(store, [
            TreeEntry(name="with.jsonl", obj_type="manifest", obj_hash="66" * 32, sidecar_hash=sc),
            TreeEntry(name="without.jsonl", obj_type="manifest", obj_hash="77" * 32),
        ])
        result = flatten_tree(store, tree_hash)
        _, _, sc1 = result["with.jsonl"]
        _, _, sc2 = result["without.jsonl"]
        assert sc1 == sc
        assert sc2 is None

    def test_return_type_is_3tuple(self, tmp_path):
        """Each value in the result dict is a 3-tuple, not a 2-tuple."""
        store = _make_store(tmp_path)
        tree_hash = _store_tree(store, [
            TreeEntry(name="x.jsonl", obj_type="manifest", obj_hash="88" * 32),
        ])
        result = flatten_tree(store, tree_hash)
        value = result["x.jsonl"]
        assert len(value) == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_tree_walker.py::TestFlattenTreeSidecar -v`

Expected: FAIL — current `flatten_tree` returns 2-tuples; unpacking to 3 raises `ValueError: not enough values to unpack`

- [ ] **Step 3: Write minimal implementation**

Replace `flatten_tree` in `src/dit/core/tree_walker.py`. The function signature changes from returning `dict[str, tuple[str, str]]` to `dict[str, tuple[str, str, str | None]]`:

```python
def flatten_tree(
    store: ObjectStore,
    tree_hash: str,
    prefix: str = "",
) -> dict[str, tuple[str, str, str | None]]:
    """Recursively expand a Tree into a flat map of path → (obj_type, obj_hash, sidecar_hash).

    Tree-type entries are descended recursively; manifest and blob entries are
    included as leaves with their full relative path.

    The sidecar_hash element is None when the TreeEntry has no sidecar attached.
    """
    data = store.read("trees", tree_hash)
    if data is None:
        return {}
    tree = deserialize_tree(data)
    result: dict[str, tuple[str, str, str | None]] = {}
    for entry in tree.entries:
        full_path = f"{prefix}{entry.name}" if not prefix else f"{prefix}/{entry.name}"
        if entry.obj_type == "tree":
            result.update(flatten_tree(store, entry.obj_hash, prefix=full_path))
        else:
            result[full_path] = (entry.obj_type, entry.obj_hash, entry.sidecar_hash)
    return result
```

Also update `resolve_path` in the same file to include `sidecar_hash` in the returned dicts (for completeness and future callers):

```python
def resolve_path(
    store: ObjectStore,
    tree_hash: str,
    path: str,
) -> list[dict] | None:
    """Navigate a nested tree to the given path and return its directory listing.

    Returns list of entry dicts with keys: name, obj_type, obj_hash, sidecar_hash.
    Returns None if the path does not exist or points to a non-tree entry.
    """
    data = store.read("trees", tree_hash)
    if data is None:
        return None

    if path == "" or path == ".":
        tree = deserialize_tree(data)
        return [
            {
                "name": e.name,
                "obj_type": e.obj_type,
                "obj_hash": e.obj_hash,
                "sidecar_hash": e.sidecar_hash,
            }
            for e in tree.entries
        ]

    parts = path.strip("/").split("/")
    current_hash = tree_hash
    for i, part in enumerate(parts):
        node_data = store.read("trees", current_hash)
        if node_data is None:
            return None
        node = deserialize_tree(node_data)
        found: TreeEntry | None = None
        for entry in node.entries:
            if entry.name == part:
                found = entry
                break
        if found is None:
            return None
        if i == len(parts) - 1:
            if found.obj_type != "tree":
                return None
            leaf_data = store.read("trees", found.obj_hash)
            if leaf_data is None:
                return None
            leaf = deserialize_tree(leaf_data)
            return [
                {
                    "name": e.name,
                    "obj_type": e.obj_type,
                    "obj_hash": e.obj_hash,
                    "sidecar_hash": e.sidecar_hash,
                }
                for e in leaf.entries
            ]
        else:
            if found.obj_type != "tree":
                return None
            current_hash = found.obj_hash
    return None
```

**Important:** Any callers of `flatten_tree` in the existing codebase that unpack into 2-tuples must be updated. Search and fix:

```bash
grep -rn "flatten_tree" src/
```

For each call site that unpacks `(obj_type, obj_hash)`, update to `(obj_type, obj_hash, _)` or `(obj_type, obj_hash, sidecar_hash)` as appropriate.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_tree_walker.py -v`

Expected: PASS

Then run the full test suite to catch any call-site breakage:

Run: `pytest tests/ -v`

Expected: All tests PASS. If any test fails due to 2-tuple unpacking of `flatten_tree` output, fix the call site.

- [ ] **Step 5: Commit**

```
git add src/dit/core/tree_walker.py tests/test_tree_walker.py
git commit -m "feat: extend flatten_tree to return 3-tuple (obj_type, obj_hash, sidecar_hash)"
```

---

## Final Verification

Run the complete test suite:

```bash
pytest tests/ -v --tb=short
```

Expected: All tests green. Key modules changed:
- `src/dit/core/objects.py` — SidecarEntry, Sidecar, serialize_sidecar, deserialize_sidecar, TreeEntry.sidecar_hash, updated serialize_tree / deserialize_tree
- `src/dit/core/sidecar.py` — new file: detect_lang, compute_sidecar
- `src/dit/core/tree_builder.py` — 3-tuple support in build_nested_tree
- `src/dit/core/walker.py` — "sidecars" set in walk_commit_objects result
- `src/dit/core/tree_walker.py` — 3-tuple return from flatten_tree

**Call-site audit checklist** — verify these files unpack `flatten_tree` correctly after Task 10:

```bash
grep -rn "flatten_tree" src/dit/
```

Any line that does `obj_type, obj_hash = flatten_tree(...)...` must become `obj_type, obj_hash, sidecar_hash = ...` or use `[:2]` if sidecar is not needed at that call site.
