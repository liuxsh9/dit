import os
from alembic.config import Config
from dit.server.config import ServerSettings
from dit.server.config import resolve_database_url


class TestServerSettings:
    def test_defaults(self, monkeypatch):
        monkeypatch.delenv("DIT_SERVER_DATABASE_URL", raising=False)
        monkeypatch.delenv("DIT_SERVER_DATA_DIR", raising=False)
        monkeypatch.delenv("DIT_SERVER_HOST", raising=False)
        monkeypatch.delenv("DIT_SERVER_PORT", raising=False)
        monkeypatch.delenv("DIT_SERVER_SERVICE_TOKEN", raising=False)
        settings = ServerSettings()
        assert settings.database_url == "postgresql+asyncpg://localhost/dit"
        assert settings.data_dir == "/data/dit"
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


class TestAlembicConfig:
    def test_alembic_uses_server_database_env_when_present(self, monkeypatch):
        monkeypatch.setenv("DIT_SERVER_DATABASE_URL", "postgresql+asyncpg://example/testdb")
        config = Config()
        assert resolve_database_url(config.get_main_option("sqlalchemy.url")) == "postgresql+asyncpg://example/testdb"

    def test_alembic_falls_back_to_ini_url(self, monkeypatch):
        monkeypatch.delenv("DIT_SERVER_DATABASE_URL", raising=False)
        config = Config()
        config.set_main_option("sqlalchemy.url", "postgresql+asyncpg://localhost/from-ini")
        assert resolve_database_url(config.get_main_option("sqlalchemy.url")) == "postgresql+asyncpg://localhost/from-ini"
