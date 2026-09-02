"""
U-362 — retire qbo.InvoiceLineItemInvoiceLine (U-349 program family 9/11) and
repoint InvoiceLineItemConnector.sync_from_qbo_invoice_line onto the shared
dbo-only line primitive (base/identity_fastpath.py::run_line_identity_fastpath_
dbo_only), cloning U-361/U-361b's shape onto the second line family.

The helper's own state machine is exhaustively pinned in
tests/test_u361_line_identity_fastpath_dbo_only_helper.py; these tests prove THIS
connector's wiring:

  * HIT: update in place, no identity re-stamp (except the one-off realm self-heal
    for a legacy QboId-without-RealmId row), ROWVERSION race -> RuntimeError.
    The source_type reset-to-Manual-on-amount-change decision and the U-272
    source-provenance mirror both survive from the with-mapping era, unchanged.
  * MISS: create, then the BARE `set_qbo_identity` stamp + U-272 provenance
    mirror + re-read; a missing realm refuses BEFORE creating; a stamp failure
    rolls the fresh line back and re-raises; a rollback that itself fails
    records an `orphan_ili_line_item` ReconciliationIssue.
  * Re-adopt (U-361b's shared matcher, cloned): MISS tries a content-fingerprint
    READOPT first (a stale-identity orphan, e.g. a QBO line-id regeneration)
    before ever creating. Restricted to Manual-sourced candidates only (U-247's
    original restriction, carried forward) — Bill/Expense/BillCreditLineItem-
    sourced lines are matched via their source FK elsewhere, never by content
    fingerprint.
  * The two executed consumers found beyond the connector: the invoice header
    connector's `_has_qbo_line_provenance` (adopt-gate for identity-lost
    invoices) repointed onto the dbo-native InvoiceLineItem.QboId column, and
    the stale-QboInvoiceLine cleanup in integrations/intuit/qbo/invoice/
    business/service.py no longer clears any line mapping first.
  * Regression: ReadInvoiceLineItemById / ReadInvoiceLineItemsByInvoiceId /
    ReadInvoiceLineItemByPublicId / UpdateInvoiceLineItemById all silently
    omitted QboId/RealmId from their projections — the exact bug class U-361's
    code review caught in ReadBillCreditLineItemById/UpdateBillCreditLineItemById,
    found here by inspecting the base SQL file directly before relying on it
    (not by the tests failing, since a Mock-backed unit test can't catch a
    missing SQL column). Fixed in the same unit; guarded here so it can't
    regress silently.

Supersedes tests/test_u293b_invoice_line_item_qbo_identity_repoint.py (the
with-mapping U-293b wiring, deleted in this unit).
"""
import sys
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from integrations.intuit.qbo.invoice.business.service import QboInvoiceService
from integrations.intuit.qbo.invoice.connector.invoice.business.service import (
    InvoiceInvoiceConnector,
)
from integrations.intuit.qbo.invoice.connector.invoice_line_item.business.service import (
    InvoiceLineItemConnector,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_u304_rollback_lock import _recording_lock_factory

LOCK_PATCH_TARGET = "integrations.intuit.qbo.base.identity_fastpath.qbo_app_lock"
INVOICE_HEADER_SERVICE = "integrations.intuit.qbo.invoice.connector.invoice.business.service"
LINE_SERVICE = "integrations.intuit.qbo.invoice.connector.invoice_line_item.business.service"

# The MISS branch runs under run_line_identity_fastpath_dbo_only's create lock —
# grant it for every test in this pure-logic module (tests that need to OBSERVE
# lock traffic patch a tracking lock over this grant explicitly).
pytestmark = pytest.mark.usefixtures("grant_qbo_app_lock")


def _make_qbo_line(**overrides):
    defaults = dict(
        id=42,
        qbo_invoice_id=4,
        qbo_line_id="1",
        description="Service",
        amount=Decimal("100"),
        unit_price=None,
        qty=None,
        line_num=1,
        service_date="2026-07-15",
        linked_txn_type=None,
        linked_txn_id=None,
        item_ref_value=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _build_connector():
    connector = InvoiceLineItemConnector()
    ili_svc = Mock()
    ili_svc.repo = Mock()
    # U-361b shape: the readopt step (run before every create) reads this —
    # default to "nothing to adopt" so tests unrelated to readopt exercise a
    # clean MISS/HIT without also needing to stub it themselves.
    ili_svc.read_by_invoice_id.return_value = []
    invoice_service = Mock()
    reconciliation_repo = Mock()
    connector.invoice_line_item_service = ili_svc
    connector.invoice_service = invoice_service
    connector.reconciliation_repo = reconciliation_repo
    return connector, ili_svc, invoice_service, reconciliation_repo


def _stamped_row(line_id, qbo_line_id, realm_id="realm-1", **overrides):
    defaults = dict(
        id=line_id, public_id=f"pub-{line_id}", row_version=f"rv-{line_id}",
        qbo_id=qbo_line_id, realm_id=realm_id, source_type="Manual", amount=Decimal("100"),
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# --- the connector no longer knows about a mapping table ----------------------


def test_connector_has_no_mapping_repo_and_no_fingerprint_adopt_method():
    connector = InvoiceLineItemConnector()
    assert not hasattr(connector, "mapping_repo")
    assert not hasattr(connector, "_find_and_match_manual_by_fingerprint")
    assert not hasattr(connector, "_record_line_identity_mapping_conflict_issue")
    assert not hasattr(connector, "create_mapping")
    assert not hasattr(connector, "get_mapping_by_invoice_line_item_id")
    assert not hasattr(connector, "get_mapping_by_qbo_invoice_line_id")


# --- HIT ----------------------------------------------------------------------


def test_hit_updates_in_place_and_does_not_restamp_a_realm_complete_row():
    connector, ili_svc, invoice_service, reconciliation_repo = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="1")
    direct = _stamped_row(55, "1", source_type="Manual", amount=Decimal("100"))
    ili_svc.read_by_qbo_identity.return_value = direct
    updated = SimpleNamespace(id=55, public_id="pub-55")
    ili_svc.update_by_public_id.return_value = updated

    result = connector.sync_from_qbo_invoice_line(19146, "inv-pub", qbo_line, frozenset({"1"}), realm_id="realm-1")

    assert result is updated
    ili_svc.read_by_qbo_identity.assert_called_once_with(19146, "1")
    ili_svc.update_by_public_id.assert_called_once()
    assert ili_svc.update_by_public_id.call_args.args == ("pub-55",)
    assert ili_svc.update_by_public_id.call_args.kwargs["row_version"] == "rv-55"
    assert ili_svc.update_by_public_id.call_args.kwargs["is_draft"] is False
    ili_svc.create.assert_not_called()
    ili_svc.repo.set_qbo_identity.assert_not_called()
    invoice_service._reset_source_as_unbilled.assert_not_called()
    reconciliation_repo.create.assert_not_called()
    # U-272 mirror still fires on every touch, HIT included.
    ili_svc.repo.set_source_provenance.assert_called_once()
    assert ili_svc.repo.set_source_provenance.call_args.kwargs["invoice_line_item_id"] == 55


def test_hit_amount_change_on_non_manual_source_resets_to_manual_and_unbills():
    """Survives from the with-mapping era unchanged: a source-backed line whose
    QBO amount diverges gets reset to Manual and its abandoned source un-billed."""
    connector, ili_svc, invoice_service, _ = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="1", amount=Decimal("999"))
    direct = _stamped_row(55, "1", source_type="BillLineItem", amount=Decimal("100"))
    ili_svc.read_by_qbo_identity.return_value = direct
    updated = SimpleNamespace(id=55, public_id="pub-55")
    ili_svc.update_by_public_id.return_value = updated

    connector.sync_from_qbo_invoice_line(19146, "inv-pub", qbo_line, frozenset({"1"}), realm_id="realm-1")

    invoice_service._reset_source_as_unbilled.assert_called_once_with(direct)
    kwargs = ili_svc.update_by_public_id.call_args.kwargs
    assert kwargs["source_type"] == "Manual"


def test_hit_heals_a_legacy_row_missing_its_realm_half():
    """U-293-dw's atomic-pair gap: a row found by QboId but stamped without a
    RealmId gets the realm written once (best-effort, no enforce_realm_pairing
    wrapper needed any more — the bare repo call carries the same guarantee via
    the sproc's own atomic-pair guard)."""
    connector, ili_svc, _, _ = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="1")
    ili_svc.read_by_qbo_identity.return_value = _stamped_row(55, "1", realm_id=None)
    ili_svc.update_by_public_id.return_value = SimpleNamespace(id=55, public_id="pub-55")

    connector.sync_from_qbo_invoice_line(19146, "inv-pub", qbo_line, frozenset({"1"}), realm_id="realm-1")

    ili_svc.repo.set_qbo_identity.assert_called_once_with(id=55, qbo_id="1", realm_id="realm-1")


