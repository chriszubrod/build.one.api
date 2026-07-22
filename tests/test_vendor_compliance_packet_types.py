"""U-117: vendor compliance packet includes Business License + Contractor's License."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from entities.vendor_compliance.business.packet_service import VendorCompliancePacketService

MODULE = "entities.vendor_compliance.business.packet_service"


def test_build_packet_includes_business_and_contractors_license_rows_and_blobs():
    vendor = SimpleNamespace(id=42, name="Acme Plumbing", abbreviation="ACME", taxpayer_id=None)
    bl = SimpleNamespace(
        license_number="BL-100",
        expiry_date="2027-06-01",
        public_id="bl-pub",
    )
    cl = SimpleNamespace(
        license_number="CL-200",
        expiry_date="2028-01-15",
        public_id="cl-pub",
    )
    bl_att = SimpleNamespace(blob_url="https://blob.example/business-license.pdf")
    cl_att = SimpleNamespace(blob_url="https://blob.example/contractors-license.pdf")

    captured_cover: dict = {}
    captured_merge: dict = {}

    def _capture_cover(_vendor_name, doc_rows, _w9_present):
        captured_cover["doc_rows"] = doc_rows
        return b"cover-pdf"

    def _capture_merge(blob_urls, leading_pdf_bytes=None):
        captured_merge["blob_urls"] = list(blob_urls)
        captured_merge["leading_pdf_bytes"] = leading_pdf_bytes
        return b"merged-pdf"

    mock_vendor_svc = MagicMock()
    mock_vendor_svc.return_value.read_by_public_id.return_value = vendor

    mock_coi_svc = MagicMock()
    mock_coi_svc.return_value.read_by_vendor_id.return_value = []

    with (
        patch(f"{MODULE}.VendorService", mock_vendor_svc),
        patch(f"{MODULE}.CertificateOfInsuranceService", mock_coi_svc),
        patch(f"{MODULE}.AttachmentService"),
        patch(f"{MODULE}.VendorInsurancePolicyService"),
        patch(f"{MODULE}.resolve_latest_w9_attachment", return_value=None),
        patch(f"{MODULE}.resolve_current_business_license", return_value=bl),
        patch(f"{MODULE}.resolve_business_license_attachment", return_value=bl_att),
        patch(f"{MODULE}.resolve_current_contractors_license", return_value=cl),
        patch(f"{MODULE}.resolve_contractors_license_attachment", return_value=cl_att),
        patch.object(VendorCompliancePacketService, "_build_cover_pdf", side_effect=_capture_cover),
        patch(f"{MODULE}.merge_pdfs", side_effect=_capture_merge),
    ):
        pdf_bytes, filename = VendorCompliancePacketService().build_packet("vendor-pub-id")

    assert pdf_bytes == b"merged-pdf"
    assert filename == "ACME-compliance-packet.pdf"

    doc_rows = captured_cover["doc_rows"]
    assert len(doc_rows) == 2
    assert doc_rows[0] == {
        "type_label": "Business License",
        "document_number": "BL-100",
        "expiry_date": "2027-06-01",
    }
    assert doc_rows[1] == {
        "type_label": "Contractor's License",
        "document_number": "CL-200",
        "expiry_date": "2028-01-15",
    }

    assert captured_merge["blob_urls"] == [
        "https://blob.example/business-license.pdf",
        "https://blob.example/contractors-license.pdf",
    ]
