# Python Standard Library Imports
import logging
from typing import Optional

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

# Local Imports
from entities.taxpayer.business.ingest_service import TaxpayerW9IngestService
from integrations.azure.document_intelligence.external.client import DocumentIntelligenceConfigError
from integrations.box.base.errors import BoxError, BoxNotFoundError, BoxPermissionError
from shared.api.responses import item_response
from shared.rbac import require_module_api
from shared.rbac_constants import Modules

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["api", "taxpayer", "w9"])
service = TaxpayerW9IngestService()


class W9ExtractRequest(BaseModel):
    attachment_public_id: Optional[str] = None
    provider: Optional[str] = None
    file_id: Optional[str] = None


class W9IngestRequest(BaseModel):
    attachment_public_id: str
    entity_name: str
    business_name: Optional[str] = None
    classification: Optional[str] = None
    taxpayer_id_number: str
    is_signed: Optional[bool] = None
    signature_date: Optional[str] = None


@router.post("/vendor/{vendor_public_id}/w9/extract")
def extract_vendor_w9_router(
    vendor_public_id: str,
    body: W9ExtractRequest,
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
        logger.exception("W-9 extract failed for vendor %s", vendor_public_id)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/vendor/{vendor_public_id}/w9/ingest")
def ingest_vendor_w9_router(
    vendor_public_id: str,
    body: W9IngestRequest,
    current_user: dict = Depends(require_module_api(Modules.VENDORS, "can_create")),
):
    try:
        return item_response(
            service.ingest(
                vendor_public_id,
                attachment_public_id=body.attachment_public_id,
                entity_name=body.entity_name,
                business_name=body.business_name,
                classification=body.classification,
                taxpayer_id_number=body.taxpayer_id_number,
                is_signed=body.is_signed,
                signature_date=body.signature_date,
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
        logger.exception("W-9 ingest failed for vendor %s", vendor_public_id)
        raise HTTPException(status_code=500, detail=str(e))
