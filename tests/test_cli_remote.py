from pathlib import Path

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

    dot = tmp_path / ".datahub"
    cfg = get_remote(dot, "origin")
    assert cfg is not None
    assert cfg["token"] == "dit_abc123"
    assert cfg["url"] == "http://server:8000"
