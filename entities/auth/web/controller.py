# Python Standard Library Imports
from datetime import datetime, timezone

# Third-party Imports
from fastapi import APIRouter, Request, Depends
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

# Local Imports
from entities.auth.api.router import _set_auth_cookies
from entities.auth.business.service import AuthService, get_current_user_web

router = APIRouter(prefix="/auth", tags=["web", "auth"])
service = AuthService()
templates = Jinja2Templates(directory="templates/auth")


@router.get("/login")
def login(request: Request):
    """
    Login.
    """
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
        }
    )


@router.get("/refresh")
def refresh(request: Request):
    """
    Refresh access token using refresh cookie, then redirect to next.
    Used when access token expired on full-page load so session is restored.
    """
    next_path = request.query_params.get("next", "/dashboard")
    if not next_path.startswith("/"):
        next_path = "/dashboard"
    refresh_token = request.cookies.get("token.refresh_token")
    if not refresh_token:
        return RedirectResponse(url="/auth/login", status_code=303)
    try:
        access_token, new_refresh_token = service.refresh_access_token(refresh_token=refresh_token)
        response = RedirectResponse(url=next_path, status_code=303)
        _set_auth_cookies(response=response, access_token=access_token, refresh_token=new_refresh_token)
        return response
    except Exception:
        return RedirectResponse(url="/auth/login", status_code=303)


@router.get("/logout")
def logout(request: Request, current_user: dict = Depends(get_current_user_web)):
    """
    Logout and invalidate the current user's session.
    """
    refresh_token = request.cookies.get("token.refresh_token")
    if refresh_token:
        service.revoke_refresh_token(refresh_token=refresh_token)
    response = RedirectResponse(url="/auth/login", status_code=303)
    # Delete access + refresh cookies
    response.delete_cookie("token.access_token", path="/")
    response.delete_cookie("token.refresh_token", path="/")
    response.delete_cookie("token.csrf", path="/")
    return response


@router.get("/reset")
def reset(request: Request):
    """
    Reset.
    """
    return RedirectResponse(url="/auth/login", status_code=303)
