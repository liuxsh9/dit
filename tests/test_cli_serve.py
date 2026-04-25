import subprocess
import sys

from typer.testing import CliRunner

from dit.cli.main import app


runner = CliRunner()


def test_serve_uses_env_defaults(monkeypatch):
    captured = {}

    def fake_run(app_instance, host, port):
        captured["app"] = app_instance
        captured["host"] = host
        captured["port"] = port

    monkeypatch.setenv("DIT_SERVER_HOST", "127.0.0.1")
    monkeypatch.setenv("DIT_SERVER_PORT", "8123")
    monkeypatch.setattr("uvicorn.run", fake_run)

    result = runner.invoke(app, ["serve"])

    assert result.exit_code == 0
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8123


def test_serve_cli_port_overrides_env(monkeypatch):
    captured = {}

    def fake_run(app_instance, host, port):
        captured["app"] = app_instance
        captured["host"] = host
        captured["port"] = port

    monkeypatch.setenv("DIT_SERVER_HOST", "127.0.0.1")
    monkeypatch.setenv("DIT_SERVER_PORT", "8123")
    monkeypatch.setattr("uvicorn.run", fake_run)

    result = runner.invoke(app, ["serve", "--port", "9001"])

    assert result.exit_code == 0
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 9001


def test_module_entrypoint_registers_serve_command():
    result = subprocess.run(
        [sys.executable, "-m", "dit.cli.main", "serve", "--help"],
        capture_output=True,
        text=True,
        cwd="/Users/lxs/code/datahub",
    )

    assert result.returncode == 0
    assert "Start the Dit HTTP API server." in result.stdout
