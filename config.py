from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    api_version: str = "1.0.0"
    host: str
    port: int

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore"  # This allows extra fields to be ignored
    )
