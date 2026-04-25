"""Integration test for dit push using FastAPI app via ASGITransport."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import httpx
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
    from dit.server.models import Base, Token
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
                id=1,
                token_hash="x" * 64,
                label="test-admin",
                permissions="admin",
                expires_at=None,
                repo_scope=None,
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


@pytest.fixture
def local_repo(tmp_path: Path, monkeypatch) -> Path:
    repo = tmp_path / "client"
    repo.mkdir()
    monkeypatch.chdir(repo)

    r = runner.invoke(app, ["init"], catch_exceptions=False)
    assert r.exit_code == 0, r.output

    jsonl = repo / "train.jsonl"
    jsonl.write_text(
        json.dumps({"messages": [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]}) + "\n"
    )

    r = runner.invoke(app, ["add", "train.jsonl"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    r = runner.invoke(app, ["commit", "-m", "initial"], catch_exceptions=False)
    assert r.exit_code == 0, r.output

    return repo


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


def test_push_creates_objects_on_server(server_app, local_repo: Path, monkeypatch) -> None:
    _patch_remote_client(monkeypatch, server_app)

    from starlette.testclient import TestClient
    sync_client = TestClient(server_app, headers={"Authorization": "Bearer dit_admin"})
    resp = sync_client.post("/api/v1/repos", json={"name": "train"})
    assert resp.status_code == 201

    dot = local_repo / ".dit"
    set_remote(dot, "origin", "http://testserver/train", token="dit_admin")

    result = runner.invoke(app, ["push"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert "Pushed" in result.output

    resp = sync_client.get("/api/v1/repos/train/refs/heads/main")
    assert resp.status_code == 200
    assert len(resp.json()["target_hash"]) == 64


def test_push_idempotent(server_app, local_repo: Path, monkeypatch) -> None:
    _patch_remote_client(monkeypatch, server_app)

    from starlette.testclient import TestClient
    sync_client = TestClient(server_app, headers={"Authorization": "Bearer dit_admin"})
    sync_client.post("/api/v1/repos", json={"name": "train"})

    dot = local_repo / ".dit"
    set_remote(dot, "origin", "http://testserver/train", token="dit_admin")

    r1 = runner.invoke(app, ["push"], catch_exceptions=False)
    assert r1.exit_code == 0
    r2 = runner.invoke(app, ["push"], catch_exceptions=False)
    assert r2.exit_code == 0
    assert "0 new objects" in r2.output or "Pushed 0" in r2.output
