"""Stress / monkey tests for StagingIndex locking and concurrency.

These tests exercise the locking layer under contention from multiple
threads performing random mixes of stage/unstage/entries/clear.
"""

import random
import threading
from pathlib import Path

from dit.core.index import StagingIndex


# ---------------------------------------------------------------------------
# Sanity checks (single-threaded, confirm basic ops still work)
# ---------------------------------------------------------------------------


class TestSanityChecks:
    def test_stage_unstage_entries_clear(self, tmp_repo: Path):
        """Basic round-trip: stage, read, unstage, clear."""
        idx = StagingIndex(tmp_repo / ".dit" / "index")

        # empty
        assert idx.entries() == {}

        # stage two items
        idx.stage("a.jsonl", "aa" * 32)
        idx.stage("b.jsonl", "bb" * 32)
        assert idx.entries() == {"a.jsonl": "aa" * 32, "b.jsonl": "bb" * 32}

        # unstage one
        idx.unstage("a.jsonl")
        assert idx.entries() == {"b.jsonl": "bb" * 32}

        # clear
        idx.clear()
        assert idx.entries() == {}


# ---------------------------------------------------------------------------
# Sequential stress
# ---------------------------------------------------------------------------


class TestSequentialStress:
    def test_stage_many_items_sequentially(self, tmp_repo: Path):
        """Stage 150 items one-by-one, verify all present."""
        idx = StagingIndex(tmp_repo / ".dit" / "index")
        n = 150
        for i in range(n):
            idx.stage(f"file_{i}.jsonl", f"hash_{i}")

        entries = idx.entries()
        assert len(entries) == n
        for i in range(n):
            assert entries[f"file_{i}.jsonl"] == f"hash_{i}"


# ---------------------------------------------------------------------------
# Concurrent reads
# ---------------------------------------------------------------------------


class TestConcurrentReads:
    def test_concurrent_readers_all_succeed(self, tmp_repo: Path):
        """10 threads reading entries simultaneously must all get the same result."""
        idx = StagingIndex(tmp_repo / ".dit" / "index")
        idx.stage("seed.jsonl", "ab" * 32)

        results: list[dict] = []
        errors: list[Exception] = []
        barrier = threading.Barrier(10, timeout=10)

        def reader():
            try:
                local_idx = StagingIndex(tmp_repo / ".dit" / "index")
                barrier.wait()
                result = local_idx.entries()
                results.append(result)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=reader) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert not errors, f"Reader threads raised exceptions: {errors}"
        assert len(results) == 10
        for r in results:
            assert r == {"seed.jsonl": "ab" * 32}


# ---------------------------------------------------------------------------
# Concurrent writes (threads)
# ---------------------------------------------------------------------------


class TestConcurrentWrites:
    def test_threaded_stage_no_data_loss(self, tmp_repo: Path):
        """10 threads each staging 10 unique items -> 100 items total."""
        index_path = tmp_repo / ".dit" / "index"
        errors: list[Exception] = []

        def writer(prefix: str):
            try:
                local_idx = StagingIndex(index_path)
                for i in range(10):
                    local_idx.stage(f"{prefix}_{i}.jsonl", f"hash_{prefix}_{i}")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(f"t{t}",)) for t in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors, f"Writer threads raised exceptions: {errors}"

        idx = StagingIndex(index_path)
        entries = idx.entries()
        assert len(entries) == 100, (
            f"Expected 100 entries, got {len(entries)}: {sorted(entries.keys())}"
        )

    def test_concurrent_writes_no_corruption(self, tmp_repo: Path):
        """Concurrent writes must not produce malformed JSON."""
        index_path = tmp_repo / ".dit" / "index"
        errors: list[Exception] = []

        def writer(prefix: str):
            try:
                local_idx = StagingIndex(index_path)
                for i in range(20):
                    local_idx.stage(f"{prefix}_{i}.jsonl", f"hash_{prefix}_{i}")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(f"w{t}",)) for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors, f"Writer threads raised exceptions: {errors}"

        # The index must be readable (not corrupted JSON)
        idx = StagingIndex(index_path)
        entries = idx.entries()
        assert isinstance(entries, dict)
        # All 100 items should be present
        assert len(entries) == 100