def test_hit_without_a_call_realm_does_not_try_to_heal():
    connector, ili_svc, _, _ = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="1")
    ili_svc.read_by_qbo_identity.return_value = _stamped_row(55, "1", realm_id=None)
    ili_svc.update_by_public_id.return_value = SimpleNamespace(id=55, public_id="pub-55")

    connector.sync_from_qbo_invoice_line(19146, "inv-pub", qbo_line, frozenset({"1"}))

    ili_svc.repo.set_qbo_identity.assert_not_called()


def test_hit_update_returning_none_raises_runtime_error_and_never_stamps():
    connector, ili_svc, _, _ = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="1")
    ili_svc.read_by_qbo_identity.return_value = _stamped_row(55, "1")
    ili_svc.update_by_public_id.return_value = None

    with pytest.raises(RuntimeError, match="concurrent write race"):
        connector.sync_from_qbo_invoice_line(19146, "inv-pub", qbo_line, frozenset({"1"}), realm_id="realm-1")

    ili_svc.repo.set_qbo_identity.assert_not_called()
    ili_svc.create.assert_not_called()


# --- MISS ---------------------------------------------------------------------


def test_miss_creates_then_bare_stamps_then_returns_the_reread():
    connector, ili_svc, _, reconciliation_repo = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="1", description="Materials", amount=Decimal("500"))
    ili_svc.read_by_qbo_identity.return_value = None
    created = SimpleNamespace(id=77, public_id="pub-77")
    ili_svc.create.return_value = created
    reread = _stamped_row(77, "1")
    ili_svc.read_by_id.return_value = reread

    result = connector.sync_from_qbo_invoice_line(19146, "inv-pub", qbo_line, frozenset({"1"}), realm_id="realm-1")

    assert result is reread  # the re-read, not the stale in-memory candidate
    ili_svc.create.assert_called_once()
    assert ili_svc.create.call_args.kwargs["invoice_public_id"] == "inv-pub"
    assert ili_svc.create.call_args.kwargs["description"] == "Materials"
    assert ili_svc.create.call_args.kwargs["amount"] == Decimal("500")
    assert ili_svc.create.call_args.kwargs["source_type"] == "Manual"
    assert ili_svc.create.call_args.kwargs["is_draft"] is False
    ili_svc.repo.set_qbo_identity.assert_called_once_with(id=77, qbo_id="1", realm_id="realm-1")
    ili_svc.repo.set_source_provenance.assert_called_once()
    assert ili_svc.repo.set_source_provenance.call_args.kwargs["invoice_line_item_id"] == 77
    ili_svc.read_by_id.assert_called_once_with(77)
    ili_svc.update_by_public_id.assert_not_called()
    ili_svc.delete_by_public_id.assert_not_called()
    reconciliation_repo.create.assert_not_called()


