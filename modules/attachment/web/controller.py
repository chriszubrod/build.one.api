# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

# Local Imports
from modules.auth.business.service import get_current_user_web

router = APIRouter(prefix="/attachment", tags=["web", "attachment"])


# Web UI templates deferred to later implementation
# Placeholder routes can be added here when templates are created

