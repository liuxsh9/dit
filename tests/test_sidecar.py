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