def test_miss_never_writes_a_mapping_but_does_check_for_a_readopt():
    connector, ili_svc, _, _ = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="1")
    ili_svc.read_by_qbo_identity.return_value = None
    ili_svc.create.return_value = SimpleNamespace(id=77, public_id="pub-77")
    ili_svc.read_by_id.return_value = _stamped_row(77, "1")

    connector.sync_from_qbo_invoice_line(19146, "inv-pub", qbo_line, frozenset({"1"}), realm_id="realm-1")

    ili_svc.read_by_invoice_id.assert_called_once_with(19146)
    assert ili_svc.read_by_qbo_identity.call_count == 2  # outer miss + re-read under lock


def test_miss_readopt_candidate_pool_excludes_non_manual_lines():
    """Bill/Expense/BillCreditLineItem-sourced lines are matched via their
    source FK elsewhere; a content-fingerprint match against one here would
    steal it from its true source. Only Manual lines are eligible."""
    connector, ili_svc, _, _ = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="2", description="Materials", amount=Decimal("500"))
    ili_svc.read_by_qbo_identity.return_value = None
    # A non-Manual line under the SAME invoice with an identical fingerprint and
    # a stale (not-live) identity — must NOT be adopted.
    non_manual_candidate = _stamped_row(
        90, "9", source_type="BillLineItem", amount=Decimal("500"),
        description="Materials",
    )
    ili_svc.read_by_invoice_id.return_value = [non_manual_candidate]
    ili_svc.create.return_value = SimpleNamespace(id=77, public_id="pub-77")
    ili_svc.read_by_id.return_value = _stamped_row(77, "2")

    connector.sync_from_qbo_invoice_line(19146, "inv-pub", qbo_line, frozenset({"2"}), realm_id="realm-1")

    # Fell through to a fresh CREATE — the non-Manual candidate was never adopted.
    ili_svc.create.assert_called_once()
    ili_svc.update_by_public_id.assert_not_called()


