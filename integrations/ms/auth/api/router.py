# Python Standard Library Imports
import logging
from urllib.parse import quote

# Third-party Imports
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

# Local Imports
from integrations.ms.auth.external.client import (
    connect_ms_oauth_2_endpoint,
    connect_ms_oauth_2_token_endpoint,
    connect_ms_oauth_2_token_endpoint_refresh,
    connect_ms_oauth_2_token_endpoint_revoke,
    test_ms_graph_connection
)
from shared.rbac import require_module_api
from shared.rbac_constants import Modules

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/v1", tags=["api", "ms-auth"])


@router.get("/ms/auth/request")
def ms_authorization_request_router(current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))):
    connect_ms_oauth_2_endpoint_resp = connect_ms_oauth_2_endpoint()
    if connect_ms_oauth_2_endpoint_resp.get("status_code") == 201:
        return (RedirectResponse(url=connect_ms_oauth_2_endpoint_resp.get('message')))
    else:
        return {
            "message": connect_ms_oauth_2_endpoint_resp.get('message'),
            "status_code": connect_ms_oauth_2_endpoint_resp.get('status_code')
        }


@router.get('/ms/auth/request/callback')
def ms_authorization_request_callback_router(request: Request):
    """
    OAuth callback endpoint for Microsoft 365.
    Exchanges authorization code for tokens, then redirects to integration list page.
    """
    try:
        connect_ms_oauth_2_token_endpoint_resp = connect_ms_oauth_2_token_endpoint(request)
        
        # Build redirect URL to integration list page with success/error message
        redirect_url = "/integration/list"
        
        # Handle response - check if it's a dict and has status_code
        if isinstance(connect_ms_oauth_2_token_endpoint_resp, dict):
            status_code = connect_ms_oauth_2_token_endpoint_resp.get("status_code")
            
            if status_code == 201:
                # Success
                message = connect_ms_oauth_2_token_endpoint_resp.get("message", "Microsoft 365 connected successfully")
                encoded_message = quote(message)
                redirect_url += f"?success=true&message={encoded_message}"
            else:
                # Error
                message = connect_ms_oauth_2_token_endpoint_resp.get("message", "Failed to connect Microsoft 365")
                encoded_message = quote(message)
                redirect_url += f"?success=false&message={encoded_message}"
        else:
            # Unexpected response format
            error_message = "Unexpected response format from OAuth token endpoint"
            encoded_message = quote(error_message)
            redirect_url += f"?success=false&message={encoded_message}"
            logger.error(f"Unexpected response type from connect_ms_oauth_2_token_endpoint: {type(connect_ms_oauth_2_token_endpoint_resp)}")
        
        return RedirectResponse(url=redirect_url)
    
    except Exception as e:
        # Catch any exceptions and redirect with error message
        logger.exception("Error in OAuth callback endpoint")
        error_message = f"An error occurred during OAuth callback: {str(e)}"
        encoded_message = quote(error_message)
        redirect_url = f"/integration/list?success=false&message={encoded_message}"
        return RedirectResponse(url=redirect_url)


@router.get('/ms/auth/refresh/request')
def ms_authorization_refresh_request_router(current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))):
    connect_ms_oauth_2_refresh_endpoint_resp = connect_ms_oauth_2_token_endpoint_refresh()
    return {
        "message": connect_ms_oauth_2_refresh_endpoint_resp.get('message'),
        "status_code": connect_ms_oauth_2_refresh_endpoint_resp.get('status_code')
    }


@router.get('/ms/auth/revoke/request')
def ms_authorization_revoke_request_router(current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))):
    connect_ms_oauth_2_revoke_endpoint_resp = connect_ms_oauth_2_token_endpoint_revoke()
    return {
        "message": connect_ms_oauth_2_revoke_endpoint_resp.get('message'),
        "status_code": connect_ms_oauth_2_revoke_endpoint_resp.get('status_code')
    }


@router.get('/ms/auth/test')
def ms_graph_test_router(current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))):
    """
    Test the Microsoft Graph API connection by calling /me endpoint.
    Returns user profile information if successful.
    """
    return test_ms_graph_connection()
