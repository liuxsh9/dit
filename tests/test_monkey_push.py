"""Monkey / stress tests for incremental push (walk_commit_objects_since)."""
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
                server_app, base_url=base_url,
                headers={"Authorization": f"token {token}"},
            )

        def _dit_prefix(self) -> str:
            return f"{self.base_url}/api/v1/repos/{self.repo}"

    monkeypatch.setattr(remote_mod, "RemoteClient", PatchedRemoteClient)


def _create_repo(tmp_path, monkeypatch, name="client"):
    """Init a dit repo and return its path."""
    repo = tmp_path / name
    repo.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo)
    r = runner.invoke(app, ["init"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    return repo


def _make_row(content: str) -> str:
    return json.dumps({"messages": [
        {"role": "user", "content": content},
        {"role": "assistant", "content": f"reply to {content}"},
    ]})


def _write_jsonl(repo: Path, rel_path: str, rows: list[str]):
    fp = repo / rel_path
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text("\n".join(rows) + "\n")


def _add_commit(repo: Path, monkeypatch, files: dict[str, list[str]], msg: str,
                deletes: list[str] | None = None):
    """Write files, stage, commit. files = {rel_path: [rows]}."""
    monkeypatch.chdir(repo)
    for rel, rows in files.items():
        _write_jsonl(repo, rel, rows)
    paths_to_add = list(files.keys())
    if deletes:
        for d in deletes:
            fp = repo / d
            if fp.exists():
                fp.unlink()
        paths_to_add.extend(deletes)
    if paths_to_add:
        r = runner.invoke(app, ["add"] + paths_to_add, catch_exceptions=False)
        assert r.exit_code == 0, r.output
    r = runner.invoke(app, ["commit", "-m", msg], catch_exceptions=False)
    assert r.exit_code == 0, r.output


def _setup_remote(repo, monkeypatch, server_app, repo_name="train"):
    """Create remote repo and configure local remote."""
    _patch_remote_client(monkeypatch, server_app)
    from starlette.testclient import TestClient
    sc = TestClient(server_app, headers={"Authorization": "Bearer dit_admin"})
    resp = sc.post("/api/v1/repos", json={"name": repo_name})
    assert resp.status_code in (201, 409), resp.text
    dot = repo / ".dit"
    set_remote(dot, "origin", f"http://testserver/{repo_name}", token="dit_admin")


def _push(repo, monkeypatch):
    monkeypatch.chdir(repo)
    r = runner.invoke(app, ["push"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    return r


def _clone_to(tmp_path, monkeypatch, server_app, dest_name="clone",
              repo_name="train"):
    _patch_remote_client(monkeypatch, server_app)
    dest = tmp_path / dest_name
    monkeypatch.chdir(tmp_path)
    r = runner.invoke(
        app,
        ["clone", f"http://testserver/{repo_name}", str(dest),
         "--token", "dit_admin"],
        catch_exceptions=False,
    )
    assert r.exit_code == 0, r.output
    return dest


def _log_json(repo, monkeypatch) -> list[dict]:
    monkeypatch.chdir(repo)
    r = runner.invoke(app, ["log", "--format", "json"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    return json.loads(r.output)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSequentialPushes:
    """10 sequential pushes, each with 1 file modification."""

    def test_ten_sequential_pushes(self, server_app, tmp_path, monkeypatch):
        repo = _create_repo(tmp_path, monkeypatch)
        _add_commit(repo, monkeypatch,
                    {"train.jsonl": [_make_row("v0")]}, "commit-0")
        _setup_remote(repo, monkeypatch, server_app)
        _push(repo, monkeypatch)

        for i in range(1, 10):
            _add_commit(repo, monkeypatch,
                        {"train.jsonl": [_make_row(f"v{i}")]}, f"commit-{i}")
            monkeypatch.chdir(repo)
            _push(repo, monkeypatch)

        clone = _clone_to(tmp_path, monkeypatch, server_app)
        commits = _log_json(clone, monkeypatch)
        assert len(commits) == 10


class TestBranchAndMerge:
    """Push after branch + merge."""

    def test_push_after_merge(self, server_app, tmp_path, monkeypatch):
        repo = _create_repo(tmp_path, monkeypatch)
        _add_commit(repo, monkeypatch,
                    {"base.jsonl": [_make_row("base")]}, "initial")
        _setup_remote(repo, monkeypatch, server_app)

        # Create branch, switch, commit on branch
        monkeypatch.chdir(repo)
        runner.invoke(app, ["branch", "feat"], catch_exceptions=False)
        runner.invoke(app, ["switch", "feat"], catch_exceptions=False)
        _add_commit(repo, monkeypatch,
                    {"feat.jsonl": [_make_row("feat")]}, "feat-commit")

        # Switch back to main, commit on main
        monkeypatch.chdir(repo)
        runner.invoke(app, ["switch", "main"], catch_exceptions=False)
        _add_commit(repo, monkeypatch,
                    {"main.jsonl": [_make_row("main")]}, "main-commit")

        # Merge feat into main
        monkeypatch.chdir(repo)
        r = runner.invoke(app, ["merge", "feat"], catch_exceptions=False)
        assert r.exit_code == 0, r.output

        _push(repo, monkeypatch)
        clone = _clone_to(tmp_path, monkeypatch, server_app)

        # Verify all three files present
        assert (clone / "base.jsonl").exists()
        assert (clone / "feat.jsonl").exists()
        assert (clone / "main.jsonl").exists()


class TestAddDeleteInterleaved:
    """Interleaved additions and deletions pushed at once."""

    def test_add_delete_interleaved(self, server_app, tmp_path, monkeypatch):
        repo = _create_repo(tmp_path, monkeypatch)

        # commit1: add A, B, C
        _add_commit(repo, monkeypatch, {
            "A.jsonl": [_make_row("a")],
            "B.jsonl": [_make_row("b")],
            "C.jsonl": [_make_row("c")],
        }, "add A,B,C")

        # commit2: delete B
        _add_commit(repo, monkeypatch, {}, "delete B", deletes=["B.jsonl"])

        # commit3: add D, E
        _add_commit(repo, monkeypatch, {
            "D.jsonl": [_make_row("d")],
            "E.jsonl": [_make_row("e")],
        }, "add D,E")

        # commit4: delete A, C
        _add_commit(repo, monkeypatch, {}, "delete A,C",
                    deletes=["A.jsonl", "C.jsonl"])

        _setup_remote(repo, monkeypatch, server_app)
        _push(repo, monkeypatch)

        clone = _clone_to(tmp_path, monkeypatch, server_app)
        assert (clone / "D.jsonl").exists()
        assert (clone / "E.jsonl").exists()
        assert not (clone / "A.jsonl").exists()
        assert not (clone / "B.jsonl").exists()
        assert not (clone / "C.jsonl").exists()

        commits = _log_json(clone, monkeypatch)
        assert len(commits) == 4


class TestPushIdempotency:
    """Second push with no changes should upload 0 objects."""

    def test_push_idempotent(self, server_app, tmp_path, monkeypatch):
        repo = _create_repo(tmp_path, monkeypatch)
        _add_commit(repo, monkeypatch,
                    {"data.jsonl": [_make_row("x")]}, "init")
        _setup_remote(repo, monkeypatch, server_app)
        _push(repo, monkeypatch)

        r2 = _push(repo, monkeypatch)
        assert "0 new objects" in r2.output or "Pushed 0" in r2.output


class TestNestedDirectories:
    """5 levels deep, 3 files at each level."""

    def test_nested_dirs(self, server_app, tmp_path, monkeypatch):
        repo = _create_repo(tmp_path, monkeypatch)
        files: dict[str, list[str]] = {}
        for depth in range(5):
            prefix = "/".join(f"d{d}" for d in range(depth + 1))
            for fi in range(3):
                rel = f"{prefix}/f{fi}.jsonl" if depth > 0 else f"f{fi}.jsonl"
                # depth 0 is root level
                if depth == 0:
                    rel = f"d0/f{fi}.jsonl"
                else:
                    rel = "/".join(f"d{d}" for d in range(depth + 1)) + f"/f{fi}.jsonl"
                files[rel] = [_make_row(f"depth{depth}-file{fi}")]

        _add_commit(repo, monkeypatch, files, "nested files")
        _setup_remote(repo, monkeypatch, server_app)
        _push(repo, monkeypatch)

        clone = _clone_to(tmp_path, monkeypatch, server_app)
        for rel in files:
            assert (clone / rel).exists(), f"Missing {rel}"


class TestCherryPick:
    """Cherry-pick middle commit from branch to main, push main."""

    def test_push_after_cherry_pick(self, server_app, tmp_path, monkeypatch):
        repo = _create_repo(tmp_path, monkeypatch)
        _add_commit(repo, monkeypatch,
                    {"base.jsonl": [_make_row("base")]}, "initial")

        # Create branch with 3 commits
        monkeypatch.chdir(repo)
        runner.invoke(app, ["branch", "feat"], catch_exceptions=False)
        runner.invoke(app, ["switch", "feat"], catch_exceptions=False)

        _add_commit(repo, monkeypatch,
                    {"f1.jsonl": [_make_row("feat1")]}, "feat-1")

        # Get hash of this commit (the middle one we'll cherry-pick)
        commits_on_feat = _log_json(repo, monkeypatch)
        middle_hash = commits_on_feat[0]["hash"]  # most recent = feat-1

        _add_commit(repo, monkeypatch,
                    {"f2.jsonl": [_make_row("feat2")]}, "feat-2")
        _add_commit(repo, monkeypatch,
                    {"f3.jsonl": [_make_row("feat3")]}, "feat-3")

        # Switch to main, cherry-pick the middle commit
        monkeypatch.chdir(repo)
        runner.invoke(app, ["switch", "main"], catch_exceptions=False)
        r = runner.invoke(app, ["cherry-pick", middle_hash],
                          catch_exceptions=False)
        assert r.exit_code == 0, r.output

        _setup_remote(repo, monkeypatch, server_app)
        _push(repo, monkeypatch)

        clone = _clone_to(tmp_path, monkeypatch, server_app)
        assert (clone / "base.jsonl").exists()
        assert (clone / "f1.jsonl").exists()
        assert not (clone / "f2.jsonl").exists()
        assert not (clone / "f3.jsonl").exists()


class TestLargeCommit:
    """100 files in one commit."""

    def test_push_100_files(self, server_app, tmp_path, monkeypatch):
        repo = _create_repo(tmp_path, monkeypatch)
        files = {
            f"file_{i:03d}.jsonl": [_make_row(f"content-{i}")]
            for i in range(100)
        }
        _add_commit(repo, monkeypatch, files, "100 files")
        _setup_remote(repo, monkeypatch, server_app)
        _push(repo, monkeypatch)

        clone = _clone_to(tmp_path, monkeypatch, server_app)
        for rel in files:
            assert (clone / rel).exists(), f"Missing {rel}"


class TestTwoClientPush:
    """Client A pushes, client B clones and pushes, verify both commits."""

    def test_two_client_sequence(self, server_app, tmp_path, monkeypatch):
        # Client A
        repo_a = _create_repo(tmp_path, monkeypatch, name="clientA")
        _add_commit(repo_a, monkeypatch,
                    {"a.jsonl": [_make_row("from-A")]}, "commit-A")
        _setup_remote(repo_a, monkeypatch, server_app)
        _push(repo_a, monkeypatch)

        # Client B clones
        clone_b = _clone_to(tmp_path, monkeypatch, server_app,
                            dest_name="clientB")
        _add_commit(clone_b, monkeypatch,
                    {"b.jsonl": [_make_row("from-B")]}, "commit-B")
        _push(clone_b, monkeypatch)

        # Fresh clone should see both
        final = _clone_to(tmp_path, monkeypatch, server_app,
                          dest_name="final")
        assert (final / "a.jsonl").exists()
        assert (final / "b.jsonl").exists()
        commits = _log_json(final, monkeypatch)
        assert len(commits) == 2


class TestRowDataIntegrity:
    """50 rows with unicode, long strings, nested JSON survive push+clone."""

    def test_row_integrity(self, server_app, tmp_path, monkeypatch):
        repo = _create_repo(tmp_path, monkeypatch)

        rows = []
        for i in range(50):
            if i % 4 == 0:
                content = f"unicode: \u4f60\u597d\u4e16\u754c \U0001f600 row-{i}"
            elif i % 4 == 1:
                content = "long " * 200 + f"row-{i}"
            elif i % 4 == 2:
                content = json.dumps({"nested": {"key": i, "list": [1, 2, 3]}})
            else:
                content = f"simple row-{i}"
            rows.append(_make_row(content))

        _write_jsonl(repo, "data.jsonl", rows)
        monkeypatch.chdir(repo)
        r = runner.invoke(app, ["add", "data.jsonl"], catch_exceptions=False)
        assert r.exit_code == 0, r.output
        r = runner.invoke(app, ["commit", "-m", "50 rows"],
                          catch_exceptions=False)
        assert r.exit_code == 0, r.output

        _setup_remote(repo, monkeypatch, server_app)
        _push(repo, monkeypatch)

        clone = _clone_to(tmp_path, monkeypatch, server_app)
        # dit canonicalizes JSON, so compare parsed rows not raw bytes
        orig_lines = (repo / "data.jsonl").read_text().strip().splitlines()
        clone_lines = (clone / "data.jsonl").read_text().strip().splitlines()
        assert len(orig_lines) == len(clone_lines) == 50
        for i, (o, c) in enumerate(zip(orig_lines, clone_lines)):
            assert json.loads(o) == json.loads(c), f"Row {i} mismatch"
