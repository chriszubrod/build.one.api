# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status

# Local Imports
from entities.contact.api.schemas import ContactCreate, ContactUpdate
from entities.contact.business.service import ContactService
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from workflows.workflow.api.process_engine import ProcessEngine, TriggerContext, EventType, Channel
from shared.api.responses import list_response, item_response, raise_workflow_error

router = APIRouter(prefix="/api/v1", tags=["api", "contact"])


@router.post("/create/contact")
def create_contact_router(body: ContactCreate, current_user: dict = Depends(require_module_api(Modules.VENDORS, "can_create"))):
    """
    Create a new contact.

    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "email": body.email,
            "office_phone": body.office_phone,
            "mobile_phone": body.mobile_phone,
            "fax": body.fax,
            "notes": body.notes,
            "user_id": body.user_id,
            "company_id": body.company_id,
            "customer_id": body.customer_id,
            "project_id": body.project_id,
            "vendor_id": body.vendor_id,
        },
        workflow_type="contact_create",
    )

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to create contact")

    return item_response(result.get("data"))


@router.get("/get/contacts")
def get_contacts_router(current_user: dict = Depends(require_module_api(Modules.VENDORS))):
    """
    Read all contacts.
    """
    contacts = ContactService().read_all()
    return list_response([contact.to_dict() for contact in contacts])


@router.get("/get/contact/{public_id}")
def get_contact_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.VENDORS))):
    """
    Read a contact by public ID.
    """
    contact = ContactService().read_by_public_id(public_id=public_id)
    return item_response(contact.to_dict())


@router.get("/get/contacts/user/{user_id}")
def get_contacts_by_user_id_router(user_id: int, current_user: dict = Depends(require_module_api(Modules.VENDORS))):
    """
    Read contacts by user ID.
    """
    contacts = ContactService().read_by_user_id(user_id=user_id)
    return list_response([contact.to_dict() for contact in contacts])


@router.get("/get/contacts/company/{company_id}")
def get_contacts_by_company_id_router(company_id: int, current_user: dict = Depends(require_module_api(Modules.VENDORS))):
    """
    Read contacts by company ID.
    """
    contacts = ContactService().read_by_company_id(company_id=company_id)
    return list_response([contact.to_dict() for contact in contacts])


@router.get("/get/contacts/customer/{customer_id}")
def get_contacts_by_customer_id_router(customer_id: int, current_user: dict = Depends(require_module_api(Modules.VENDORS))):
    """
    Read contacts by customer ID.
    """
    contacts = ContactService().read_by_customer_id(customer_id=customer_id)
    return list_response([contact.to_dict() for contact in contacts])


@router.get("/get/contacts/project/{project_id}")
def get_contacts_by_project_id_router(project_id: int, current_user: dict = Depends(require_module_api(Modules.VENDORS))):
    """
    Read contacts by project ID.
    """
    contacts = ContactService().read_by_project_id(project_id=project_id)
    return list_response([contact.to_dict() for contact in contacts])


@router.get("/get/contacts/vendor/{vendor_id}")
def get_contacts_by_vendor_id_router(vendor_id: int, current_user: dict = Depends(require_module_api(Modules.VENDORS))):
    """
    Read contacts by vendor ID.
    """
    contacts = ContactService().read_by_vendor_id(vendor_id=vendor_id)
    return list_response([contact.to_dict() for contact in contacts])


@router.put("/update/contact/{public_id}")
def update_contact_by_public_id_router(public_id: str, body: ContactUpdate, current_user: dict = Depends(require_module_api(Modules.VENDORS, "can_update"))):
    """
    Update a contact by public ID.

    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "public_id": public_id,
            "row_version": body.row_version,
            "email": body.email,
            "office_phone": body.office_phone,
            "mobile_phone": body.mobile_phone,
            "fax": body.fax,
            "notes": body.notes,
        },
        workflow_type="contact_update",
    )

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to update contact")

    return item_response(result.get("data"))


@router.delete("/delete/contact/{public_id}")
def delete_contact_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.VENDORS, "can_delete"))):
    """
    Delete a contact by public ID.

    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "public_id": public_id,
        },
        workflow_type="contact_delete",
    )

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to delete contact")

    return item_response(result.get("data"))