def test_miss_readopts_a_stale_manual_orphan_by_fingerprint_reusing_its_dbo_id():
    """The U-361b money-double-count fix, cloned: a Manual line whose QBO
    Line.Id was regenerated (content unchanged) is re-stamped in place, never
    minting a sibling row."""
    connector, ili_svc, _, _ = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="2", description="Materials", amount=Decimal("500"))
    ili_svc.read_by_qbo_identity.return_value = None
    stale_orphan = _stamped_row(
        55, "1", source_type="Manual", amount=Decimal("500"), description="Materials",
    )
    ili_svc.read_by_invoice_id.return_value = [stale_orphan]
    updated = SimpleNamespace(id=55, public_id="pub-55")
    ili_svc.update_by_public_id.return_value = updated
    reread = _stamped_row(55, "2", description="Materials", amount=Decimal("500"))
    ili_svc.read_by_id.return_value = reread

    # live_qbo_line_ids does NOT contain "1" any more -> stale_orphan is eligible.
    result = connector.sync_from_qbo_invoice_line(19146, "inv-pub", qbo_line, frozenset({"2"}), realm_id="realm-1")

    assert result is reread
    ili_svc.create.assert_not_called()  # reused the existing dbo.Id, never minted
    ili_svc.update_by_public_id.assert_called_once()
    assert ili_svc.update_by_public_id.call_args.args == ("pub-55",)
    ili_svc.repo.set_qbo_identity.assert_called_once_with(id=55, qbo_id="2", realm_id="realm-1")


# --- caches_preloaded=True (production batch path) readopt safety --------------
#
# Regression coverage for a review finding (2026-09-02): the readopt candidate
# pool's caches_preloaded=True branch reads InvoiceLineItem rows out of the
# connector-level `_line_item_cache` — populated by InvoiceInvoiceConnector.
# preload_caches() via ReadInvoiceLineItems. That sproc silently omitted
# QboId/RealmId (fixed in this unit alongside the other 4), so every cached
# candidate read as unstamped regardless of its real identity — the
# live_qbo_line_ids guard the whole readopt design exists to enforce was
# inert on this path. A second, independent gap compounded it: a line stamped
# earlier in the SAME batch run stayed looking unstamped for the rest of the
# run because `_stamp_line_identity` never wrote the post-stamp re-read back
# into the cache. Both are fixed; these tests prove it stays fixed.


