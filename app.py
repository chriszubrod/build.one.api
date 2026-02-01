# Python Standard Library Imports
import logging
import os
import sys
from pathlib import Path
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

# Third-party Imports
from fastapi import Depends, FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from typing_extensions import Annotated

# Local Imports
import config
from services.auth.business.service import WebAuthenticationRequired

from services.address.api.router import router as address_api_router
from services.address.web.controller import router as address_web_router
from services.address_type.api.router import router as address_type_api_router
from services.address_type.web.controller import router as address_type_web_router
from services.auth.api.router import router as auth_api_router
from services.auth.web.controller import router as auth_web_router
from services.customer.api.router import router as customer_api_router
from services.customer.web.controller import router as customer_web_router
from services.integration.api.router import router as integration_api_router
from services.integration.web.controller import router as integration_web_router
from services.organization.api.router import router as organization_api_router
from services.organization.web.controller import router as organization_web_router
from services.company.api.router import router as company_api_router
from services.company.web.controller import router as company_web_router
from services.cost_code.api.router import router as cost_code_api_router
from services.cost_code.web.controller import router as cost_code_web_router
from services.sub_cost_code.api.router import router as sub_cost_code_api_router
from services.sub_cost_code.web.controller import router as sub_cost_code_web_router
from services.module.api.router import router as module_api_router
from services.module.web.controller import router as module_web_router
from services.project.api.router import router as project_api_router
from services.project.web.controller import router as project_web_router
from services.user.api.router import router as user_api_router
from services.user.web.controller import router as user_web_router
from services.user_role.api.router import router as user_role_api_router
from services.user_role.web.controller import router as user_role_web_router
from services.vendor.api.router import router as vendor_api_router
from services.vendor.web.controller import router as vendor_web_router
from services.vendor_address.api.router import router as vendor_address_api_router
from services.vendor_address.web.controller import router as vendor_address_web_router
from services.vendor_type.api.router import router as vendor_type_api_router
from services.vendor_type.web.controller import router as vendor_type_web_router
from services.taxpayer.api.router import router as taxpayer_api_router
from services.taxpayer.web.controller import router as taxpayer_web_router
from services.role_module.api.router import router as role_module_api_router
from services.role_module.web.controller import router as role_module_web_router
from services.admin.api.router import router as admin_api_router
from services.admin.web.controller import router as admin_web_router
from services.dashboard.api.router import router as dashboard_api_router
from services.dashboard.web.controller import router as dashboard_web_router
from services.attachment.api.router import router as attachment_api_router
from services.attachment.web.controller import router as attachment_web_router
from services.bill.api.router import router as bill_api_router
from services.bill.web.controller import router as bill_web_router
from services.bill_line_item.api.router import router as bill_line_item_api_router
from services.bill_line_item_attachment.api.router import router as bill_line_item_attachment_api_router
from services.expense.api.router import router as expense_api_router
from services.expense.web.controller import router as expense_web_router
from services.expense_line_item.api.router import router as expense_line_item_api_router
from services.expense_line_item_attachment.api.router import router as expense_line_item_attachment_api_router
from services.bill_credit.api.router import router as bill_credit_api_router
from services.bill_credit.web.controller import router as bill_credit_web_router
from services.bill_credit_line_item.api.router import router as bill_credit_line_item_api_router
from services.bill_credit_line_item_attachment.api.router import router as bill_credit_line_item_attachment_api_router
from services.taxpayer_attachment.api.router import router as taxpayer_attachment_api_router
from services.taxpayer_attachment.web.controller import router as taxpayer_attachment_web_router
from services.legal.web.controller import router as legal_web_router
from services.payment_term.api.router import router as payment_term_api_router
from services.payment_term.web.controller import router as payment_term_web_router
from services.contract_labor.api.router import router as contract_labor_api_router
from services.contract_labor.web.controller import router as contract_labor_web_router
from services.search.api.router import router as search_api_router
from services.qa.api.router import router as qa_api_router
from services.anomaly.api.router import router as anomaly_api_router
from services.categorization.api.router import router as categorization_api_router
from services.copilot.api.router import router as copilot_api_router
from services.tasks.api.router import router as tasks_api_router
from services.tasks.web.controller import router as tasks_web_router

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
from integrations.intuit.qbo.vendorcredit.api.router import router as qbo_vendorcredit_api_router


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
app.include_router(user_role_api_router)
app.include_router(user_role_web_router)
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
app.include_router(qbo_vendorcredit_api_router)
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
app.include_router(admin_api_router)
app.include_router(admin_web_router)
app.include_router(dashboard_api_router)
app.include_router(dashboard_web_router)
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
app.include_router(payment_term_web_router)
app.include_router(contract_labor_api_router)
app.include_router(contract_labor_web_router)
app.include_router(search_api_router)
app.include_router(qa_api_router)
app.include_router(anomaly_api_router)
app.include_router(categorization_api_router)
app.include_router(copilot_api_router)
app.include_router(tasks_api_router)
app.include_router(tasks_web_router)


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
