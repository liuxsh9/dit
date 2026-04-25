"""Tests for dit gc CLI command."""
import json
import os
import time
from pathlib import Path

from typer.testing import CliRunner

from dit.cli.main import app
from dit.core.objects import (
    Commit, Manifest, ManifestEntry, Tree, TreeEntry,
    serialize_manifest, serialize_tree, serialize_commit,
)
from dit.core.refs import RefStore
from dit.core.store import ObjectStore

runner = CliRunner()


def _setup_repo(tmp_path: Path) -> tuple[Path, ObjectStore, RefStore]:
    dot = tmp_path / ".datahub"
    dot.mkdir()
    (dot / "objects").mkdir()
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)
    refs.init()
    return dot, store, refs


def _write_row(store: ObjectStore, content: dict) -> str:
    data = json.dumps(content, separators=(",", ":"), sort_keys=True).encode()
    return store.write("rows", data)


def _make_commit(store: ObjectStore, rows: list[dict], parent_hashes=None, author="alice") -> str:
    from dit.core.hash import row_hash as compute_row_hash, query_fingerprint as compute_qfp

    entries = []
    for row in rows:
        rh = compute_row_hash(row)
        _write_row(store, row)
        qfp = compute_qfp(row) if "messages" in row else None
        entries.append(ManifestEntry(row_hash=rh, query_fingerprint=qfp))
    manifest = Manifest(entries=entries)
    m_hash = store.write("manifests", serialize_manifest(manifest))

    tree = Tree(entries=[TreeEntry(name="data.jsonl", obj_type="manifest", obj_hash=m_hash)])
    t_hash = store.write("trees", serialize_tree(tree))

    c = Commit(
        tree_hash=t_hash,
        parent_hashes=parent_hashes or [],
        author=author,
        message="test",
        timestamp=int(time.time()),
    )
    return store.write("commits", serialize_commit(c))


def _write_orphan_row(store: ObjectStore, content: dict, age_seconds: int = 90000) -> str:
    """Write a row object with an artificially old mtime."""
    data = json.dumps(content, separators=(",", ":"), sort_keys=True).encode()
    h = store.write("rows", data)
    obj_path = store._object_path("rows", h)
    old_time = time.time() - age_seconds
    os.utime(obj_path, (old_time, old_time))
    return h


def test_gc_dry_run_table(tmp_path):
    """dry-run table: lists orphan counts, orphan file still exists."""
    dot, store, refs = _setup_repo(tmp_path)
    os.chdir(tmp_path)

    # Create a real commit reachable from main
    c_hash = _make_commit(store, [{"instruction": "hello"}])
    refs.set_branch("main", c_hash)

    # Write an orphan row (old enough to be collected)
    orphan_hash = _write_orphan_row(store, {"instruction": "orphan"}, age_seconds=90000)

    result = runner.invoke(app, ["gc", "--dry-run"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert "dry run" in result.output.lower()
    assert "Would delete" in result.output
    # Orphan row should show up in the would-delete column
    assert "1" in result.output

    # Orphan still exists (dry run)
    obj_path = store._object_path("rows", orphan_hash)
    assert obj_path.exists(), "Orphan should NOT be deleted during dry run"


def test_gc_actual_delete(tmp_path):
    """Actual GC deletes orphan objects."""
    dot, store, refs = _setup_repo(tmp_path)
    os.chdir(tmp_path)

    c_hash = _make_commit(store, [{"instruction": "keep me"}])
    refs.set_branch("main", c_hash)

    orphan_hash = _write_orphan_row(store, {"instruction": "orphan2"}, age_seconds=90000)
    obj_path = store._object_path("rows", orphan_hash)
    assert obj_path.exists()

    result = runner.invoke(app, ["gc"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert "deleted" in result.output.lower() or "Deleted" in result.output

    assert not obj_path.exists(), "Orphan should be deleted after gc"


def test_gc_json_format(tmp_path):
    """--format json returns valid JSON with expected keys."""
    dot, store, refs = _setup_repo(tmp_path)
    os.chdir(tmp_path)

    c_hash = _make_commit(store, [{"instruction": "json test"}])
    refs.set_branch("main", c_hash)

    _write_orphan_row(store, {"instruction": "orphan json"}, age_seconds=90000)

    result = runner.invoke(app, ["gc", "--format", "json"], catch_exceptions=False)
    assert result.exit_code == 0, result.output

    data = json.loads(result.output)
    assert "live_counts" in data
    assert "deleted_counts" in data
    assert "skipped_counts" in data
    assert "total_scanned" in data
    assert "total_deleted" in data
    assert "tmp_deleted" in data
    assert "errors" in data


def test_gc_custom_grace(tmp_path):
    """--grace 1 (1h) treats a 2h-old orphan as collectable."""
    dot, store, refs = _setup_repo(tmp_path)
    os.chdir(tmp_path)

    c_hash = _make_commit(store, [{"instruction": "base"}])
    refs.set_branch("main", c_hash)

    # 2h old orphan — older than 1h grace
    orphan_hash = _write_orphan_row(store, {"instruction": "2h orphan"}, age_seconds=7200)
    obj_path = store._object_path("rows", orphan_hash)
    assert obj_path.exists()

    result = runner.invoke(app, ["gc", "--grace", "1", "--dry-run"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    # In dry-run the orphan should appear as "would delete"
    assert "dry run" in result.output.lower()
    # Orphan still exists (dry run)
    assert obj_path.exists()

    # Now actually run with --grace 1 and verify deletion
    result2 = runner.invoke(app, ["gc", "--grace", "1"], catch_exceptions=False)
    assert result2.exit_code == 0, result2.output
    assert not obj_path.exists(), "2h-old orphan should be deleted with --grace 1"


def test_gc_no_commits(tmp_path):
    """Empty repo (no branches) runs gc without error."""
    _dot, _store, _refs = _setup_repo(tmp_path)
    os.chdir(tmp_path)

    result = runner.invoke(app, ["gc"], catch_exceptions=False)
    assert result.exit_code == 0, result.output


def test_gc_includes_tags_as_roots(tmp_path):
    """Commit only referenced by a tag (no branch) should be counted as live."""
    dot, store, refs = _setup_repo(tmp_path)
    os.chdir(tmp_path)

    c_hash = _make_commit(store, [{"instruction": "tagged only"}])
    # Only a tag, no branch points here
    refs.set_tag("v0.1", c_hash)

    result = runner.invoke(app, ["gc", "--format", "json"], catch_exceptions=False)
    assert result.exit_code == 0, result.output

    data = json.loads(result.output)
    assert data["live_counts"].get("commits", 0) >= 1, (
        "Commit referenced only by tag must be live"
    )
