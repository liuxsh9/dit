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

    old_by_qfp: dict[str, list[ManifestEntry]] = {}
    for e in raw_removed:
        if e.query_fingerprint:
            old_by_qfp.setdefault(e.query_fingerprint, []).append(e)

    refreshed: list[tuple[str, str, str]] = []
    refreshed_old_hashes: set[str] = set()
    refreshed_new_hashes: set[str] = set()

    for e in raw_added:
        if e.query_fingerprint and e.query_fingerprint in old_by_qfp:
            old_entry = old_by_qfp[e.query_fingerprint].pop(0)
            if not old_by_qfp[e.query_fingerprint]:
                del old_by_qfp[e.query_fingerprint]
            refreshed.append((old_entry.row_hash, e.row_hash, e.query_fingerprint))
            refreshed_old_hashes.add(old_entry.row_hash)
            refreshed_new_hashes.add(e.row_hash)

    added = [e for e in raw_added if e.row_hash not in refreshed_new_hashes]
    removed = [e for e in raw_removed if e.row_hash not in refreshed_old_hashes]

    # Manifest entry order is semantically significant for the stored file. If
    # membership is unchanged but the entry sequence differs, surface it as a
    # full-row replacement so `diff` matches `status`/`commit` behavior.
    if not added and not removed and not refreshed and old.entries != new.entries:
        added = list(new.entries)
        removed = list(old.entries)

    return DiffResult(added=added, removed=removed, refreshed=refreshed)
