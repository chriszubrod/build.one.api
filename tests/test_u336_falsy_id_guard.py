"""U-336: Vendor/Customer QBO pull falsy-id guard parity with Item/Attachable.

U-505 added the fifth family, CompanyInfo. Its guard lives in the same place
(`QboCompanyInfoService._build_company_info`), but its tests live in
tests/test_u504_company_info_staging_repoint.py because what they pin is not
guard *parity* -- it is the watermark regression the guard's ABSENCE caused
once CompanyInfo went transient and stopped staging a row.

TALLY (U-507): all ELEVEN watermarked QBO pull families now HOLD on a missing
QBO Id; none skips. Three mechanisms, pinned as a test by
tests/test_u507_staging_skip_removal.py::test_every_pull_family_holds_on_a_missing_qbo_id:

  * raise-and-hold guard  -- company_info, item, customer, vendor (+ attachable,
    which is not watermarked): their external read schema declares `id` as
    Optional so a malformed row REACHES the guard, which raises ValueError and
    is recorded as a staging failure rather than aborting the batch.
    ⚠️ CORRECTED (U-507 round 2): this sentence was FALSE for attachable when
    written. The mechanism depends on `str_strip_whitespace` turning a blank
    into "" so the `if not x.id` guard sees something falsy -- and attachable's
    local `_QboBaseModel` set only `populate_by_name`, so `Id=" "` stayed a
    TRUTHY " ", walked past the guard, and stamped `dbo.Attachment.QboId = " "`.
    Four of the five stripped; attachable did not. Fixed by giving attachable
    the same config as its siblings, which makes the sentence true rather than
    by rewording it. Pinned at RUNTIME by
    tests/test_u507_staging_skip_removal.py::test_raise_and_hold_families_reject_whitespace_at_runtime.
  * fail-and-hold in the staging loop -- reimburse_charge (U-507 replaced its
    lone skip-and-advance with record_staging_failure + `continue`).
  * schema-rejected falsy `id` -- term, account, bill, purchase, invoice,
    vendorcredit. These have NO in-service guard and need none. Their external
    read schema declares `id: str` REQUIRED *and* binds the shared before-validator
    `integrations.intuit.qbo.base.id_validation.require_non_blank_qbo_id`. Between
    them those two reject every shape of a missing QBO Id -- absent, NULL, `""`,
    and whitespace-only -- so `[QboBill(**row) for row in page]` raises pydantic
    ValidationError on a malformed row and aborts the WHOLE page before anything
    stages. They hold HARDER than a guard does: sync_from_qbo never returns,
    commit is never reached, so no hold marker is stamped and the bounded
    force-advance (QBO_WATERMARK_HOLD_BOUND_SECONDS) cannot engage at all.

    PRECISION MATTERS HERE -- this docstring has been wrong TWICE.
      (1) It first claimed these six "still stage, so a truthy PK carries them
          into the connector". False: the pull raises before anything stages.
      (2) The correction then claimed "`id` is REQUIRED so a no-Id row aborts the
          page". ALSO false, and it is what let U-507's tally guard pass
          vacuously: pydantic's required rule rejects an ABSENT or NULL field,
          NOT a falsy one. `{"Id": ""}` validated, staged with QBO id `""`,
          recorded SUCCESS and ADVANCED the watermark; `{"Id": " "}` did the same
          (stripped to `""` on the five that inherit `str_strip_whitespace`, kept
          VERBATIM as a truthy garbage id on vendorcredit, which does not strip).
    The invariant is NOT "required" and NOT "truthy" -- it is `str(Id).strip()` is
    non-empty, and as of U-507 fix round 1 the shared validator is what enforces
    it. `required` still does its own half of the job (absent / NULL); neither
    half is sufficient alone. Pinned by
    tests/test_u507_staging_skip_removal.py::test_schema_required_family_rejects_a_blank_or_whitespace_qbo_id,
    which proves rejection by CONSTRUCTING each schema with a falsy Id rather
    than by inspecting `is_required()`.

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
