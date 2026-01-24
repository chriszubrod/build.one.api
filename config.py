from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application settings that work in both local development and Azure App Service.
    
    - Local development: Reads from .env file
    - Azure App Service: Reads from environment variables (which override .env if present)
    
    Pydantic Settings automatically handles this - environment variables take precedence
    over .env file values, making it work seamlessly in both environments.
    """
    
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

    # Azure OpenAI Configuration
    azure_openai_endpoint: Optional[str] = None
    azure_openai_api_key: Optional[str] = None
    azure_openai_deployment_name: str = "gpt-4o-mini"
    azure_openai_api_version: str = "2024-02-01"

    # Azure AI Document Intelligence Configuration
    azure_document_intelligence_endpoint: Optional[str] = None
    azure_document_intelligence_key: Optional[str] = None

    # Azure AI Search Configuration
    azure_search_endpoint: Optional[str] = None
    azure_search_api_key: Optional[str] = None
    azure_search_index_name: str = "documents-index"

    # Local AI Configuration
    local_embedding_model: str = "all-MiniLM-L6-v2"

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore"  # This allows extra fields to be ignored
    )
