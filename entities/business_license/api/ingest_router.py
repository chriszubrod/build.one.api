# Python Standard Library Imports
import logging
from typing import Optional

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

# Local Imports
from entities.business_license.business.ingest_service import BusinessLicenseIngestService
from integrations.azure.document_intelligence.external.client import DocumentIntelligenceConfigError
from integrations.box.base.errors import BoxError, BoxNotFoundError, BoxPermissionError
from shared.api.responses import item_response
from shared.rbac import require_module_api
from shared.rbac_constants import Modules

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["api", "business-license"])
service = BusinessLicenseIngestService()


class BLExtractRequest(BaseModel):
    attachment_public_id: Optional[str] = None
    provider: Optional[str] = None
    file_id: Optional[str] = None


class BLIngestRequest(BaseModel):
    attachment_public_id: str
    license_number: Optional[str] = None
    issuing_authority: Optional[str] = None
    issue_date: Optional[str] = None
    expiry_date: Optional[str] = None
    verification_status: Optional[str] = "Received"


@router.post("/vendor/{vendor_public_id}/business-license/extract")
def extract_vendor_business_license_router(
    vendor_public_id: str,
    body: BLExtractRequest,
    current_user: dict = Depends(require_module_api(Modules.VENDORS, "can_create")),
):
    if not body.attachment_public_id and not (body.provider and body.file_id):
        raise HTTPException(
            status_code=400,
            detail="provide attachment_public_id or (provider + file_id)",
        )
    try:
        return item_response(
            service.extract(
                vendor_public_id,
                attachment_public_id=body.attachment_public_id,
                provider=body.provider,
                file_id=body.file_id,
            )
        )
    except ValueError as e:
        msg = str(e)
        if "not found" in msg.lower():
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=400, detail=msg)
    except DocumentIntelligenceConfigError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except BoxNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except BoxPermissionError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except BoxError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Business license extract failed for vendor %s", vendor_public_id)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/vendor/{vendor_public_id}/business-license/ingest")
def ingest_vendor_business_license_router(
    vendor_public_id: str,
    body: BLIngestRequest,
    current_user: dict = Depends(require_module_api(Modules.VENDORS, "can_create")),
):
    try:
        return item_response(
            service.ingest(
                vendor_public_id,
                attachment_public_id=body.attachment_public_id,
                license_number=body.license_number,
                issuing_authority=body.issuing_authority,
                issue_date=body.issue_date,
                expiry_date=body.expiry_date,
                verification_status=body.verification_status,
            )
        )
    except ValueError as e:
        msg = str(e)
        if "not found" in msg.lower():
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=400, detail=msg)
    except DocumentIntelligenceConfigError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except BoxNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except BoxPermissionError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except BoxError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Business license ingest failed for vendor %s", vendor_public_id)
        raise HTTPException(status_code=500, detail=str(e))
