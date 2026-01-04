# Python Standard Library Imports
import logging

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
def intuit_authorization_request_callback_router(
    request: Request,
    current_user: dict = Depends(get_current_user_api)
):
    """
    OAuth 2.0 callback endpoint for QuickBooks Online.
    Requires authentication to ensure only authenticated users can complete OAuth flow.
    """
    # Log all query parameters for debugging
    logger.info("=" * 80)
    logger.info("INTUIT OAUTH CALLBACK RECEIVED")
    logger.info("=" * 80)
    logger.info(f"Full callback URL: {request.url}")
    logger.info(f"Query parameters: {dict(request.query_params)}")
    
    # Check for error parameters from Intuit (OAuth 2.0 error response)
    error = request.query_params.get('error')
    error_description = request.query_params.get('error_description')
    error_uri = request.query_params.get('error_uri')
    error_reason = request.query_params.get('error_reason')
    
    if error:
        logger.error("=" * 80)
        logger.error("INTUIT OAUTH ERROR DETECTED")
        logger.error("=" * 80)
        logger.error(f"Error code: {error}")
        logger.error(f"Error description: {error_description}")
        logger.error(f"Error URI: {error_uri}")
        logger.error(f"Error reason: {error_reason}")
        logger.error(f"All query params: {dict(request.query_params)}")
        logger.error("=" * 80)
        
        return {
            "error": error,
            "error_description": error_description,
            "error_uri": error_uri,
            "error_reason": error_reason,
            "message": f"OAuth error: {error} - {error_description or 'No description provided'}",
            "status_code": 400,
            "query_params": dict(request.query_params)
        }
    
    # Check for successful authorization code
    code = request.query_params.get('code')
    state = request.query_params.get('state')
    realm_id = request.query_params.get('realmId')
    
    logger.info(f"Authorization code received: {code[:10] if code else 'None'}...")
    logger.info(f"State: {state}")
    logger.info(f"Realm ID: {realm_id}")
    logger.info("=" * 80)
    
    # Continue with normal token exchange flow
    connect_intuit_oauth_2_token_endpoint_resp = connect_intuit_oauth_2_token_endpoint(request)
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
