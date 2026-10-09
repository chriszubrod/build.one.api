"""
U-119 (api half of the U-116 spine teardown): the dead
`GET /api/v1/view/vendor-compliance-document/{public_id}/attachment` route and
its only service dependency, `VendorCompliancePacketService.resolve_single_doc`,
are removed. No caller in web, iOS, MCP or scheduler used it; the web views
compliance PDFs through the generic `/api/v1/view/attachment/{public_id}`, which
must stay mounted.
"""

import entities.vendor_compliance.api.router as vendor_compliance_router
from app import app
from entities.vendor_compliance.business.packet_service import VendorCompliancePacketService


def test_dead_vendor_compliance_document_attachment_route_is_not_mounted():
    mounted_paths = {getattr(r, "path", "") for r in app.routes}
    assert "/api/v1/view/vendor-compliance-document/{public_id}/attachment" not in mounted_paths


def test_generic_attachment_view_route_is_still_mounted():
    mounted_paths = {getattr(r, "path", "") for r in app.routes}
    assert "/api/v1/view/attachment/{public_id}" in mounted_paths


def test_resolve_single_doc_service_method_is_removed():
    assert not hasattr(VendorCompliancePacketService, "resolve_single_doc")


def test_dead_handler_is_removed_from_vendor_compliance_router_module():
    assert not hasattr(vendor_compliance_router, "view_vendor_compliance_coi_attachment_router")
