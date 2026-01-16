# Python Standard Library Imports
import logging

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException

# Local Imports
from integrations.ms.sharepoint.site.api.schemas import (
    SiteSearchRequest,
    SiteLinkRequest,
    SiteUpdateRequest,
    SiteCompanyLinkRequest,
)
from integrations.ms.sharepoint.site.business.service import MsSiteService
from integrations.ms.sharepoint.site.connector.company.business.service import SiteCompanyConnector
from integrations.ms.sharepoint.external.client import list_site_drives
from modules.auth.business.service import get_current_user_api

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/v1/ms/sharepoint/site", tags=["api", "ms-sharepoint-site"])


@router.get("/search")
def search_sites_router(
    query: str,
    current_user: dict = Depends(get_current_user_api)
):
    """
    Search for SharePoint sites via MS Graph API.
    Returns sites matching the query - does not store results.
    """
    service = MsSiteService()
    result = service.search_sites(query=query)
    return result


@router.get("/followed")
def get_followed_sites_router(
    current_user: dict = Depends(get_current_user_api)
):
    """
    Get SharePoint sites that the current user follows.
    Uses user-delegated authentication.
    """
    service = MsSiteService()
    result = service.get_followed_sites()
    return result


@router.get("/{site_id}/drives")
def get_drives_for_site_router(
    site_id: str,
    current_user: dict = Depends(get_current_user_api)
):
    """
    Get all drives (document libraries) for a SharePoint site.
    The site_id is the MS Graph site ID.
    """
    result = list_site_drives(site_id=site_id)
    return result


@router.post("")
def link_site_router(
    body: SiteLinkRequest,
    current_user: dict = Depends(get_current_user_api)
):
    """
    Link a SharePoint site by fetching from MS Graph and storing locally.
    """
    service = MsSiteService()
    result = service.link_site(site_id=body.site_id)
    
    if result.get("status_code") >= 400:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message", "Failed to link site")
        )
    
    return result


@router.get("")
def list_linked_sites_router(
    current_user: dict = Depends(get_current_user_api)
):
    """
    List all linked SharePoint sites.
    """
    service = MsSiteService()
    sites = service.read_all()
    return {
        "message": f"Found {len(sites)} linked sites",
        "status_code": 200,
        "sites": [site.to_dict() for site in sites]
    }


@router.get("/{public_id}")
def get_linked_site_router(
    public_id: str,
    current_user: dict = Depends(get_current_user_api)
):
    """
    Get a linked SharePoint site by public ID.
    """
    service = MsSiteService()
    site = service.read_by_public_id(public_id=public_id)
    
    if not site:
        raise HTTPException(
            status_code=404,
            detail="Linked site not found"
        )
    
    return {
        "message": "Site retrieved successfully",
        "status_code": 200,
        "site": site.to_dict()
    }


@router.put("/{public_id}")
def update_linked_site_router(
    public_id: str,
    body: SiteUpdateRequest,
    current_user: dict = Depends(get_current_user_api)
):
    """
    Update a linked SharePoint site's display name.
    """
    service = MsSiteService()
    result = service.update_by_public_id(
        public_id=public_id,
        display_name=body.display_name
    )
    
    if result.get("status_code") >= 400:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message", "Failed to update site")
        )
    
    return result


@router.delete("/{public_id}")
def unlink_site_router(
    public_id: str,
    current_user: dict = Depends(get_current_user_api)
):
    """
    Unlink a SharePoint site by removing it from the database.
    """
    service = MsSiteService()
    result = service.unlink_site(public_id=public_id)
    
    if result.get("status_code") >= 400:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message", "Failed to unlink site")
        )
    
    return result


@router.post("/{public_id}/refresh")
def refresh_linked_site_router(
    public_id: str,
    current_user: dict = Depends(get_current_user_api)
):
    """
    Refresh a linked site by fetching latest data from MS Graph.
    """
    service = MsSiteService()
    result = service.refresh_site(public_id=public_id)
    
    if result.get("status_code") >= 400:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message", "Failed to refresh site")
        )
    
    return result


# =============================================================================
# Site-Company Connector Endpoints
# =============================================================================


@router.post("/connector/company")
def link_site_to_company_router(
    body: SiteCompanyLinkRequest,
    current_user: dict = Depends(get_current_user_api)
):
    """
    Link a SharePoint site to a Company.
    Creates the site record if needed and establishes the mapping.
    """
    connector = SiteCompanyConnector()
    result = connector.link_site_to_company(
        company_id=body.company_id,
        ms_graph_site_id=body.ms_graph_site_id
    )
    
    if result.get("status_code") >= 400:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message", "Failed to link site to company")
        )
    
    return result


@router.get("/connector/company/{company_id}")
def get_site_for_company_router(
    company_id: int,
    current_user: dict = Depends(get_current_user_api)
):
    """
    Get the linked SharePoint site for a Company.
    """
    connector = SiteCompanyConnector()
    site = connector.get_site_for_company(company_id=company_id)
    
    if not site:
        return {
            "message": "No linked site found for this company",
            "status_code": 404,
            "site": None
        }
    
    return {
        "message": "Site retrieved successfully",
        "status_code": 200,
        "site": site.to_dict()
    }


@router.delete("/connector/company/{company_id}")
def unlink_site_from_company_router(
    company_id: int,
    current_user: dict = Depends(get_current_user_api)
):
    """
    Unlink the SharePoint site from a Company.
    """
    connector = SiteCompanyConnector()
    result = connector.unlink_by_company_id(company_id=company_id)
    
    if result.get("status_code") >= 400:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message", "Failed to unlink site from company")
        )
    
    return result
