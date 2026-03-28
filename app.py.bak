# Python Standard Library Imports
import config
from urllib.parse import quote
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

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
from entities.address.web.controller import router as address_web_router
from entities.address_type.api.router import router as address_type_api_router
from entities.address_type.web.controller import router as address_type_web_router
from entities.auth.api.router import router as auth_api_router
from entities.auth.web.controller import router as auth_web_router
from entities.customer.api.router import router as customer_api_router
from entities.customer.web.controller import router as customer_web_router
from entities.integration.api.router import router as integration_api_router
from entities.integration.web.controller import router as integration_web_router
from entities.organization.api.router import router as organization_api_router
from entities.organization.web.controller import router as organization_web_router
from entities.company.api.router import router as company_api_router
from entities.company.web.controller import router as company_web_router
from entities.cost_code.api.router import router as cost_code_api_router
from entities.cost_code.web.controller import router as cost_code_web_router
from entities.sub_cost_code.api.router import router as sub_cost_code_api_router
from entities.sub_cost_code.web.controller import router as sub_cost_code_web_router
from entities.module.api.router import router as module_api_router
from entities.module.web.controller import router as module_web_router
from entities.project.api.router import router as project_api_router
from entities.project.web.controller import router as project_web_router
from entities.user.api.router import router as user_api_router
from entities.user.web.controller import router as user_web_router
from entities.role.api.router import router as role_api_router
from entities.role.web.controller import router as role_web_router
from entities.user_role.api.router import router as user_role_api_router
from entities.user_role.web.controller import router as user_role_web_router
from entities.user_project.api.router import router as user_project_api_router
from entities.user_project.web.controller import router as user_project_web_router
from entities.vendor.api.router import router as vendor_api_router
from entities.vendor.web.controller import router as vendor_web_router
from entities.vendor_address.api.router import router as vendor_address_api_router
from entities.vendor_address.web.controller import router as vendor_address_web_router
from entities.vendor_type.api.router import router as vendor_type_api_router
from entities.vendor_type.web.controller import router as vendor_type_web_router
from entities.taxpayer.api.router import router as taxpayer_api_router
from entities.taxpayer.web.controller import router as taxpayer_web_router
from entities.role_module.api.router import router as role_module_api_router
from entities.role_module.web.controller import router as role_module_web_router
from entities.user_module.api.router import router as user_module_api_router
from entities.user_module.web.controller import router as user_module_web_router
from entities.contact.api.router import router as contact_api_router
from entities.admin.api.router import router as admin_api_router
from entities.admin.web.controller import router as admin_web_router
from entities.dashboard.api.router import router as dashboard_api_router
from entities.dashboard.web.controller import router as dashboard_web_router
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
from entities.payment_term.web.controller import router as payment_term_web_router
from entities.contract_labor.api.router import router as contract_labor_api_router
from entities.contract_labor.web.controller import router as contract_labor_web_router
from entities.invoice.api.router import router as invoice_api_router
from entities.invoice.web.controller import router as invoice_web_router
from entities.invoice_line_item.api.router import router as invoice_line_item_api_router
from entities.invoice_line_item.web.controller import router as invoice_line_item_web_router
from entities.invoice_attachment.api.router import router as invoice_attachment_api_router
from entities.invoice_attachment.web.controller import router as invoice_attachment_web_router
from entities.invoice_line_item_attachment.api.router import router as invoice_line_item_attachment_api_router
from entities.invoice_line_item_attachment.web.controller import router as invoice_line_item_attachment_web_router
from entities.search.api.router import router as search_api_router
from entities.qa.api.router import router as qa_api_router
from entities.anomaly.api.router import router as anomaly_api_router
from entities.categorization.api.router import router as categorization_api_router
from entities.copilot.api.router import router as copilot_api_router
from entities.inbox.web.controller import router as inbox_web_router
from entities.classification_override.api.router import router as classification_override_api_router
from entities.classification_override.web.controller import router as classification_override_web_router
from core.workflow.api.pending_action_router import router as pending_action_api_router
from core.ai.agents.vendor_agent.api.router import router as vendor_agent_api_router
from core.ai.agents.bill_agent.api.router import router as bill_agent_api_router
from core.ai.agents.expense_agent.api.router import router as expense_agent_api_router
from core.ai.agents.expense_categorization.api.router import router as expense_categorization_api_router

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
app.include_router(address_web_router)
app.include_router(address_type_api_router)
app.include_router(address_type_web_router)
app.include_router(auth_api_router)
app.include_router(auth_web_router)
app.include_router(customer_api_router)
app.include_router(customer_web_router)
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
app.include_router(project_api_router)
app.include_router(project_web_router)
app.include_router(user_api_router)
app.include_router(user_web_router)
app.include_router(role_api_router)
app.include_router(role_web_router)
app.include_router(user_role_api_router)
app.include_router(user_role_web_router)
app.include_router(user_project_api_router)
app.include_router(user_project_web_router)
app.include_router(contact_api_router)
app.include_router(vendor_api_router)
app.include_router(vendor_web_router)
app.include_router(vendor_address_api_router)
app.include_router(vendor_address_web_router)
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
app.include_router(vendor_type_web_router)
app.include_router(taxpayer_api_router)
app.include_router(taxpayer_web_router)
app.include_router(role_module_api_router)
app.include_router(role_module_web_router)
app.include_router(user_module_api_router)
app.include_router(user_module_web_router)
app.include_router(admin_api_router)
app.include_router(admin_web_router)
app.include_router(dashboard_api_router)
app.include_router(dashboard_web_router)
app.include_router(attachment_api_router)
app.include_router(attachment_web_router)
app.include_router(inbox_web_router)
app.include_router(classification_override_api_router)
app.include_router(classification_override_web_router)
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
app.include_router(payment_term_web_router)
app.include_router(contract_labor_api_router)
app.include_router(contract_labor_web_router)
app.include_router(invoice_api_router)
app.include_router(invoice_web_router)
app.include_router(invoice_line_item_api_router)
app.include_router(invoice_line_item_web_router)
app.include_router(invoice_attachment_api_router)
app.include_router(invoice_attachment_web_router)
app.include_router(invoice_line_item_attachment_api_router)
app.include_router(invoice_line_item_attachment_web_router)
app.include_router(search_api_router)
app.include_router(qa_api_router)
app.include_router(anomaly_api_router)
app.include_router(categorization_api_router)
app.include_router(copilot_api_router)
app.include_router(pending_action_api_router)
app.include_router(vendor_agent_api_router)
app.include_router(bill_agent_api_router)
app.include_router(expense_agent_api_router)
app.include_router(expense_categorization_api_router)


@app.on_event("startup")
async def startup_event():
    from core.ai.agents.bill_agent.scheduler import start_scheduler
    start_scheduler()


@app.on_event("shutdown")
async def shutdown_event():
    from core.ai.agents.bill_agent.scheduler import stop_scheduler
    stop_scheduler()


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
