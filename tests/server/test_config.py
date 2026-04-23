import os
from dit.server.config import ServerSettings


class TestServerSettings:
    def test_defaults(self):
        settings = ServerSettings()
        assert settings.database_url == "postgresql+asyncpg://localhost/datahub"
        assert settings.data_dir == "/data/datahub"
        assert settings.host == "0.0.0.0"
        assert settings.port == 8000

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("DIT_SERVER_DATABASE_URL", "sqlite+aiosqlite:///test.db")
        monkeypatch.setenv("DIT_SERVER_PORT", "9999")
        settings = ServerSettings()
        assert settings.database_url == "sqlite+aiosqlite:///test.db"
        assert settings.port == 9999

    def test_data_dir_override(self, monkeypatch):
        monkeypatch.setenv("DIT_SERVER_DATA_DIR", "/tmp/test-data")
        settings = ServerSettings()
        assert settings.data_dir == "/tmp/test-data"
