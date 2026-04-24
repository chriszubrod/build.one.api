# Python Standard Library Imports
import html
import logging

# Third-party Imports
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

# Local Imports
from integrations.intuit.qbo.auth.external.client import (
    connect_intuit_oauth_2_endpoint,
    connect_intuit_oauth_2_token_endpoint,
    connect_intuit_oauth_2_token_endpoint_refresh,
    connect_intuit_oauth_2_token_endpoint_revoke
)
from shared.rbac import require_module_api
from shared.rbac_constants import Modules

logger = logging.getLogger(__name__)


def _callback_landing(success: bool, message: str) -> HTMLResponse:
    """
    Render the small self-contained landing page the user sees after Intuit's
    OAuth redirect lands on the API host. There's no public web UI to bounce
    to — React runs locally — so we render inline HTML here and ask the user
    to return to their local Build One tab.
    """
    title = "QuickBooks connected" if success else "Connection failed"
    icon_char = "✓" if success else "✗"
    icon_color = "#16a34a" if success else "#dc2626"
    body = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Build One — {html.escape(title)}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 0; background: #f8fafc; display: flex; align-items: center; justify-content: center; min-height: 100vh; color: #1f2937; }}
  .card {{ max-width: 480px; width: calc(100% - 32px); background: #ffffff; padding: 40px; border-radius: 12px; box-shadow: 0 4px 16px rgba(0,0,0,0.08); text-align: center; }}
  .icon {{ font-size: 48px; line-height: 1; margin-bottom: 16px; color: {icon_color}; }}
  h1 {{ margin: 0 0 12px; font-size: 22px; }}
  p {{ color: #475569; line-height: 1.5; margin: 0 0 12px; }}
  .hint {{ margin-top: 24px; font-size: 14px; color: #94a3b8; }}
</style>
</head>
<body>
  <div class="card">
    <div class="icon">{icon_char}</div>
    <h1>{html.escape(title)}</h1>
    <p>{html.escape(message)}</p>
    <p class="hint">You can close this tab and return to Build One.</p>
  </div>
</body>
</html>"""
    return HTMLResponse(content=body, status_code=200 if success else 400)


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

    Intuit redirects the user's browser here after authorization. We exchange
    the code for access + refresh tokens, store them, then render an inline
    HTML landing page since the API host has no React UI to bounce to.
    """
    try:
        resp = connect_intuit_oauth_2_token_endpoint(request)

        if isinstance(resp, dict):
            if resp.get("status_code") == 201:
                return _callback_landing(
                    True, resp.get("message", "QuickBooks Online connected successfully"),
                )
            return _callback_landing(
                False, resp.get("message", "Failed to connect QuickBooks Online"),
            )

        logger.error(f"Unexpected response type from connect_intuit_oauth_2_token_endpoint: {type(resp)}")
        return _callback_landing(
            False, "Unexpected response format from OAuth token endpoint",
        )

    except Exception as e:
        logger.exception("Error in OAuth callback endpoint")
        return _callback_landing(
            False, f"An error occurred during OAuth callback: {str(e)}",
        )


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
