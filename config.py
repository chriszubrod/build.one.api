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

    # Encryption
    encryption_key: Optional[str] = None
    # Override that takes precedence when set — useful for running local
    # against prod-encrypted DB rows without overwriting the dev-only
    # encryption_key.
    azure_encryption_key: Optional[str] = None

    # Admin / scheduler-trigger secret. Shared with the build.one.scheduler
    # Azure Function; caller passes it in the X-Drain-Secret header. When
    # unset, the admin endpoints fail closed with 503.
    drain_secret: Optional[str] = None

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

    # Invoice Inbox Configuration
    # The email address of the dedicated invoice inbox account (separate licensed M365 user).
    # When set, the Inbox feature reads from this mailbox via the Graph API /users/{email}/ path.
    # The authenticated service account must have delegated Mail.Read permission for this address.
    # Leave blank to fall back to the primary authenticated user's own mailbox.
    invoice_inbox_email: Optional[str] = None

    # Azure AI Document Intelligence Configuration
    # Used by the email-agent pipeline to extract structured data from
    # vendor-invoice PDFs (deterministic — never hallucinates dollar amounts).
    azure_document_intelligence_endpoint: Optional[str] = None
    azure_document_intelligence_key: Optional[str] = None
    # Override the API version if needed; the default tracks the current GA.
    azure_document_intelligence_api_version: str = "2024-11-30"

    # Azure App Service default domain for the prod API. Used by smoke
    # scripts and any local tooling that needs to hit the deployed API.
    # Format: <name>.<region>.azurewebsites.net
    azure_default_domain: Optional[str] = None

    # Intelligence Layer — provider API keys
    anthropic_api_key: Optional[str] = None

    # Intelligence Layer — internal API base URL
    # Agents call their own API surface as HTTP clients so they go through
    # the same RBAC, ProcessEngine routing, and audit logs as human users.
    internal_api_base_url: str = "http://localhost:8000"

    # Intelligence Layer — per-agent credentials
    # Each Agent carries a `credentials_key` (e.g. "scout_agent") and the
    # auth helper reads `{key}_username` / `{key}_password` from here.
    scout_agent_username: Optional[str] = None
    scout_agent_password: Optional[str] = None
    sub_cost_code_agent_username: Optional[str] = None
    sub_cost_code_agent_password: Optional[str] = None
    cost_code_agent_username: Optional[str] = None
    cost_code_agent_password: Optional[str] = None
    customer_agent_username: Optional[str] = None
    customer_agent_password: Optional[str] = None
    project_agent_username: Optional[str] = None
    project_agent_password: Optional[str] = None
    vendor_agent_username: Optional[str] = None
    vendor_agent_password: Optional[str] = None
    bill_agent_username: Optional[str] = None
    bill_agent_password: Optional[str] = None
    bill_credit_agent_username: Optional[str] = None
    bill_credit_agent_password: Optional[str] = None
    expense_agent_username: Optional[str] = None
    expense_agent_password: Optional[str] = None
    invoice_agent_username: Optional[str] = None
    invoice_agent_password: Optional[str] = None

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore"  # This allows extra fields to be ignored
    )
