"""Integration tests for dit fetch and dit pull commands."""
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


def _init_and_commit(repo: Path, filename: str, rows: list[dict], message: str, monkeypatch) -> None:
    monkeypatch.chdir(repo)
    jsonl = repo / filename
    with open(jsonl, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    r = runner.invoke(app, ["add", filename], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    r = runner.invoke(app, ["commit", "-m", message], catch_exceptions=False)
    assert r.exit_code == 0, r.output


def test_pull_updates_local_data(server_app, tmp_path: Path, monkeypatch) -> None:
    _patch_remote_client(monkeypatch, server_app)

    from starlette.testclient import TestClient
    sync_client = TestClient(server_app, headers={"Authorization": "Bearer dit_admin"})
    sync_client.post("/api/v1/repos", json={"name": "shared"})

    # Client A: init + commit v1 + push
    client_a = tmp_path / "client_a"
    client_a.mkdir()
    monkeypatch.chdir(client_a)
    runner.invoke(app, ["init"], catch_exceptions=False)
    _init_and_commit(
        client_a, "data.jsonl",
        [{"messages": [{"role": "user", "content": "v1"}, {"role": "assistant", "content": "r1"}]}],
        "v1", monkeypatch,
    )
    set_remote(client_a / ".dit", "origin", "http://testserver/shared", token="dit_admin")
    monkeypatch.chdir(client_a)
    r = runner.invoke(app, ["push"], catch_exceptions=False)
    assert r.exit_code == 0, r.output

    # Clone to client_b
    client_b = tmp_path / "client_b"
    r = runner.invoke(
        app,
        ["clone", "http://testserver/shared", str(client_b), "--token", "dit_admin"],
        catch_exceptions=False,
    )
    assert r.exit_code == 0, r.output
    assert "v1" in (client_b / "data.jsonl").read_text()

    # Client A: add v2 + push
    _init_and_commit(
        client_a, "data.jsonl",
        [
            {"messages": [{"role": "user", "content": "v1"}, {"role": "assistant", "content": "r1"}]},
            {"messages": [{"role": "user", "content": "v2"}, {"role": "assistant", "content": "r2"}]},
        ],
        "v2", monkeypatch,
    )
    monkeypatch.chdir(client_a)
    r = runner.invoke(app, ["push"], catch_exceptions=False)
    assert r.exit_code == 0, r.output

    # Client B: pull
    monkeypatch.chdir(client_b)
    r = runner.invoke(app, ["pull"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    assert "Pulled" in r.output

    b_data = (client_b / "data.jsonl").read_text()
    lines = [line for line in b_data.splitlines() if line.strip()]
    assert len(lines) == 2
    assert "v2" in b_data


def test_pull_materializes_nested_directories(server_app, tmp_path: Path, monkeypatch) -> None:
    """Pull must recursively materialize files in subdirectories."""
    _patch_remote_client(monkeypatch, server_app)

    from starlette.testclient import TestClient
    sync_client = TestClient(server_app, headers={"Authorization": "Bearer dit_admin"})
    sync_client.post("/api/v1/repos", json={"name": "nested"})

    # Client A: init with top-level file, push
    client_a = tmp_path / "client_a"
    client_a.mkdir()
    monkeypatch.chdir(client_a)
    runner.invoke(app, ["init"], catch_exceptions=False)
    _init_and_commit(
        client_a, "top.jsonl",
        [{"messages": [{"role": "user", "content": "v1"}, {"role": "assistant", "content": "r1"}]}],
        "v1", monkeypatch,
    )
    set_remote(client_a / ".dit", "origin", "http://testserver/nested", token="dit_admin")
    monkeypatch.chdir(client_a)
    r = runner.invoke(app, ["push"], catch_exceptions=False)
    assert r.exit_code == 0, r.output

    # Clone to client_b
    client_b = tmp_path / "client_b"
    r = runner.invoke(
        app,
        ["clone", "http://testserver/nested", str(client_b), "--token", "dit_admin"],
        catch_exceptions=False,
    )
    assert r.exit_code == 0, r.output

    # Client A: add nested file + push
    subdir = client_a / "sub"
    subdir.mkdir()
    nested_jsonl = subdir / "nested.jsonl"
    nested_jsonl.write_text(json.dumps({"messages": [{"role": "user", "content": "deep"}, {"role": "assistant", "content": "deep-a"}]}) + "\n")
    monkeypatch.chdir(client_a)
    r = runner.invoke(app, ["add", "sub/nested.jsonl"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    r = runner.invoke(app, ["commit", "-m", "add nested"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    r = runner.invoke(app, ["push"], catch_exceptions=False)
    assert r.exit_code == 0, r.output

    # Client B: pull
    monkeypatch.chdir(client_b)
    r = runner.invoke(app, ["pull"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    assert "Pulled" in r.output

    # Verify nested file materialized
    nested_path = client_b / "sub" / "nested.jsonl"
    assert nested_path.exists(), f"Nested file not materialized after pull. Dir contents: {list(client_b.rglob('*'))}"
    content = nested_path.read_text()
    assert "deep" in content


def test_pull_already_up_to_date(server_app, tmp_path: Path, monkeypatch) -> None:
    _patch_remote_client(monkeypatch, server_app)

    from starlette.testclient import TestClient
    sync_client = TestClient(server_app, headers={"Authorization": "Bearer dit_admin"})
    sync_client.post("/api/v1/repos", json={"name": "stable"})

    src = tmp_path / "src"
    src.mkdir()
    monkeypatch.chdir(src)
    runner.invoke(app, ["init"], catch_exceptions=False)
    _init_and_commit(
        src, "x.jsonl",
        [{"messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]}],
        "init", monkeypatch,
    )
    set_remote(src / ".dit", "origin", "http://testserver/stable", token="dit_admin")
    monkeypatch.chdir(src)
    runner.invoke(app, ["push"], catch_exceptions=False)

    clone_dir = tmp_path / "clone"
    runner.invoke(
        app,
        ["clone", "http://testserver/stable", str(clone_dir), "--token", "dit_admin"],
        catch_exceptions=False,
    )

    monkeypatch.chdir(clone_dir)
    r = runner.invoke(app, ["pull"], catch_exceptions=False)
    assert r.exit_code == 0
    assert "up to date" in r.output.lower()


def test_fetch_defaults_to_current_branch_after_checkout(server_app, tmp_path: Path, monkeypatch) -> None:
    _patch_remote_client(monkeypatch, server_app)

    from starlette.testclient import TestClient
    sync_client = TestClient(server_app, headers={"Authorization": "Bearer dit_admin"})
    sync_client.post("/api/v1/repos", json={"name": "fetch-branches"})

    src = tmp_path / "src"
    src.mkdir()
    monkeypatch.chdir(src)
    runner.invoke(app, ["init"], catch_exceptions=False)
    _init_and_commit(
        src,
        "data.jsonl",
        [{"messages": [{"role": "user", "content": "main"}, {"role": "assistant", "content": "main-v1"}]}],
        "main v1",
        monkeypatch,
    )
    set_remote(src / ".dit", "origin", "http://testserver/fetch-branches", token="dit_admin")
    monkeypatch.chdir(src)
    r = runner.invoke(app, ["push"], catch_exceptions=False)
    assert r.exit_code == 0, r.output

    r = runner.invoke(app, ["checkout", "-b", "feature"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    _init_and_commit(
        src,
        "data.jsonl",
        [{"messages": [{"role": "user", "content": "feature"}, {"role": "assistant", "content": "feature-v1"}]}],
        "feature v1",
        monkeypatch,
    )
    r = runner.invoke(app, ["push", "--branch", "feature"], catch_exceptions=False)
    assert r.exit_code == 0, r.output

    clone_dir = tmp_path / "clone_feature"
    r = runner.invoke(
        app,
        ["clone", "http://testserver/fetch-branches", str(clone_dir), "--token", "dit_admin", "--branch", "feature"],
        catch_exceptions=False,
    )
    assert r.exit_code == 0, r.output

    _init_and_commit(
        src,
        "data.jsonl",
        [{"messages": [{"role": "user", "content": "feature"}, {"role": "assistant", "content": "feature-v2"}]}],
        "feature v2",
        monkeypatch,
    )
    r = runner.invoke(app, ["push", "--branch", "feature"], catch_exceptions=False)
    assert r.exit_code == 0, r.output

    monkeypatch.chdir(clone_dir)
    result = runner.invoke(app, ["fetch"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert "origin/feature" in result.output
    assert "origin/main" not in result.output


def test_pull_defaults_to_current_branch_after_checkout(server_app, tmp_path: Path, monkeypatch) -> None:
    _patch_remote_client(monkeypatch, server_app)

    from starlette.testclient import TestClient
    sync_client = TestClient(server_app, headers={"Authorization": "Bearer dit_admin"})
    sync_client.post("/api/v1/repos", json={"name": "branches"})

    src = tmp_path / "src"
    src.mkdir()
    monkeypatch.chdir(src)
    runner.invoke(app, ["init"], catch_exceptions=False)
    _init_and_commit(
        src,
        "data.jsonl",
        [{"messages": [{"role": "user", "content": "main"}, {"role": "assistant", "content": "main-v1"}]}],
        "main v1",
        monkeypatch,
    )
    set_remote(src / ".dit", "origin", "http://testserver/branches", token="dit_admin")
    monkeypatch.chdir(src)
    r = runner.invoke(app, ["push"], catch_exceptions=False)
    assert r.exit_code == 0, r.output

    r = runner.invoke(app, ["checkout", "-b", "feature"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    _init_and_commit(
        src,
        "data.jsonl",
        [{"messages": [{"role": "user", "content": "feature"}, {"role": "assistant", "content": "feature-v1"}]}],
        "feature v1",
        monkeypatch,
    )
    r = runner.invoke(app, ["push", "--branch", "feature"], catch_exceptions=False)
    assert r.exit_code == 0, r.output

    clone_dir = tmp_path / "clone_feature"
    r = runner.invoke(
        app,
        ["clone", "http://testserver/branches", str(clone_dir), "--token", "dit_admin", "--branch", "feature"],
        catch_exceptions=False,
    )
    assert r.exit_code == 0, r.output
    assert "feature-v1" in (clone_dir / "data.jsonl").read_text()

    _init_and_commit(
        src,
        "data.jsonl",
        [{"messages": [{"role": "user", "content": "feature"}, {"role": "assistant", "content": "feature-v2"}]}],
        "feature v2",
        monkeypatch,
    )
    r = runner.invoke(app, ["push", "--branch", "feature"], catch_exceptions=False)
    assert r.exit_code == 0, r.output

    monkeypatch.chdir(clone_dir)
    result = runner.invoke(app, ["pull"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert (clone_dir / ".dit" / "HEAD").read_text().strip() == "ref:feature"
    assert "feature-v2" in (clone_dir / "data.jsonl").read_text()
    assert "main-v1" not in (clone_dir / "data.jsonl").read_text()
