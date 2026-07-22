"""Pure-logic tests for vendor-compliance request schemas (U-089).

Coverage-type discriminators are constrained enums; a bad value must be rejected
at the API boundary (belt-and-suspenders with the DB CHECK constraints).
"""
import pytest
from pydantic import ValidationError

from entities.vendor_compliance.api.schemas import VendorFolderImportRequest
from entities.vendor_insurance_policy.api.schemas import (
    VendorInsurancePolicyCreate,
)


def test_folder_import_accepts_valid_document_type():
    body = VendorFolderImportRequest(
        graph_item_id="item-1",
        document_type="CERTIFICATE_OF_INSURANCE",
    )
    assert body.document_type == "CERTIFICATE_OF_INSURANCE"


def test_folder_import_rejects_unknown_document_type():
    with pytest.raises(ValidationError):
        VendorFolderImportRequest(
            graph_item_id="item-1",
            document_type="W9",
        )


def test_policy_create_accepts_valid_coverage_type():
    body = VendorInsurancePolicyCreate(
        certificate_of_insurance_public_id="coi-1",
        coverage_type="GL",
        each_occurrence="1000000.00",
    )
    assert body.coverage_type == "GL"
    assert body.each_occurrence == "1000000.00"


def test_policy_create_rejects_unknown_coverage_type():
    with pytest.raises(ValidationError):
        VendorInsurancePolicyCreate(
            certificate_of_insurance_public_id="coi-1",
            coverage_type="FLOOD",
        )
