"""U-336: Vendor/Customer QBO pull falsy-id guard parity with Item/Attachable.

Upstream staged-upsert guards in QboVendorService._upsert_vendor and
QboCustomerService._upsert_customer (production pull path), plus the vendor
external-schema Optional id/sync_token override that lets a malformed (no-Id)
QBO record reach the guard instead of aborting the whole pull batch with a
ValidationError inside QboVendorClient.query_vendors.

The connector-side falsy-qbo_id backstops this unit's comments point at are
already pinned by each family's own tests — do NOT re-pin them here:
test_u290_vendor_qbo_identity_repoint.py::test_vendor_no_qbo_id_raises,
test_u276_customer_project_qbo_identity_repoint.py::test_customer_no_qbo_id_raises
and ::test_project_no_qbo_id_raises.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from integrations.intuit.qbo.customer.business.service import QboCustomerService
from integrations.intuit.qbo.customer.external.schemas import (
    QboCustomer as QboCustomerExternal,
)
from integrations.intuit.qbo.vendor.business.service import QboVendorService
from integrations.intuit.qbo.vendor.external.schemas import (
    QboVendor as QboVendorExternal,
)
from tests.test_u269_qbo_staging_try_except import _client_cm


def test_vendor_external_schema_parses_missing_id_to_none():
    vendor = QboVendorExternal(**{"DisplayName": "No Id Vendor"})
    assert vendor.id is None
    assert vendor.sync_token is None


def test_upsert_vendor_no_qbo_id_raises():
    repo = MagicMock()
    service = QboVendorService(repo=repo)
    qbo_vendor = QboVendorExternal(**{"DisplayName": "No Id Vendor"})

    with pytest.raises(ValueError, match="QBO Vendor must have an ID"):
        service._upsert_vendor(qbo_vendor, "realm-1")

    repo.read_by_qbo_id_and_realm_id.assert_not_called()


def test_upsert_customer_no_qbo_id_raises():
    repo = MagicMock()
    service = QboCustomerService(repo=repo)
    qbo_customer = QboCustomerExternal(**{"DisplayName": "No Id Customer", "Job": False})

    with pytest.raises(ValueError, match="QBO Customer must have an ID"):
        service._upsert_customer(qbo_customer, "realm-1")

    repo.read_by_qbo_id_and_realm_id.assert_not_called()


def test_vendor_staging_loop_skips_falsy_id_record_and_continues():
    repo = MagicMock()
    service = QboVendorService(repo=repo)
    qbo_vendors = [
        QboVendorExternal(**{"Id": "v-1", "SyncToken": "0", "DisplayName": "V1"}),
        QboVendorExternal(**{"DisplayName": "No Id Vendor"}),
        QboVendorExternal(**{"Id": "v-3", "SyncToken": "0", "DisplayName": "V3"}),
    ]
    good_1 = SimpleNamespace(id=101)
    good_3 = SimpleNamespace(id=103)
    repo.read_by_qbo_id_and_realm_id.return_value = None
    repo.create.side_effect = [good_1, good_3]

    with patch(
        "integrations.intuit.qbo.vendor.business.service.QboVendorClient",
        return_value=_client_cm(qbo_vendors),
    ):
        outcome = service.sync_from_qbo(realm_id="realm-1", sync_to_modules=False)

    assert outcome.fetched == 3
    assert outcome.synced == [good_1, good_3]
    assert outcome.staging_failed_ids == ["None"]
    assert repo.create.call_count == 2


def test_customer_staging_loop_skips_falsy_id_record_and_continues():
    repo = MagicMock()
    service = QboCustomerService(repo=repo)
    qbo_customers = [
        QboCustomerExternal(**{"Id": "c-1", "SyncToken": "0", "DisplayName": "C1", "Job": False}),
        QboCustomerExternal(**{"DisplayName": "No Id Customer", "Job": False}),
        QboCustomerExternal(**{"Id": "c-3", "SyncToken": "0", "DisplayName": "C3", "Job": False}),
    ]
    good_1 = SimpleNamespace(id=201)
    good_3 = SimpleNamespace(id=203)
    repo.read_by_qbo_id_and_realm_id.return_value = None
    repo.create.side_effect = [good_1, good_3]

    with patch(
        "integrations.intuit.qbo.customer.business.service.QboCustomerClient",
        return_value=_client_cm(qbo_customers),
    ):
        outcome = service.sync_from_qbo(realm_id="realm-1", sync_to_modules=False)

    assert outcome.fetched == 3
    assert outcome.synced == [good_1, good_3]
    assert outcome.staging_failed_ids == ["None"]
    assert repo.create.call_count == 2
