# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends

# Local Imports
from integrations.intuit.qbo.client.api.schemas import QboClientCreate, QboClientUpdate
from integrations.intuit.qbo.client.business.service import QboClientService
from modules.auth.business.service import get_current_user_api as get_current_qbo_client_api

router = APIRouter(prefix="/api/v1", tags=["api", "qbo-client"])
service = QboClientService()


@router.post("/create/qbo-client")
def create_qbo_client_router(body: QboClientCreate, current_user: dict = Depends(get_current_qbo_client_api)):
    """
    Create a new QBO client.
    """
    qbo_client = service.create(
        client_id=body.client_id,
        client_secret=body.client_secret,
    )
    return qbo_client.to_dict()


@router.get("/get/qbo-clients")
def get_qbo_clients_router(current_user: dict = Depends(get_current_qbo_client_api)):
    """
    Read all QBO clients.
    """
    qbo_clients = service.read_all()
    return [qbo_client.to_dict() for qbo_client in qbo_clients]


@router.get("/get/qbo-client/{client_id}")
def get_qbo_client_by_client_id_router(client_id: str, current_user: dict = Depends(get_current_qbo_client_api)):
    """
    Read a QBO client by client ID.
    """
    qbo_client = service.read_by_client_id(client_id)
    return qbo_client.to_dict()


@router.put("/update/qbo-client/{client_id}")
def update_qbo_client_by_client_id_router(client_id: str, body: QboClientUpdate, current_user: dict = Depends(get_current_qbo_client_api)):
    """
    Update a QBO client by client ID.
    """
    qbo_client = service.update_by_client_id(client_id, body.client_secret)
    return qbo_client.to_dict()


@router.delete("/delete/qbo-client/{client_id}")
def delete_qbo_client_by_client_id_router(client_id: str, current_user: dict = Depends(get_current_qbo_client_api)):
    """
    Delete a QBO client by client ID.
    """
    qbo_client = service.delete_by_client_id(client_id)
    return qbo_client.to_dict()
