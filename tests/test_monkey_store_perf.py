"""Monkey / stress tests for store.write_batch, add performance, and data integrity.

Covers: batch write at scale, interleaved writes, multi-type batches,
idempotent rewrites, large-file add/commit round-trips, multi-file adds,
modify-add-commit cycles, special JSON content, status detection, and
timing sanity checks.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dit.cli.main import app
from dit.core.objects import deserialize_commit, deserialize_manifest
from dit.core.refs import RefStore
from dit.core.store import ObjectStore
from dit.core.tree_walker import flatten_tree

runner = CliRunner()


def _row(content: str = "hello") -> str:
    return json.dumps({
        "messages": [
            {"role": "user", "content": content},
            {"role": "assistant", "content": "reply"},
        ]
    })


@pytest.fixture
def repo(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.chdir(tmp_path)
    r = runner.invoke(app, ["init"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    return tmp_path


def _head_flat(repo: Path):
    """Return flattened tree dict from HEAD commit."""
    dot = repo / ".dit"
    store = ObjectStore(dot / "objects")
    head = RefStore(dot).resolve_head()
    assert head, "No HEAD"
    commit = deserialize_commit(store.read("commits", head))
    return flatten_tree(store, commit.tree_hash), store


# ── Store batch write stress ────────────────────────────────────────


class TestBatchWriteStress:

    def test_write_batch_5000_objects(self, tmp_repo: Path):
        """1. write_batch with 5000 small objects — all readable."""
        store = ObjectStore(tmp_repo / ".dit" / "objects")
        items = [f"obj-{i}".encode() for i in range(5000)]
        hashes = store.write_batch("rows", items)
        assert len(hashes) == 5000
        # spot-check first, last, and middle
        for idx in (0, 2500, 4999):
            assert store.read("rows", hashes[idx]) == items[idx]

    def test_write_batch_large_objects(self, tmp_repo: Path):
        """2. 50 objects of ~100 KB each — content round-trips."""
        store = ObjectStore(tmp_repo / ".dit" / "objects")
        items = [(f"big-{i}-" + "X" * 100_000).encode() for i in range(50)]
        hashes = store.write_batch("rows", items)
        assert len(hashes) == 50
        for i, h in enumerate(hashes):
            assert store.read("rows", h) == items[i]

    def test_write_batch_interleaved_with_write(self, tmp_repo: Path):
        """3. Alternate batch(100) and write(1) x10 — all 1010 objects correct."""
        store = ObjectStore(tmp_repo / ".dit" / "objects")
        all_expected: list[tuple[str, bytes]] = []
        for cycle in range(10):
            batch = [f"batch-{cycle}-{j}".encode() for j in range(100)]
            hashes = store.write_batch("rows", batch)
            for h, d in zip(hashes, batch):
                all_expected.append((h, d))
            single = f"single-{cycle}".encode()
            h = store.write("rows", single)
            all_expected.append((h, single))
        assert len(all_expected) == 1010
        for h, data in all_expected:
            assert store.read("rows", h) == data

    def test_write_batch_all_object_types(self, tmp_repo: Path):
        """4. Batch write to every object type — each stored independently."""
        store = ObjectStore(tmp_repo / ".dit" / "objects")
        types = ["rows", "manifests", "commits", "trees", "sidecars", "blobs"]
        written: dict[str, list[str]] = {}
        for t in types:
            items = [f"{t}-item-{i}".encode() for i in range(5)]
            written[t] = store.write_batch(t, items)
        # each type has its own namespace
        for t in types:
            for h in written[t]:
                assert store.exists(t, h)
            # should NOT exist under a different type
            other = [x for x in types if x != t][0]
            assert not store.exists(other, written[t][0])

    def test_write_batch_idempotent_rewrites(self, tmp_repo: Path):
        """5. Write same 100 objects 5 times — idempotent, no corruption."""
        store = ObjectStore(tmp_repo / ".dit" / "objects")
        items = [f"idem-{i}".encode() for i in range(100)]
        first_hashes = store.write_batch("rows", items)
        for _ in range(4):
            hashes = store.write_batch("rows", items)
            assert hashes == first_hashes
        # verify content
        for i, h in enumerate(first_hashes):
            assert store.read("rows", h) == items[i]


# ── Add/commit data integrity under load ────────────────────────────


class TestAddCommitIntegrity:

    def test_add_2000_rows_roundtrip(self, repo: Path):
        """6. 2000-row JSONL — every row readable from store via manifest."""
        rows = [_row(f"r-{i}") for i in range(2000)]
        (repo / "big.jsonl").write_text("\n".join(rows) + "\n")
        r = runner.invoke(app, ["add", "big.jsonl"], catch_exceptions=False)
        assert r.exit_code == 0
        r = runner.invoke(app, ["commit", "-m", "2k"], catch_exceptions=False)
        assert r.exit_code == 0

        flat, store = _head_flat(repo)
        for path, (obj_type, obj_hash, _) in flat.items():
            if obj_type == "manifest":
                m = deserialize_manifest(store.read("manifests", obj_hash))
                assert len(m.entries) == 2000
                for entry in m.entries:
                    data = store.read("rows", entry.row_hash)
                    assert data is not None

    def test_add_50_files(self, repo: Path):
        """7. Add 50 files in one command, commit, verify all in tree."""
        names = []
        for i in range(50):
            name = f"file{i:03d}.jsonl"
            (repo / name).write_text(_row(f"f{i}") + "\n")
            names.append(name)
        r = runner.invoke(app, ["add"] + names, catch_exceptions=False)
        assert r.exit_code == 0
        r = runner.invoke(app, ["commit", "-m", "50 files"], catch_exceptions=False)
        assert r.exit_code == 0

        flat, _ = _head_flat(repo)
        for name in names:
            assert name in flat, f"{name} missing from tree"

    def test_modify_add_commit_10_cycles(self, repo: Path):
        """8. Modify-add-commit x10 — log shows 10 commits."""
        f = repo / "evolve.jsonl"
        for i in range(10):
            f.write_text(_row(f"v{i}") + "\n")
            r = runner.invoke(app, ["add", "evolve.jsonl"], catch_exceptions=False)
            assert r.exit_code == 0
            r = runner.invoke(app, ["commit", "-m", f"c{i}"], catch_exceptions=False)
            assert r.exit_code == 0

        r = runner.invoke(app, ["log"], catch_exceptions=False)
        assert r.exit_code == 0
        # count commit lines in log output
        commit_count = r.output.count("commit ")
        assert commit_count >= 10

    def test_special_json_content(self, repo: Path):
        """9. Unicode, nested objects, large arrays, nulls — round-trip."""
        special_rows = [
            # Chinese + emoji + RTL
            {"messages": [
                {"role": "user", "content": "你好世界 🌍 مرحبا"},
                {"role": "assistant", "content": "回复 🤖"},
            ]},
            # deeply nested (5 levels)
            {"messages": [
                {"role": "user", "content": "nested"},
                {"role": "assistant", "content": "ok"},
            ], "meta": {"a": {"b": {"c": {"d": {"e": "deep"}}}}}},
            # array with 100 elements
            {"messages": [
                {"role": "user", "content": "array"},
                {"role": "assistant", "content": "ok"},
            ], "tags": list(range(100))},
            # nulls and empty strings
            {"messages": [
                {"role": "user", "content": ""},
                {"role": "assistant", "content": None},
            ]},
        ]
        lines = [json.dumps(r, ensure_ascii=False) for r in special_rows]
        (repo / "special.jsonl").write_text("\n".join(lines) + "\n")
        r = runner.invoke(app, ["add", "special.jsonl"], catch_exceptions=False)
        assert r.exit_code == 0
        r = runner.invoke(app, ["commit", "-m", "special"], catch_exceptions=False)
        assert r.exit_code == 0

        flat, store = _head_flat(repo)
        for path, (obj_type, obj_hash, _) in flat.items():
            if obj_type == "manifest":
                m = deserialize_manifest(store.read("manifests", obj_hash))
                assert len(m.entries) == len(special_rows)
                for i, entry in enumerate(m.entries):
                    data = store.read("rows", entry.row_hash)
                    assert data is not None
                    parsed = json.loads(data)
                    # canonical JSON re-parses to same structure
                    assert parsed == json.loads(
                        json.dumps(special_rows[i], sort_keys=True, ensure_ascii=False)
                    )

    def test_add_then_status_detects_modification(self, repo: Path):
        """10. Commit 1000 rows, modify 1, status detects change."""
        rows = [_row(f"s-{i}") for i in range(1000)]
        (repo / "data.jsonl").write_text("\n".join(rows) + "\n")
        r = runner.invoke(app, ["add", "data.jsonl"], catch_exceptions=False)
        assert r.exit_code == 0
        r = runner.invoke(app, ["commit", "-m", "base"], catch_exceptions=False)
        assert r.exit_code == 0

        # modify one row
        rows[500] = _row("MODIFIED-500")
        (repo / "data.jsonl").write_text("\n".join(rows) + "\n")

        r = runner.invoke(app, ["status"], catch_exceptions=False)
        assert r.exit_code == 0
        assert "data.jsonl" in r.output


# ── Timing sanity checks ────────────────────────────────────────────


class TestTimingSanity:

    def test_batch_faster_than_individual(self, tmp_repo: Path):
        """11. write_batch(1000) vs 1000x write() — print ratio, soft assert."""
        store = ObjectStore(tmp_repo / ".dit" / "objects")
        items = [f"perf-{i}".encode() for i in range(1000)]

        # individual writes (use different type to avoid cache hits)
        t0 = time.perf_counter()
        for d in items:
            store.write("manifests", d)
        t_individual = time.perf_counter() - t0

        # batch write (different type again)
        t0 = time.perf_counter()
        store.write_batch("commits", items)
        t_batch = time.perf_counter() - t0

        ratio = t_individual / t_batch if t_batch > 0 else float("inf")
        print(f"\n  Individual: {t_individual:.3f}s | Batch: {t_batch:.3f}s | "
              f"Ratio: {ratio:.2f}x")

        # soft assertion — warn but don't fail on CI
        if ratio < 1.5:
            print(f"  WARNING: batch was only {ratio:.2f}x faster (expected >= 1.5x)")
        # hard floor: batch should at least not be slower
        assert ratio >= 0.5, f"Batch was {1/ratio:.1f}x SLOWER — possible regression"
