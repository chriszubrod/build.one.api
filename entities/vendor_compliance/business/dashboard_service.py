# Python Standard Library Imports
import logging
from datetime import date
from types import SimpleNamespace
from typing import Optional

# Third-party Imports

# Local Imports
from entities.certificate_of_insurance.business.service import CertificateOfInsuranceService
from entities.vendor.business.service import VendorService
from entities.vendor_compliance.business.coverage_resolver import resolve_coverage_map
from entities.vendor_compliance.business.read_helpers import (
    resolve_business_license_attachment,
    resolve_contractors_license_attachment,
    resolve_current_business_license,
    resolve_current_contractors_license,
    resolve_latest_w9_attachment,
)
from entities.vendor_compliance.business.validity import (
    compute_doc_status,
    days_until_expiry,
)
from entities.vendor_insurance_policy.business.service import VendorInsurancePolicyService
from entities.vendor_type.business.service import VendorTypeService
from entities.vendor_type_required_coverage.business.service import VendorTypeRequiredCoverageService
from integrations.box.folder.persistence.repo import BoxVendorFolderRepository
from integrations.ms.sharepoint.driveitem.connector.vendor.business.service import (
    DriveItemVendorConnector,
)

logger = logging.getLogger(__name__)

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

        roster = [self._build_roster_entry(vendor, today) for vendor in roster_vendors]

        return {"roster": roster, "suggestions": suggestions}

    def _build_roster_entry(self, vendor, today: date) -> dict:
        slots = {}
        slots["CERTIFICATE_OF_INSURANCE"] = self._build_coi_slot(vendor, today)

        slots[W9_SLOT] = self._build_w9_slot(vendor)
        slots["BUSINESS_LICENSE"] = self._build_business_license_slot(vendor, today)
        slots["CONTRACTORS_LICENSE"] = self._build_contractors_license_slot(vendor, today)

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

    def _build_coi_slot(self, vendor, today: date) -> dict:
        try:
            vendor_type_id = getattr(vendor, "vendor_type_id", None)
            required_coverage_types: list[str] = []
            if vendor_type_id:
                required_rows = VendorTypeRequiredCoverageService().read_by_vendor_type_id(
                    int(vendor_type_id)
                )
                required_coverage_types = [
                    row.coverage_type for row in required_rows if row.coverage_type
                ]

            certs = CertificateOfInsuranceService().read_by_vendor_id(int(vendor.id))
            latest_certificate_public_id = certs[0].public_id if certs else None
            verification_status = certs[0].verification_status if certs else None

            annotated_policies = []
            policy_service = VendorInsurancePolicyService()
            for cert in certs:
                if not cert.id:
                    continue
                policies = policy_service.read_by_certificate_of_insurance_id(int(cert.id))
                for policy in policies:
                    annotated_policies.append(
                        SimpleNamespace(
                            coverage_type=policy.coverage_type,
                            expiry_date=policy.expiry_date,
                            carrier=policy.carrier,
                            policy_number=policy.policy_number,
                            each_occurrence=policy.each_occurrence,
                            aggregate=policy.aggregate,
                            public_id=policy.public_id,
                            certificate_public_id=cert.public_id,
                        )
                    )

            resolved = resolve_coverage_map(required_coverage_types, annotated_policies, today)

            earliest_expiry: Optional[str] = None
            for coverage_type in required_coverage_types:
                entry = resolved["coverages"].get(coverage_type) or {}
                expiry = entry.get("expiry_date")
                if not expiry:
                    continue
                if earliest_expiry is None or expiry < earliest_expiry:
                    earliest_expiry = expiry

            slot = {
                **resolved,
                "document_public_id": latest_certificate_public_id,
                "verification_status": verification_status,
                "policy_count": len(annotated_policies),
                "certificate_count": len(certs),
                "expiry_date": earliest_expiry,
                "days_until_expiry": days_until_expiry(earliest_expiry, today),
            }
            return slot
        except Exception as error:
            logger.warning(
                "vendor_compliance_dashboard.coi_slot.failed",
                extra={
                    "event_name": "vendor_compliance_dashboard.coi_slot.failed",
                    "vendor_id": getattr(vendor, "id", None),
                    "error": str(error),
                },
            )
            return {
                "status": "missing",
                "compliant": False,
                "coverages": {},
                "extra_coverages": [],
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

    def _build_contractors_license_slot(self, vendor, today):
        cl = resolve_current_contractors_license(vendor)
        if not cl:
            return {"status": "missing"}
        att = resolve_contractors_license_attachment(cl)
        return {
            "status": compute_doc_status(cl.expiry_date, today),
            "document_public_id": cl.public_id,
            "document_number": cl.license_number,
            "issuing_authority": cl.issuing_authority,
            "classification": cl.classification,
            "expiry_date": cl.expiry_date,
            "days_until_expiry": days_until_expiry(cl.expiry_date, today),
            "verification_status": cl.verification_status,
            "attachment_public_id": att.public_id if att else None,
        }
