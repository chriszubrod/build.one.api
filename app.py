# Python Standard Library Imports
import logging
import os
import config
from urllib.parse import quote
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

# Azure Application Insights / OpenTelemetry instrumentation.
# Activates only when APPLICATIONINSIGHTS_CONNECTION_STRING is set (prod App
# Service). Auto-instruments FastAPI requests, outbound httpx calls (QBO),
# Python logging (including structured `extra={}` fields which surface as
# customDimensions in App Insights), and Python exceptions. No code changes
# needed in request handlers or the QBO client — the instrumentation sees
# everything through the stdlib layers we already use.
if os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING"):
    from azure.monitor.opentelemetry import configure_azure_monitor
    configure_azure_monitor(logger_name="", enable_live_metrics=False)

# Third-party Imports
from fastapi import Depends, FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from typing_extensions import Annotated

# Local Imports
from entities.auth.business.service import (
    RefreshRequired,
    WebAuthenticationRequired,
    get_current_user_api,
)

from entities.address.api.router import router as address_api_router
from entities.address_type.api.router import router as address_type_api_router
from entities.auth.api.router import router as auth_api_router
from entities.auth.web.controller import router as auth_web_router
from entities.customer.api.router import router as customer_api_router
from entities.integration.api.router import router as integration_api_router
from entities.integration.web.controller import router as integration_web_router
from entities.organization.api.router import router as organization_api_router
from entities.company.api.router import router as company_api_router
from entities.cost_code.api.router import router as cost_code_api_router
from entities.sub_cost_code.api.router import router as sub_cost_code_api_router
from entities.module.api.router import router as module_api_router
from entities.project.api.router import router as project_api_router
from entities.user.api.router import router as user_api_router
from entities.role.api.router import router as role_api_router
from entities.user_role.api.router import router as user_role_api_router
from entities.user_project.api.router import router as user_project_api_router
from entities.vendor.api.router import router as vendor_api_router
from entities.vendor_address.api.router import router as vendor_address_api_router
from entities.vendor_type.api.router import router as vendor_type_api_router
from entities.taxpayer.api.router import router as taxpayer_api_router
from entities.role_module.api.router import router as role_module_api_router
from entities.user_module.api.router import router as user_module_api_router
from entities.contact.api.router import router as contact_api_router
from entities.admin.api.router import router as admin_api_router
from entities.admin.web.controller import router as admin_web_router
from entities.dashboard.api.router import router as dashboard_api_router
from entities.attachment.api.router import router as attachment_api_router
from entities.attachment.web.controller import router as attachment_web_router
from entities.bill.api.router import router as bill_api_router
from entities.bill.web.controller import router as bill_web_router
from entities.bill_line_item.api.router import router as bill_line_item_api_router
from entities.bill_line_item_attachment.api.router import router as bill_line_item_attachment_api_router
from entities.expense.api.router import router as expense_api_router
from entities.expense.web.controller import router as expense_web_router
from entities.expense_line_item.api.router import router as expense_line_item_api_router
from entities.expense_line_item_attachment.api.router import router as expense_line_item_attachment_api_router
from entities.bill_credit.api.router import router as bill_credit_api_router
from entities.bill_credit.web.controller import router as bill_credit_web_router
from entities.bill_credit_line_item.api.router import router as bill_credit_line_item_api_router
from entities.bill_credit_line_item_attachment.api.router import router as bill_credit_line_item_attachment_api_router
from entities.taxpayer_attachment.api.router import router as taxpayer_attachment_api_router
from entities.taxpayer_attachment.web.controller import router as taxpayer_attachment_web_router
from entities.legal.web.controller import router as legal_web_router
from entities.payment_term.api.router import router as payment_term_api_router
from entities.contract_labor.api.router import router as contract_labor_api_router
from entities.contract_labor.web.controller import router as contract_labor_web_router
from entities.time_entry.api.router import router as time_entry_api_router
from entities.time_entry.api.router import time_log_router as time_log_api_router
from entities.invoice.api.router import router as invoice_api_router
from entities.invoice.web.controller import router as invoice_web_router
from entities.invoice_line_item.api.router import router as invoice_line_item_api_router
from entities.invoice_line_item.web.controller import router as invoice_line_item_web_router
from entities.invoice_attachment.api.router import router as invoice_attachment_api_router
from entities.invoice_attachment.web.controller import router as invoice_attachment_web_router
from entities.invoice_line_item_attachment.api.router import router as invoice_line_item_attachment_api_router
from entities.invoice_line_item_attachment.web.controller import router as invoice_line_item_attachment_web_router
from entities.review_status.api.router import router as review_status_api_router
from core.workflow.api.pending_action_router import router as pending_action_api_router
from shared.api.lookups import router as lookups_api_router
from shared.api.admin import router as scheduler_admin_api_router

