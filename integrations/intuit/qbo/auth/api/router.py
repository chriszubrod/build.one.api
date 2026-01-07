# Python Standard Library Imports
import logging
from urllib.parse import quote

# Third-party Imports
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

# Local Imports
from integrations.intuit.qbo.auth.external.client import (
    connect_intuit_oauth_2_endpoint,
    connect_intuit_oauth_2_token_endpoint,
    connect_intuit_oauth_2_token_endpoint_refresh,
    connect_intuit_oauth_2_token_endpoint_revoke
)
from modules.auth.business.service import get_current_user_api

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/v1", tags=["api", "qbo-auth"])


@router.get("/intuit/qbo/auth/request")
def intuit_authorization_request_router(current_user: dict = Depends(get_current_user_api)):
    connect_intuit_oauth_2_endpoint_resp = connect_intuit_oauth_2_endpoint()
    if connect_intuit_oauth_2_endpoint_resp.get("status_code") == 201:
        return (RedirectResponse(url=connect_intuit_oauth_2_endpoint_resp.get('message')))
    else:
        return {
            "message": connect_intuit_oauth_2_endpoint_resp.get('message'),
            "status_code": connect_intuit_oauth_2_endpoint_resp.get('status_code')
        }


@router.get('/intuit/qbo/auth/request/callback')
def intuit_authorization_request_callback_router(
    request: Request
):
    """
    OAuth callback endpoint for Intuit QuickBooks Online.
    Exchanges authorization code for tokens, then redirects to integration list page.
    """
    connect_intuit_oauth_2_token_endpoint_resp = connect_intuit_oauth_2_token_endpoint(request)
    
    # Build redirect URL to integration list page with success/error message
    redirect_url = "/integration/list"
    
    if connect_intuit_oauth_2_token_endpoint_resp.get("status_code") == 201:
        # Success
        message = connect_intuit_oauth_2_token_endpoint_resp.get("message", "QuickBooks Online connected successfully")
        encoded_message = quote(message)
        redirect_url += f"?success=true&message={encoded_message}"
    else:
        # Error
        message = connect_intuit_oauth_2_token_endpoint_resp.get("message", "Failed to connect QuickBooks Online")
        encoded_message = quote(message)
        redirect_url += f"?success=false&message={encoded_message}"
    
    return RedirectResponse(url=redirect_url)


@router.get('/intuit/qbo/auth/refresh/request')
def intuit_authorization_refresh_request_router(current_user: dict = Depends(get_current_user_api)):
    connect_intuit_oauth_2_refresh_endpoint_resp = connect_intuit_oauth_2_token_endpoint_refresh()
    return {
        "message": connect_intuit_oauth_2_refresh_endpoint_resp.get('message'),
        "status_code": connect_intuit_oauth_2_refresh_endpoint_resp.get('status_code')
    }


@router.get('/intuit/qbo/auth/revoke/request')
def intuit_authorization_revoke_request_router(current_user: dict = Depends(get_current_user_api)):
    connect_intuit_oauth_2_rerevoke_endpoint_resp = connect_intuit_oauth_2_token_endpoint_revoke()
    return {
        "message": connect_intuit_oauth_2_rerevoke_endpoint_resp.get('message'),
        "status_code": connect_intuit_oauth_2_rerevoke_endpoint_resp.get('status_code')
    }
