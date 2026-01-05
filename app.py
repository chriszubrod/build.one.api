# Python Standard Library Imports
import logging
import os
import sys
from pathlib import Path
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

# Third-party Imports
from fastapi import Depends, FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from typing_extensions import Annotated

# Local Imports
import config

from modules.address.api.router import router as address_api_router
from modules.address.web.controller import router as address_web_router
from modules.address_type.api.router import router as address_type_api_router
from modules.address_type.web.controller import router as address_type_web_router
from modules.auth.api.router import router as auth_api_router
from modules.auth.web.controller import router as auth_web_router
from modules.integration.api.router import router as integration_api_router
from modules.integration.web.controller import router as integration_web_router
from modules.organization.api.router import router as organization_api_router
from modules.organization.web.controller import router as organization_web_router
from modules.company.api.router import router as company_api_router
from modules.company.web.controller import router as company_web_router
from modules.cost_code.api.router import router as cost_code_api_router
from modules.cost_code.web.controller import router as cost_code_web_router
from modules.sub_cost_code.api.router import router as sub_cost_code_api_router
from modules.sub_cost_code.web.controller import router as sub_cost_code_web_router
from modules.module.api.router import router as module_api_router
from modules.module.web.controller import router as module_web_router
from modules.user.api.router import router as user_api_router
from modules.user.web.controller import router as user_web_router
from modules.user_role.api.router import router as user_role_api_router
from modules.user_role.web.controller import router as user_role_web_router
from modules.vendor.api.router import router as vendor_api_router
from modules.vendor.web.controller import router as vendor_web_router
from modules.vendor_address.api.router import router as vendor_address_api_router
from modules.vendor_address.web.controller import router as vendor_address_web_router
from modules.vendor_type.api.router import router as vendor_type_api_router
from modules.vendor_type.web.controller import router as vendor_type_web_router
from modules.taxpayer.api.router import router as taxpayer_api_router
from modules.taxpayer.web.controller import router as taxpayer_web_router
from modules.role_module.api.router import router as role_module_api_router
from modules.role_module.web.controller import router as role_module_web_router
from modules.dashboard.web.controller import router as dashboard_web_router
from modules.attachment.api.router import router as attachment_api_router
from modules.attachment.web.controller import router as attachment_web_router
from modules.taxpayer_attachment.api.router import router as taxpayer_attachment_api_router
from modules.taxpayer_attachment.web.controller import router as taxpayer_attachment_web_router
from modules.legal.web.controller import router as legal_web_router

from integrations.intuit.qbo.auth.api.router import router as intuit_qbo_auth_api_router
from integrations.sync.api.router import router as sync_api_router
from integrations.sync.web.controller import router as sync_web_router
from integrations.intuit.qbo.vendor.api.router import router as qbo_vendor_api_router
from integrations.intuit.qbo.vendor.web.controller import router as qbo_vendor_web_router
from integrations.intuit.qbo.client.api.router import router as qbo_client_api_router
from integrations.intuit.qbo.client.web.controller import router as qbo_client_web_router


