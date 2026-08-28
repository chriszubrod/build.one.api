"""Pure-logic test for U-330 (part b): `UpdateVendorById`'s in-sproc
`ROLLBACK TRANSACTION` (error-266 trap -- pyodbc runs autocommit-off, so an
in-proc ROLLBACK zeroes the driver's implicit outer transaction) is replaced
with the validate-first / always-COMMIT shape: RowVersion + IsDeleted checked
in the UPDATE's WHERE, `COMMIT` unconditional, the UPDATE's own `OUTPUT`
clause projects the updated row (zero matched rows means an empty result
set -- no separate final SELECT needed, since this sproc projects no joined
columns, unlike `UpdateBudgetRevisionById`). The SQL change alone would
silently turn a concurrency conflict into a quiet `None` (HTTP 200, no data)
instead of today's 409, because `VendorService.update_by_public_id` had no
None-check on the repo's update result -- this unit's companion Python fix
adds that check, raising `ValueError("Concurrency conflict: ...")` exactly
like `BudgetService`/`BudgetRevisionService` do for the identical shape, so
the caller-visible 409 behavior is unchanged even though the conflict signal
moved from a raised SQL error to an empty result set.

No live DB, per the pure-logic harness: the SQL shape is proven statically
(fails red on the pre-fix file, which had two `ROLLBACK TRANSACTION`
branches and no `SET NOCOUNT ON`); the Python contract is proven by mocking
`VendorRepository.update_by_id`'s return value to `None`, which is exactly
what `cursor.fetchone()` sees when the new sproc's OUTPUT clause returns zero
rows."""
import re
from unittest.mock import MagicMock, Mock

import pytest

from entities.vendor.business.model import Vendor
from entities.vendor.business.service import VendorService
from integrations.intuit.qbo.vendor.business.model import QboVendor
from integrations.intuit.qbo.vendor.connector.vendor.business.service import VendorVendorConnector
from tests.test_u330a_vendor_read_by_name_qbo_projection import _proc_body
from tests.test_update_read_column_parity import _strip_sql_comments


def test_update_vendor_by_id_has_no_in_proc_rollback():
    """Static regression guard for the SQL rewrite itself: fails red on the
    pre-fix file (two `ROLLBACK TRANSACTION` branches) and green post-fix."""
    body = _strip_sql_comments(_proc_body("UpdateVendorById"))

    assert "ROLLBACK" not in body.upper()


def test_update_vendor_by_id_commits_unconditionally_exactly_once():
    """Validate-first / always-COMMIT shape: exactly one COMMIT, reached on
    every path (no RETURN before it), and NOCOUNT is set."""
    raw_body = _proc_body("UpdateVendorById")
    body = _strip_sql_comments(raw_body)

    assert body.upper().count("COMMIT TRANSACTION") == 1
    assert "RETURN" not in body.upper()
    assert "SET NOCOUNT ON" in raw_body.upper()


def test_update_vendor_by_id_uses_output_clause_with_no_separate_final_select():
    """The empty-result-set conflict signal comes from the UPDATE's own
    OUTPUT clause (zero matched rows -> zero OUTPUT rows), not from a
    separate final SELECT re-filtering by the (now-stale, post-update)
    @RowVersion parameter -- a naive `SELECT ... WHERE [RowVersion] =
    @RowVersion` would return zero rows on every SUCCESSFUL update too, since
    ROWVERSION changes on every write. OUTPUT's INSERTED.* reflects the
    POST-update row (the new RowVersion), so it has no such problem."""
    body = _strip_sql_comments(_proc_body("UpdateVendorById"))

    assert "OUTPUT" in body.upper()
    assert "INSERTED.[ROWVERSION]" in body.upper()
    assert re.search(r"\bSELECT\b", body, re.IGNORECASE) is None


def _existing_vendor(row_version="AAAAAAAAAAE="):
    return Vendor(
        id=42,
        public_id="00000000-0000-0000-0000-000000000042",
        row_version=row_version,
        created_datetime="2026-01-01 00:00:00",
        modified_datetime="2026-01-02 00:00:00",
        name="Acme Builders",
        abbreviation="ACME",
        taxpayer_id=None,
        vendor_type_id=None,
        is_draft=False,
    )


def test_update_by_public_id_raises_concurrency_conflict_when_repo_returns_none():
    """Proves the companion Python fix: when the repo's update returns None
    (the new empty-result-set conflict signal a stale RowVersion now
    produces), the service must raise a 'Concurrency conflict' ValueError --
    not silently return None. `shared.api.responses.raise_workflow_error`
    maps any 'concurrency'-containing message to HTTP 409, so this message
    shape is load-bearing for preserving the caller's existing behavior.

    Red-before-fix: reverting this unit's service.py change (back to a bare
    `return self.repo.update_by_id(existing)`) makes this test fail, because
    the service would return None instead of raising."""
    mock_repo = MagicMock()
    mock_repo.read_by_public_id.return_value = _existing_vendor()
    mock_repo.update_by_id.return_value = None

    service = VendorService(repo=mock_repo)

    with pytest.raises(ValueError, match="(?i)concurrency conflict"):
        service.update_by_public_id(
            "00000000-0000-0000-0000-000000000042",
            row_version="AAAAAAAAAAI=",
            notes="updated notes",
        )


