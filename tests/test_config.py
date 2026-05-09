from pathlib import Path
from dit.core.config import (
    get_remote,
    get_user_identity,
    load_config,
    remove_remote,
    save_config,
    set_remote,
    set_user_identity,
)


class TestConfig:
    def test_load_missing(self, tmp_path: Path):
        assert load_config(tmp_path) == {}

    def test_save_and_load(self, tmp_path: Path):
        save_config(tmp_path, {"key": "value"})
        assert load_config(tmp_path) == {"key": "value"}

    def test_set_remote(self, tmp_path: Path):
        set_remote(tmp_path, "origin", "http://localhost:8000", "tok123")
        remote = get_remote(tmp_path, "origin")
        assert remote == {"url": "http://localhost:8000", "token": "tok123"}

    def test_set_remote_default_token(self, tmp_path: Path):
        set_remote(tmp_path, "origin", "http://localhost:8000")
        remote = get_remote(tmp_path, "origin")
        assert remote == {"url": "http://localhost:8000", "token": ""}

    def test_get_remote_missing(self, tmp_path: Path):
        assert get_remote(tmp_path, "nope") is None

    def test_remove_remote(self, tmp_path: Path):
        set_remote(tmp_path, "origin", "http://localhost:8000")
        assert remove_remote(tmp_path, "origin") is True
        assert get_remote(tmp_path, "origin") is None

    def test_remove_remote_missing(self, tmp_path: Path):
        assert remove_remote(tmp_path, "nope") is False

    def test_multiple_remotes(self, tmp_path: Path):
        set_remote(tmp_path, "origin", "http://a:8000")
        set_remote(tmp_path, "upstream", "http://b:8000")
        assert get_remote(tmp_path, "origin")["url"] == "http://a:8000"
        assert get_remote(tmp_path, "upstream")["url"] == "http://b:8000"

    def test_user_identity_round_trip(self, tmp_path: Path):
        set_user_identity(tmp_path, name="Alice", email="alice@example.com")

        assert get_user_identity(tmp_path) == {
            "name": "Alice",
            "email": "alice@example.com",
        }

    def test_user_identity_preserves_existing_fields(self, tmp_path: Path):
        set_user_identity(tmp_path, name="Alice", email="alice@example.com")
        set_user_identity(tmp_path, name="Bob")

        assert get_user_identity(tmp_path) == {
            "name": "Bob",
            "email": "alice@example.com",
        }
