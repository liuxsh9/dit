"""Tests for dit clone --sparse functionality."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dit.cli.main import app
from dit.core.config import set_remote
from dit.core.sparse import is_sparse, load_sparse_paths

runner = CliRunner()


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


def _push_to_server(server_app, src_dir: Path, monkeypatch) -> None:
    from starlette.testclient import TestClient
    sync_client = TestClient(server_app, headers={"Authorization": "Bearer dit_admin"})
    resp = sync_client.post("/api/v1/repos", json={"name": "dataset"})
    assert resp.status_code in (201, 409)

    dot = src_dir / ".dit"
    set_remote(dot, "origin", "http://testserver/dataset", token="dit_admin")

    monkeypatch.chdir(src_dir)
    result = runner.invoke(app, ["push"], catch_exceptions=False)
    assert result.exit_code == 0, result.output


def _create_test_repo(tmp_path: Path, monkeypatch) -> Path:
    """Create a test repo with nested structure: bug-fix/train.jsonl, general/eval.jsonl, top.jsonl."""
    src = tmp_path / "src"
    src.mkdir()
    monkeypatch.chdir(src)
    runner.invoke(app, ["init"], catch_exceptions=False)

    (src / "bug-fix").mkdir()
    bf_jsonl = src / "bug-fix" / "train.jsonl"
    bf_jsonl.write_text(
        json.dumps({"messages": [{"role": "user", "content": "bf-q1"}, {"role": "assistant", "content": "bf-a1"}]}) + "\n"
        + json.dumps({"messages": [{"role": "user", "content": "bf-q2"}, {"role": "assistant", "content": "bf-a2"}]}) + "\n"
    )

    (src / "general").mkdir()
    gen_jsonl = src / "general" / "eval.jsonl"
    gen_jsonl.write_text(
        json.dumps({"messages": [{"role": "user", "content": "gen-q1"}, {"role": "assistant", "content": "gen-a1"}]}) + "\n"
    )

    top_jsonl = src / "top.jsonl"
    top_jsonl.write_text(
        json.dumps({"messages": [{"role": "user", "content": "top-q"}, {"role": "assistant", "content": "top-a"}]}) + "\n"
    )

    runner.invoke(app, ["add", "bug-fix/train.jsonl"], catch_exceptions=False)
    runner.invoke(app, ["add", "general/eval.jsonl"], catch_exceptions=False)
    runner.invoke(app, ["add", "top.jsonl"], catch_exceptions=False)
    runner.invoke(app, ["commit", "-m", "init with 3 files"], catch_exceptions=False)
    return src


def test_sparse_clone_creates_dit_dir(server_app, tmp_path: Path, monkeypatch) -> None:
    src = _create_test_repo(tmp_path, monkeypatch)
    _patch_remote_client(monkeypatch, server_app)
    _push_to_server(server_app, src, monkeypatch)

    clone_dir = tmp_path / "sparse_clone"
    result = runner.invoke(
        app,
        ["clone", "--sparse", "http://testserver/dataset", str(clone_dir), "--token", "dit_admin"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert (clone_dir / ".dit").is_dir()
    assert is_sparse(clone_dir / ".dit")


def test_sparse_clone_no_data_files_materialized(server_app, tmp_path: Path, monkeypatch) -> None:
    src = _create_test_repo(tmp_path, monkeypatch)
    _patch_remote_client(monkeypatch, server_app)
    _push_to_server(server_app, src, monkeypatch)

    clone_dir = tmp_path / "sparse_clone"
    runner.invoke(
        app,
        ["clone", "--sparse", "http://testserver/dataset", str(clone_dir), "--token", "dit_admin"],
        catch_exceptions=False,
    )
    assert not (clone_dir / "top.jsonl").exists()
    assert not (clone_dir / "bug-fix" / "train.jsonl").exists()
    assert not (clone_dir / "general" / "eval.jsonl").exists()


def test_sparse_clone_creates_directory_skeleton(server_app, tmp_path: Path, monkeypatch) -> None:
    src = _create_test_repo(tmp_path, monkeypatch)
    _patch_remote_client(monkeypatch, server_app)
    _push_to_server(server_app, src, monkeypatch)

    clone_dir = tmp_path / "sparse_clone"
    runner.invoke(
        app,
        ["clone", "--sparse", "http://testserver/dataset", str(clone_dir), "--token", "dit_admin"],
        catch_exceptions=False,
    )
    assert (clone_dir / "bug-fix").is_dir()
    assert (clone_dir / "general").is_dir()


def test_sparse_clone_has_empty_sparse_checkout(server_app, tmp_path: Path, monkeypatch) -> None:
    src = _create_test_repo(tmp_path, monkeypatch)
    _patch_remote_client(monkeypatch, server_app)
    _push_to_server(server_app, src, monkeypatch)

    clone_dir = tmp_path / "sparse_clone"
    runner.invoke(
        app,
        ["clone", "--sparse", "http://testserver/dataset", str(clone_dir), "--token", "dit_admin"],
        catch_exceptions=False,
    )
    paths = load_sparse_paths(clone_dir / ".dit")
    assert paths == set()


def test_sparse_clone_has_tree_objects(server_app, tmp_path: Path, monkeypatch) -> None:
    """Sparse clone must download tree objects so we can browse the structure."""
    from dit.core.objects import deserialize_commit
    from dit.core.tree_walker import flatten_tree

    src = _create_test_repo(tmp_path, monkeypatch)
    _patch_remote_client(monkeypatch, server_app)
    _push_to_server(server_app, src, monkeypatch)

    clone_dir = tmp_path / "sparse_clone"
    runner.invoke(
        app,
        ["clone", "--sparse", "http://testserver/dataset", str(clone_dir), "--token", "dit_admin"],
        catch_exceptions=False,
    )

    from dit.core.store import ObjectStore
    from dit.core.refs import RefStore
    store = ObjectStore(clone_dir / ".dit" / "objects")
    refs = RefStore(clone_dir / ".dit")
    head = refs.resolve_head()
    assert head is not None
    commit = deserialize_commit(store.read("commits", head))
    flat = flatten_tree(store, commit.tree_hash)
    assert "bug-fix/train.jsonl" in flat
    assert "general/eval.jsonl" in flat
    assert "top.jsonl" in flat


def test_sparse_clone_no_row_objects(server_app, tmp_path: Path, monkeypatch) -> None:
    """Sparse clone should NOT download row objects."""
    src = _create_test_repo(tmp_path, monkeypatch)
    _patch_remote_client(monkeypatch, server_app)
    _push_to_server(server_app, src, monkeypatch)

    clone_dir = tmp_path / "sparse_clone"
    runner.invoke(
        app,
        ["clone", "--sparse", "http://testserver/dataset", str(clone_dir), "--token", "dit_admin"],
        catch_exceptions=False,
    )

    rows_dir = clone_dir / ".dit" / "objects" / "rows"
    if rows_dir.exists():
        row_files = list(rows_dir.rglob("*"))
        row_files = [f for f in row_files if f.is_file()]
        assert len(row_files) == 0, f"Expected no row objects in sparse clone, found {len(row_files)}"


def test_sparse_clone_output_message(server_app, tmp_path: Path, monkeypatch) -> None:
    src = _create_test_repo(tmp_path, monkeypatch)
    _patch_remote_client(monkeypatch, server_app)
    _push_to_server(server_app, src, monkeypatch)

    clone_dir = tmp_path / "sparse_clone"
    result = runner.invoke(
        app,
        ["clone", "--sparse", "http://testserver/dataset", str(clone_dir), "--token", "dit_admin"],
        catch_exceptions=False,
    )
    assert "sparse" in result.output.lower()


def test_normal_clone_not_sparse(server_app, tmp_path: Path, monkeypatch) -> None:
    """Regular clone (no --sparse) should NOT create sparse-checkout file."""
    src = _create_test_repo(tmp_path, monkeypatch)
    _patch_remote_client(monkeypatch, server_app)
    _push_to_server(server_app, src, monkeypatch)

    clone_dir = tmp_path / "full_clone"
    runner.invoke(
        app,
        ["clone", "http://testserver/dataset", str(clone_dir), "--token", "dit_admin"],
        catch_exceptions=False,
    )
    assert not is_sparse(clone_dir / ".dit")
    assert (clone_dir / "top.jsonl").exists()