def test_update_by_public_id_returns_vendor_on_successful_update():
    """Happy path unchanged: a real update still returns the updated Vendor,
    not an exception."""
    mock_repo = MagicMock()
    mock_repo.read_by_public_id.return_value = _existing_vendor()
    updated = _existing_vendor(row_version="AAAAAAAAAAI=")
    updated.notes = "updated notes"
    mock_repo.update_by_id.return_value = updated

    service = VendorService(repo=mock_repo)

    result = service.update_by_public_id(
        "00000000-0000-0000-0000-000000000042",
        row_version="AAAAAAAAAAI=",
        notes="updated notes",
    )

    assert result is updated


def test_update_by_public_id_returns_none_when_vendor_not_found():
    """Not-found stays a distinct, earlier-returning None -- the service
    checks existence via read_by_public_id BEFORE ever calling repo.
    update_by_id, so this path never reaches the conflict-raising branch."""
    mock_repo = MagicMock()
    mock_repo.read_by_public_id.return_value = None

    service = VendorService(repo=mock_repo)

    result = service.update_by_public_id(
        "00000000-0000-0000-0000-000000000099", notes="irrelevant",
    )

    assert result is None
    mock_repo.update_by_id.assert_not_called()


# --------------------------------------------------------------------------- #
# QBO vendor connector — direct repo.update_by_id caller (Codex round-1 P1)
# --------------------------------------------------------------------------- #
#
# VendorService.update_by_public_id's None-check above does NOT protect this
# call site: VendorVendorConnector._apply_vendor_fields_and_sync
# (integrations/intuit/qbo/vendor/connector/vendor/business/service.py) calls
# `self.vendor_service.repo.update_by_id(vendor)` directly, bypassing the
# service layer entirely. Before this unit's SQL change, a stale RowVersion
# here raised a DatabaseConcurrencyError (RAISERROR); after it, the same race
# returns None, which the connector previously reassigned straight into
# `vendor` and dereferenced on the very next line. Fixed by returning None
# (not raising directly) -- `run_identity_fastpath_dbo_only`'s own `_apply()`
# already raises `raise_concurrent_write_race` (a RuntimeError) unconditionally
# whenever its `apply_fields` callback returns None, mirroring
# `ProjectCustomerConnector._apply_project_fields_and_sync`'s established
# convention for the identical shared primitive.


def _make_qbo_vendor(**overrides):
    defaults = dict(
        id=1, public_id=None, row_version=None, created_datetime=None,
        modified_datetime=None, qbo_id="QBO-V-1", sync_token=None, realm_id="r1",
        display_name="Acme Supply", title=None, given_name=None, middle_name=None,
        family_name=None, suffix=None, company_name=None, print_on_check_name=None,
        tax_identifier=None, vendor_1099=None, active=None, primary_email_addr=None,
        primary_phone=None, mobile=None, fax=None, bill_addr_id=None, balance=None,
        acct_num=None, web_addr=None,
    )
    defaults.update(overrides)
    return QboVendor(**defaults)


def _build_vendor_connector():
    connector = VendorVendorConnector(
        vendor_service=Mock(), vendor_address_service=Mock(),
        address_connector=Mock(), reconciliation_repo=Mock(),
    )
    connector._sync_addresses = Mock()
    connector.vendor_service.read_deleted_by_qbo_identity.return_value = None
    return connector


def test_qbo_connector_raises_cleanly_on_stale_rowversion_instead_of_crashing():
    """Regression for Codex round-1 P1 on this unit: a concurrent web edit
    racing the QBO pull's name-fill write must not crash with an opaque
    AttributeError on `None.id`. The shared `run_identity_fastpath_dbo_only`
    primitive (not a hand-rolled guard in the connector -- see the /simplify
    altitude-pass fix above) raises a RuntimeError instead.

    Red-before-fix: reverting the connector's guard (integrations/intuit/qbo/
    vendor/connector/vendor/business/service.py's `_apply_vendor_fields_and_
    sync` returning `vendor` unconditionally instead of `None` on a miss)
    makes this raise AttributeError instead, which `pytest.raises(
    RuntimeError, ...)` does not catch."""
    connector = _build_vendor_connector()
    qbo_vendor = _make_qbo_vendor(display_name="Acme Supply")
    vendor = Mock(id=100)
    vendor.name = ""  # `Mock(name=...)` sets the mock's OWN repr name, not this attribute
    connector.vendor_service.read_by_qbo_identity.return_value = vendor
    connector.vendor_service.repo.update_by_id.return_value = None

    with pytest.raises(RuntimeError, match=r"Failed to update Vendor 100.*concurrent write race"):
        connector.sync_from_qbo_vendor(qbo_vendor)
