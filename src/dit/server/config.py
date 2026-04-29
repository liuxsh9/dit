import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class ServerSettings(BaseSettings):
    database_url: str = "postgresql+asyncpg://localhost/dit"
    data_dir: str = "/data/dit"
    host: str = "0.0.0.0"
    port: int = 8000
    service_token: str = ""
    rate_limit: str = ""

    model_config = SettingsConfigDict(env_prefix="DIT_SERVER_")


def resolve_database_url(default_url: str) -> str:
    return os.environ.get("DIT_SERVER_DATABASE_URL") or default_url