# Intelligence layer — import scout's package to register agent + tools, then
# include its HTTP router for the SSE/run endpoints.
import intelligence.agents.scout  # noqa: F401 — triggers registration of scout + sub-agents
from intelligence.api.router import router as intelligence_api_router

from integrations.intuit.qbo.auth.api.router import router as intuit_qbo_auth_api_router
from integrations.intuit.qbo.company_info.api.router import router as intuit_qbo_company_info_api_router
from integrations.intuit.qbo.physical_address.api.router import router as intuit_qbo_physical_address_api_router
from integrations.sync.api.router import router as sync_api_router
from integrations.sync.web.controller import router as sync_web_router
from integrations.intuit.qbo.vendor.api.router import router as qbo_vendor_api_router
from integrations.intuit.qbo.vendor.web.controller import router as qbo_vendor_web_router
from integrations.intuit.qbo.client.api.router import router as qbo_client_api_router
from integrations.intuit.qbo.client.web.controller import router as qbo_client_web_router
from integrations.ms.auth.api.router import router as ms_auth_api_router
from integrations.ms.sharepoint.site.api.router import router as ms_sharepoint_site_api_router
from integrations.ms.sharepoint.drive.api.router import router as ms_sharepoint_drive_api_router
from integrations.ms.sharepoint.driveitem.api.router import router as ms_sharepoint_driveitem_api_router
from integrations.ms.mail.message.api.router import router as ms_mail_api_router
from integrations.intuit.qbo.account.api.router import router as qbo_account_api_router
from integrations.intuit.qbo.purchase.api.router import router as qbo_purchase_api_router
from integrations.intuit.qbo.purchase.web.controller import router as qbo_purchase_web_router
from integrations.intuit.qbo.vendorcredit.api.router import router as qbo_vendorcredit_api_router
from integrations.intuit.qbo.bill.api.router import router as qbo_bill_api_router
from integrations.intuit.qbo.invoice.api.router import router as qbo_invoice_api_router


logger = logging.getLogger(__name__)

app = FastAPI()


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


# Enable proxy headers middleware to handle HTTPS correctly in Azure
app.add_middleware(ProxyHeadersMiddleware)

# CORS — allow the React dev server and deployed web app to call this API
from fastapi.middleware.cors import CORSMiddleware

_cors_origins_env = os.getenv("CORS_ALLOW_ORIGINS", "")
_default_origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]
_allowed_origins = [o.strip() for o in _cors_origins_env.split(",") if o.strip()] or _default_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RefreshRequired)
async def refresh_required_handler(request: Request, exc: RefreshRequired):
    """
    Access token expired but refresh token may be valid.
    Redirect to web refresh endpoint to restore session then continue to requested page.
    """
    return RedirectResponse(url=f"/auth/refresh?next={quote(exc.next_path, safe='/')}", status_code=303)


@app.exception_handler(WebAuthenticationRequired)
async def web_authentication_required_handler(request: Request, exc: WebAuthenticationRequired):
    """
    Exception handler for web authentication failures.
    Redirects to login page when authentication is required.
    """
    return RedirectResponse(url="/auth/login", status_code=303)

