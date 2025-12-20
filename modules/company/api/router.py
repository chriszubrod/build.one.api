# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends

# Local Imports
from modules.company.api.schemas import CompanyCreate, CompanyUpdate
from modules.company.business.service import CompanyService
from modules.auth.business.service import get_current_user_api

router = APIRouter(prefix="/api/v1", tags=["api", "company"])


@router.post("/create/company")
def create_company_router(body: CompanyCreate, current_user: dict = Depends(get_current_user_api)):
    """
    Create a new company.
    """
    company = CompanyService().create(
        name=body.name,
        website=body.website,
    )
    return company.to_dict()


@router.get("/get/companies")
def get_companies_router(current_user: dict = Depends(get_current_user_api)):
    """
    Read all companies.
    """
    companies = CompanyService().read_all()
    return [company.to_dict() for company in companies]


@router.get("/get/company/{public_id}")
def get_company_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Read a company by public ID.
    """
    company = CompanyService().read_by_public_id(public_id=public_id)
    return company.to_dict()


@router.put("/update/company/{public_id}")
def update_company_by_public_id_router(public_id: str, body: CompanyUpdate, current_user: dict = Depends(get_current_user_api)):
    """
    Update a company by public ID.
    """
    company = CompanyService().update_by_public_id(public_id=public_id, company=body)
    return company.to_dict()


@router.delete("/delete/company/{public_id}")
def delete_company_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Delete a company by public ID.
    """
    company = CompanyService().delete_by_public_id(public_id=public_id)
    return company.to_dict()
