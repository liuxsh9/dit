import multiprocessing
import threading
import time
from pathlib import Path

from dit.core.index import StagingIndex


class TestStagingIndex:
    def test_empty_index(self, tmp_repo: Path):
        idx = StagingIndex(tmp_repo / ".dit" / "index")
        assert idx.entries() == {}

    def test_stage_and_read(self, tmp_repo: Path):
        idx = StagingIndex(tmp_repo / ".dit" / "index")
        idx.stage("coding.jsonl", "aa" * 32)
        entries = idx.entries()
        assert entries == {"coding.jsonl": "aa" * 32}

    def test_stage_overwrites(self, tmp_repo: Path):
        idx = StagingIndex(tmp_repo / ".dit" / "index")
        idx.stage("coding.jsonl", "aa" * 32)
        idx.stage("coding.jsonl", "bb" * 32)
        assert idx.entries()["coding.jsonl"] == "bb" * 32

    def test_unstage(self, tmp_repo: Path):
        idx = StagingIndex(tmp_repo / ".dit" / "index")
        idx.stage("coding.jsonl", "aa" * 32)
        idx.unstage("coding.jsonl")
        assert idx.entries() == {}

    def test_clear(self, tmp_repo: Path):
        idx = StagingIndex(tmp_repo / ".dit" / "index")
        idx.stage("a.jsonl", "aa" * 32)
        idx.stage("b.jsonl", "bb" * 32)
        idx.clear()
        assert idx.entries() == {}

    def test_persistence(self, tmp_repo: Path):
        path = tmp_repo / ".dit" / "index"
        idx1 = StagingIndex(path)
        idx1.stage("x.jsonl", "cc" * 32)
        idx2 = StagingIndex(path)
        assert idx2.entries() == {"x.jsonl": "cc" * 32}


def _concurrent_worker(index_path_str, prefix, count):
    """Worker function for multiprocessing (must be top-level for pickling)."""
    from dit.core.index import StagingIndex

    idx = StagingIndex(Path(index_path_str))
    for i in range(count):
        idx.stage(f"{prefix}_{i}.jsonl", f"hash_{prefix}_{i}")


class TestIndexConcurrency:
    def test_concurrent_stage_no_data_loss(self, tmp_repo: Path):
        """Two processes staging different files concurrently must not lose data."""
        index_path = tmp_repo / ".dit" / "index"
        index_path.parent.mkdir(parents=True, exist_ok=True)

        n = 20
        p1 = multiprocessing.Process(
            target=_concurrent_worker, args=(str(index_path), "a", n)
        )
        p2 = multiprocessing.Process(
            target=_concurrent_worker, args=(str(index_path), "b", n)
        )
        p1.start()
        p2.start()
        p1.join(timeout=30)
        p2.join(timeout=30)

        idx = StagingIndex(index_path)
        entries = idx.entries()
        assert len(entries) == 2 * n, (
            f"Expected {2 * n} entries, got {len(entries)}: {sorted(entries.keys())}"
        )


class TestIndexSharedLock:
    def test_entries_uses_shared_lock(self, tmp_repo: Path):
        """Two concurrent readers must not block each other."""
        idx = StagingIndex(tmp_repo / ".dit" / "index")
        idx.stage("a.jsonl", "aa" * 32)

        results: list[str] = []
        barrier = threading.Barrier(2, timeout=5)

        def reader(name: str) -> None:
            # Each thread gets its own StagingIndex so _lock_fd is not shared
            local_idx = StagingIndex(tmp_repo / ".dit" / "index")
            local_idx._acquire_lock(shared=True)
            try:
                barrier.wait()  # both hold the shared lock at the same time
                results.append(name)
                time.sleep(0.1)
            finally:
                local_idx._release_lock()

        t1 = threading.Thread(target=reader, args=("r1",))
        t2 = threading.Thread(target=reader, args=("r2",))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)
        assert len(results) == 2

    def test_entries_typed_returns_type_info(self, tmp_repo: Path):
        """entries_typed() must return (type, hash) tuples."""
        idx = StagingIndex(tmp_repo / ".dit" / "index")
        idx.stage("a.jsonl", "aa" * 32, obj_type="manifest")
        typed = idx.entries_typed()
        assert typed == {"a.jsonl": ("manifest", "aa" * 32)}

    def test_stage_delete_marks_deletion(self, tmp_repo: Path):
        """stage_delete() must record an entry with empty hash and type 'delete'."""
        idx = StagingIndex(tmp_repo / ".dit" / "index")
        idx.stage("a.jsonl", "aa" * 32)
        idx.stage_delete("a.jsonl")
        typed = idx.entries_typed()
        assert typed["a.jsonl"] == ("delete", "")
