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
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
import config

logger = logging.getLogger(__name__)
_settings = config.Settings()


def _integration_list_redirect(success: bool, message: str) -> str:
    """
    Build the post-OAuth redirect URL that points at the React web app's
    integration list, with ?success= and ?message= query params so the React
    `IntegrationList` page can surface a toast.

    Falls back to a relative URL when `WEB_APP_URL` is unset; that path will
    404 on the API host now that the Jinja `/integration/list` page is gone,
    so `WEB_APP_URL` should be set in prod App Service Application Settings.
    """
    base = (_settings.web_app_url or "").rstrip("/")
    encoded = quote(message) if message else ""
    qp = f"?success={'true' if success else 'false'}"
    if encoded:
        qp += f"&message={encoded}"
    return f"{base}/integration/list{qp}"


router = APIRouter(prefix="/api/v1", tags=["api", "qbo-auth"])


@router.get("/intuit/qbo/auth/request")
def intuit_authorization_request_router(current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))):
    connect_intuit_oauth_2_endpoint_resp = connect_intuit_oauth_2_endpoint()
    if connect_intuit_oauth_2_endpoint_resp.get("status_code") == 201:
        return (RedirectResponse(url=connect_intuit_oauth_2_endpoint_resp.get('message')))
    else:
        return {
            "message": connect_intuit_oauth_2_endpoint_resp.get('message'),
            "status_code": connect_intuit_oauth_2_endpoint_resp.get('status_code')
        }


@router.get('/intuit/qbo/auth/request/callback')
def intuit_authorization_request_callback_router(request: Request):
    """
    OAuth callback endpoint for Intuit QuickBooks Online.
    Exchanges authorization code for tokens, then redirects to the React
    integration list page on the web-app host (see `settings.web_app_url`).
    """
    try:
        resp = connect_intuit_oauth_2_token_endpoint(request)

        if isinstance(resp, dict):
            status_code = resp.get("status_code")
            if status_code == 201:
                return RedirectResponse(url=_integration_list_redirect(
                    True, resp.get("message", "QuickBooks Online connected successfully"),
                ))
            return RedirectResponse(url=_integration_list_redirect(
                False, resp.get("message", "Failed to connect QuickBooks Online"),
            ))

        logger.error(f"Unexpected response type from connect_intuit_oauth_2_token_endpoint: {type(resp)}")
        return RedirectResponse(url=_integration_list_redirect(
            False, "Unexpected response format from OAuth token endpoint",
        ))

    except Exception as e:
        logger.exception("Error in OAuth callback endpoint")
        return RedirectResponse(url=_integration_list_redirect(
            False, f"An error occurred during OAuth callback: {str(e)}",
        ))


@router.get('/intuit/qbo/auth/refresh/request')
def intuit_authorization_refresh_request_router(current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))):
    connect_intuit_oauth_2_refresh_endpoint_resp = connect_intuit_oauth_2_token_endpoint_refresh()
    return {
        "message": connect_intuit_oauth_2_refresh_endpoint_resp.get('message'),
        "status_code": connect_intuit_oauth_2_refresh_endpoint_resp.get('status_code')
    }


@router.get('/intuit/qbo/auth/revoke/request')
def intuit_authorization_revoke_request_router(current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))):
    connect_intuit_oauth_2_rerevoke_endpoint_resp = connect_intuit_oauth_2_token_endpoint_revoke()
    return {
        "message": connect_intuit_oauth_2_rerevoke_endpoint_resp.get('message'),
        "status_code": connect_intuit_oauth_2_rerevoke_endpoint_resp.get('status_code')
    }
