# Python Standard Library Imports
from datetime import datetime, timezone

# Third-party Imports
from fastapi import APIRouter, Request, Depends
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

# Local Imports
from modules.auth.business.service import AuthService, get_current_user_web

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


@router.get("/signup")
def signup(request: Request):
    """
    Signup.
    """
    return templates.TemplateResponse(
        "signup.html",
        {
            "request": request,
        }
    )


@router.get("/logout")
def logout(request: Request, current_user: dict = Depends(get_current_user_web)):
    """
    Logout and invalidate the current user's session.
    """
    response = RedirectResponse(url="/auth/login", status_code=303)
    # Delete the correct cookie name
    response.delete_cookie("token.access_token", path="/")
    return response


@router.get("/reset")
def reset(request: Request):
    """
    Reset.
    """
    return RedirectResponse(url="/auth/login", status_code=303)
