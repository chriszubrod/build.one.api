# Python Standard Library Imports
import io

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import StreamingResponse

# Local Imports
from entities.vendor_compliance.business.dashboard_service import VendorComplianceDashboardService
from entities.vendor_compliance.business.packet_service import VendorCompliancePacketService
from shared.api.responses import item_response
from shared.rbac import require_module_api
from shared.rbac_constants import Modules

router = APIRouter(prefix="/api/v1", tags=["api", "vendor-compliance"])
dashboard_service = VendorComplianceDashboardService()
packet_service = VendorCompliancePacketService()


@router.get("/get/vendor-compliance/dashboard")
def get_vendor_compliance_dashboard_router(
    current_user: dict = Depends(require_module_api(Modules.VENDORS, "can_read")),
):
    try:
        return item_response(dashboard_service.build_dashboard())
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/generate/vendor-compliance/{vendor_public_id}/packet")
def generate_vendor_compliance_packet_router(
    vendor_public_id: str,
    current_user: dict = Depends(require_module_api(Modules.VENDORS, "can_read")),
):
    try:
        pdf_bytes, filename = packet_service.build_packet(vendor_public_id)
        safe_filename = filename.replace('"', "'")
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'inline; filename="{safe_filename}"'},
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/view/vendor-compliance-document/{public_id}/attachment")
def view_vendor_compliance_coi_attachment_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.VENDORS, "can_read")),
):
    try:
        content, filename = packet_service.resolve_single_doc(public_id)
        safe_filename = filename.replace('"', "'")
        return StreamingResponse(
            io.BytesIO(content),
            media_type="application/pdf",
            headers={"Content-Disposition": f'inline; filename="{safe_filename}"'},
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