def test_readopt_with_preloaded_cache_never_steals_a_still_live_line():
    """A cached candidate whose dbo-native qbo_id IS in live_qbo_line_ids must
    never be treated as a stale-identity orphan, even under caches_preloaded=True
    — this is the exact scenario find_stale_identity_orphan's own docstring
    calls "identity theft" of a line correctly bound elsewhere in this pull."""
    connector, ili_svc, _, _ = _build_connector()
    connector._caches_preloaded = True
    still_live = _stamped_row(
        55, "1", source_type="Manual", amount=Decimal("500"), description="Materials",
    )
    still_live.invoice_id = 19146
    connector._line_item_cache = {55: still_live}
    qbo_line = _make_qbo_line(qbo_line_id="2", description="Materials", amount=Decimal("500"))
    ili_svc.read_by_qbo_identity.return_value = None
    ili_svc.create.return_value = SimpleNamespace(id=77, public_id="pub-77")
    ili_svc.read_by_id.return_value = _stamped_row(77, "2", description="Materials", amount=Decimal("500"))

    # live_qbo_line_ids contains "1" (still_live's current identity) AND "2"
    # (the incoming line) — still_live is correctly bound elsewhere in this
    # same pull and must not be adopted.
    connector.sync_from_qbo_invoice_line(19146, "inv-pub", qbo_line, frozenset({"1", "2"}), realm_id="realm-1")

    ili_svc.read_by_invoice_id.assert_not_called()  # served entirely from the cache
    ili_svc.create.assert_called_once()  # fell through to a fresh CREATE, not a theft
    ili_svc.update_by_public_id.assert_not_called()


def test_readopt_cache_is_refreshed_after_a_stamp_so_two_identical_lines_in_one_run_both_land():
    """Two distinct QBO lines with an identical (description, amount)
    fingerprint, processed back-to-back in the same batch run (the exact
    scenario find_stale_identity_orphan's own docstring names: "a 50-50 split,
    repeated draws"). The first MISS creates+stamps line 77; without the
    post-stamp cache refresh, line 77 (qbo_id=None in the stale cache entry)
    would look like a stale orphan to the SECOND line's readopt scan and get
    wrongly re-adopted — collapsing two $2,500 draw lines into one."""
    connector, ili_svc, _, _ = _build_connector()
    connector._caches_preloaded = True
    connector._line_item_cache = {}
    ili_svc.read_by_qbo_identity.return_value = None

    first_line = _make_qbo_line(qbo_line_id="1", description="Draw", amount=Decimal("2500"))
    # source_type/qbo_id=None mirror InvoiceLineItemService.create()'s REAL
    # return shape (a fresh row genuinely has no identity yet) — a bare
    # SimpleNamespace missing these fields would make the Manual-only filter
    # exclude the candidate outright, masking the bug this test targets.
    created_first = SimpleNamespace(
        id=77, public_id="pub-77", invoice_id=19146, source_type="Manual",
        description="Draw", amount=Decimal("2500"), qbo_id=None,
    )
    ili_svc.create.return_value = created_first
    reread_first = _stamped_row(77, "1", description="Draw", amount=Decimal("2500"))
    reread_first.invoice_id = 19146
    ili_svc.read_by_id.return_value = reread_first

    connector.sync_from_qbo_invoice_line(19146, "inv-pub", first_line, frozenset({"1", "2"}), realm_id="realm-1")

    # The cache must now hold the STAMPED row (qbo_id="1"), not the pre-stamp
    # create() return (qbo_id=None).
    assert connector._line_item_cache[77].qbo_id == "1"

    second_line = _make_qbo_line(qbo_line_id="2", description="Draw", amount=Decimal("2500"))
    created_second = SimpleNamespace(id=78, public_id="pub-78", invoice_id=19146)
    ili_svc.create.return_value = created_second
    reread_second = _stamped_row(78, "2", description="Draw", amount=Decimal("2500"))
    ili_svc.read_by_id.return_value = reread_second

    connector.sync_from_qbo_invoice_line(19146, "inv-pub", second_line, frozenset({"1", "2"}), realm_id="realm-1")

    # A second, distinct dbo row was created — line 77 (now correctly showing
    # qbo_id="1" in the cache) was never mistaken for a stale orphan.
    assert ili_svc.create.call_count == 2
    ili_svc.update_by_public_id.assert_not_called()


def test_miss_with_missing_realm_refuses_before_creating():
    connector, ili_svc, _, _ = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="1")
    ili_svc.read_by_qbo_identity.return_value = None

    with pytest.raises(RuntimeError, match="realm_id is missing"):
        connector.sync_from_qbo_invoice_line(19146, "inv-pub", qbo_line, frozenset({"1"}))  # no realm_id

    ili_svc.create.assert_not_called()
    ili_svc.repo.set_qbo_identity.assert_not_called()
    ili_svc.delete_by_public_id.assert_not_called()


