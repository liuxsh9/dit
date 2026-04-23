from pydantic_settings import BaseSettings, SettingsConfigDict


class ServerSettings(BaseSettings):
    database_url: str = "postgresql+asyncpg://localhost/datahub"
    data_dir: str = "/data/datahub"
    host: str = "0.0.0.0"
    port: int = 8000

    model_config = SettingsConfigDict(env_prefix="DIT_SERVER_")
