"""Tests for dit sparse-checkout add/remove/list/disable subcommands."""
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
                server_app, base_url=base_url,
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


def _sparse_clone(server_app, tmp_path, monkeypatch) -> Path:
    """Create a source repo, push, then sparse clone it. Returns clone_dir."""
    src = tmp_path / "src"
    src.mkdir()
    monkeypatch.chdir(src)
    runner.invoke(app, ["init"], catch_exceptions=False)

    (src / "bug-fix").mkdir()
    (src / "bug-fix" / "train.jsonl").write_text(
        json.dumps({"messages": [{"role": "user", "content": "bf-q"}, {"role": "assistant", "content": "bf-a"}]}) + "\n"
    )
    (src / "general").mkdir()
    (src / "general" / "eval.jsonl").write_text(
        json.dumps({"messages": [{"role": "user", "content": "g-q"}, {"role": "assistant", "content": "g-a"}]}) + "\n"
    )
    (src / "top.jsonl").write_text(
        json.dumps({"messages": [{"role": "user", "content": "t-q"}, {"role": "assistant", "content": "t-a"}]}) + "\n"
    )

    runner.invoke(app, ["add", "bug-fix/train.jsonl"], catch_exceptions=False)
    runner.invoke(app, ["add", "general/eval.jsonl"], catch_exceptions=False)
    runner.invoke(app, ["add", "top.jsonl"], catch_exceptions=False)
    runner.invoke(app, ["commit", "-m", "init"], catch_exceptions=False)

    _patch_remote_client(monkeypatch, server_app)
    _push_to_server(server_app, src, monkeypatch)

    clone_dir = tmp_path / "sparse_clone"
    result = runner.invoke(
        app,
        ["clone", "--sparse", "http://testserver/dataset", str(clone_dir), "--token", "dit_admin"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    monkeypatch.chdir(clone_dir)
    return clone_dir


class TestSparseCheckoutAdd:
    def test_add_fetches_single_file(self, server_app, tmp_path, monkeypatch) -> None:
        clone_dir = _sparse_clone(server_app, tmp_path, monkeypatch)
        assert not (clone_dir / "bug-fix" / "train.jsonl").exists()

        result = runner.invoke(app, ["sparse-checkout", "add", "bug-fix/train.jsonl"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        assert (clone_dir / "bug-fix" / "train.jsonl").exists()
        rows = [json.loads(line) for line in (clone_dir / "bug-fix" / "train.jsonl").read_text().splitlines() if line.strip()]
        assert len(rows) == 1
        assert rows[0]["messages"][0]["content"] == "bf-q"

    def test_add_updates_sparse_config(self, server_app, tmp_path, monkeypatch) -> None:
        clone_dir = _sparse_clone(server_app, tmp_path, monkeypatch)
        runner.invoke(app, ["sparse-checkout", "add", "bug-fix/train.jsonl"], catch_exceptions=False)
        paths = load_sparse_paths(clone_dir / ".dit")
        assert "bug-fix/train.jsonl" in paths

    def test_add_directory(self, server_app, tmp_path, monkeypatch) -> None:
        clone_dir = _sparse_clone(server_app, tmp_path, monkeypatch)
        result = runner.invoke(app, ["sparse-checkout", "add", "bug-fix/"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        assert (clone_dir / "bug-fix" / "train.jsonl").exists()
        paths = load_sparse_paths(clone_dir / ".dit")
        assert "bug-fix/" in paths

    def test_add_nonexistent_path_fails(self, server_app, tmp_path, monkeypatch) -> None:
        _sparse_clone(server_app, tmp_path, monkeypatch)
        result = runner.invoke(app, ["sparse-checkout", "add", "nonexistent/file.jsonl"])
        assert result.exit_code != 0

    def test_add_not_sparse_repo_fails(self, tmp_path, monkeypatch) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        monkeypatch.chdir(repo)
        runner.invoke(app, ["init"], catch_exceptions=False)
        result = runner.invoke(app, ["sparse-checkout", "add", "some.jsonl"])
        assert result.exit_code != 0
        assert "not a sparse" in result.output.lower()


class TestSparseCheckoutList:
    def test_list_shows_all_files(self, server_app, tmp_path, monkeypatch) -> None:
        _sparse_clone(server_app, tmp_path, monkeypatch)
        result = runner.invoke(app, ["sparse-checkout", "list"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        assert "bug-fix/train.jsonl" in result.output
        assert "general/eval.jsonl" in result.output
        assert "top.jsonl" in result.output

    def test_list_marks_fetched(self, server_app, tmp_path, monkeypatch) -> None:
        _sparse_clone(server_app, tmp_path, monkeypatch)
        runner.invoke(app, ["sparse-checkout", "add", "top.jsonl"], catch_exceptions=False)
        result = runner.invoke(app, ["sparse-checkout", "list"], catch_exceptions=False)
        # Fetched files should have a different marker than unfetched
        lines = result.output.strip().split("\n")
        fetched = [line for line in lines if "top.jsonl" in line]
        unfetched = [line for line in lines if "bug-fix/train.jsonl" in line]
        assert len(fetched) == 1
        assert len(unfetched) == 1
        assert fetched[0].strip().startswith("[x]") or fetched[0].strip().startswith("[X]")
        assert unfetched[0].strip().startswith("[ ]")


class TestSparseCheckoutRemove:
    def test_remove_deletes_file_and_updates_config(self, server_app, tmp_path, monkeypatch) -> None:
        clone_dir = _sparse_clone(server_app, tmp_path, monkeypatch)
        runner.invoke(app, ["sparse-checkout", "add", "bug-fix/train.jsonl"], catch_exceptions=False)
        assert (clone_dir / "bug-fix" / "train.jsonl").exists()

        result = runner.invoke(app, ["sparse-checkout", "remove", "bug-fix/train.jsonl"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        assert not (clone_dir / "bug-fix" / "train.jsonl").exists()
        paths = load_sparse_paths(clone_dir / ".dit")
        assert "bug-fix/train.jsonl" not in paths


class TestSparseCheckoutDisable:
    def test_disable_converts_to_full(self, server_app, tmp_path, monkeypatch) -> None:
        clone_dir = _sparse_clone(server_app, tmp_path, monkeypatch)
        result = runner.invoke(app, ["sparse-checkout", "disable"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        assert not is_sparse(clone_dir / ".dit")
        # All files should now be materialized
        assert (clone_dir / "bug-fix" / "train.jsonl").exists()
        assert (clone_dir / "general" / "eval.jsonl").exists()
        assert (clone_dir / "top.jsonl").exists()

