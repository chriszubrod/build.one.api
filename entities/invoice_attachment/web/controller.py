# Python Standard Library Imports
import logging

# Third-party Imports
from fastapi import APIRouter

# Local Imports

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/invoice_attachment", tags=["web", "invoice_attachment"])
