"""Robustness tests for dit push/pull/clone workflows.

Stress-tests edge cases that could cause data loss or corruption.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dit.cli.main import app
from dit.core.config import set_remote

runner = CliRunner()

# ---------------------------------------------------------------------------
# Fixtures & helpers (copied from test_cli_push.py)
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


def _make_row(content: str) -> dict:
    """Build a minimal valid JSONL row."""
    return {"messages": [
        {"role": "user", "content": content},
        {"role": "assistant", "content": f"reply-{content}"},
    ]}


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _init_repo(repo: Path, monkeypatch) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo)
    r = runner.invoke(app, ["init"], catch_exceptions=False)
    assert r.exit_code == 0, r.output


def _add_commit(repo: Path, filename: str, rows: list[dict], message: str, monkeypatch) -> None:
    monkeypatch.chdir(repo)
    _write_jsonl(repo / filename, rows)
    r = runner.invoke(app, ["add", filename], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    r = runner.invoke(app, ["commit", "-m", message], catch_exceptions=False)
    assert r.exit_code == 0, r.output


def _create_remote_repo(server_app, repo_name: str) -> None:
    from starlette.testclient import TestClient
    sync = TestClient(server_app, headers={"Authorization": "Bearer dit_admin"})
    resp = sync.post("/api/v1/repos", json={"name": repo_name})
    assert resp.status_code == 201, resp.text


def _setup_remote(repo: Path, url: str) -> None:
    set_remote(repo / ".dit", "origin", url, token="dit_admin")


def _push(monkeypatch, repo: Path) -> str:
    monkeypatch.chdir(repo)
    r = runner.invoke(app, ["push"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    return r.output


def _clone(dest: str, url: str) -> str:
    r = runner.invoke(
        app,
        ["clone", url, dest, "--token", "dit_admin"],
        catch_exceptions=False,
    )
    assert r.exit_code == 0, r.output
    return r.output


# ---------------------------------------------------------------------------
# Test classes
# ---------------------------------------------------------------------------

class TestPushCloneRoundtrip:
    """Push data, clone it, verify integrity."""

    def test_push_then_clone_multi_file(self, server_app, tmp_path, monkeypatch):
        """Push a multi-file repo, clone it, verify every row matches."""
        _patch_remote_client(monkeypatch, server_app)
        _create_remote_repo(server_app, "multi")

        src = tmp_path / "src"
        _init_repo(src, monkeypatch)
        files = {
            "train.jsonl": [_make_row("t1"), _make_row("t2")],
            "eval.jsonl": [_make_row("e1")],
            "sub/nested.jsonl": [_make_row("n1"), _make_row("n2"), _make_row("n3")],
        }
        for fname, rows in files.items():
            _write_jsonl(src / fname, rows)
        monkeypatch.chdir(src)
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        r = runner.invoke(app, ["commit", "-m", "multi-file"], catch_exceptions=False)
        assert r.exit_code == 0, r.output
        _setup_remote(src, "http://testserver/multi")
        _push(monkeypatch, src)

        clone_dir = tmp_path / "clone"
        _clone(str(clone_dir), "http://testserver/multi")

        for fname, expected_rows in files.items():
            cloned = _read_jsonl(clone_dir / fname)
            assert len(cloned) == len(expected_rows), f"{fname}: row count mismatch"
            for orig, got in zip(expected_rows, cloned):
                assert orig == got, f"{fname}: row content mismatch"

    def test_clone_preserves_commit_history(self, server_app, tmp_path, monkeypatch):
        """Clone, then dit log should show all commits."""
        _patch_remote_client(monkeypatch, server_app)
        _create_remote_repo(server_app, "history")

        src = tmp_path / "src"
        _init_repo(src, monkeypatch)
        _add_commit(src, "a.jsonl", [_make_row("c1")], "first commit", monkeypatch)
        _add_commit(src, "a.jsonl", [_make_row("c1"), _make_row("c2")], "second commit", monkeypatch)
        _add_commit(src, "a.jsonl", [_make_row("c1"), _make_row("c2"), _make_row("c3")], "third commit", monkeypatch)
        _setup_remote(src, "http://testserver/history")
        _push(monkeypatch, src)

        clone_dir = tmp_path / "clone"
        _clone(str(clone_dir), "http://testserver/history")

        monkeypatch.chdir(clone_dir)
        r = runner.invoke(app, ["log", "--oneline"], catch_exceptions=False)
        assert r.exit_code == 0, r.output
        log_lines = [line for line in r.output.strip().splitlines() if line.strip()]
        assert len(log_lines) == 3, f"Expected 3 commits in log, got {len(log_lines)}: {r.output}"
        assert "third commit" in r.output
        assert "first commit" in r.output

    def test_large_payload_push(self, server_app, tmp_path, monkeypatch):
        """Push a repo with 20+ files across nested directories."""
        _patch_remote_client(monkeypatch, server_app)
        _create_remote_repo(server_app, "large")

        src = tmp_path / "src"
        _init_repo(src, monkeypatch)

        expected = {}
        for i in range(25):
            subdir = f"dir{i // 5}"
            fname = f"{subdir}/file{i}.jsonl"
            rows = [_make_row(f"row-{i}-{j}") for j in range(3)]
            _write_jsonl(src / fname, rows)
            expected[fname] = rows

        monkeypatch.chdir(src)
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        r = runner.invoke(app, ["commit", "-m", "large push"], catch_exceptions=False)
        assert r.exit_code == 0, r.output
        _setup_remote(src, "http://testserver/large")
        _push(monkeypatch, src)

        clone_dir = tmp_path / "clone"
        _clone(str(clone_dir), "http://testserver/large")

        for fname, rows in expected.items():
            cloned = _read_jsonl(clone_dir / fname)
            assert len(cloned) == len(rows), f"{fname}: expected {len(rows)} rows, got {len(cloned)}"


class TestPushPullModifications:
    """Push-pull roundtrips with modifications."""

    def test_push_pull_roundtrip_with_update(self, server_app, tmp_path, monkeypatch):
        """Push v1, clone, push v2 from original, pull in clone, verify."""
        _patch_remote_client(monkeypatch, server_app)
        _create_remote_repo(server_app, "roundtrip")

        src = tmp_path / "src"
        _init_repo(src, monkeypatch)
        _add_commit(src, "data.jsonl", [_make_row("v1")], "v1", monkeypatch)
        _setup_remote(src, "http://testserver/roundtrip")
        _push(monkeypatch, src)

        clone_dir = tmp_path / "clone"
        _clone(str(clone_dir), "http://testserver/roundtrip")
        assert _read_jsonl(clone_dir / "data.jsonl")[0] == _make_row("v1")

        # Push v2 from original
        _add_commit(src, "data.jsonl", [_make_row("v1"), _make_row("v2")], "v2", monkeypatch)
        _push(monkeypatch, src)

        # Pull in clone
        monkeypatch.chdir(clone_dir)
        r = runner.invoke(app, ["pull"], catch_exceptions=False)
        assert r.exit_code == 0, r.output
        assert "Pulled" in r.output

        rows = _read_jsonl(clone_dir / "data.jsonl")
        assert len(rows) == 2
        assert rows[1] == _make_row("v2")

    def test_push_after_deleting_file(self, server_app, tmp_path, monkeypatch):
        """Delete a tracked file, commit deletion, push — remote should reflect it."""
        _patch_remote_client(monkeypatch, server_app)
        _create_remote_repo(server_app, "delfile")

        src = tmp_path / "src"
        _init_repo(src, monkeypatch)
        _add_commit(src, "keep.jsonl", [_make_row("keep")], "add keep", monkeypatch)
        _add_commit(src, "remove.jsonl", [_make_row("gone")], "add remove", monkeypatch)
        _setup_remote(src, "http://testserver/delfile")
        _push(monkeypatch, src)

        # Delete the file and commit
        (src / "remove.jsonl").unlink()
        monkeypatch.chdir(src)
        r = runner.invoke(app, ["add", "remove.jsonl"], catch_exceptions=False)
        assert r.exit_code == 0, r.output
        r = runner.invoke(app, ["commit", "-m", "delete remove.jsonl"], catch_exceptions=False)
        assert r.exit_code == 0, r.output
        _push(monkeypatch, src)

        # Clone fresh — deleted file should not appear
        clone_dir = tmp_path / "clone"
        _clone(str(clone_dir), "http://testserver/delfile")
        assert (clone_dir / "keep.jsonl").exists()
        assert not (clone_dir / "remove.jsonl").exists(), "Deleted file should not appear in clone"

    def test_pull_after_remote_deletes_file(self, server_app, tmp_path, monkeypatch):
        """Remote deletes a file, local pulls — file should be removed locally."""
        _patch_remote_client(monkeypatch, server_app)
        _create_remote_repo(server_app, "rmdel")

        src = tmp_path / "src"
        _init_repo(src, monkeypatch)
        _add_commit(src, "stay.jsonl", [_make_row("stay")], "add stay", monkeypatch)
        _add_commit(src, "bye.jsonl", [_make_row("bye")], "add bye", monkeypatch)
        _setup_remote(src, "http://testserver/rmdel")
        _push(monkeypatch, src)

        # Clone
        clone_dir = tmp_path / "clone"
        _clone(str(clone_dir), "http://testserver/rmdel")
        assert (clone_dir / "bye.jsonl").exists()

        # Source deletes the file and pushes
        (src / "bye.jsonl").unlink()
        monkeypatch.chdir(src)
        runner.invoke(app, ["add", "bye.jsonl"], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "remove bye"], catch_exceptions=False)
        _push(monkeypatch, src)

        # Clone pulls
        monkeypatch.chdir(clone_dir)
        r = runner.invoke(app, ["pull"], catch_exceptions=False)
        assert r.exit_code == 0, r.output
        assert not (clone_dir / "bye.jsonl").exists(), "Deleted file should be removed after pull"
        assert (clone_dir / "stay.jsonl").exists()


class TestErrorHandling:
    """Edge cases that should fail gracefully."""

    def test_clone_empty_repo(self, server_app, tmp_path, monkeypatch):
        """Clone a repo with no commits — should handle gracefully."""
        _patch_remote_client(monkeypatch, server_app)
        _create_remote_repo(server_app, "empty")

        clone_dir = tmp_path / "clone"
        r = runner.invoke(
            app,
            ["clone", "http://testserver/empty", str(clone_dir), "--token", "dit_admin"],
        )
        # Should fail with a clear message about missing branch, not crash
        assert r.exit_code != 0 or "not found" in r.output.lower() or "fatal" in r.output.lower(), \
            f"Expected error for empty repo clone, got: {r.output}"

    def test_push_with_no_remote(self, tmp_path, monkeypatch):
        """Push with no remote configured should fail with clear error."""
        repo = tmp_path / "nopush"
        _init_repo(repo, monkeypatch)
        _add_commit(repo, "x.jsonl", [_make_row("x")], "init", monkeypatch)

        monkeypatch.chdir(repo)
        r = runner.invoke(app, ["push"])
        assert r.exit_code != 0
        assert "remote" in r.output.lower() or "fatal" in r.output.lower(), \
            f"Expected remote error, got: {r.output}"

    def test_pull_with_no_remote(self, tmp_path, monkeypatch):
        """Pull with no remote configured should fail with clear error."""
        repo = tmp_path / "nopull"
        _init_repo(repo, monkeypatch)
        _add_commit(repo, "x.jsonl", [_make_row("x")], "init", monkeypatch)

        monkeypatch.chdir(repo)
        r = runner.invoke(app, ["pull"])
        assert r.exit_code != 0
        assert "remote" in r.output.lower() or "fatal" in r.output.lower(), \
            f"Expected remote error, got: {r.output}"

    def test_clone_to_existing_non_empty_directory(self, server_app, tmp_path, monkeypatch):
        """Clone to existing non-empty directory should fail gracefully."""
        _patch_remote_client(monkeypatch, server_app)
        _create_remote_repo(server_app, "occupied")

        # Push something so the repo is not empty
        src = tmp_path / "src"
        _init_repo(src, monkeypatch)
        _add_commit(src, "d.jsonl", [_make_row("d")], "init", monkeypatch)
        _setup_remote(src, "http://testserver/occupied")
        _push(monkeypatch, src)

        # Create a non-empty destination
        dest = tmp_path / "occupied_dest"
        dest.mkdir()
        (dest / "blocker.txt").write_text("I exist")

        r = runner.invoke(
            app,
            ["clone", "http://testserver/occupied", str(dest), "--token", "dit_admin"],
        )
        assert r.exit_code != 0
        assert "already exists" in r.output.lower() or "not empty" in r.output.lower(), \
            f"Expected non-empty dir error, got: {r.output}"


class TestConflictsAndDivergence:
    """Diverged history and uncommitted change scenarios."""

    def test_push_diverged_history(self, server_app, tmp_path, monkeypatch):
        """Two clients push different commits — second should be rejected."""
        _patch_remote_client(monkeypatch, server_app)
        _create_remote_repo(server_app, "diverge")

        # Client A: init, commit, push
        client_a = tmp_path / "client_a"
        _init_repo(client_a, monkeypatch)
        _add_commit(client_a, "data.jsonl", [_make_row("a1")], "a-init", monkeypatch)
        _setup_remote(client_a, "http://testserver/diverge")
        _push(monkeypatch, client_a)

        # Client B: clone
        client_b = tmp_path / "client_b"
        _clone(str(client_b), "http://testserver/diverge")

        # Client A: push a second commit
        _add_commit(client_a, "data.jsonl", [_make_row("a1"), _make_row("a2")], "a-v2", monkeypatch)
        _push(monkeypatch, client_a)

        # Client B: make a different commit and try to push
        _add_commit(client_b, "data.jsonl", [_make_row("a1"), _make_row("b2")], "b-v2", monkeypatch)
        monkeypatch.chdir(client_b)
        r = runner.invoke(app, ["push"])
        # Should be rejected — local is not a descendant of remote
        assert r.exit_code != 0, f"Diverged push should fail, got: {r.output}"
        assert "rejected" in r.output.lower() or "not a descendant" in r.output.lower() or \
               "pull" in r.output.lower(), f"Expected conflict message, got: {r.output}"

    def test_pull_when_local_has_uncommitted_changes(self, server_app, tmp_path, monkeypatch):
        """Pull when local has uncommitted changes — should reject with error."""
        _patch_remote_client(monkeypatch, server_app)
        _create_remote_repo(server_app, "dirty")

        src = tmp_path / "src"
        _init_repo(src, monkeypatch)
        _add_commit(src, "data.jsonl", [_make_row("v1")], "v1", monkeypatch)
        _setup_remote(src, "http://testserver/dirty")
        _push(monkeypatch, src)

        clone_dir = tmp_path / "clone"
        _clone(str(clone_dir), "http://testserver/dirty")

        # Push v2 from source
        _add_commit(src, "data.jsonl", [_make_row("v1"), _make_row("v2")], "v2", monkeypatch)
        _push(monkeypatch, src)

        # Modify clone's working copy WITHOUT committing
        _write_jsonl(clone_dir / "data.jsonl", [_make_row("local-edit")])

        monkeypatch.chdir(clone_dir)
        r = runner.invoke(app, ["pull"])
        assert r.exit_code != 0, f"Pull with uncommitted changes should fail, got: {r.output}"
        assert "uncommitted" in r.output.lower(), f"Expected uncommitted changes error, got: {r.output}"

        # Verify local changes were NOT overwritten
        rows = _read_jsonl(clone_dir / "data.jsonl")
        assert len(rows) == 1, "Local changes should be preserved"
        assert rows[0]["messages"][0]["content"] == "local-edit", "Local edit should not be overwritten"


class TestBatchDownload:
    """Verify batch-download is used in clone and sparse-checkout flows."""

    def test_clone_uses_batch_download(self, server_app, tmp_path, monkeypatch):
        """Clone a repo with multiple files, verify all data arrives correctly."""
        _patch_remote_client(monkeypatch, server_app)
        _create_remote_repo(server_app, "batch-clone")

        src = tmp_path / "src"
        _init_repo(src, monkeypatch)
        files = {
            "a.jsonl": [_make_row("a1"), _make_row("a2")],
            "b.jsonl": [_make_row("b1"), _make_row("b2"), _make_row("b3")],
            "sub/c.jsonl": [_make_row("c1")],
        }
        for fname, rows in files.items():
            _write_jsonl(src / fname, rows)
        monkeypatch.chdir(src)
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        r = runner.invoke(app, ["commit", "-m", "multi-file"], catch_exceptions=False)
        assert r.exit_code == 0, r.output
        _setup_remote(src, "http://testserver/batch-clone")
        _push(monkeypatch, src)

        clone_dir = tmp_path / "clone"
        _clone(str(clone_dir), "http://testserver/batch-clone")

        for fname, expected_rows in files.items():
            cloned = _read_jsonl(clone_dir / fname)
            assert len(cloned) == len(expected_rows), f"{fname}: row count mismatch"
            for orig, got in zip(expected_rows, cloned):
                assert orig == got, f"{fname}: row content mismatch"

    def test_sparse_checkout_add_batch(self, server_app, tmp_path, monkeypatch):
        """Sparse clone, then sparse-checkout add a directory with multiple files."""
        _patch_remote_client(monkeypatch, server_app)
        _create_remote_repo(server_app, "batch-sparse")

        src = tmp_path / "src"
        _init_repo(src, monkeypatch)
        files = {
            "train.jsonl": [_make_row("t1"), _make_row("t2")],
            "data/eval.jsonl": [_make_row("e1")],
            "data/test.jsonl": [_make_row("te1"), _make_row("te2")],
        }
        for fname, rows in files.items():
            _write_jsonl(src / fname, rows)
        monkeypatch.chdir(src)
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        r = runner.invoke(app, ["commit", "-m", "multi-file"], catch_exceptions=False)
        assert r.exit_code == 0, r.output
        _setup_remote(src, "http://testserver/batch-sparse")
        _push(monkeypatch, src)

        # Sparse clone (no file data downloaded)
        clone_dir = tmp_path / "sparse-clone"
        r = runner.invoke(
            app,
            ["clone", "http://testserver/batch-sparse", str(clone_dir),
             "--token", "dit_admin", "--sparse"],
            catch_exceptions=False,
        )
        assert r.exit_code == 0, r.output

        # Verify no data files exist yet
        assert not (clone_dir / "data" / "eval.jsonl").exists()
        assert not (clone_dir / "data" / "test.jsonl").exists()

        # sparse-checkout add the data/ directory
        monkeypatch.chdir(clone_dir)
        r = runner.invoke(
            app, ["sparse-checkout", "add", "data/"],
            catch_exceptions=False,
        )
        assert r.exit_code == 0, r.output

        # Verify data files are now materialized
        assert (clone_dir / "data" / "eval.jsonl").exists()
        assert (clone_dir / "data" / "test.jsonl").exists()
        eval_rows = _read_jsonl(clone_dir / "data" / "eval.jsonl")
        assert len(eval_rows) == 1
        assert eval_rows[0] == _make_row("e1")
        test_rows = _read_jsonl(clone_dir / "data" / "test.jsonl")
        assert len(test_rows) == 2


class TestBinaryUpload:
    """Verify binary upload is used in push workflows."""

    def test_push_uses_binary_upload(self, server_app, tmp_path, monkeypatch):
        """Push a repo, verify it works end-to-end with binary upload path."""
        _patch_remote_client(monkeypatch, server_app)
        _create_remote_repo(server_app, "bin-push")

        src = tmp_path / "src"
        _init_repo(src, monkeypatch)
        files = {
            "train.jsonl": [_make_row("t1"), _make_row("t2")],
            "eval.jsonl": [_make_row("e1")],
        }
        for fname, rows in files.items():
            _write_jsonl(src / fname, rows)
        monkeypatch.chdir(src)
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        r = runner.invoke(app, ["commit", "-m", "binary push test"], catch_exceptions=False)
        assert r.exit_code == 0, r.output
        _setup_remote(src, "http://testserver/bin-push")
        _push(monkeypatch, src)

        # Clone and verify data integrity
        clone_dir = tmp_path / "clone"
        _clone(str(clone_dir), "http://testserver/bin-push")

        for fname, expected_rows in files.items():
            cloned = _read_jsonl(clone_dir / fname)
            assert len(cloned) == len(expected_rows), f"{fname}: row count mismatch"
            for orig, got in zip(expected_rows, cloned):
                assert orig == got, f"{fname}: row content mismatch"
