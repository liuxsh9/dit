from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from dit.cli.main import app

runner = CliRunner()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"], catch_exceptions=False)
    assert result.exit_code == 0
    return tmp_path


def test_remote_add(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["remote", "add", "origin", "http://localhost:8000"])
    assert result.exit_code == 0
    assert "origin" in result.output


def test_remote_list_empty(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["remote", "list"])
    assert result.exit_code == 0
    assert "No remotes configured" in result.output


def test_remote_list_shows_added(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["remote", "add", "origin", "http://server:8000"])
    result = runner.invoke(app, ["remote", "list"])
    assert result.exit_code == 0
    assert "origin" in result.output
    assert "http://server:8000" in result.output


def test_remote_remove(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["remote", "add", "origin", "http://server:8000"])
    result = runner.invoke(app, ["remote", "remove", "origin"])
    assert result.exit_code == 0
    list_result = runner.invoke(app, ["remote", "list"])
    assert "No remotes configured" in list_result.output


def test_remote_remove_missing_exits_nonzero(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["remote", "remove", "no-such-remote"])
    assert result.exit_code != 0


def test_remote_add_with_token(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app,
        ["remote", "add", "origin", "http://server:8000", "--token", "dit_abc123"],
    )
    assert result.exit_code == 0
    from dit.core.config import get_remote

    dot = tmp_path / ".dit"
    cfg = get_remote(dot, "origin")
    assert cfg is not None
    assert cfg["token"] == "dit_abc123"
    assert cfg["url"] == "http://server:8000"


def test_auth_set_token(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["remote", "add", "origin", "http://server:8000"])
    result = runner.invoke(app, ["auth", "set-token", "dit_newsecret123"])
    assert result.exit_code == 0
    assert "Token stored" in result.output
    from dit.core.config import get_remote

    dot = tmp_path / ".dit"
    cfg = get_remote(dot, "origin")
    assert cfg is not None
    assert cfg["token"] == "dit_newsecret123"
    assert cfg["url"] == "http://server:8000"


def test_auth_set_token_no_remote_exits_nonzero(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["auth", "set-token", "dit_abc", "--remote", "no-such-remote"])
    assert result.exit_code != 0


def test_auth_set_token_custom_remote(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["remote", "add", "upstream", "http://upstream:9000"])
    result = runner.invoke(
        app, ["auth", "set-token", "dit_upstreamtok", "--remote", "upstream"]
    )
    assert result.exit_code == 0
    from dit.core.config import get_remote

    dot = tmp_path / ".dit"
    cfg = get_remote(dot, "upstream")
    assert cfg["token"] == "dit_upstreamtok"


def test_push_reports_unauthorized_without_traceback(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data.jsonl").write_text(
        '{"messages":[{"role":"user","content":"q"},{"role":"assistant","content":"a"}]}\n'
    )
    assert runner.invoke(app, ["add", "data.jsonl"]).exit_code == 0
    assert runner.invoke(app, ["commit", "-m", "init"]).exit_code == 0
    assert runner.invoke(app, ["remote", "add", "origin", "http://server:8000/repo"]).exit_code == 0

    request = httpx.Request("GET", "http://server:8000/api/v1/repos/repo/refs/heads/main")
    response = httpx.Response(401, request=request, json={"detail": "Invalid token"})

    class UnauthorizedRemote:
        def get_ref(self, ref_type: str, name: str) -> str | None:
            raise httpx.HTTPStatusError(
                "Client error '401 Unauthorized' for url 'http://server:8000/api/v1/repos/repo/refs/heads/main'",
                request=request,
                response=response,
            )

    monkeypatch.setattr("dit.cli.main._build_remote_client", lambda dot, remote_name="origin": UnauthorizedRemote())
    result = runner.invoke(app, ["push"])
    assert result.exit_code != 0
    assert "401" in result.output
    assert "Invalid token" in result.output
    assert "Traceback" not in result.output


def test_push_reports_connection_error_without_traceback(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data.jsonl").write_text(
        '{"messages":[{"role":"user","content":"q"},{"role":"assistant","content":"a"}]}\n'
    )
    assert runner.invoke(app, ["add", "data.jsonl"]).exit_code == 0
    assert runner.invoke(app, ["commit", "-m", "init"]).exit_code == 0
    assert runner.invoke(app, ["remote", "add", "origin", "http://server:8000/repo"]).exit_code == 0

    request = httpx.Request("GET", "http://server:8000/api/v1/repos/repo/refs/heads/main")

    class BadHostRemote:
        def get_ref(self, ref_type: str, name: str) -> str | None:
            raise httpx.ConnectError("Connection refused", request=request)

    monkeypatch.setattr("dit.cli.main._build_remote_client", lambda dot, remote_name="origin": BadHostRemote())
    result = runner.invoke(app, ["push"])
    assert result.exit_code != 0
    assert "Connection refused" in result.output
    assert "Traceback" not in result.output