def test_miss_stamp_failure_rolls_back_the_fresh_line_and_reraises():
    connector, ili_svc, _, reconciliation_repo = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="1")
    ili_svc.read_by_qbo_identity.return_value = None
    ili_svc.create.return_value = SimpleNamespace(id=77, public_id="pub-77")
    ili_svc.repo.set_qbo_identity.side_effect = RuntimeError("stamp db error")

    with pytest.raises(RuntimeError, match="stamp db error"):
        connector.sync_from_qbo_invoice_line(19146, "inv-pub", qbo_line, frozenset({"1"}), realm_id="realm-1")

    ili_svc.delete_by_public_id.assert_called_once_with("pub-77")
    reconciliation_repo.create.assert_not_called()  # rollback succeeded: nothing to record


def test_miss_stamp_that_did_not_land_rolls_back_and_reraises():
    connector, ili_svc, _, _ = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="1")
    ili_svc.read_by_qbo_identity.return_value = None
    ili_svc.create.return_value = SimpleNamespace(id=77, public_id="pub-77")
    ili_svc.read_by_id.return_value = _stamped_row(77, None, realm_id=None)  # sproc declined

    with pytest.raises(RuntimeError, match="identity stamp did not land"):
        connector.sync_from_qbo_invoice_line(19146, "inv-pub", qbo_line, frozenset({"1"}), realm_id="realm-1")

    ili_svc.delete_by_public_id.assert_called_once_with("pub-77")


def test_miss_rollback_failure_records_an_orphan_line_issue_and_reraises_the_original():
    connector, ili_svc, _, reconciliation_repo = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="1")
    ili_svc.read_by_qbo_identity.return_value = None
    ili_svc.create.return_value = SimpleNamespace(id=77, public_id="pub-77")
    ili_svc.repo.set_qbo_identity.side_effect = RuntimeError("stamp db error")
    ili_svc.delete_by_public_id.side_effect = RuntimeError("delete also failed")

    with pytest.raises(RuntimeError, match="stamp db error"):
        connector.sync_from_qbo_invoice_line(19146, "inv-pub", qbo_line, frozenset({"1"}), realm_id="realm-1")

    reconciliation_repo.create.assert_called_once()
    kwargs = reconciliation_repo.create.call_args.kwargs
    assert kwargs["drift_type"] == "orphan_ili_line_item"
    assert kwargs["entity_type"] == "InvoiceLineItem"
    assert kwargs["entity_public_id"] == "pub-77"
    assert kwargs["qbo_id"] == "1"
    assert kwargs["realm_id"] == "realm-1"
    assert "Invoice 19146" in kwargs["details"]
    assert "delete also failed" in kwargs["details"]


def test_miss_readopt_stamp_failure_leaves_the_orphan_untouched_and_records_issue():
    """The readopt-specific rollback shape (U-361b decision §2): a matched
    stale-identity orphan's re-stamp failing must NEVER delete it — the row is
    real, pre-existing data."""
    connector, ili_svc, _, reconciliation_repo = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="2", description="Materials", amount=Decimal("500"))
    ili_svc.read_by_qbo_identity.return_value = None
    stale_orphan = _stamped_row(55, "1", source_type="Manual", amount=Decimal("500"), description="Materials")
    ili_svc.read_by_invoice_id.return_value = [stale_orphan]
    ili_svc.update_by_public_id.return_value = SimpleNamespace(id=55, public_id="pub-55")
    ili_svc.repo.set_qbo_identity.side_effect = RuntimeError("readopt stamp db error")

    with pytest.raises(RuntimeError, match="readopt stamp db error"):
        connector.sync_from_qbo_invoice_line(19146, "inv-pub", qbo_line, frozenset({"2"}), realm_id="realm-1")

    ili_svc.delete_by_public_id.assert_not_called()  # never rolled back
    reconciliation_repo.create.assert_called_once()
    kwargs = reconciliation_repo.create.call_args.kwargs
    assert kwargs["drift_type"] == "ili_line_readopt_failed"
    assert kwargs["entity_public_id"] == "pub-55"


