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
    signup_registration_code: Optional[str] = None

    # Agent Credentials
    agent_one_username: Optional[str] = None
    agent_one_password: Optional[str] = None

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
    azure_storage_timeout: float = 120.0  # seconds for blob operations (download/upload)

    # Azure OpenAI Configuration
    azure_openai_endpoint: Optional[str] = None
    azure_openai_api_key: Optional[str] = None
    azure_openai_deployment_name: str = "gpt-4o-mini"
    azure_embedding_deployment_name: str = "text-embedding-ada-002"
    azure_openai_api_version: str = "2024-02-01"

    # Anthropic Configuration
    anthropic_api_key: Optional[str] = None
    anthropic_model: str = "claude-3-haiku-20240307"

    # Azure AI Document Intelligence Configuration
    azure_document_intelligence_endpoint: Optional[str] = None
    azure_document_intelligence_key: Optional[str] = None

    # Invoice Inbox Configuration
    # The email address of the dedicated invoice inbox account (separate licensed M365 user).
    # When set, the Inbox feature reads from this mailbox via the Graph API /users/{email}/ path.
    # The authenticated service account must have delegated Mail.Read permission for this address.
    # Leave blank to fall back to the primary authenticated user's own mailbox.
    invoice_inbox_email: Optional[str] = None

    # Azure AI Search Configuration
    azure_search_endpoint: Optional[str] = None
    azure_search_api_key: Optional[str] = None
    azure_search_index_name: str = "documents-index"

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore"  # This allows extra fields to be ignored
    )
