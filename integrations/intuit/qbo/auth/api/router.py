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
    connect_intuit_oauth_2_token_endpoint_resp = connect_intuit_oauth_2_token_endpoint(request)
    return {
        "message": connect_intuit_oauth_2_token_endpoint_resp.get("message"),
        "status_code": connect_intuit_oauth_2_token_endpoint_resp.get("status_code")
    }


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