# ---------------------------------------------------------------------------
# Clear while staging
# ---------------------------------------------------------------------------


class TestClearWhileStaging:
    def test_clear_during_concurrent_stage(self, tmp_repo: Path):
        """Clear while another thread is staging must not crash."""
        index_path = tmp_repo / ".dit" / "index"
        errors: list[Exception] = []
        start_event = threading.Event()

        def stager():
            try:
                local_idx = StagingIndex(index_path)
                start_event.wait(timeout=5)
                for i in range(30):
                    local_idx.stage(f"item_{i}.jsonl", f"hash_{i}")
            except Exception as exc:
                errors.append(exc)

        def clearer():
            try:
                local_idx = StagingIndex(index_path)
                start_event.wait(timeout=5)
                for _ in range(10):
                    local_idx.clear()
            except Exception as exc:
                errors.append(exc)

        t_stage = threading.Thread(target=stager)
        t_clear = threading.Thread(target=clearer)
        t_stage.start()
        t_clear.start()
        start_event.set()
        t_stage.join(timeout=30)
        t_clear.join(timeout=30)

        assert not errors, f"Threads raised exceptions: {errors}"

        # Index must be readable regardless of final state
        idx = StagingIndex(index_path)
        entries = idx.entries()
        assert isinstance(entries, dict)


# ---------------------------------------------------------------------------
# Monkey test: random operations from multiple threads
# ---------------------------------------------------------------------------


class TestMonkeyRandomOps:
    def test_random_ops_no_exceptions(self, tmp_repo: Path):
        """5 threads, 50 random ops each. No exceptions, consistent final state."""
        index_path = tmp_repo / ".dit" / "index"
        errors: list[Exception] = []
        ops_log: list[tuple] = []  # not used for assertion, just debug
        log_lock = threading.Lock()

        def monkey(thread_id: int):
            try:
                local_idx = StagingIndex(index_path)
                rng = random.Random(thread_id)  # deterministic per-thread
                for i in range(50):
                    op = rng.choice(["stage", "unstage", "entries", "clear"])
                    key = f"t{thread_id}_item_{rng.randint(0, 19)}.jsonl"
                    if op == "stage":
                        local_idx.stage(key, f"hash_{thread_id}_{i}")
                    elif op == "unstage":
                        local_idx.unstage(key)
                    elif op == "entries":
                        result = local_idx.entries()
                        assert isinstance(result, dict)
                    elif op == "clear":
                        local_idx.clear()
                    with log_lock:
                        ops_log.append((thread_id, op, key))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=monkey, args=(tid,)) for tid in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        assert not errors, f"Monkey threads raised exceptions: {errors}"

        # Final state must be readable and internally consistent
        idx = StagingIndex(index_path)
        entries = idx.entries()
        assert isinstance(entries, dict)
        # Every value must be a string (hash)
        for k, v in entries.items():
            assert isinstance(k, str)
            assert isinstance(v, str)

    def test_random_ops_heavy_contention(self, tmp_repo: Path):
        """10 threads, 30 ops each, biased toward writes. Stress the lock."""
        index_path = tmp_repo / ".dit" / "index"
        errors: list[Exception] = []

        def monkey(thread_id: int):
            try:
                local_idx = StagingIndex(index_path)
                rng = random.Random(thread_id + 1000)
                for i in range(30):
                    # 70% writes, 20% reads, 10% clears
                    roll = rng.random()
                    key = f"t{thread_id}_f{rng.randint(0, 9)}.jsonl"
                    if roll < 0.35:
                        local_idx.stage(key, f"h_{thread_id}_{i}")
                    elif roll < 0.70:
                        local_idx.unstage(key)
                    elif roll < 0.90:
                        local_idx.entries()
                    else:
                        local_idx.clear()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=monkey, args=(tid,)) for tid in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        assert not errors, f"Heavy contention raised exceptions: {errors}"

        idx = StagingIndex(index_path)
        entries = idx.entries()
        assert isinstance(entries, dict)
