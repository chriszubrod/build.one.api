# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status

# Local Imports
from entities.contact.api.schemas import ContactCreate, ContactUpdate
from entities.contact.business.service import ContactService
from entities.auth.business.service import get_current_user_api
from workflows.workflow.api.router import TriggerRouter, TriggerContext, TriggerType, TriggerSource

router = APIRouter(prefix="/api/v1", tags=["api", "contact"])


@router.post("/create/contact")
def create_contact_router(body: ContactCreate, current_user: dict = Depends(get_current_user_api)):
    """
    Create a new contact.

    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=TriggerType.API_CALL,
        trigger_source=TriggerSource.API,
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

    result = TriggerRouter().route_instant(context)

    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to create contact")
        )

    return result.get("data")


@router.get("/get/contacts")
def get_contacts_router(current_user: dict = Depends(get_current_user_api)):
    """
    Read all contacts.
    """
    contacts = ContactService().read_all()
    return [contact.to_dict() for contact in contacts]


@router.get("/get/contact/{public_id}")
def get_contact_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Read a contact by public ID.
    """
    contact = ContactService().read_by_public_id(public_id=public_id)
    return contact.to_dict()


@router.get("/get/contacts/user/{user_id}")
def get_contacts_by_user_id_router(user_id: int, current_user: dict = Depends(get_current_user_api)):
    """
    Read contacts by user ID.
    """
    contacts = ContactService().read_by_user_id(user_id=user_id)
    return [contact.to_dict() for contact in contacts]


@router.get("/get/contacts/company/{company_id}")
def get_contacts_by_company_id_router(company_id: int, current_user: dict = Depends(get_current_user_api)):
    """
    Read contacts by company ID.
    """
    contacts = ContactService().read_by_company_id(company_id=company_id)
    return [contact.to_dict() for contact in contacts]


@router.get("/get/contacts/customer/{customer_id}")
def get_contacts_by_customer_id_router(customer_id: int, current_user: dict = Depends(get_current_user_api)):
    """
    Read contacts by customer ID.
    """
    contacts = ContactService().read_by_customer_id(customer_id=customer_id)
    return [contact.to_dict() for contact in contacts]


@router.get("/get/contacts/project/{project_id}")
def get_contacts_by_project_id_router(project_id: int, current_user: dict = Depends(get_current_user_api)):
    """
    Read contacts by project ID.
    """
    contacts = ContactService().read_by_project_id(project_id=project_id)
    return [contact.to_dict() for contact in contacts]


@router.get("/get/contacts/vendor/{vendor_id}")
def get_contacts_by_vendor_id_router(vendor_id: int, current_user: dict = Depends(get_current_user_api)):
    """
    Read contacts by vendor ID.
    """
    contacts = ContactService().read_by_vendor_id(vendor_id=vendor_id)
    return [contact.to_dict() for contact in contacts]


@router.put("/update/contact/{public_id}")
def update_contact_by_public_id_router(public_id: str, body: ContactUpdate, current_user: dict = Depends(get_current_user_api)):
    """
    Update a contact by public ID.

    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=TriggerType.API_CALL,
        trigger_source=TriggerSource.API,
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

    result = TriggerRouter().route_instant(context)

    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to update contact")
        )

    return result.get("data")


@router.delete("/delete/contact/{public_id}")
def delete_contact_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Delete a contact by public ID.

    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=TriggerType.API_CALL,
        trigger_source=TriggerSource.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "public_id": public_id,
        },
        workflow_type="contact_delete",
    )

    result = TriggerRouter().route_instant(context)

    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to delete contact")
        )

    return result.get("data")
