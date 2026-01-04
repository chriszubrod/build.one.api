# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse

# Local Imports
from integrations.intuit.qbo.auth.external.client import (
    connect_intuit_oauth_2_endpoint,
    connect_intuit_oauth_2_token_endpoint,
    connect_intuit_oauth_2_token_endpoint_refresh,
    connect_intuit_oauth_2_token_endpoint_revoke
)
from modules.auth.business.service import get_current_user_api


router = APIRouter(prefix="/api/v1", tags=["api", "qbo-auth"])


@router.get("/intuit/qbo/auth/request")
def intuit_authorization_request_router(current_user: dict = Depends(get_current_user_api)):
    """
    Initiate OAuth 2.0 authorization flow for QuickBooks Online.
    Requires authentication to ensure only authenticated users can connect integrations.
    """
    connect_intuit_oauth_2_endpoint_resp = connect_intuit_oauth_2_endpoint()
    if connect_intuit_oauth_2_endpoint_resp.get("status_code") == 201:
        return (RedirectResponse(connect_intuit_oauth_2_endpoint_resp.get('message')))
    else:
        return {
            "message": connect_intuit_oauth_2_endpoint_resp.get('message'),
            "status_code": connect_intuit_oauth_2_endpoint_resp.get('status_code')
        }


@router.get('/intuit/qbo/auth/request/callback')
def intuit_authorization_request_callback_router(current_user: dict = Depends(get_current_user_api)):
    """
    OAuth 2.0 callback endpoint for QuickBooks Online.
    Requires authentication to ensure only authenticated users can complete OAuth flow.
    """
    connect_intuit_oauth_2_token_endpoint_resp = connect_intuit_oauth_2_token_endpoint()
    return {
        "message": connect_intuit_oauth_2_token_endpoint_resp.get("message"),
        "status_code": connect_intuit_oauth_2_token_endpoint_resp.get("status_code")
    }


@router.get('/intuit/qbo/auth/refresh/request')
def intuit_authorization_refresh_request_router(current_user: dict = Depends(get_current_user_api)):
    """
    Refresh OAuth 2.0 tokens for QuickBooks Online.
    Requires authentication to ensure only authenticated users can refresh tokens.
    """
    connect_intuit_oauth_2_refresh_endpoint_resp = connect_intuit_oauth_2_token_endpoint_refresh()
    return {
        "message": connect_intuit_oauth_2_refresh_endpoint_resp.get('message'),
        "status_code": connect_intuit_oauth_2_refresh_endpoint_resp.get('status_code')
    }


@router.get('/intuit/qbo/auth/revoke/request')
def intuit_authorization_revoke_request_router(current_user: dict = Depends(get_current_user_api)):
    """
    Revoke OAuth 2.0 tokens for QuickBooks Online.
    Requires authentication to ensure only authenticated users can revoke tokens.
    """
    connect_intuit_oauth_2_rerevoke_endpoint_resp = connect_intuit_oauth_2_token_endpoint_revoke()
    return {
        "message": connect_intuit_oauth_2_rerevoke_endpoint_resp.get('message'),
        "status_code": connect_intuit_oauth_2_rerevoke_endpoint_resp.get('status_code')
    }
