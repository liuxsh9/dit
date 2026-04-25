# src/dit/core/sidecar.py
"""Sidecar computation: per-row metadata (char count, tokens, fields, language)."""
from __future__ import annotations

from typing import Optional

from dit.core.objects import Sidecar, SidecarEntry
from dit.core.store import ObjectStore


def detect_lang(parsed_json: dict) -> Optional[str]:
    longest = _find_longest_string(parsed_json)
    if longest is None or len(longest) < 10:
        return None

    for ch in longest:
        cp = ord(ch)
        if (
            0x4E00 <= cp <= 0x9FFF
            or 0x3400 <= cp <= 0x4DBF
            or 0x20000 <= cp <= 0x2A6DF
            or 0x3000 <= cp <= 0x303F
            or 0xFF00 <= cp <= 0xFFEF
        ):
            return "zh"
        if 0x0400 <= cp <= 0x04FF:
            return "ru"
        if 0x0600 <= cp <= 0x06FF:
            return "ar"

    return "en"


def _find_longest_string(value: object) -> Optional[str]:
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


def sidecar_summary(sidecar) -> dict:
    row_count = len(sidecar.entries)
    if row_count == 0:
        return {
            "row_count": 0,
            "char_count": 0,
            "token_estimate": 0,
            "avg_fields": 0.0,
            "lang_distribution": {},
        }
    total_chars = sum(e.char_count for e in sidecar.entries)
    total_tokens = sum(e.token_estimate for e in sidecar.entries)
    avg_fields = sum(e.field_count for e in sidecar.entries) / row_count
    lang_counts: dict[str, int] = {}
    for e in sidecar.entries:
        k = e.lang or "unknown"
        lang_counts[k] = lang_counts.get(k, 0) + 1
    return {
        "row_count": row_count,
        "char_count": total_chars,
        "token_estimate": total_tokens,
        "avg_fields": round(avg_fields, 2),
        "lang_distribution": lang_counts,
    }
