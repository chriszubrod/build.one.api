# Python Standard Library Imports

# Third-party Imports

# Local Imports
from fastapi import FastAPI, APIRouter, HTTPException
from modules.organization.business.service import OrganizationService
from modules.organization.api.schemas import (
    OrganizationCreate,
    OrganizationUpdate
)

router = APIRouter(prefix="/api/v1", tags=["api", "organization"])
service = OrganizationService()


@router.post("/create/organization")
def create_organization_router(body: OrganizationCreate):
    """
    Create a new organization.
    """
    _org = service.create(
        name=body.name,
        website=body.website
    )
    return _org.to_dict()


@router.get("/get/organizations")
def get_organizations_router():
    """
    Read all organizations.
    """
    _orgs = service.read_all()
    return [org.to_dict() for org in _orgs]


@router.get("/get/organization/{public_id}")
def get_organization_by_public_id_router(public_id: str):
    """
    Read an organization by public ID.
    """
    _org = service.read_by_public_id(public_id=public_id)
    return _org.to_dict()


@router.put("/update/organization/{public_id}")
def update_organization_by_id_router(public_id: str, body: OrganizationUpdate):
    """
    Update an organization by ID.
    """
    _org = service.update_by_public_id(public_id=public_id, org=body)
    return _org.to_dict()


@router.delete("/delete/organization/{public_id}")
def delete_organization_by_public_id_router(public_id: str):
    """
    Soft delete an organization by ID.
    """
    _org = service.delete_by_public_id(public_id=public_id)
    return _org.to_dict()
