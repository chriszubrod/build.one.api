"""
Certificate of insurance PDF load, DI extraction, and ingest for a vendor.

The fetch/back-half lives in entities.vendor_compliance.business.ingest_fetch.
"""

# Python Standard Library Imports
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

# Third-party Imports

# Local Imports
from entities.certificate_of_insurance.business.coi_parser import (
    parse_certificate_of_insurance_fields,
)


class CertificateOfInsuranceIngestService:
    """Orchestrates COI PDF load, DI extraction, and ingest for a vendor."""

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
            blob_category="certificate_of_insurance",
        )
        di = DocumentIntelligenceService().extract_invoice(data, content_type)
        fields = parse_certificate_of_insurance_fields(di)

        return {
            "issuing_authority": fields["issuing_authority"],
            "issue_date": fields["issue_date"],
            "policies": fields["policies"],
            "attachment_public_id": att_public_id,
            "confidence": fields["confidence"],
            "unresolved": fields["unresolved"],
        }

    def ingest(
        self,
        vendor_public_id: str,
        *,
        attachment_public_id: str,
        issuing_authority: Optional[str] = None,
        issue_date: Optional[str] = None,
        verification_status: str = "Received",
        policies: Optional[list[dict[str, Any]]] = None,
    ) -> dict:
        from entities.attachment.business.service import AttachmentService
        from entities.certificate_of_insurance.business.service import (
            CertificateOfInsuranceService,
        )
        from entities.vendor.business.service import VendorService
        from entities.vendor_insurance_policy.business.service import (
            VendorInsurancePolicyService,
        )

        vendor = VendorService().read_by_public_id(public_id=vendor_public_id)
        if not vendor:
            raise ValueError(f"Vendor with public_id '{vendor_public_id}' not found")

        att = AttachmentService().read_by_public_id(public_id=attachment_public_id)
        if not att or not att.id:
            # The cert PDF is the evidence a COI exists — never create a
            # certificate row pointing at nothing (Codex U-116 finding #2).
            raise ValueError(
                f"Attachment with public_id '{attachment_public_id}' not found"
            )
        attachment_id = int(att.id)

        # Pre-validate the whole policy batch BEFORE any write. The cert and each
        # policy commit through separate sprocs, so a malformed row partway through
        # would otherwise leave a committed cert + partial policies (Codex #3).
        policy_rows = policies or []
        _valid_coverage = {"GL", "WC", "OTHER"}
        for i, p in enumerate(policy_rows):
            ct = p.get("coverage_type")
            if ct not in _valid_coverage:
                raise ValueError(
                    f"policies[{i}] invalid coverage_type {ct!r}; "
                    f"expected one of {sorted(_valid_coverage)}"
                )
            for money_field in ("each_occurrence", "aggregate"):
                val = p.get(money_field)
                if val is not None and val != "":
                    try:
                        Decimal(str(val))
                    except (InvalidOperation, ValueError) as e:
                        raise ValueError(
                            f"policies[{i}] invalid {money_field} {val!r}"
                        ) from e

        cert = CertificateOfInsuranceService().create(
            vendor_public_id=vendor_public_id,
            issuing_authority=issuing_authority,
            issue_date=issue_date,
            attachment_id=attachment_id,
            verification_status=verification_status,
        )

        vip_service = VendorInsurancePolicyService()
        cert_public_id = str(cert.public_id)
        for p in policy_rows:
            vip_service.create(
                certificate_of_insurance_public_id=cert_public_id,
                coverage_type=p["coverage_type"],
                carrier=p.get("carrier"),
                policy_number=p.get("policy_number"),
                each_occurrence=p.get("each_occurrence"),
                aggregate=p.get("aggregate"),
                effective_date=p.get("effective_date"),
                expiry_date=p.get("expiry_date"),
            )

        return {
            "certificate": cert.to_dict(),
            "policy_count": len(policy_rows),
            "vendor_public_id": vendor_public_id,
        }
