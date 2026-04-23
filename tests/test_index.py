from pathlib import Path

from dit.core.index import StagingIndex


class TestStagingIndex:
    def test_empty_index(self, tmp_repo: Path):
        idx = StagingIndex(tmp_repo / ".datahub" / "index")
        assert idx.entries() == {}

    def test_stage_and_read(self, tmp_repo: Path):
        idx = StagingIndex(tmp_repo / ".datahub" / "index")
        idx.stage("coding.jsonl", "aa" * 32)
        entries = idx.entries()
        assert entries == {"coding.jsonl": "aa" * 32}

    def test_stage_overwrites(self, tmp_repo: Path):
        idx = StagingIndex(tmp_repo / ".datahub" / "index")
        idx.stage("coding.jsonl", "aa" * 32)
        idx.stage("coding.jsonl", "bb" * 32)
        assert idx.entries()["coding.jsonl"] == "bb" * 32

    def test_unstage(self, tmp_repo: Path):
        idx = StagingIndex(tmp_repo / ".datahub" / "index")
        idx.stage("coding.jsonl", "aa" * 32)
        idx.unstage("coding.jsonl")
        assert idx.entries() == {}

    def test_clear(self, tmp_repo: Path):
        idx = StagingIndex(tmp_repo / ".datahub" / "index")
        idx.stage("a.jsonl", "aa" * 32)
        idx.stage("b.jsonl", "bb" * 32)
        idx.clear()
        assert idx.entries() == {}

    def test_persistence(self, tmp_repo: Path):
        path = tmp_repo / ".datahub" / "index"
        idx1 = StagingIndex(path)
        idx1.stage("x.jsonl", "cc" * 32)
        idx2 = StagingIndex(path)
        assert idx2.entries() == {"x.jsonl": "cc" * 32}
