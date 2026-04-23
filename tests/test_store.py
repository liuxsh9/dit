from pathlib import Path

from dit.core.store import ObjectStore


class TestObjectStore:
    def test_write_and_read(self, tmp_repo: Path):
        store = ObjectStore(tmp_repo / ".datahub" / "objects")
        data = b"hello world"
        h = store.write("rows", data)
        assert len(h) == 64
        assert store.read("rows", h) == data

    def test_read_nonexistent_returns_none(self, tmp_repo: Path):
        store = ObjectStore(tmp_repo / ".datahub" / "objects")
        assert store.read("rows", "00" * 32) is None

    def test_exists(self, tmp_repo: Path):
        store = ObjectStore(tmp_repo / ".datahub" / "objects")
        data = b"test data"
        h = store.write("rows", data)
        assert store.exists("rows", h) is True
        assert store.exists("rows", "00" * 32) is False

    def test_two_level_sharding(self, tmp_repo: Path):
        store = ObjectStore(tmp_repo / ".datahub" / "objects")
        data = b"sharding test"
        h = store.write("rows", data)
        expected_path = store.root / "rows" / h[0:2] / h[2:4] / h
        assert expected_path.exists()

    def test_zstd_compression(self, tmp_repo: Path):
        store = ObjectStore(tmp_repo / ".datahub" / "objects")
        data = b"A" * 10000
        h = store.write("rows", data)
        raw_path = store.root / "rows" / h[0:2] / h[2:4] / h
        assert raw_path.stat().st_size < len(data)

    def test_idempotent_write(self, tmp_repo: Path):
        store = ObjectStore(tmp_repo / ".datahub" / "objects")
        data = b"same content"
        h1 = store.write("rows", data)
        h2 = store.write("rows", data)
        assert h1 == h2

    def test_different_types_independent(self, tmp_repo: Path):
        store = ObjectStore(tmp_repo / ".datahub" / "objects")
        data = b"some bytes"
        h = store.write("manifests", data)
        assert store.exists("manifests", h) is True
        assert store.exists("rows", h) is False

    def test_batch_exists(self, tmp_repo: Path):
        store = ObjectStore(tmp_repo / ".datahub" / "objects")
        h1 = store.write("rows", b"one")
        h2 = store.write("rows", b"two")
        missing = "00" * 32
        result = store.batch_exists("rows", [h1, h2, missing])
        assert result == {h1: True, h2: True, missing: False}