def test_miss_create_failure_propagates_with_nothing_to_roll_back():
    connector, ili_svc, _, reconciliation_repo = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="1")
    ili_svc.read_by_qbo_identity.return_value = None
    ili_svc.create.side_effect = RuntimeError("create failed")

    with pytest.raises(RuntimeError, match="create failed"):
        connector.sync_from_qbo_invoice_line(19146, "inv-pub", qbo_line, frozenset({"1"}), realm_id="realm-1")

    ili_svc.repo.set_qbo_identity.assert_not_called()
    ili_svc.delete_by_public_id.assert_not_called()
    reconciliation_repo.create.assert_called_once()
    assert reconciliation_repo.create.call_args.kwargs["drift_type"] == "ili_line_create_failed"


def test_miss_racer_under_lock_is_updated_not_duplicated():
    connector, ili_svc, _, _ = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="1")
    racer = _stamped_row(90, "1")
    ili_svc.read_by_qbo_identity.side_effect = [None, racer]
    updated = SimpleNamespace(id=90, public_id="pub-90")
    ili_svc.update_by_public_id.return_value = updated

    result = connector.sync_from_qbo_invoice_line(19146, "inv-pub", qbo_line, frozenset({"1"}), realm_id="realm-1")

    assert result is updated
    ili_svc.create.assert_not_called()
    ili_svc.repo.set_qbo_identity.assert_not_called()


# --- guards ---------------------------------------------------------------------


def test_missing_qbo_line_id_fails_closed_without_creating():
    connector, ili_svc, _, _ = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id=None)

    with pytest.raises(ValueError, match="has no QBO Line.Id"):
        connector.sync_from_qbo_invoice_line(19146, "inv-pub", qbo_line, frozenset({"1"}), realm_id="realm-1")

    ili_svc.read_by_qbo_identity.assert_not_called()
    ili_svc.create.assert_not_called()


def test_create_lock_key_is_parent_and_line_scoped():
    connector, ili_svc, _, _ = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="1")
    ili_svc.read_by_qbo_identity.return_value = None
    ili_svc.create.return_value = SimpleNamespace(id=77, public_id="pub-77")
    ili_svc.read_by_id.return_value = _stamped_row(77, "1")
    recorded, recording_lock = _recording_lock_factory()

    with patch(LOCK_PATCH_TARGET, side_effect=recording_lock):
        connector.sync_from_qbo_invoice_line(19146, "inv-pub", qbo_line, frozenset({"1"}), realm_id="realm-1")

    assert recorded == ["qbo_dbo_line_identity_create:InvoiceLineItem:19146:1"]


# --- InvoiceInvoiceConnector._has_qbo_line_provenance repointed onto dbo-native ---


ILI_SERVICE = "entities.invoice_line_item.business.service.InvoiceLineItemService"


def test_has_qbo_line_provenance_true_when_any_line_carries_dbo_qbo_id():
    connector = InvoiceInvoiceConnector(invoice_service=Mock(), reconciliation_repo=Mock())
    with patch(ILI_SERVICE) as ili_cls:
        ili_cls.return_value.read_by_invoice_id.return_value = [
            SimpleNamespace(id=1, qbo_id=None), SimpleNamespace(id=2, qbo_id="7"),
        ]
        assert connector._has_qbo_line_provenance(1057) is True


def test_has_qbo_line_provenance_false_when_no_line_carries_dbo_qbo_id():
    connector = InvoiceInvoiceConnector(invoice_service=Mock(), reconciliation_repo=Mock())
    with patch(ILI_SERVICE) as ili_cls:
        ili_cls.return_value.read_by_invoice_id.return_value = [
            SimpleNamespace(id=1, qbo_id=None), SimpleNamespace(id=2, qbo_id=None),
        ]
        assert connector._has_qbo_line_provenance(1057) is False


def test_has_qbo_line_provenance_false_with_no_lines():
    connector = InvoiceInvoiceConnector(invoice_service=Mock(), reconciliation_repo=Mock())
    with patch(ILI_SERVICE) as ili_cls:
        ili_cls.return_value.read_by_invoice_id.return_value = []
        assert connector._has_qbo_line_provenance(1057) is False


# --- the executed consumer in invoice/business/service.py -----------------------


