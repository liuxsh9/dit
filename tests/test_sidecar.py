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
        parsed = {"text": "Hi"}
        assert detect_lang(parsed) is None

    def test_exactly_9_chars_returns_none(self):
        parsed = {"text": "123456789"}
        assert detect_lang(parsed) is None

    def test_exactly_10_chars_detects_lang(self):
        parsed = {"text": "1234567890"}
        assert detect_lang(parsed) == "en"

    def test_nested_dict_finds_longest(self):
        parsed = {
            "short": "hi",
            "nested": {"inner": "这是一段足够长的中文文本用于语言识别"},
        }
        assert detect_lang(parsed) == "zh"

    def test_list_values_searched(self):
        parsed = {"items": ["short", "Это достаточно длинный русский текст для теста"]}
        assert detect_lang(parsed) == "ru"

    def test_no_string_values_returns_none(self):
        parsed = {"count": 42, "flag": True}
        assert detect_lang(parsed) is None

    def test_uses_longest_string_not_first(self):
        parsed = {
            "short_zh": "中文",
            "long_en": "This is a sufficiently long English string for detection.",
        }
        assert detect_lang(parsed) == "en"

    def test_empty_dict_returns_none(self):
        assert detect_lang({}) is None


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
    data = json.dumps(content, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return store.write("rows", data)


def _write_manifest(store: ObjectStore, row_hashes: list[str]) -> str:
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
        store = ObjectStore(tmp_path / "objects")
        row = {"text": "Deterministic content for testing purposes."}
        row_hash = _write_row(store, row)
        manifest_hash = _write_manifest(store, [row_hash])

        sidecar1 = compute_sidecar(store, manifest_hash)
        sidecar2 = compute_sidecar(store, manifest_hash)

        from dit.core.objects import serialize_sidecar
        assert serialize_sidecar(sidecar1) == serialize_sidecar(sidecar2)

    def test_sidecar_entry_order_matches_manifest(self, tmp_path):
        store = ObjectStore(tmp_path / "objects")
        rows = [{"id": i, "text": f"Row number {i} with some text content."} for i in range(5)]
        row_hashes = [_write_row(store, r) for r in rows]
        manifest_hash = _write_manifest(store, row_hashes)

        sidecar = compute_sidecar(store, manifest_hash)
        for i, entry in enumerate(sidecar.entries):
            assert entry.row_hash == row_hashes[i]
