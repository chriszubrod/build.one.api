# Python Standard Library Imports
import io

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import StreamingResponse

# Local Imports
from entities.vendor.business.service import VendorService
from entities.vendor_compliance_document.api.schemas import (
    VendorComplianceDocumentCreate,
    VendorComplianceDocumentUpdate,
)
from entities.vendor_compliance_document.business.dashboard_service import VendorComplianceDashboardService
from entities.vendor_compliance_document.business.packet_service import VendorCompliancePacketService
from entities.vendor_compliance_document.business.service import VendorComplianceDocumentService
from core.workflow.api.process_engine import ProcessEngine, TriggerContext, EventType, Channel
from shared.api.responses import list_response, item_response, raise_workflow_error, raise_not_found
from shared.rbac import require_module_api
from shared.rbac_constants import Modules

router = APIRouter(prefix="/api/v1", tags=["api", "vendor-compliance-document"])
service = VendorComplianceDocumentService()
dashboard_service = VendorComplianceDashboardService()
packet_service = VendorCompliancePacketService()


@router.post("/create/vendor-compliance-document")
def create_vendor_compliance_document_router(
    body: VendorComplianceDocumentCreate,
    current_user: dict = Depends(require_module_api(Modules.VENDORS, "can_create")),
):
    """
    Create a new vendor compliance document.

    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "vendor_public_id": body.vendor_public_id,
            "document_type": body.document_type,
            "issuing_authority": body.issuing_authority,
            "document_number": body.document_number,
            "classification": body.classification,
            "issue_date": body.issue_date,
            "expiry_date": body.expiry_date,
            "attachment_public_id": body.attachment_public_id,
            "verification_status": body.verification_status,
        },
        workflow_type="vendor_compliance_document_create",
    )

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to create vendor compliance document")

    return item_response(result.get("data"))


@router.get("/get/vendor-compliance-documents/by-vendor/{vendor_public_id}")
def get_vendor_compliance_documents_by_vendor_router(
    vendor_public_id: str,
    current_user: dict = Depends(require_module_api(Modules.VENDORS, "can_read")),
):
    """
    Read vendor compliance documents by vendor public ID.
    """
    try:
        vendor = VendorService().read_by_public_id(public_id=vendor_public_id)
        if not vendor or not vendor.id:
            raise_not_found("Vendor")
        documents = service.read_by_vendor_id(vendor_id=int(vendor.id))
        return list_response([doc.to_dict() for doc in documents])
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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


@router.get("/get/vendor-compliance-document/{public_id}")
def get_vendor_compliance_document_by_public_id_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.VENDORS, "can_read")),
):
    """
    Read a vendor compliance document by public ID.
    """
    try:
        document = service.read_by_public_id(public_id=public_id)
        if not document:
            raise_not_found("Vendor compliance document")
        return item_response(document.to_dict())
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/update/vendor-compliance-document/{public_id}")
def update_vendor_compliance_document_by_public_id_router(
    public_id: str,
    body: VendorComplianceDocumentUpdate,
    current_user: dict = Depends(require_module_api(Modules.VENDORS, "can_update")),
):
    """
    Update a vendor compliance document by public ID.

    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "public_id": public_id,
            "row_version": body.row_version,
            "issuing_authority": body.issuing_authority,
            "document_number": body.document_number,
            "classification": body.classification,
            "issue_date": body.issue_date,
            "expiry_date": body.expiry_date,
            "attachment_public_id": body.attachment_public_id,
            "verification_status": body.verification_status,
        },
        workflow_type="vendor_compliance_document_update",
    )

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to update vendor compliance document")

    return item_response(result.get("data"))


@router.delete("/delete/vendor-compliance-document/{public_id}")
def delete_vendor_compliance_document_by_public_id_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.VENDORS, "can_delete")),
):
    """
    Delete a vendor compliance document by public ID.

    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "public_id": public_id,
        },
        workflow_type="vendor_compliance_document_delete",
    )

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to delete vendor compliance document")

    return item_response(result.get("data"))


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
def view_vendor_compliance_document_attachment_router(
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
