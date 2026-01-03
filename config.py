from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    
    # Environment
    env: str = "development"
    debug: bool = False
    host: str
    port: int
    
    # Database Configuration
    db_driver: str
    db_server: str
    db_name: str
    db_user: str
    db_password: str
    db_encrypt: str = "yes"
    
    # Security
    secret_key: str
    algorithm: str
    access_token_expire_seconds: int
    refresh_token_expire_seconds: int
    iterations: int

    # Encryption
    encryption_key: Optional[str] = None

    # Logging
    log_level: str = "INFO"
    log_file: Optional[str] = None
    log_console: bool = True
    
    # API Settings
    api_version: str = "1.0.0"
    rate_limit_per_minute: int = 60
    max_content_length: int = 16777216
    
    # Azure Blob Storage Configuration
    azure_storage_account_name: Optional[str] = None
    azure_storage_account_key: Optional[str] = None
    azure_storage_sas_token: Optional[str] = None
    azure_storage_container_name: str = "attachments"

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore"  # This allows extra fields to be ignored
    )
