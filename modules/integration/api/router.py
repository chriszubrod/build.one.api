# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends

# Local Imports
from integrations.intuit.qbo.auth.external.client import (
    connect_intuit_oauth_2_endpoint,
    connect_intuit_oauth_2_token_endpoint,
    connect_intuit_oauth_2_token_endpoint_refresh,
    connect_intuit_oauth_2_token_endpoint_revoke
)
from modules.integration.api.schemas import IntegrationCreate, IntegrationUpdate
from modules.integration.business.service import IntegrationService
from modules.auth.business.service import get_current_user_api

router = APIRouter(prefix="/api/v1", tags=["api", "integration"])


@router.post("/create/integration")
def create_integration_router(body: IntegrationCreate, current_user: dict = Depends(get_current_user_api)):
    """
    Create a new integration.
    """
    integration = IntegrationService().create(
        name=body.name,
        status=body.status,
        endpoint=body.endpoint
    )
    return integration.to_dict()


@router.get("/get/integrations")
def get_integrations_router(current_user: dict = Depends(get_current_user_api)):
    """
    Read all integrations.
    """
    integrations = IntegrationService().read_all()
    return [integration.to_dict() for integration in integrations]


@router.get("/get/integration/{public_id}")
def get_integration_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Read a integration by public ID.
    """
    integration = IntegrationService().read_by_public_id(public_id=public_id)
    return integration.to_dict()


@router.put("/update/integration/{public_id}")
def update_integration_by_public_id_router(public_id: str, body: IntegrationUpdate, current_user: dict = Depends(get_current_user_api)):
    """
    Update a integration by public ID.
    """
    integration = IntegrationService().update_by_public_id(public_id=public_id, integration=body)
    return integration.to_dict()


@router.delete("/delete/integration/{public_id}")
def delete_integration_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Delete a integration by public ID.
    """
    integration = IntegrationService().delete_by_public_id(public_id=public_id)
    return integration.to_dict()


@router.post("/connect/integration/{public_id}")
def connect_integration_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Connect an integration.
    """
    print('Connect integration router called with public_id:', public_id)
    integration = IntegrationService().read_by_public_id(public_id=public_id)
    print('Integration:', integration.to_dict())
    return integration.to_dict()