app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(address_api_router)
app.include_router(address_type_api_router)
app.include_router(auth_api_router)
app.include_router(auth_web_router)
app.include_router(customer_api_router)
app.include_router(integration_api_router)
app.include_router(integration_web_router)
app.include_router(organization_api_router)
app.include_router(company_api_router)
app.include_router(cost_code_api_router)
app.include_router(sub_cost_code_api_router)
app.include_router(module_api_router)
app.include_router(project_api_router)
app.include_router(user_api_router)
app.include_router(role_api_router)
app.include_router(user_role_api_router)
app.include_router(user_project_api_router)
app.include_router(contact_api_router)
app.include_router(vendor_api_router)
app.include_router(vendor_address_api_router)
app.include_router(intuit_qbo_auth_api_router)
app.include_router(intuit_qbo_company_info_api_router)
app.include_router(intuit_qbo_physical_address_api_router)
app.include_router(sync_api_router)
app.include_router(sync_web_router)
app.include_router(qbo_vendor_api_router)
app.include_router(qbo_vendor_web_router)
app.include_router(qbo_client_api_router)
app.include_router(qbo_client_web_router)
app.include_router(qbo_account_api_router)
app.include_router(qbo_purchase_api_router)
app.include_router(qbo_purchase_web_router)
app.include_router(qbo_vendorcredit_api_router)
app.include_router(qbo_bill_api_router)
app.include_router(qbo_invoice_api_router)
app.include_router(ms_auth_api_router)
app.include_router(ms_sharepoint_site_api_router)
app.include_router(ms_sharepoint_drive_api_router)
app.include_router(ms_sharepoint_driveitem_api_router)
app.include_router(ms_mail_api_router)
app.include_router(vendor_type_api_router)
app.include_router(taxpayer_api_router)
app.include_router(role_module_api_router)
app.include_router(user_module_api_router)
app.include_router(admin_api_router)
app.include_router(admin_web_router)
app.include_router(dashboard_api_router)
app.include_router(attachment_api_router)
app.include_router(attachment_web_router)
app.include_router(bill_api_router)
app.include_router(bill_web_router)
app.include_router(bill_line_item_api_router)
app.include_router(bill_line_item_attachment_api_router)
app.include_router(expense_api_router)
app.include_router(expense_web_router)
app.include_router(expense_line_item_api_router)
app.include_router(expense_line_item_attachment_api_router)
app.include_router(bill_credit_api_router)
app.include_router(bill_credit_web_router)
app.include_router(bill_credit_line_item_api_router)
app.include_router(bill_credit_line_item_attachment_api_router)
app.include_router(taxpayer_attachment_api_router)
app.include_router(taxpayer_attachment_web_router)
app.include_router(legal_web_router)
app.include_router(payment_term_api_router)
app.include_router(contract_labor_api_router)
app.include_router(contract_labor_web_router)
app.include_router(time_entry_api_router)
app.include_router(time_log_api_router)
app.include_router(invoice_api_router)
app.include_router(invoice_web_router)
app.include_router(invoice_line_item_api_router)
app.include_router(invoice_line_item_web_router)
app.include_router(invoice_attachment_api_router)
app.include_router(invoice_attachment_web_router)
app.include_router(invoice_line_item_attachment_api_router)
app.include_router(invoice_line_item_attachment_web_router)
app.include_router(review_status_api_router)
app.include_router(pending_action_api_router)
app.include_router(lookups_api_router)
app.include_router(scheduler_admin_api_router)
app.include_router(intelligence_api_router)


@app.on_event("startup")
async def startup_event():
    # RBAC module validation
    from shared.rbac import validate_module_constants
    rbac_warnings = validate_module_constants()
    for w in rbac_warnings:
        logger.warning(w)
    if not rbac_warnings:
        logger.info("RBAC startup validation passed — all module constants match database records.")

    # Capture the running event loop so sync mutation hooks can safely
    # dispatch profile-change events into the SSE subscriber queues.
    from shared.profile_events import register_event_loop
    register_event_loop()

    # Start the recurring-jobs scheduler (QBO outbox drain, etc.). Gated on
    # ENABLE_SCHEDULER=true so local dev runs silently by default; prod App
    # Service sets the flag in Application Settings.
    from shared.scheduler import start_scheduler
    start_scheduler()


@app.on_event("shutdown")
async def shutdown_event():
    # Stop the scheduler cleanly so in-flight outbox drains finish.
    from shared.scheduler import shutdown_scheduler
    shutdown_scheduler()


def get_settings():
    return config.Settings()


@app.get("/")
def root():
    return RedirectResponse(url="/dashboard")


@app.get("/ping")
def ping():
    return {"message": "pong"}


@app.get("/info")
async def info(
    settings: Annotated[config.Settings, Depends(get_settings)],
    current_user: dict = Depends(get_current_user_api),
):
    return {
        "api_version": settings.api_version,
        "host": settings.host,
        "port": settings.port
    }
