# Python Standard Library Imports
import logging
from datetime import date
from typing import Optional

# Third-party Imports

# Local Imports
from entities.vendor.business.service import VendorService
from entities.vendor_compliance_document.business.read_helpers import (
    latest_document_by_type,
    resolve_business_license_attachment,
    resolve_current_business_license,
    resolve_latest_w9_attachment,
)
from entities.vendor_compliance_document.business.service import VendorComplianceDocumentService
from entities.vendor_compliance_document.business.validity import (
    compute_doc_status,
    days_until_expiry,
)
from entities.vendor_insurance_policy.business.service import VendorInsurancePolicyService
from entities.vendor_type.business.service import VendorTypeService
from integrations.box.folder.persistence.repo import BoxVendorFolderRepository
from integrations.ms.sharepoint.driveitem.connector.vendor.business.service import (
    DriveItemVendorConnector,
)

logger = logging.getLogger(__name__)

SLOT_DOCUMENT_TYPES = (
    "CONTRACTORS_LICENSE",
    "CERTIFICATE_OF_INSURANCE",
)
W9_SLOT = "W9"


class VendorComplianceDashboardService:
    """
    Read-model assembler for the vendor compliance dashboard.
    """

    def build_dashboard(self, today: Optional[date] = None) -> dict:
        today = today or date.today()

        # TODO(U-089): dedicated roster sproc if the roster grows
        all_vendors = VendorService().read_all()
        roster_vendors = [
            v for v in all_vendors
            if v.track_compliance and not v.is_deleted
        ]

        tradesman_type = VendorTypeService().read_by_name("Tradesman")
        tradesman_type_id = int(tradesman_type.id) if tradesman_type and tradesman_type.id else None

        suggestions = []
        if tradesman_type_id is not None:
            suggestions = [
                {
                    "vendor_public_id": v.public_id,
                    "vendor_name": v.name,
                    "vendor_type": "Tradesman",
                }
                for v in all_vendors
                if not v.is_deleted
                and not v.track_compliance
                and v.vendor_type_id == tradesman_type_id
            ]

        doc_service = VendorComplianceDocumentService()
        roster = [self._build_roster_entry(vendor, doc_service, today) for vendor in roster_vendors]

        return {"roster": roster, "suggestions": suggestions}

    def _build_roster_entry(self, vendor, doc_service: VendorComplianceDocumentService, today: date) -> dict:
        docs = doc_service.read_by_vendor_id(vendor_id=int(vendor.id))
        latest_by_type = latest_document_by_type(docs)

        slots = {}
        for doc_type in SLOT_DOCUMENT_TYPES:
            doc = latest_by_type.get(doc_type)
            if doc is None:
                slots[doc_type] = {"status": "missing"}
            else:
                slots[doc_type] = {
                    "status": compute_doc_status(doc.expiry_date, today),
                    "document_public_id": doc.public_id,
                    "document_number": doc.document_number,
                    "issuing_authority": doc.issuing_authority,
                    "expiry_date": doc.expiry_date,
                    "days_until_expiry": days_until_expiry(doc.expiry_date, today),
                    "verification_status": doc.verification_status,
                }
                if doc_type == "CERTIFICATE_OF_INSURANCE" and doc.id:
                    policies = VendorInsurancePolicyService().read_by_compliance_document_id(int(doc.id))
                    slots[doc_type]["policy_count"] = len(policies)

        slots[W9_SLOT] = self._build_w9_slot(vendor)
        slots["BUSINESS_LICENSE"] = self._build_business_license_slot(vendor, today)

        vendor_id = int(vendor.id)
        try:
            sharepoint_linked = bool(
                DriveItemVendorConnector().get_driveitem_for_vendor(vendor_id)
            )
        except Exception as error:
            logger.warning(
                "vendor_compliance_dashboard.sharepoint_folder_lookup.failed",
                extra={
                    "event_name": "vendor_compliance_dashboard.sharepoint_folder_lookup.failed",
                    "vendor_id": vendor_id,
                    "error": str(error),
                },
            )
            sharepoint_linked = False

        try:
            box_linked = bool(BoxVendorFolderRepository().read_by_vendor_id(vendor_id))
        except Exception as error:
            logger.warning(
                "vendor_compliance_dashboard.box_folder_lookup.failed",
                extra={
                    "event_name": "vendor_compliance_dashboard.box_folder_lookup.failed",
                    "vendor_id": vendor_id,
                    "error": str(error),
                },
            )
            box_linked = False

        folders = {
            "sharepoint": {"status": "linked" if sharepoint_linked else "missing"},
            "box": {"status": "linked" if box_linked else "missing"},
            "both_linked": sharepoint_linked and box_linked,
        }

        return {
            "vendor_public_id": vendor.public_id,
            "vendor_name": vendor.name,
            "vendor_abbreviation": vendor.abbreviation,
            "slots": slots,
            "folders": folders,
        }

    def _build_w9_slot(self, vendor) -> dict:
        att = resolve_latest_w9_attachment(vendor)
        if att and att.public_id:
            return {"status": "present", "attachment_public_id": att.public_id}
        return {"status": "missing"}

    def _build_business_license_slot(self, vendor, today):
        bl = resolve_current_business_license(vendor)
        if not bl:
            return {"status": "missing"}
        att = resolve_business_license_attachment(bl)
        return {
            "status": compute_doc_status(bl.expiry_date, today),
            "document_public_id": bl.public_id,
            "document_number": bl.license_number,
            "issuing_authority": bl.issuing_authority,
            "expiry_date": bl.expiry_date,
            "days_until_expiry": days_until_expiry(bl.expiry_date, today),
            "verification_status": bl.verification_status,
            "attachment_public_id": att.public_id if att else None,
        }
