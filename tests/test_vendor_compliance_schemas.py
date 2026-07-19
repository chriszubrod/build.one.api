"""Pure-logic tests for vendor-compliance request schemas (U-089).

The document-type and coverage-type discriminators are constrained enums; a bad
value must be rejected at the API boundary (belt-and-suspenders with the DB CHECK
constraints), and a valid one accepted.
"""
import pytest
from pydantic import ValidationError

from entities.vendor_compliance_document.api.schemas import (
    VendorComplianceDocumentCreate,
)
from entities.vendor_insurance_policy.api.schemas import (
    VendorInsurancePolicyCreate,
)


def test_document_create_accepts_valid_type_and_defaults_status():
    body = VendorComplianceDocumentCreate(
        vendor_public_id="vend-1",
        document_type="CERTIFICATE_OF_INSURANCE",
    )
    assert body.document_type == "CERTIFICATE_OF_INSURANCE"
    assert body.verification_status == "Received"  # default


def test_document_create_rejects_unknown_type():
    with pytest.raises(ValidationError):
        VendorComplianceDocumentCreate(vendor_public_id="v", document_type="W9")


def test_document_create_rejects_unknown_verification_status():
    with pytest.raises(ValidationError):
        VendorComplianceDocumentCreate(
            vendor_public_id="v",
            document_type="BUSINESS_LICENSE",
            verification_status="Approved",  # not in {Received,Verified,Rejected}
        )


def test_policy_create_accepts_valid_coverage_type():
    body = VendorInsurancePolicyCreate(
        compliance_document_public_id="doc-1",
        coverage_type="GL",
        each_occurrence="1000000.00",
    )
    assert body.coverage_type == "GL"
    assert body.each_occurrence == "1000000.00"


def test_policy_create_rejects_unknown_coverage_type():
    with pytest.raises(ValidationError):
        VendorInsurancePolicyCreate(
            compliance_document_public_id="doc-1",
            coverage_type="FLOOD",
        )
