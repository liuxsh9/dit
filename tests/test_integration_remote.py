"""Full remote collaboration integration test.

Scenario:
  1. Start test server (starlette TestClient + in-memory SQLite)
  2. Create repo on server
  3. Client A: init local, add JSONL, commit, push
  4. Client B: clone from server, verify data matches
  5. Client B: modify data, add, commit, push
  6. Client A: pull, verify updated data
  7. Verify both clients have identical working directory content
"""
from __future__ import annotations

import asyncio
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


def _patch_remote_client(monkeypatch, server_app) -> None:
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


def _chdir_invoke(monkeypatch, repo: Path, cmd: list[str]) -> None:
    monkeypatch.chdir(repo)
    r = runner.invoke(app, cmd, catch_exceptions=False)
    assert r.exit_code == 0, f"Command {cmd} failed:\n{r.output}"


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with open(path, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


V1_ROWS = [
    {"messages": [{"role": "user", "content": "What is 2+2?"}, {"role": "assistant", "content": "4"}]},
    {"messages": [{"role": "user", "content": "Name a color."}, {"role": "assistant", "content": "Blue"}]},
]

V2_ROWS = V1_ROWS + [
    {"messages": [{"role": "user", "content": "Capital of France?"}, {"role": "assistant", "content": "Paris"}]},
]


def test_full_remote_collaboration_workflow(
    server_app, tmp_path: Path, monkeypatch
) -> None:
    """Complete push/clone/push/pull round-trip between two clients."""
    _patch_remote_client(monkeypatch, server_app)

    SERVER_REPO_URL = "http://testserver/sft-data"
    TOKEN = "dit_admin"

    # Step 1: Create repo on server
    from starlette.testclient import TestClient
    sync_client = TestClient(server_app, headers={"Authorization": f"Bearer {TOKEN}"})
    resp = sync_client.post("/api/v1/repos", json={"name": "sft-data"})
    assert resp.status_code == 201

    # Step 2: Client A — init + add + commit v1 + push
    client_a = tmp_path / "client_a"
    client_a.mkdir()
    _chdir_invoke(monkeypatch, client_a, ["init"])

    _write_jsonl(client_a / "train.jsonl", V1_ROWS)
    _chdir_invoke(monkeypatch, client_a, ["add", "train.jsonl"])
    _chdir_invoke(monkeypatch, client_a, ["commit", "-m", "v1: initial training data"])

    set_remote(client_a / ".dit", "origin", SERVER_REPO_URL, token=TOKEN)
    _chdir_invoke(monkeypatch, client_a, ["push"])

    # Step 3: Client B — clone + verify v1 data
    client_b = tmp_path / "client_b"
    r = runner.invoke(
        app,
        ["clone", SERVER_REPO_URL, str(client_b), "--token", TOKEN],
        catch_exceptions=False,
    )
    assert r.exit_code == 0, r.output

    b_rows_v1 = _read_jsonl(client_b / "train.jsonl")
    assert len(b_rows_v1) == 2
    assert b_rows_v1[0]["messages"][0]["content"] == "What is 2+2?"
    assert b_rows_v1[1]["messages"][0]["content"] == "Name a color."

    # Step 4: Client B — add row, commit v2, push
    _write_jsonl(client_b / "train.jsonl", V2_ROWS)
    _chdir_invoke(monkeypatch, client_b, ["add", "train.jsonl"])
    _chdir_invoke(monkeypatch, client_b, ["commit", "-m", "v2: add geography question"])
    _chdir_invoke(monkeypatch, client_b, ["push"])

    # Step 5: Client A — pull + verify v2
    _chdir_invoke(monkeypatch, client_a, ["pull"])

    a_rows_v2 = _read_jsonl(client_a / "train.jsonl")
    assert len(a_rows_v2) == 3
    assert a_rows_v2[2]["messages"][0]["content"] == "Capital of France?"

    # Step 6: Verify both clients have identical JSONL content
    b_rows_v2 = _read_jsonl(client_b / "train.jsonl")
    assert a_rows_v2 == b_rows_v2

    # Step 7: Verify server ref points to latest commit
    resp = sync_client.get("/api/v1/repos/sft-data/refs/heads/main")
    assert resp.status_code == 200
    final_hash = resp.json()["target_hash"]
    assert len(final_hash) == 64


def test_diverged_push_rejected(server_app, tmp_path: Path, monkeypatch) -> None:
    """Two clients both push from same base — second push should be rejected."""
    _patch_remote_client(monkeypatch, server_app)

    TOKEN = "dit_admin"
    from starlette.testclient import TestClient
    sync_client = TestClient(server_app, headers={"Authorization": f"Bearer {TOKEN}"})
    sync_client.post("/api/v1/repos", json={"name": "conflict-repo"})

    # Client A — push v1
    client_a = tmp_path / "a"
    client_a.mkdir()
    _chdir_invoke(monkeypatch, client_a, ["init"])
    _write_jsonl(client_a / "data.jsonl", V1_ROWS)
    _chdir_invoke(monkeypatch, client_a, ["add", "data.jsonl"])
    _chdir_invoke(monkeypatch, client_a, ["commit", "-m", "v1"])
    set_remote(client_a / ".dit", "origin", "http://testserver/conflict-repo", token=TOKEN)
    _chdir_invoke(monkeypatch, client_a, ["push"])

    # Client B — clone
    client_b = tmp_path / "b"
    runner.invoke(app, ["clone", "http://testserver/conflict-repo", str(client_b), "--token", TOKEN], catch_exceptions=False)

    # Client A adds v2 and pushes (advances remote)
    _write_jsonl(client_a / "data.jsonl", V2_ROWS)
    _chdir_invoke(monkeypatch, client_a, ["add", "data.jsonl"])
    _chdir_invoke(monkeypatch, client_a, ["commit", "-m", "v2-a"])
    _chdir_invoke(monkeypatch, client_a, ["push"])

    # Client B adds independent commit (diverged)
    _write_jsonl(
        client_b / "data.jsonl",
        V1_ROWS + [{"messages": [{"role": "user", "content": "diverged"}, {"role": "assistant", "content": "yes"}]}],
    )
    _chdir_invoke(monkeypatch, client_b, ["add", "data.jsonl"])
    _chdir_invoke(monkeypatch, client_b, ["commit", "-m", "diverged"])

    # Client B push should fail
    monkeypatch.chdir(client_b)
    r = runner.invoke(app, ["push"], catch_exceptions=False)
    assert r.exit_code != 0
    assert "descendant" in r.output or "rejected" in r.output or "not a descendant" in r.output
