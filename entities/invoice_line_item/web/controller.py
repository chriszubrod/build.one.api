# Python Standard Library Imports
import logging

# Third-party Imports
from fastapi import APIRouter

# Local Imports

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/invoice_line_item", tags=["web", "invoice_line_item"])
