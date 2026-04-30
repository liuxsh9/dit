from __future__ import annotations

from dit.core.objects import Manifest, ManifestEntry
from dit.core.diff import diff_manifests


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

    def test_diff_duplicate_query_fingerprint(self):
        """Multiple removed rows sharing same query_fingerprint should each match separately."""
        qfp = "same_query_fp"
        old = Manifest(entries=[
            ManifestEntry(row_hash="old_a", query_fingerprint=qfp),
            ManifestEntry(row_hash="old_b", query_fingerprint=qfp),
        ])
        new = Manifest(entries=[
            ManifestEntry(row_hash="new_a", query_fingerprint=qfp),
            ManifestEntry(row_hash="new_b", query_fingerprint=qfp),
        ])
        result = diff_manifests(old, new)
        assert len(result.refreshed) == 2
        assert len(result.added) == 0
        assert len(result.removed) == 0

    def test_summary(self):
        old = Manifest(entries=[_entry("aa"), _entry("bb")])
        new = Manifest(entries=[_entry("aa"), _entry("cc"), _entry("dd")])
        result = diff_manifests(old, new)
        s = result.summary()
        assert "+2" in s
        assert "-1" in s
