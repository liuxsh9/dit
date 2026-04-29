"""End-to-end stress tests: add -> commit -> push (binary upload) -> clone -> verify."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dit.cli.main import app
from dit.core.config import set_remote

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixtures (copied from test_cli_push.py / test_cli_clone.py)
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_repo_on_server(server_app, name: str):
    from starlette.testclient import TestClient
    c = TestClient(server_app, headers={"Authorization": "Bearer dit_admin"})
    resp = c.post("/api/v1/repos", json={"name": name})
    assert resp.status_code in (201, 409), resp.text


def _init_repo(path: Path, monkeypatch):
    monkeypatch.chdir(path)
    r = runner.invoke(app, ["init"], catch_exceptions=False)
    assert r.exit_code == 0, r.output


def _write_jsonl(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _make_row(content: str) -> dict:
    return {"messages": [{"role": "user", "content": "q"}, {"role": "assistant", "content": content}]}


def _add_commit(monkeypatch, repo: Path, files: list[str], msg: str):
    monkeypatch.chdir(repo)
    for f in files:
        r = runner.invoke(app, ["add", f], catch_exceptions=False)
        assert r.exit_code == 0, r.output
    r = runner.invoke(app, ["commit", "-m", msg], catch_exceptions=False)
    assert r.exit_code == 0, r.output


def _push(monkeypatch, repo: Path, branch: str = "main"):
    monkeypatch.chdir(repo)
    r = runner.invoke(app, ["push", "--branch", branch], catch_exceptions=False)
    assert r.exit_code == 0, r.output


def _clone(monkeypatch, dest: Path, token: str = "dit_admin", repo_name: str = "dataset"):
    r = runner.invoke(
        app,
        ["clone", f"http://testserver/{repo_name}", str(dest), "--token", token],
        catch_exceptions=False,
    )
    assert r.exit_code == 0, r.output


def _pull(monkeypatch, repo: Path, branch: str = "main"):
    monkeypatch.chdir(repo)
    r = runner.invoke(app, ["pull", "--branch", branch], catch_exceptions=False)
    assert r.exit_code == 0, r.output


def _setup_remote(repo: Path, repo_name: str = "dataset"):
    set_remote(repo / ".dit", "origin", f"http://testserver/{repo_name}", token="dit_admin")


# ---------------------------------------------------------------------------
# 1. Push 10 files with binary upload, clone, verify
# ---------------------------------------------------------------------------

def test_push_10_files_clone_verify(server_app, tmp_path, monkeypatch):
    _patch_remote_client(monkeypatch, server_app)
    _create_repo_on_server(server_app, "dataset")

    src = tmp_path / "src"
    src.mkdir()
    _init_repo(src, monkeypatch)

    expected = {}
    for i in range(10):
        rows = [_make_row(f"answer-{i}-{j}") for j in range(5)]
        fname = f"file_{i}.jsonl"
        _write_jsonl(src / fname, rows)
        expected[fname] = rows

    _add_commit(monkeypatch, src, ["."], "add 10 files")
    _setup_remote(src)
    _push(monkeypatch, src)

    clone_dir = tmp_path / "clone"
    _clone(monkeypatch, clone_dir)

    for fname, rows in expected.items():
        cloned = _read_jsonl(clone_dir / fname)
        assert cloned == rows, f"Mismatch in {fname}"


# ---------------------------------------------------------------------------
# 2. Push large objects (50 rows x 10KB each)
# ---------------------------------------------------------------------------

def test_push_large_objects(server_app, tmp_path, monkeypatch):
    _patch_remote_client(monkeypatch, server_app)
    _create_repo_on_server(server_app, "dataset")

    src = tmp_path / "src"
    src.mkdir()
    _init_repo(src, monkeypatch)

    big_rows = [_make_row("X" * 10_000 + f"-{i}") for i in range(50)]
    _write_jsonl(src / "big.jsonl", big_rows)
    _add_commit(monkeypatch, src, ["big.jsonl"], "large objects")
    _setup_remote(src)
    _push(monkeypatch, src)

    clone_dir = tmp_path / "clone"
    _clone(monkeypatch, clone_dir)

    cloned = _read_jsonl(clone_dir / "big.jsonl")
    assert len(cloned) == 50
    for i, row in enumerate(cloned):
        assert row["messages"][1]["content"] == "X" * 10_000 + f"-{i}"


# ---------------------------------------------------------------------------
# 3. Push mixed object types (jsonl manifests + blob files)
# ---------------------------------------------------------------------------

def test_push_mixed_object_types(server_app, tmp_path, monkeypatch):
    _patch_remote_client(monkeypatch, server_app)
    _create_repo_on_server(server_app, "dataset")

    src = tmp_path / "src"
    src.mkdir()
    _init_repo(src, monkeypatch)

    rows = [_make_row("mixed-test")]
    _write_jsonl(src / "data.jsonl", rows)
    (src / "config.json").write_text('{"version": 1}')

    _add_commit(monkeypatch, src, ["data.jsonl", "config.json"], "mixed types")
    _setup_remote(src)
    _push(monkeypatch, src)

    clone_dir = tmp_path / "clone"
    _clone(monkeypatch, clone_dir)

    assert _read_jsonl(clone_dir / "data.jsonl") == rows
    assert json.loads((clone_dir / "config.json").read_text()) == {"version": 1}


# ---------------------------------------------------------------------------
# 4. Full lifecycle: init -> add -> commit -> push -> clone -> modify -> push -> pull
# ---------------------------------------------------------------------------

def test_full_lifecycle(server_app, tmp_path, monkeypatch):
    _patch_remote_client(monkeypatch, server_app)
    _create_repo_on_server(server_app, "dataset")

    # Repo A: create and push
    repo_a = tmp_path / "repo_a"
    repo_a.mkdir()
    _init_repo(repo_a, monkeypatch)
    rows_v1 = [_make_row("v1-hello")]
    _write_jsonl(repo_a / "train.jsonl", rows_v1)
    _add_commit(monkeypatch, repo_a, ["train.jsonl"], "v1")
    _setup_remote(repo_a)
    _push(monkeypatch, repo_a)

    # Repo B: clone
    repo_b = tmp_path / "repo_b"
    _clone(monkeypatch, repo_b)

    assert _read_jsonl(repo_b / "train.jsonl") == rows_v1

    # Repo B: modify, commit, push
    rows_v2 = rows_v1 + [_make_row("v2-world")]
    _write_jsonl(repo_b / "train.jsonl", rows_v2)
    _add_commit(monkeypatch, repo_b, ["train.jsonl"], "v2")
    _push(monkeypatch, repo_b)

    # Repo A: pull
    _pull(monkeypatch, repo_a)
    assert _read_jsonl(repo_a / "train.jsonl") == rows_v2


# ---------------------------------------------------------------------------
# 5. Three-way collaboration
# ---------------------------------------------------------------------------

def test_three_way_collaboration(server_app, tmp_path, monkeypatch):
    _patch_remote_client(monkeypatch, server_app)
    _create_repo_on_server(server_app, "dataset")

    # A pushes
    repo_a = tmp_path / "a"
    repo_a.mkdir()
    _init_repo(repo_a, monkeypatch)
    rows_a = [_make_row("from-A")]
    _write_jsonl(repo_a / "train.jsonl", rows_a)
    _add_commit(monkeypatch, repo_a, ["train.jsonl"], "A initial")
    _setup_remote(repo_a)
    _push(monkeypatch, repo_a)

    # B clones, modifies, pushes
    repo_b = tmp_path / "b"
    _clone(monkeypatch, repo_b)
    rows_b = rows_a + [_make_row("from-B")]
    _write_jsonl(repo_b / "train.jsonl", rows_b)
    _add_commit(monkeypatch, repo_b, ["train.jsonl"], "B adds row")
    _push(monkeypatch, repo_b)

    # C clones fresh
    repo_c = tmp_path / "c"
    _clone(monkeypatch, repo_c)
    assert _read_jsonl(repo_c / "train.jsonl") == rows_b

    # A pulls
    _pull(monkeypatch, repo_a)
    assert _read_jsonl(repo_a / "train.jsonl") == rows_b

    # All three identical
    assert _read_jsonl(repo_a / "train.jsonl") == _read_jsonl(repo_b / "train.jsonl")
    assert _read_jsonl(repo_b / "train.jsonl") == _read_jsonl(repo_c / "train.jsonl")


# ---------------------------------------------------------------------------
# 6. Sparse clone -> sparse-checkout add -> modify -> push -> full clone
# ---------------------------------------------------------------------------

def test_sparse_clone_modify_push_full_clone(server_app, tmp_path, monkeypatch):
    _patch_remote_client(monkeypatch, server_app)
    _create_repo_on_server(server_app, "dataset")

    # Push two files
    src = tmp_path / "src"
    src.mkdir()
    _init_repo(src, monkeypatch)
    rows_a = [_make_row("file-a")]
    rows_b = [_make_row("file-b")]
    _write_jsonl(src / "a.jsonl", rows_a)
    _write_jsonl(src / "b.jsonl", rows_b)
    _add_commit(monkeypatch, src, ["a.jsonl", "b.jsonl"], "two files")
    _setup_remote(src)
    _push(monkeypatch, src)

    # Sparse clone
    sparse_dir = tmp_path / "sparse"
    r = runner.invoke(
        app,
        ["clone", "http://testserver/dataset", str(sparse_dir),
         "--token", "dit_admin", "--sparse"],
        catch_exceptions=False,
    )
    assert r.exit_code == 0, r.output

    # Sparse-checkout add a.jsonl
    monkeypatch.chdir(sparse_dir)
    r = runner.invoke(app, ["sparse-checkout", "add", "a.jsonl"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    assert (sparse_dir / "a.jsonl").exists()
    assert _read_jsonl(sparse_dir / "a.jsonl") == rows_a

    # Modify a.jsonl, commit, push
    rows_a_mod = [_make_row("file-a-modified")]
    _write_jsonl(sparse_dir / "a.jsonl", rows_a_mod)
    _add_commit(monkeypatch, sparse_dir, ["a.jsonl"], "modify a")
    _push(monkeypatch, sparse_dir)

    # Full clone from scratch
    full_dir = tmp_path / "full"
    _clone(monkeypatch, full_dir)
    assert _read_jsonl(full_dir / "a.jsonl") == rows_a_mod
    assert _read_jsonl(full_dir / "b.jsonl") == rows_b


# ---------------------------------------------------------------------------
# 7. Branch workflow over remote
# ---------------------------------------------------------------------------

def test_branch_workflow_over_remote(server_app, tmp_path, monkeypatch):
    _patch_remote_client(monkeypatch, server_app)
    _create_repo_on_server(server_app, "dataset")

    # Push main
    src = tmp_path / "src"
    src.mkdir()
    _init_repo(src, monkeypatch)
    rows_main = [_make_row("main-content")]
    _write_jsonl(src / "train.jsonl", rows_main)
    _add_commit(monkeypatch, src, ["train.jsonl"], "main commit")
    _setup_remote(src)
    _push(monkeypatch, src)

    # Create branch, commit on branch, push branch
    monkeypatch.chdir(src)
    r = runner.invoke(app, ["branch", "feature"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    r = runner.invoke(app, ["switch", "feature"], catch_exceptions=False)
    assert r.exit_code == 0, r.output

    rows_feat = rows_main + [_make_row("feature-content")]
    _write_jsonl(src / "train.jsonl", rows_feat)
    _add_commit(monkeypatch, src, ["train.jsonl"], "feature commit")
    _push(monkeypatch, src, branch="feature")

    # Clone from scratch on feature branch
    clone_dir = tmp_path / "clone_feat"
    r = runner.invoke(
        app,
        ["clone", "http://testserver/dataset", str(clone_dir),
         "--token", "dit_admin", "--branch", "feature"],
        catch_exceptions=False,
    )
    assert r.exit_code == 0, r.output
    assert _read_jsonl(clone_dir / "train.jsonl") == rows_feat


# ---------------------------------------------------------------------------
# 8. Empty commit push (commit with no file changes)
# ---------------------------------------------------------------------------

def test_empty_commit_push(server_app, tmp_path, monkeypatch):
    _patch_remote_client(monkeypatch, server_app)
    _create_repo_on_server(server_app, "dataset")

    src = tmp_path / "src"
    src.mkdir()
    _init_repo(src, monkeypatch)
    rows = [_make_row("initial")]
    _write_jsonl(src / "train.jsonl", rows)
    _add_commit(monkeypatch, src, ["train.jsonl"], "first")
    _setup_remote(src)
    _push(monkeypatch, src)

    # Try to commit with no changes — dit may reject this
    monkeypatch.chdir(src)
    r = runner.invoke(app, ["commit", "-m", "empty"], catch_exceptions=False)
    if r.exit_code != 0:
        pytest.skip("dit does not allow empty commits")

    _push(monkeypatch, src)

    clone_dir = tmp_path / "clone"
    _clone(monkeypatch, clone_dir)
    assert _read_jsonl(clone_dir / "train.jsonl") == rows


# ---------------------------------------------------------------------------
# 9. Push after reset --hard
# ---------------------------------------------------------------------------

def test_push_after_reset_hard(server_app, tmp_path, monkeypatch):
    _patch_remote_client(monkeypatch, server_app)
    _create_repo_on_server(server_app, "dataset")

    src = tmp_path / "src"
    src.mkdir()
    _init_repo(src, monkeypatch)

    # First commit
    rows_v1 = [_make_row("original")]
    _write_jsonl(src / "train.jsonl", rows_v1)
    _add_commit(monkeypatch, src, ["train.jsonl"], "v1")

    # Reset --hard back to HEAD (same commit, but we'll overwrite the file)
    monkeypatch.chdir(src)
    r = runner.invoke(app, ["reset", "--hard"], catch_exceptions=False)
    assert r.exit_code == 0, r.output

    # Write different content and re-commit
    rows_v2 = [_make_row("replaced")]
    _write_jsonl(src / "train.jsonl", rows_v2)
    _add_commit(monkeypatch, src, ["train.jsonl"], "v2 after reset")
    _setup_remote(src)
    _push(monkeypatch, src)

    clone_dir = tmp_path / "clone"
    _clone(monkeypatch, clone_dir)
    assert _read_jsonl(clone_dir / "train.jsonl") == rows_v2


# ---------------------------------------------------------------------------
# 10. Unicode filenames through the full pipeline
# ---------------------------------------------------------------------------

def test_unicode_filenames(server_app, tmp_path, monkeypatch):
    _patch_remote_client(monkeypatch, server_app)
    _create_repo_on_server(server_app, "dataset")

    src = tmp_path / "src"
    src.mkdir()
    _init_repo(src, monkeypatch)

    rows_cn = [_make_row("Chinese content")]
    rows_sp = [_make_row("spaced content")]
    _write_jsonl(src / "\u8bad\u7ec3\u6570\u636e.jsonl", rows_cn)
    _write_jsonl(src / "my data.jsonl", rows_sp)

    _add_commit(monkeypatch, src, ["\u8bad\u7ec3\u6570\u636e.jsonl", "my data.jsonl"], "unicode names")
    _setup_remote(src)
    _push(monkeypatch, src)

    clone_dir = tmp_path / "clone"
    _clone(monkeypatch, clone_dir)

    assert _read_jsonl(clone_dir / "\u8bad\u7ec3\u6570\u636e.jsonl") == rows_cn
    assert _read_jsonl(clone_dir / "my data.jsonl") == rows_sp
