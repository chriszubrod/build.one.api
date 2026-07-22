# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from entities.taxpayer.business.w9_parser import parse_w9_fields


class TaxpayerW9IngestService:
    """Orchestrates W-9 PDF load, DI extraction, and taxpayer ingest for a vendor."""

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
            blob_category="taxpayer_w9",
        )
        di = DocumentIntelligenceService().extract_invoice(data, content_type)
        fields = parse_w9_fields(di)

        tin = fields.get("taxpayer_id_number")
        last4 = tin[-4:] if tin and len(tin) >= 4 else None

        return {
            "entity_name": fields["entity_name"],
            "business_name": fields["business_name"],
            "classification": fields["classification"],
            "taxpayer_id_last4": last4,
            "is_signed": fields["is_signed"],
            "signature_date": fields["signature_date"],
            "attachment_public_id": att_public_id,
            "confidence": fields["confidence"],
            "unresolved": fields["unresolved"],
        }

    def ingest(
        self,
        vendor_public_id: str,
        *,
        attachment_public_id: str,
        entity_name: str,
        business_name: Optional[str] = None,
        classification: Optional[str] = None,
        taxpayer_id_number: str,
        is_signed: Optional[bool] = None,
        signature_date: Optional[str] = None,
    ) -> dict:
        from entities.taxpayer.business.service import TaxpayerService
        from entities.taxpayer_attachment.business.service import TaxpayerAttachmentService
        from entities.vendor.business.service import VendorService

        vendor = VendorService().read_by_public_id(public_id=vendor_public_id)
        if not vendor:
            raise ValueError(f"Vendor with public_id '{vendor_public_id}' not found")

        existing = TaxpayerService().read_by_taxpayer_id_number(taxpayer_id_number)
        if existing:
            taxpayer = existing
        else:
            taxpayer = TaxpayerService().create(
                entity_name=entity_name,
                business_name=business_name,
                classification=classification,
                taxpayer_id_number=taxpayer_id_number,
                is_signed=(1 if is_signed else 0),
                signature_date=signature_date,
            )

        TaxpayerAttachmentService().create(
            taxpayer_public_id=str(taxpayer.public_id),
            attachment_public_id=attachment_public_id,
        )

        VendorService().update_by_public_id(
            vendor_public_id,
            row_version=vendor.row_version,
            taxpayer_public_id=str(taxpayer.public_id),
        )

        return {
            "taxpayer": taxpayer.to_dict(),
            "vendor_public_id": vendor_public_id,
            "reused_existing": existing is not None,
        }
