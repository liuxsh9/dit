"""Monkey / stress tests for batch-download via clone, pull, and sparse-checkout."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dit.cli.main import app
from dit.core.config import set_remote

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixtures (copied from test_cli_push.py)
# ---------------------------------------------------------------------------

@pytest.fixture
def server_app(tmp_path):
    from dit.server.app import create_app
    from dit.server.config import ServerSettings
    from dit.server.auth import get_session, verify_token
    from dit.server.database import create_db_engine, create_session_factory
    from dit.server.models import Base
    import asyncio

    settings = ServerSettings(
        database_url="sqlite+aiosqlite:///:memory:",
        data_dir=str(tmp_path / "server"),
    )
    fastapi_app = create_app(settings=settings)

    async def _setup():
        engine = await create_db_engine(settings.database_url)
        async with engine.begin() as conn:
            for table in Base.metadata.tables.values():
                table.schema = None
            await conn.run_sync(Base.metadata.create_all)
        factory = create_session_factory(engine)

        async def override_session():
            async with factory() as s:
                yield s

        def override_token():
            from types import SimpleNamespace
            return SimpleNamespace(
                id=1, token_hash="x" * 64, label="test-admin",
                permissions="admin", expires_at=None, repo_scope=None,
            )

        fastapi_app.dependency_overrides[get_session] = override_session
        fastapi_app.dependency_overrides[verify_token] = override_token
        return engine

    loop = asyncio.new_event_loop()
    engine = loop.run_until_complete(_setup())
    yield fastapi_app
    fastapi_app.dependency_overrides.clear()
    for table in Base.metadata.tables.values():
        table.schema = "dit"
    loop.run_until_complete(engine.dispose())
    loop.close()


def _patch_remote_client(monkeypatch, server_app):
    import dit.core.remote as remote_mod
    from starlette.testclient import TestClient

    class PatchedRemoteClient(remote_mod.RemoteClient):
        def __init__(self, base_url: str, token: str = "", repo: str = "") -> None:
            self.base_url = base_url.rstrip("/")
            self.repo = repo
            self.client = TestClient(
                server_app,
                base_url=base_url,
                headers={"Authorization": f"token {token}"},
            )

        def _dit_prefix(self) -> str:
            return f"{self.base_url}/api/v1/repos/{self.repo}"

    monkeypatch.setattr(remote_mod, "RemoteClient", PatchedRemoteClient)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_row(tag: str, idx: int) -> dict:
    """Generate a deterministic JSONL row."""
    return {"messages": [
        {"role": "user", "content": f"{tag}-q{idx}"},
        {"role": "assistant", "content": f"{tag}-a{idx}"},
    ]}


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r, sort_keys=True) + "\n")


def _read_jsonl(path: Path) -> list[dict]:
    lines = path.read_text().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _init_repo(d: Path, monkeypatch) -> None:
    monkeypatch.chdir(d)
    r = runner.invoke(app, ["init"], catch_exceptions=False)
    assert r.exit_code == 0, r.output


def _add_commit(d: Path, files: list[str], msg: str, monkeypatch) -> None:
    monkeypatch.chdir(d)
    for f in files:
        r = runner.invoke(app, ["add", f], catch_exceptions=False)
        assert r.exit_code == 0, r.output
    r = runner.invoke(app, ["commit", "-m", msg], catch_exceptions=False)
    assert r.exit_code == 0, r.output


def _create_remote_repo(server_app):
    from starlette.testclient import TestClient
    c = TestClient(server_app, headers={"Authorization": "Bearer dit_admin"})
    resp = c.post("/api/v1/repos", json={"name": "dataset"})
    assert resp.status_code in (201, 409)


def _push(d: Path, monkeypatch) -> None:
    monkeypatch.chdir(d)
    r = runner.invoke(app, ["push"], catch_exceptions=False)
    assert r.exit_code == 0, r.output


def _clone(dest: str, monkeypatch, sparse: bool = False) -> str:
    args = ["clone", "http://testserver/dataset", dest, "--token", "dit_admin"]
    if sparse:
        args.append("--sparse")
    r = runner.invoke(app, args, catch_exceptions=False)
    assert r.exit_code == 0, r.output
    return r.output


def _pull(d: Path, monkeypatch) -> str:
    monkeypatch.chdir(d)
    r = runner.invoke(app, ["pull"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    return r.output


# ---------------------------------------------------------------------------
# 1. Clone with 50+ files across 10 directories
# ---------------------------------------------------------------------------

def test_clone_50_files_10_dirs(server_app, tmp_path, monkeypatch):
    _patch_remote_client(monkeypatch, server_app)
    src = tmp_path / "src"
    src.mkdir()
    _init_repo(src, monkeypatch)

    expected: dict[str, list[dict]] = {}
    all_files: list[str] = []
    for d in range(10):
        for f in range(5):
            rel = f"dir{d}/file{f}.jsonl"
            rows = [_make_row(f"d{d}f{f}", i) for i in range(3)]
            _write_jsonl(src / rel, rows)
            expected[rel] = rows
            all_files.append(rel)

    _add_commit(src, all_files, "50 files", monkeypatch)
    _create_remote_repo(server_app)
    set_remote(src / ".dit", "origin", "http://testserver/dataset", token="dit_admin")
    _push(src, monkeypatch)

    clone_dir = tmp_path / "clone50"
    _clone(str(clone_dir), monkeypatch)

    for rel, rows in expected.items():
        cloned = _read_jsonl(clone_dir / rel)
        assert cloned == rows, f"Mismatch in {rel}"


# ---------------------------------------------------------------------------
# 2. Clone then pull with 20 file changes
# ---------------------------------------------------------------------------

def test_clone_then_pull_20_changes(server_app, tmp_path, monkeypatch):
    _patch_remote_client(monkeypatch, server_app)
    src = tmp_path / "src"
    src.mkdir()
    _init_repo(src, monkeypatch)

    v1: dict[str, list[dict]] = {}
    all_files: list[str] = []
    for i in range(30):
        rel = f"f{i}.jsonl"
        rows = [_make_row(f"v1-{i}", j) for j in range(2)]
        _write_jsonl(src / rel, rows)
        v1[rel] = rows
        all_files.append(rel)

    _add_commit(src, all_files, "v1", monkeypatch)
    _create_remote_repo(server_app)
    set_remote(src / ".dit", "origin", "http://testserver/dataset", token="dit_admin")
    _push(src, monkeypatch)

    clone_dir = tmp_path / "clone_pull"
    _clone(str(clone_dir), monkeypatch)

    # Modify 20 files in src
    changed_files: list[str] = []
    v2 = dict(v1)
    for i in range(20):
        rel = f"f{i}.jsonl"
        rows = [_make_row(f"v2-{i}", j) for j in range(3)]
        _write_jsonl(src / rel, rows)
        v2[rel] = rows
        changed_files.append(rel)

    _add_commit(src, changed_files, "v2", monkeypatch)
    _push(src, monkeypatch)

    _pull(clone_dir, monkeypatch)

    for rel, rows in v2.items():
        cloned = _read_jsonl(clone_dir / rel)
        assert cloned == rows, f"Mismatch in {rel}"


# ---------------------------------------------------------------------------
# 3. Sparse clone then sparse-checkout add entire directory
# ---------------------------------------------------------------------------

def test_sparse_clone_add_directory(server_app, tmp_path, monkeypatch):
    _patch_remote_client(monkeypatch, server_app)
    src = tmp_path / "src"
    src.mkdir()
    _init_repo(src, monkeypatch)

    expected: dict[str, list[dict]] = {}
    all_files: list[str] = []
    for d in range(5):
        for f in range(5):
            rel = f"group{d}/item{f}.jsonl"
            rows = [_make_row(f"g{d}i{f}", i) for i in range(2)]
            _write_jsonl(src / rel, rows)
            expected[rel] = rows
            all_files.append(rel)

    _add_commit(src, all_files, "25 files", monkeypatch)
    _create_remote_repo(server_app)
    set_remote(src / ".dit", "origin", "http://testserver/dataset", token="dit_admin")
    _push(src, monkeypatch)

    clone_dir = tmp_path / "sparse_clone"
    _clone(str(clone_dir), monkeypatch, sparse=True)

    # No data files should be materialized yet
    for d in range(5):
        for f in range(5):
            assert not (clone_dir / f"group{d}/item{f}.jsonl").exists()

    # sparse-checkout add one directory
    monkeypatch.chdir(clone_dir)
    r = runner.invoke(
        app, ["sparse-checkout", "add", "group2/"], catch_exceptions=False,
    )
    assert r.exit_code == 0, r.output

    # All 5 files in group2 should be materialized
    for f in range(5):
        rel = f"group2/item{f}.jsonl"
        assert (clone_dir / rel).exists(), f"{rel} not materialized"
        cloned = _read_jsonl(clone_dir / rel)
        assert cloned == expected[rel]

    # Other dirs still absent
    for d in [0, 1, 3, 4]:
        for f in range(5):
            assert not (clone_dir / f"group{d}/item{f}.jsonl").exists()


# ---------------------------------------------------------------------------
# 4. Sparse-checkout add, remove, re-add
# ---------------------------------------------------------------------------

def test_sparse_add_remove_readd(server_app, tmp_path, monkeypatch):
    _patch_remote_client(monkeypatch, server_app)
    src = tmp_path / "src"
    src.mkdir()
    _init_repo(src, monkeypatch)

    rows = [_make_row("cycle", i) for i in range(4)]
    _write_jsonl(src / "data.jsonl", rows)
    _add_commit(src, ["data.jsonl"], "init", monkeypatch)
    _create_remote_repo(server_app)
    set_remote(src / ".dit", "origin", "http://testserver/dataset", token="dit_admin")
    _push(src, monkeypatch)

    clone_dir = tmp_path / "sparse_cycle"
    _clone(str(clone_dir), monkeypatch, sparse=True)
    monkeypatch.chdir(clone_dir)

    # Add
    r = runner.invoke(app, ["sparse-checkout", "add", "data.jsonl"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    assert (clone_dir / "data.jsonl").exists()
    assert _read_jsonl(clone_dir / "data.jsonl") == rows

    # Remove
    r = runner.invoke(app, ["sparse-checkout", "remove", "data.jsonl"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    assert not (clone_dir / "data.jsonl").exists()

    # Re-add
    r = runner.invoke(app, ["sparse-checkout", "add", "data.jsonl"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    assert (clone_dir / "data.jsonl").exists()
    assert _read_jsonl(clone_dir / "data.jsonl") == rows


# ---------------------------------------------------------------------------
# 5. Batch-download with duplicate hashes (two files sharing identical rows)
# ---------------------------------------------------------------------------

def test_clone_duplicate_hashes(server_app, tmp_path, monkeypatch):
    _patch_remote_client(monkeypatch, server_app)
    src = tmp_path / "src"
    src.mkdir()
    _init_repo(src, monkeypatch)

    # Two files with identical content -> same row hashes
    identical_rows = [_make_row("dup", i) for i in range(3)]
    _write_jsonl(src / "alpha.jsonl", identical_rows)
    _write_jsonl(src / "beta.jsonl", identical_rows)
    _add_commit(src, ["alpha.jsonl", "beta.jsonl"], "dup", monkeypatch)

    _create_remote_repo(server_app)
    set_remote(src / ".dit", "origin", "http://testserver/dataset", token="dit_admin")
    _push(src, monkeypatch)

    clone_dir = tmp_path / "clone_dup"
    _clone(str(clone_dir), monkeypatch)

    assert _read_jsonl(clone_dir / "alpha.jsonl") == identical_rows
    assert _read_jsonl(clone_dir / "beta.jsonl") == identical_rows


# ---------------------------------------------------------------------------
# 6. Clone repo with single huge file (500 rows)
# ---------------------------------------------------------------------------

def test_clone_huge_file_500_rows(server_app, tmp_path, monkeypatch):
    _patch_remote_client(monkeypatch, server_app)
    src = tmp_path / "src"
    src.mkdir()
    _init_repo(src, monkeypatch)

    rows = [_make_row("big", i) for i in range(500)]
    _write_jsonl(src / "huge.jsonl", rows)
    _add_commit(src, ["huge.jsonl"], "huge", monkeypatch)

    _create_remote_repo(server_app)
    set_remote(src / ".dit", "origin", "http://testserver/dataset", token="dit_admin")
    _push(src, monkeypatch)

    clone_dir = tmp_path / "clone_huge"
    _clone(str(clone_dir), monkeypatch)

    cloned = _read_jsonl(clone_dir / "huge.jsonl")
    assert len(cloned) == 500
    assert cloned == rows


# ---------------------------------------------------------------------------
# 7. Pull after remote adds new files
# ---------------------------------------------------------------------------

def test_pull_new_files_added(server_app, tmp_path, monkeypatch):
    _patch_remote_client(monkeypatch, server_app)
    src = tmp_path / "src"
    src.mkdir()
    _init_repo(src, monkeypatch)

    v1_files: list[str] = []
    v1_data: dict[str, list[dict]] = {}
    for i in range(5):
        rel = f"orig{i}.jsonl"
        rows = [_make_row(f"orig{i}", j) for j in range(2)]
        _write_jsonl(src / rel, rows)
        v1_files.append(rel)
        v1_data[rel] = rows

    _add_commit(src, v1_files, "v1", monkeypatch)
    _create_remote_repo(server_app)
    set_remote(src / ".dit", "origin", "http://testserver/dataset", token="dit_admin")
    _push(src, monkeypatch)

    clone_dir = tmp_path / "clone_add"
    _clone(str(clone_dir), monkeypatch)

    # Push v2 with 5 more files
    new_files: list[str] = []
    all_data = dict(v1_data)
    for i in range(5):
        rel = f"new{i}.jsonl"
        rows = [_make_row(f"new{i}", j) for j in range(2)]
        _write_jsonl(src / rel, rows)
        new_files.append(rel)
        all_data[rel] = rows

    _add_commit(src, new_files, "v2-add", monkeypatch)
    _push(src, monkeypatch)

    _pull(clone_dir, monkeypatch)

    for rel, rows in all_data.items():
        assert (clone_dir / rel).exists(), f"{rel} missing after pull"
        assert _read_jsonl(clone_dir / rel) == rows


# ---------------------------------------------------------------------------
# 8. Pull after remote deletes files
# ---------------------------------------------------------------------------

def test_pull_files_deleted(server_app, tmp_path, monkeypatch):
    _patch_remote_client(monkeypatch, server_app)
    src = tmp_path / "src"
    src.mkdir()
    _init_repo(src, monkeypatch)

    all_files: list[str] = []
    for i in range(10):
        rel = f"item{i}.jsonl"
        rows = [_make_row(f"item{i}", 0)]
        _write_jsonl(src / rel, rows)
        all_files.append(rel)

    _add_commit(src, all_files, "v1-10files", monkeypatch)
    _create_remote_repo(server_app)
    set_remote(src / ".dit", "origin", "http://testserver/dataset", token="dit_admin")
    _push(src, monkeypatch)

    clone_dir = tmp_path / "clone_del"
    _clone(str(clone_dir), monkeypatch)
    assert len(list(clone_dir.glob("*.jsonl"))) == 10

    # Delete 5 files in src, stage deletions via "dit add <missing-tracked-file>"
    monkeypatch.chdir(src)
    for i in range(5):
        (src / f"item{i}.jsonl").unlink()
    deleted = [f"item{i}.jsonl" for i in range(5)]
    _add_commit(src, deleted, "v2-delete5", monkeypatch)
    _push(src, monkeypatch)

    _pull(clone_dir, monkeypatch)

    # Only 5 files should remain
    surviving = list(clone_dir.glob("*.jsonl"))
    assert len(surviving) == 5, f"Expected 5 files, got {len(surviving)}: {surviving}"
    for i in range(5, 10):
        assert (clone_dir / f"item{i}.jsonl").exists()
    for i in range(5):
        assert not (clone_dir / f"item{i}.jsonl").exists()
