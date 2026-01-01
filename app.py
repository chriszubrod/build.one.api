# Python Standard Library Imports

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

from integrations.intuit.qbo.auth.api.router import router as intuit_qbo_auth_api_router
from integrations.sync.api.router import router as sync_api_router
from integrations.sync.web.controller import router as sync_web_router
from integrations.intuit.qbo.vendor.api.router import router as qbo_vendor_api_router
from integrations.intuit.qbo.vendor.web.controller import router as qbo_vendor_web_router
from integrations.intuit.qbo.client.api.router import router as qbo_client_api_router
from integrations.intuit.qbo.client.web.controller import router as qbo_client_web_router


app = FastAPI()

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
