"""Tests for pull and checkout sparse awareness."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dit.cli.main import app
from dit.core.config import set_remote
from dit.core.sparse import save_sparse_paths

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


def _push_from(server_app, src_dir: Path, monkeypatch) -> None:
    from starlette.testclient import TestClient
    sync_client = TestClient(server_app, headers={"Authorization": "Bearer dit_admin"})
    resp = sync_client.post("/api/v1/repos", json={"name": "dataset"})
    assert resp.status_code in (201, 409)
    dot = src_dir / ".dit"
    set_remote(dot, "origin", "http://testserver/dataset", token="dit_admin")
    monkeypatch.chdir(src_dir)
    result = runner.invoke(app, ["push"], catch_exceptions=False)
    assert result.exit_code == 0, result.output


class TestPullSparse:
    def test_pull_only_materializes_sparse_files(self, server_app, tmp_path, monkeypatch) -> None:
        # Create source repo with 2 files
        src = tmp_path / "src"
        src.mkdir()
        monkeypatch.chdir(src)
        runner.invoke(app, ["init"], catch_exceptions=False)

        (src / "a.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "a"}]}) + "\n"
        )
        (src / "b.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "b"}]}) + "\n"
        )
        runner.invoke(app, ["add", "a.jsonl"], catch_exceptions=False)
        runner.invoke(app, ["add", "b.jsonl"], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "v1"], catch_exceptions=False)

        _patch_remote_client(monkeypatch, server_app)
        _push_from(server_app, src, monkeypatch)

        # Sparse clone, fetch only a.jsonl
        clone_dir = tmp_path / "clone"
        runner.invoke(
            app,
            ["clone", "--sparse", "http://testserver/dataset", str(clone_dir), "--token", "dit_admin"],
            catch_exceptions=False,
        )
        monkeypatch.chdir(clone_dir)
        runner.invoke(app, ["sparse-checkout", "add", "a.jsonl"], catch_exceptions=False)
        assert (clone_dir / "a.jsonl").exists()
        assert not (clone_dir / "b.jsonl").exists()

        # Push an update from source
        monkeypatch.chdir(src)
        (src / "a.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "a-updated"}]}) + "\n"
        )
        runner.invoke(app, ["add", "a.jsonl"], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "v2"], catch_exceptions=False)
        runner.invoke(app, ["push"], catch_exceptions=False)

        # Pull in sparse clone
        monkeypatch.chdir(clone_dir)
        result = runner.invoke(app, ["pull"], catch_exceptions=False)
        assert result.exit_code == 0, result.output

        # a.jsonl should be updated
        rows = [json.loads(line) for line in (clone_dir / "a.jsonl").read_text().splitlines() if line.strip()]
        assert rows[0]["messages"][0]["content"] == "a-updated"
        # b.jsonl should still NOT be materialized
        assert not (clone_dir / "b.jsonl").exists()


class TestCheckoutSparse:
    def test_checkout_respects_sparse(self, tmp_path, monkeypatch) -> None:
        """Branch switch in sparse mode should only materialize sparse set files."""
        repo = tmp_path / "repo"
        repo.mkdir()
        monkeypatch.chdir(repo)
        runner.invoke(app, ["init"], catch_exceptions=False)

        (repo / "a.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "a"}]}) + "\n"
        )
        (repo / "b.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "b"}]}) + "\n"
        )
        runner.invoke(app, ["add", "a.jsonl"], catch_exceptions=False)
        runner.invoke(app, ["add", "b.jsonl"], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "main commit"], catch_exceptions=False)

        # Create a branch with different content
        runner.invoke(app, ["branch", "feature"], catch_exceptions=False)
        runner.invoke(app, ["checkout", "feature"], catch_exceptions=False)
        (repo / "a.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "a-feature"}]}) + "\n"
        )
        runner.invoke(app, ["add", "a.jsonl"], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "feature commit"], catch_exceptions=False)

        # Switch back to main, enable sparse with only a.jsonl
        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)
        dot = repo / ".dit"
        save_sparse_paths(dot, {"a.jsonl"})
        # Remove b.jsonl to simulate sparse state
        (repo / "b.jsonl").unlink()

        # Checkout feature branch
        runner.invoke(app, ["checkout", "feature"], catch_exceptions=False)
        # a.jsonl should be updated (it's in sparse set)
        rows = [json.loads(line) for line in (repo / "a.jsonl").read_text().splitlines() if line.strip()]
        assert rows[0]["messages"][0]["content"] == "a-feature"
        # b.jsonl should NOT be materialized
        assert not (repo / "b.jsonl").exists()
