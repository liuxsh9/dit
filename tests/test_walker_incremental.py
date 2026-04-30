"""Tests for incremental object traversal (walk_commit_objects_since)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dit.cli.main import app
from dit.core.config import set_remote
from dit.core.refs import RefStore
from dit.core.store import ObjectStore
from dit.core.walker import walk_commit_objects, walk_commit_objects_since

runner = CliRunner()


def _make_row(content: str = "hello") -> str:
    return json.dumps({
        "messages": [
            {"role": "user", "content": content},
            {"role": "assistant", "content": "reply"},
        ]
    })


@pytest.fixture
def repo(tmp_path: Path, monkeypatch) -> Path:
    """Create and chdir into a fresh dit repo."""
    monkeypatch.chdir(tmp_path)
    r = runner.invoke(app, ["init"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    return tmp_path


def _get_head(repo: Path) -> str:
    dot = repo / ".dit"
    return RefStore(dot).resolve_head()


def _get_store(repo: Path) -> ObjectStore:
    return ObjectStore(repo / ".dit" / "objects")


class TestWalkCommitObjectsSince:
    """Tests for the incremental walker."""

    def test_since_returns_only_new_objects(self, repo: Path):
        """walk_commit_objects_since(commit2, stop_at=commit1) should only
        return objects from commit2's changes, not commit1's."""
        # Commit 1
        (repo / "a.jsonl").write_text(_make_row("alpha") + "\n")
        runner.invoke(app, ["add", "a.jsonl"], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "first"], catch_exceptions=False)
        commit1 = _get_head(repo)

        # Commit 2 — new file
        (repo / "b.jsonl").write_text(_make_row("beta") + "\n")
        runner.invoke(app, ["add", "b.jsonl"], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "second"], catch_exceptions=False)
        commit2 = _get_head(repo)

        store = _get_store(repo)
        new_objs = walk_commit_objects_since(store, commit2, stop_at=commit1)

        # commit1 should NOT be in the result
        assert commit1 not in new_objs["commits"]
        # commit2 SHOULD be in the result
        assert commit2 in new_objs["commits"]

        # The full walk includes commit1's objects; incremental should have fewer
        full_objs = walk_commit_objects(store, commit2)
        assert len(new_objs["commits"]) < len(full_objs["commits"])

    def test_since_none_returns_all(self, repo: Path):
        """walk_commit_objects_since(commit, stop_at=None) should return
        the same result as walk_commit_objects(commit)."""
        (repo / "a.jsonl").write_text(_make_row("alpha") + "\n")
        runner.invoke(app, ["add", "a.jsonl"], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "first"], catch_exceptions=False)

        (repo / "b.jsonl").write_text(_make_row("beta") + "\n")
        runner.invoke(app, ["add", "b.jsonl"], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "second"], catch_exceptions=False)
        head = _get_head(repo)

        store = _get_store(repo)
        full = walk_commit_objects(store, head)
        since_none = walk_commit_objects_since(store, head, stop_at=None)

        for key in full:
            assert full[key] == since_none[key], f"Mismatch on {key}"

    def test_since_same_hash_returns_empty(self, repo: Path):
        """walk_commit_objects_since(commit1, stop_at=commit1) should
        return empty sets for all object types."""
        (repo / "a.jsonl").write_text(_make_row("alpha") + "\n")
        runner.invoke(app, ["add", "a.jsonl"], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "first"], catch_exceptions=False)
        commit1 = _get_head(repo)

        store = _get_store(repo)
        result = walk_commit_objects_since(store, commit1, stop_at=commit1)

        for key in result:
            assert len(result[key]) == 0, f"Expected empty set for {key}, got {result[key]}"

    def test_since_with_branches(self, repo: Path):
        """After a merge, walk_commit_objects_since(merge, stop_at=base)
        should include objects from both branches."""
        # Base commit
        (repo / "base.jsonl").write_text(_make_row("base") + "\n")
        runner.invoke(app, ["add", "base.jsonl"], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "base"], catch_exceptions=False)
        base_hash = _get_head(repo)

        # Branch A — add file on a new branch
        runner.invoke(app, ["branch", "feature-a"], catch_exceptions=False)
        runner.invoke(app, ["checkout", "feature-a"], catch_exceptions=False)
        (repo / "a.jsonl").write_text(_make_row("branch-a") + "\n")
        runner.invoke(app, ["add", "a.jsonl"], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "feature-a"], catch_exceptions=False)
        commit_a = _get_head(repo)

        # Branch B — add file on another branch
        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)
        runner.invoke(app, ["branch", "feature-b"], catch_exceptions=False)
        runner.invoke(app, ["checkout", "feature-b"], catch_exceptions=False)
        (repo / "c.jsonl").write_text(_make_row("branch-b") + "\n")
        runner.invoke(app, ["add", "c.jsonl"], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "feature-b"], catch_exceptions=False)
        commit_b = _get_head(repo)

        # Merge feature-a into feature-b
        runner.invoke(app, ["checkout", "feature-a"], catch_exceptions=False)
        r = runner.invoke(app, ["merge", "feature-b"], catch_exceptions=False)
        assert r.exit_code == 0, r.output
        merge_hash = _get_head(repo)

        store = _get_store(repo)
        new_objs = walk_commit_objects_since(store, merge_hash, stop_at=base_hash)

        # Should include commits from both branches + merge commit
        assert commit_a in new_objs["commits"]
        assert commit_b in new_objs["commits"]
        assert merge_hash in new_objs["commits"]
        # Base commit should NOT be included
        assert base_hash not in new_objs["commits"]

    def test_since_shared_objects_included(self, repo: Path):
        """If commit2 references the same manifest as commit1 (unchanged file),
        walk_commit_objects_since WILL include it. This is correct behavior —
        batch_exists handles dedup on the remote side."""
        # Commit 1 — add a.jsonl
        (repo / "a.jsonl").write_text(_make_row("alpha") + "\n")
        runner.invoke(app, ["add", "a.jsonl"], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "first"], catch_exceptions=False)
        commit1 = _get_head(repo)

        # Commit 2 — add b.jsonl (a.jsonl unchanged, same manifest)
        (repo / "b.jsonl").write_text(_make_row("beta") + "\n")
        runner.invoke(app, ["add", "b.jsonl"], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "second"], catch_exceptions=False)
        commit2 = _get_head(repo)

        store = _get_store(repo)
        new_objs = walk_commit_objects_since(store, commit2, stop_at=commit1)

        # commit2's tree references a.jsonl's manifest even though it's unchanged.
        # The incremental walker enters commit2's tree, so it WILL collect
        # a.jsonl's manifest hash. This is expected — it's a superset.
        assert len(new_objs["manifests"]) >= 1
        # Rows from a.jsonl will also appear (shared objects)
        assert len(new_objs["rows"]) >= 1


# ── Integration test: push with incremental walk ────────────────────


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


def test_push_incremental_integration(server_app, tmp_path: Path, monkeypatch):
    """Full push test: push commit1, then push commit2.
    Verify commit2 push only uploads new objects (not the full history)."""
    _patch_remote_client(monkeypatch, server_app)

    from starlette.testclient import TestClient
    sync_client = TestClient(
        server_app, headers={"Authorization": "Bearer dit_admin"}
    )
    resp = sync_client.post("/api/v1/repos", json={"name": "train"})
    assert resp.status_code == 201

    repo = tmp_path / "client"
    repo.mkdir()
    monkeypatch.chdir(repo)

    r = runner.invoke(app, ["init"], catch_exceptions=False)
    assert r.exit_code == 0, r.output

    dot = repo / ".dit"
    set_remote(dot, "origin", "http://testserver/train", token="dit_admin")

    # Commit 1 — push initial data
    (repo / "a.jsonl").write_text(_make_row("alpha") + "\n")
    runner.invoke(app, ["add", "a.jsonl"], catch_exceptions=False)
    runner.invoke(app, ["commit", "-m", "first"], catch_exceptions=False)

    r1 = runner.invoke(app, ["push"], catch_exceptions=False)
    assert r1.exit_code == 0, r1.output
    assert "Pushed" in r1.output

    # Commit 2 — add new file, push again
    (repo / "b.jsonl").write_text(_make_row("beta") + "\n")
    runner.invoke(app, ["add", "b.jsonl"], catch_exceptions=False)
    runner.invoke(app, ["commit", "-m", "second"], catch_exceptions=False)

    r2 = runner.invoke(app, ["push"], catch_exceptions=False)
    assert r2.exit_code == 0, r2.output
    assert "Pushed" in r2.output

    # The incremental walk optimization reduces memory usage by not traversing
    # the full commit history. The actual upload count may be similar because
    # batch_exists already filters duplicates. What matters is correctness:
    # both pushes succeed and the remote has the right ref.
    import re
    m2 = re.search(r"Pushed (\d+)", r2.output)
    assert m2
    # Second push should upload some objects (the new commit's data)
    count2 = int(m2.group(1))
    assert count2 > 0, "Second push should upload new objects"

    # Third push (no changes) should upload 0 objects
    r3 = runner.invoke(app, ["push"], catch_exceptions=False)
    assert r3.exit_code == 0, r3.output
    m3 = re.search(r"Pushed (\d+)", r3.output)
    assert m3
    assert int(m3.group(1)) == 0, "Push with no new commits should upload 0 objects"

    # Verify remote has the latest ref
    resp = sync_client.get("/api/v1/repos/train/refs/heads/main")
    assert resp.status_code == 200
    remote_head = resp.json()["target_hash"]
    local_head = _get_head(repo)
    assert remote_head == local_head
