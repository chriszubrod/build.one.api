# Python Standard Library Imports
import logging
from typing import Any, Optional

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

# Local Imports
from entities.certificate_of_insurance.business.ingest_service import (
    CertificateOfInsuranceIngestService,
)
from integrations.azure.document_intelligence.external.client import DocumentIntelligenceConfigError
from integrations.box.base.errors import BoxError, BoxNotFoundError, BoxPermissionError
from shared.api.responses import item_response
from shared.rbac import require_module_api
from shared.rbac_constants import Modules

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["api", "certificate-of-insurance"])
service = CertificateOfInsuranceIngestService()


class COIExtractRequest(BaseModel):
    attachment_public_id: Optional[str] = None
    provider: Optional[str] = None
    file_id: Optional[str] = None


class COIPolicyIngestItem(BaseModel):
    coverage_type: str
    carrier: Optional[str] = None
    policy_number: Optional[str] = None
    each_occurrence: Optional[str] = None
    aggregate: Optional[str] = None
    effective_date: Optional[str] = None
    expiry_date: Optional[str] = None


class COIIngestRequest(BaseModel):
    attachment_public_id: str
    issuing_authority: Optional[str] = None
    issue_date: Optional[str] = None
    verification_status: Optional[str] = "Received"
    policies: Optional[list[COIPolicyIngestItem]] = None


@router.post("/vendor/{vendor_public_id}/certificate-of-insurance/extract")
def extract_vendor_certificate_of_insurance_router(
    vendor_public_id: str,
    body: COIExtractRequest,
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
        logger.exception(
            "Certificate of insurance extract failed for vendor %s", vendor_public_id
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/vendor/{vendor_public_id}/certificate-of-insurance/ingest")
def ingest_vendor_certificate_of_insurance_router(
    vendor_public_id: str,
    body: COIIngestRequest,
    current_user: dict = Depends(require_module_api(Modules.VENDORS, "can_create")),
):
    try:
        policy_dicts: Optional[list[dict[str, Any]]] = None
        if body.policies is not None:
            policy_dicts = [p.model_dump() for p in body.policies]
        return item_response(
            service.ingest(
                vendor_public_id,
                attachment_public_id=body.attachment_public_id,
                issuing_authority=body.issuing_authority,
                issue_date=body.issue_date,
                verification_status=body.verification_status or "Received",
                policies=policy_dicts,
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
        logger.exception(
            "Certificate of insurance ingest failed for vendor %s", vendor_public_id
        )
        raise HTTPException(status_code=500, detail=str(e))
