"""
Business license PDF load, DI extraction, and ingest for a vendor.

The fetch/back-half lives in entities.vendor_compliance.business.ingest_fetch.
"""

# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from entities.business_license.business.bl_parser import parse_business_license_fields


class BusinessLicenseIngestService:
    """Orchestrates business-license PDF load, DI extraction, and ingest for a vendor."""

    def extract(
        self,
        vendor_public_id: str,
        *,
        attachment_public_id: Optional[str] = None,
        provider: Optional[str] = None,
        file_id: Optional[str] = None,
    ) -> dict:
        from entities.vendor.business.service import VendorService
        from entities.vendor_compliance.business.ingest_fetch import (
            load_compliance_pdf_and_attachment,
        )
        from integrations.azure.document_intelligence.business.service import (
            DocumentIntelligenceService,
        )

        vendor = VendorService().read_by_public_id(public_id=vendor_public_id)
        if not vendor:
            raise ValueError(f"Vendor with public_id '{vendor_public_id}' not found")

        data, att_public_id, _name, content_type = load_compliance_pdf_and_attachment(
            vendor,
            attachment_public_id=attachment_public_id,
            provider=provider,
            file_id=file_id,
            blob_category="business_license",
        )
        di = DocumentIntelligenceService().extract_invoice(data, content_type)
        fields = parse_business_license_fields(di)

        return {
            "license_number": fields["license_number"],
            "issuing_authority": fields["issuing_authority"],
            "issue_date": fields["issue_date"],
            "expiry_date": fields["expiry_date"],
            "attachment_public_id": att_public_id,
            "confidence": fields["confidence"],
            "unresolved": fields["unresolved"],
        }

    def ingest(
        self,
        vendor_public_id: str,
        *,
        attachment_public_id: str,
        license_number: Optional[str] = None,
        issuing_authority: Optional[str] = None,
        issue_date: Optional[str] = None,
        expiry_date: Optional[str] = None,
        verification_status: str = "Received",
    ) -> dict:
        from entities.business_license.business.service import BusinessLicenseService
        from entities.business_license_attachment.business.service import (
            BusinessLicenseAttachmentService,
        )
        from entities.vendor.business.service import VendorService

        vendor = VendorService().read_by_public_id(public_id=vendor_public_id)
        if not vendor:
            raise ValueError(f"Vendor with public_id '{vendor_public_id}' not found")

        bl = BusinessLicenseService().create(
            vendor_public_id=vendor_public_id,
            license_number=license_number,
            issuing_authority=issuing_authority,
            issue_date=issue_date,
            expiry_date=expiry_date,
            verification_status=verification_status,
        )

        BusinessLicenseAttachmentService().create(
            business_license_public_id=str(bl.public_id),
            attachment_public_id=attachment_public_id,
        )

        return {
            "business_license": bl.to_dict(),
            "vendor_public_id": vendor_public_id,
        }