def setup_logging():
    """Configure logging based on settings."""
    settings = config.Settings()
    
    # Get root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
    
    # Remove existing handlers to avoid duplicates
    root_logger.handlers.clear()
    
    # Create formatter
    formatter = logging.Formatter(
        fmt='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Console handler (if enabled)
    if settings.log_console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)
    
    # File handler (if configured)
    if settings.log_file:
        try:
            # In Azure App Service, use writable location if path is not writable
            is_azure = bool(os.getenv("WEBSITE_INSTANCE_ID"))
            if is_azure and settings.log_file.startswith("/var/log/"):
                # Azure App Service: /var/log/ is read-only, use /home/LogFiles/ instead
                log_file = settings.log_file.replace("/var/log/", "/home/LogFiles/")
                logging.info(f"Azure detected: Redirecting log file from {settings.log_file} to {log_file}")
            else:
                log_file = settings.log_file
            
            # Create directory if it doesn't exist
            log_path = Path(log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            
            file_handler = logging.FileHandler(log_file)
            file_handler.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
            file_handler.setFormatter(formatter)
            root_logger.addHandler(file_handler)
        except (PermissionError, OSError) as e:
            # Fall back to console logging only if file logging fails
            logging.warning(
                f"Cannot create log file at {settings.log_file}: {e}. "
                "Falling back to console logging only. "
                "In Azure App Service, logs are automatically captured from stdout/stderr."
            )
    
    # Set uvicorn loggers to same level (if uvicorn is available)
    # These loggers exist even if uvicorn isn't imported, so this is safe
    try:
        logging.getLogger("uvicorn").setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
        logging.getLogger("uvicorn.access").setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
    except Exception:
        # Ignore if uvicorn loggers don't exist
        pass
    
    logging.info(f"Logging initialized - Level: {settings.log_level}, Console: {settings.log_console}, File: {settings.log_file or 'None'}")


# Setup logging first
setup_logging()


class ProxyHeadersMiddleware(BaseHTTPMiddleware):
    """Fix request URL scheme when behind a reverse proxy (Azure App Service)."""
    async def dispatch(self, request: Request, call_next):
        # Check for X-Forwarded-Proto header (set by Azure App Service)
        forwarded_proto = request.headers.get("X-Forwarded-Proto")
        if forwarded_proto == "https":
            # Update the request URL to use HTTPS
            request.scope["scheme"] = "https"
        
        response = await call_next(request)
        return response


app = FastAPI()

# Only enable proxy headers middleware in production (Azure App Service)
# Azure App Service sets WEBSITE_INSTANCE_ID when running in Azure
if os.getenv("WEBSITE_INSTANCE_ID"):
    # In Azure, trust forwarded headers from Azure's infrastructure
    # Azure App Service handles HTTPS termination and sets these headers securely
    app.add_middleware(ProxyHeadersMiddleware)
else:
    # In development, also enable for local testing behind proxies
    app.add_middleware(ProxyHeadersMiddleware)

app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(address_api_router)
app.include_router(address_web_router)
app.include_router(address_type_api_router)
app.include_router(address_type_web_router)
app.include_router(auth_api_router)
app.include_router(auth_web_router)
app.include_router(integration_api_router)
app.include_router(integration_web_router)
app.include_router(organization_api_router)
app.include_router(organization_web_router)
app.include_router(company_api_router)
app.include_router(company_web_router)
app.include_router(cost_code_api_router)
app.include_router(cost_code_web_router)
app.include_router(sub_cost_code_api_router)
app.include_router(sub_cost_code_web_router)
app.include_router(module_api_router)
app.include_router(module_web_router)
app.include_router(user_api_router)
app.include_router(user_web_router)
app.include_router(user_role_api_router)
app.include_router(user_role_web_router)
app.include_router(vendor_api_router)
app.include_router(vendor_web_router)
app.include_router(vendor_address_api_router)
app.include_router(vendor_address_web_router)
app.include_router(intuit_qbo_auth_api_router)
app.include_router(sync_api_router)
app.include_router(sync_web_router)
app.include_router(qbo_vendor_api_router)
app.include_router(qbo_vendor_web_router)
app.include_router(qbo_client_api_router)
app.include_router(qbo_client_web_router)
app.include_router(vendor_type_api_router)
app.include_router(vendor_type_web_router)
app.include_router(taxpayer_api_router)
app.include_router(taxpayer_web_router)
app.include_router(role_module_api_router)
app.include_router(role_module_web_router)
app.include_router(dashboard_web_router)
app.include_router(attachment_api_router)
app.include_router(attachment_web_router)
app.include_router(taxpayer_attachment_api_router)
app.include_router(taxpayer_attachment_web_router)
app.include_router(legal_web_router)


def get_settings():
    return config.Settings()


@app.get("/")
def root():
    return RedirectResponse(url="/dashboard")


@app.get("/ping")
def ping():
    return {"message": "pong"}


@app.get("/info")
async def info(settings: Annotated[config.Settings, Depends(get_settings)]):
    return {
        "api_version": settings.api_version,
        "host": settings.host,
        "port": settings.port
    }