def test_stale_line_cleanup_clears_legacy_mapping_row_before_the_staging_line():
    """_upsert_invoice_lines' stale-line cleanup used to delete the line's
    InvoiceLineItemInvoiceLine mapping row first (via a full repo class), then
    the staging line. U-362 retired the connector-level repo/model, but the
    mapping TABLE isn't dropped by this unit and still carries a live NO
    ACTION FK onto qbo.InvoiceLine — so a lightweight OBJECT_ID-guarded raw-SQL
    bridge (mirroring the entities-side one) must still clear it first, or the
    staging-line delete 547s and the stale row survives to poison a future
    pull's live_qbo_line_ids."""
    line_repo = Mock()
    line_repo.read_by_qbo_invoice_id.return_value = [
        SimpleNamespace(id=501, qbo_line_id="1"),
        SimpleNamespace(id=502, qbo_line_id="9"),  # no longer in the QBO response
    ]
    svc = QboInvoiceService(line_repo=line_repo)
    incoming = [
        SimpleNamespace(
            id="1", line_num=1, description="d", amount=Decimal("1"), detail_type="SalesItemLineDetail",
            sales_item_line_detail=None, discount_line_detail=None, linked_txn=None,
        )
    ]
    call_order = []
    line_repo.delete_by_id.side_effect = lambda *_: call_order.append("line")

    mock_cursor = Mock()
    mock_cursor.execute.side_effect = lambda *_: call_order.append("mapping")
    mock_conn = Mock()
    mock_conn.__enter__ = Mock(return_value=mock_conn)
    mock_conn.__exit__ = Mock(return_value=False)
    mock_conn.cursor.return_value = mock_cursor

    with patch("shared.database.get_connection", return_value=mock_conn):
        svc._upsert_invoice_lines(30, incoming)

    assert call_order == ["mapping", "line"]
    line_repo.delete_by_id.assert_called_once_with(502)
    sql_text = mock_cursor.execute.call_args.args[0]
    assert "OBJECT_ID" in sql_text
    assert mock_cursor.execute.call_args.args[1] == (502,)


# --- regression: the identity-projection gap found by SQL inspection -----------
#
# Static, no-DB-required guard: the sprocs the dbo-only fast path (and the
# provenance/readopt consumers) re-read through MUST project QboId/RealmId, or
# the helper's own "did the stamp land" verification (and _has_qbo_line_
# provenance, and the readopt candidate scan) are fed rows that can never carry
# the identity they actually hold in the DB — every single line CREATE would
# self-rollback in production, and every provenance/readopt check would read
# every line as unstamped. Same bug class U-361's code review found in
# ReadBillCreditLineItemById/UpdateBillCreditLineItemById; found here by
# inspecting the base SQL file directly. Mutation-proven: reverting any of the
# 4 sprocs' projections below makes this RED.

_BASE_SQL = Path("entities/invoice_line_item/sql/dbo.invoice_line_item.sql").read_text()


def _sproc_body(name: str) -> str:
    start = _BASE_SQL.index(f"PROCEDURE {name}\n")
    end = _BASE_SQL.index("\nGO", start)
    return _BASE_SQL[start:end]


@pytest.mark.parametrize("sproc_name", [
    "ReadInvoiceLineItemById",
    "ReadInvoiceLineItemsByInvoiceId",
    "ReadInvoiceLineItemByPublicId",
    "ReadInvoiceLineItems",
])
def test_read_sprocs_project_qbo_identity_columns(sproc_name):
    body = _sproc_body(sproc_name)
    assert "[QboId]" in body, f"{sproc_name} must SELECT [QboId]."
    assert "[RealmId]" in body, f"{sproc_name} must SELECT [RealmId]."


def test_update_by_id_sproc_projects_qbo_identity_columns():
    body = _sproc_body("UpdateInvoiceLineItemById")
    assert "INSERTED.[QboId]" in body, (
        "UpdateInvoiceLineItemById's OUTPUT must include INSERTED.[QboId] - "
        "the HIT path's update_by_public_id return value should carry the "
        "row's real identity, not silently read back as None."
    )
    assert "INSERTED.[RealmId]" in body, "UpdateInvoiceLineItemById's OUTPUT must also include INSERTED.[RealmId]."
