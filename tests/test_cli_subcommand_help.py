"""Tests for sub-app help display when invoked without a subcommand.

Covers: dit remote, dit auth, dit meta, dit sparse-checkout.
Each sub-app should show help text (exit 0), list available subcommands,
and gracefully reject invalid subcommands.
"""
from __future__ import annotations

import pytest
from typer.testing import CliRunner

from dit.cli.main import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def repo(tmp_path, monkeypatch):
    """Initialize a dit repo and chdir into it."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"], catch_exceptions=False)
    assert result.exit_code == 0
    return tmp_path


# ---------------------------------------------------------------------------
# Parameterised: bare sub-app shows help and exits 0
# ---------------------------------------------------------------------------

SUB_APPS = [
    ("remote", "Manage remote repositories", ["add", "remove", "list"]),
    ("auth", "Manage authentication credentials", ["set-token", "login"]),
    ("meta", "Manage sidecar metadata", ["compute", "show", "diff"]),
    ("sparse-checkout", "Manage sparse checkout configuration", ["add", "remove", "list", "disable"]),
]


@pytest.mark.parametrize("cmd,description,subcommands", SUB_APPS, ids=[s[0] for s in SUB_APPS])
class TestSubAppHelp:
    """Verify each sub-app prints help when invoked without a subcommand."""

    def test_exits_zero(self, cmd, description, subcommands):
        result = runner.invoke(app, [cmd])
        assert result.exit_code == 0, (
            f"`dit {cmd}` exited {result.exit_code}; output:\n{result.output}"
        )

    def test_shows_description(self, cmd, description, subcommands):
        result = runner.invoke(app, [cmd])
        assert description in result.output, (
            f"Expected description '{description}' in output:\n{result.output}"
        )

    def test_lists_subcommands(self, cmd, description, subcommands):
        result = runner.invoke(app, [cmd])
        for sub in subcommands:
            assert sub in result.output, (
                f"Expected subcommand '{sub}' listed in `dit {cmd}` help:\n{result.output}"
            )


# ---------------------------------------------------------------------------
# Existing subcommands still work (smoke tests inside an initialised repo)
# ---------------------------------------------------------------------------

class TestExistingSubcommands:
    """Ensure the callback doesn't break real subcommand dispatch."""

    def test_remote_list(self, repo):
        result = runner.invoke(app, ["remote", "list"])
        assert result.exit_code == 0
        assert "No remotes configured" in result.output or "origin" in result.output

    def test_remote_add_and_list(self, repo):
        add = runner.invoke(app, ["remote", "add", "origin", "http://example.com"])
        assert add.exit_code == 0
        ls = runner.invoke(app, ["remote", "list"])
        assert ls.exit_code == 0
        assert "origin" in ls.output

    def test_auth_login(self, repo):
        result = runner.invoke(
            app,
            ["auth", "login", "--url", "http://forgejo:3000", "--token", "tok"],
        )
        assert result.exit_code == 0
        assert "Logged in" in result.output or "saved" in result.output.lower()


# ---------------------------------------------------------------------------
# Monkey test: invalid subcommands are rejected gracefully
# ---------------------------------------------------------------------------

INVALID_SUBCOMMANDS = [
    "nonexistent",
    "xyzzy",
    "--not-a-flag",
    "123",
    "add-remove",
]


@pytest.mark.parametrize("cmd,description,subcommands", SUB_APPS, ids=[s[0] for s in SUB_APPS])
@pytest.mark.parametrize("bad_sub", INVALID_SUBCOMMANDS)
def test_invalid_subcommand_rejected(cmd, description, subcommands, bad_sub):
    """Invoking a sub-app with a random invalid subcommand should fail gracefully."""
    result = runner.invoke(app, [cmd, bad_sub])
    # Typer/Click should reject unknown subcommands with exit code != 0
    # and should NOT produce a Python traceback.
    assert result.exit_code != 0, (
        f"`dit {cmd} {bad_sub}` unexpectedly succeeded (exit 0):\n{result.output}"
    )
    assert "Traceback" not in result.output, (
        f"`dit {cmd} {bad_sub}` produced a traceback:\n{result.output}"
    )
