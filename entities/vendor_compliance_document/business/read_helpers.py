from typing import Optional


def latest_document_by_type(docs) -> dict:
    """First-seen VendorComplianceDocument per document_type.
    Relies on the read sproc's ORDER BY DocumentType, CreatedDatetime DESC (newest first)."""
    latest = {}
    for doc in docs:
        dt = doc.document_type
        if dt and dt not in latest:
            latest[dt] = doc
    return latest


def resolve_latest_w9_attachment(vendor):
    """Resolve a vendor's latest W-9 Attachment via Taxpayer -> TaxpayerAttachment -> Attachment.
    Returns the Attachment (may lack blob_url/public_id — caller checks the field it needs) or None.
    Failure-isolated: any lookup error -> None (never raises)."""
    from entities.attachment.business.service import AttachmentService
    from entities.taxpayer.business.service import TaxpayerService
    from entities.taxpayer_attachment.business.service import TaxpayerAttachmentService
    try:
        if not vendor.taxpayer_id:
            return None
        taxpayer = TaxpayerService().read_by_id(vendor.taxpayer_id)
        if not taxpayer or not taxpayer.public_id:
            return None
        links = TaxpayerAttachmentService().read_by_taxpayer_id(taxpayer.public_id)
        if not links:
            return None
        latest = links[0]
        if not latest.attachment_id:
            return None
        return AttachmentService().read_by_id(latest.attachment_id)
    except Exception:
        return None


def resolve_current_business_license(vendor):
    """The vendor's current BusinessLicense (latest by the read sproc ordering:
    ExpiryDate DESC, IssueDate DESC, Id DESC), or None."""
    from entities.business_license.business.service import BusinessLicenseService

    try:
        licenses = BusinessLicenseService().read_by_vendor_id(int(vendor.id))
        return licenses[0] if licenses else None
    except Exception:
        return None


def resolve_business_license_attachment(business_license):
    """The Attachment linked to a BusinessLicense (latest link), or None."""
    from entities.attachment.business.service import AttachmentService
    from entities.business_license_attachment.business.service import (
        BusinessLicenseAttachmentService,
    )

    try:
        if not business_license or not business_license.public_id:
            return None
        links = BusinessLicenseAttachmentService().read_by_business_license_id(
            str(business_license.public_id)
        )
        if not links or not links[0].attachment_id:
            return None
        return AttachmentService().read_by_id(links[0].attachment_id)
    except Exception:
        return None
