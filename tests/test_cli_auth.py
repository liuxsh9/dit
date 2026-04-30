import json
from typer.testing import CliRunner
from dit.cli.main import app


class TestAuthLogin:
    def test_auth_login_stores_credentials(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()

        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0

        result = runner.invoke(
            app,
            ["auth", "login", "--url", "http://forgejo:3000", "--token", "mytoken123"],
        )
        assert result.exit_code == 0, result.output
        assert "Logged in" in result.output or "credentials saved" in result.output.lower()

        creds_path = tmp_path / ".dit" / "credentials"
        assert creds_path.exists()
        data = json.loads(creds_path.read_text())
        assert data["url"] == "http://forgejo:3000"
        assert data["token"] == "mytoken123"

    def test_auth_login_updates_existing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        runner.invoke(app, ["init"])

        runner.invoke(app, ["auth", "login", "--url", "http://forgejo:3000", "--token", "old"])
        runner.invoke(app, ["auth", "login", "--url", "http://forgejo:3000", "--token", "new"])

        creds_path = tmp_path / ".dit" / "credentials"
        data = json.loads(creds_path.read_text())
        assert data["token"] == "new"
