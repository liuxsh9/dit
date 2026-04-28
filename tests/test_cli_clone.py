"""Integration test for dit clone using starlette TestClient."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dit.cli.main import app
from dit.core.config import set_remote

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
            # Dit server uses direct paths without /dit/ infix
            return f"{self.base_url}/api/v1/repos/{self.repo}"

    monkeypatch.setattr(remote_mod, "RemoteClient", PatchedRemoteClient)


def _push_to_server(server_app, src_dir: Path, monkeypatch) -> None:
    from starlette.testclient import TestClient
    sync_client = TestClient(server_app, headers={"Authorization": "Bearer dit_admin"})
    resp = sync_client.post("/api/v1/repos", json={"name": "dataset"})
    assert resp.status_code == 201

    dot = src_dir / ".dit"
    set_remote(dot, "origin", "http://testserver/dataset", token="dit_admin")

    monkeypatch.chdir(src_dir)
    result = runner.invoke(app, ["push"], catch_exceptions=False)
    assert result.exit_code == 0, result.output


def test_clone_creates_jsonl_files(server_app, tmp_path: Path, monkeypatch) -> None:
    src = tmp_path / "src"
    src.mkdir()
    monkeypatch.chdir(src)
    runner.invoke(app, ["init"], catch_exceptions=False)

    rows = [
        {"messages": [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}]},
        {"messages": [{"role": "user", "content": "q2"}, {"role": "assistant", "content": "a2"}]},
    ]
    jsonl = src / "train.jsonl"
    with open(jsonl, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")

    runner.invoke(app, ["add", "train.jsonl"], catch_exceptions=False)
    runner.invoke(app, ["commit", "-m", "init"], catch_exceptions=False)

    _patch_remote_client(monkeypatch, server_app)
    _push_to_server(server_app, src, monkeypatch)

    clone_dir = tmp_path / "clone"
    result = runner.invoke(
        app,
        ["clone", "http://testserver/dataset", str(clone_dir), "--token", "dit_admin"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert "Clone complete" in result.output

    cloned_jsonl = clone_dir / "train.jsonl"
    assert cloned_jsonl.exists()
    cloned_rows = [json.loads(line) for line in cloned_jsonl.read_text().splitlines() if line.strip()]
    assert len(cloned_rows) == 2
    assert cloned_rows[0]["messages"][0]["content"] == "q1"
    assert cloned_rows[1]["messages"][0]["content"] == "q2"


def test_clone_materializes_nested_directories(server_app, tmp_path: Path, monkeypatch) -> None:
    """Clone must recursively materialize files in subdirectories."""
    src = tmp_path / "src"
    src.mkdir()
    monkeypatch.chdir(src)
    runner.invoke(app, ["init"], catch_exceptions=False)

    # Create nested directory structure
    subdir = src / "bug-fix"
    subdir.mkdir()
    nested_jsonl = subdir / "train.jsonl"
    rows = [
        {"messages": [{"role": "user", "content": "nested-q"}, {"role": "assistant", "content": "nested-a"}]},
    ]
    with open(nested_jsonl, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")

    # Also a top-level file
    top_jsonl = src / "top.jsonl"
    top_jsonl.write_text(json.dumps({"messages": [{"role": "user", "content": "top"}, {"role": "assistant", "content": "top-a"}]}) + "\n")

    runner.invoke(app, ["add", "bug-fix/train.jsonl"], catch_exceptions=False)
    runner.invoke(app, ["add", "top.jsonl"], catch_exceptions=False)
    runner.invoke(app, ["commit", "-m", "nested"], catch_exceptions=False)

    _patch_remote_client(monkeypatch, server_app)
    _push_to_server(server_app, src, monkeypatch)

    clone_dir = tmp_path / "clone_nested"
    result = runner.invoke(
        app,
        ["clone", "http://testserver/dataset", str(clone_dir), "--token", "dit_admin"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert "Clone complete" in result.output

    # Verify nested file exists
    cloned_nested = clone_dir / "bug-fix" / "train.jsonl"
    assert cloned_nested.exists(), f"Nested file not materialized. Dir contents: {list(clone_dir.rglob('*'))}"
    cloned_rows = [json.loads(line) for line in cloned_nested.read_text().splitlines() if line.strip()]
    assert len(cloned_rows) == 1
    assert cloned_rows[0]["messages"][0]["content"] == "nested-q"

    # Verify top-level file also exists
    assert (clone_dir / "top.jsonl").exists()


def test_clone_sets_up_remote_config(server_app, tmp_path: Path, monkeypatch) -> None:
    src = tmp_path / "src"
    src.mkdir()
    monkeypatch.chdir(src)
    runner.invoke(app, ["init"], catch_exceptions=False)

    jsonl = src / "data.jsonl"
    jsonl.write_text(json.dumps({"messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]}) + "\n")
    runner.invoke(app, ["add", "data.jsonl"], catch_exceptions=False)
    runner.invoke(app, ["commit", "-m", "first"], catch_exceptions=False)

    _patch_remote_client(monkeypatch, server_app)
    _push_to_server(server_app, src, monkeypatch)

    clone_dir = tmp_path / "cloned"
    runner.invoke(
        app,
        ["clone", "http://testserver/dataset", str(clone_dir), "--token", "dit_admin"],
        catch_exceptions=False,
    )

    from dit.core.config import get_remote
    cfg = get_remote(clone_dir / ".dit", "origin")
    assert cfg is not None
    assert "dataset" in cfg["url"]
