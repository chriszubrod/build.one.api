"""
U-361 — retire qbo.VendorCreditLineItemBillCreditLineItem (U-349 program family
8/11, the FIRST line-item family) and repoint VendorCreditLineItemConnector.
sync_from_qbo_line onto the shared dbo-only line primitive
(base/identity_fastpath.py::run_line_identity_fastpath_dbo_only).

The helper's own state machine is exhaustively pinned in
tests/test_u361_line_identity_fastpath_dbo_only_helper.py; these tests prove THIS
connector's wiring and the two design-§3/§4 build notes:

  * HIT: update in place, no identity re-stamp (except the one-off realm self-heal
    for a legacy QboId-without-RealmId row), ROWVERSION race -> RuntimeError.
  * MISS: create, then the BARE `set_qbo_identity` stamp + re-read (the U-341
    `create_mapping_then_stamp` / `stamp_line_identity_or_warn` wrappers guarded the
    mapping write, which no longer exists); a missing realm refuses BEFORE creating;
    a stamp failure rolls the fresh line back and re-raises; a rollback that itself
    fails records an `orphan_bcli_line_item` ReconciliationIssue.
  * U-361b: MISS now tries a content-fingerprint READOPT first (a stale-
    identity orphan, e.g. a QBO line-id regeneration) before ever creating —
    see tests/test_u361b_line_readopt_fix.py for that fix's own coverage. No
    mapping read/write anywhere (the mapping table itself stays retired).
  * The create lock is REACHABLE (design §3's /simplify-review note): the header
    connector's HIT path syncs lines with NO lock held, so the line helper's own
    lock is the only serialization for a line MISS there — shown here with a
    lock-tracking test, not asserted. On the header's CREATE path the line lock
    nests INSIDE the header create lock (one-directional, documented).
  * The two executed consumers the assignment missed ("0 reconciliation
    consumers"): the deleted-credit reconcile and the stale-line cleanup in
    integrations/intuit/qbo/vendorcredit/business/service.py no longer touch any
    line mapping.

Supersedes tests/test_u293b_bill_credit_line_item_qbo_identity_repoint.py (the
with-mapping U-293b wiring, deleted in this unit).
"""
import sys
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from integrations.intuit.qbo.vendorcredit.business.service import QboVendorCreditService
from integrations.intuit.qbo.vendorcredit.connector.bill_credit.business.service import (
    VendorCreditBillCreditConnector,
)
from integrations.intuit.qbo.vendorcredit.connector.bill_credit_line_item.business.service import (
    VendorCreditLineItemConnector,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_u304_rollback_lock import _recording_lock_factory

LOCK_PATCH_TARGET = "integrations.intuit.qbo.base.identity_fastpath.qbo_app_lock"
VC_HEADER_SERVICE = "integrations.intuit.qbo.vendorcredit.connector.bill_credit.business.service"
LINE_SERVICE = "integrations.intuit.qbo.vendorcredit.connector.bill_credit_line_item.business.service"

# The MISS branch runs under run_line_identity_fastpath_dbo_only's create lock —
# grant it for every test in this pure-logic module (tests that need to OBSERVE
# lock traffic patch a tracking lock over this grant explicitly).
pytestmark = pytest.mark.usefixtures("grant_qbo_app_lock")


def _make_qbo_line(**overrides):
    defaults = dict(
        id=42,
        qbo_vendor_credit_id=4,
        qbo_line_id="1",
        description="Credit",
        amount=Decimal("50"),
        qty=Decimal("1"),
        unit_price=Decimal("50"),
        billable_status=None,
        customer_ref_value=None,
        item_ref_value=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _build_connector():
    connector = VendorCreditLineItemConnector()
    bcli_svc = Mock()
    bcli_svc.repo = Mock()
    # U-361b: the readopt step (run before every create) reads this — default to
    # "nothing to adopt" so tests unrelated to readopt exercise a clean MISS/HIT
    # without also needing to stub it themselves.
    bcli_svc.read_by_bill_credit_id.return_value = []
    reconciliation_repo = Mock()
    connector.bill_credit_line_item_service = bcli_svc
    connector.reconciliation_repo = reconciliation_repo
    connector._get_project_public_id = Mock(return_value=None)
    connector._get_sub_cost_code_id = Mock(return_value=None)
    return connector, bcli_svc, reconciliation_repo


def _stamped_row(line_id, qbo_line_id, realm_id="realm-1"):
    return SimpleNamespace(
        id=line_id, public_id=f"pub-{line_id}", row_version=f"rv-{line_id}",
        qbo_id=qbo_line_id, realm_id=realm_id,
    )


# --- the connector no longer knows about a mapping table ----------------------


def test_connector_has_no_mapping_repo_and_no_fingerprint_adopt():
    connector = VendorCreditLineItemConnector()
    assert not hasattr(connector, "mapping_repo")
    assert not hasattr(connector, "_match_unmapped_by_fingerprint")
    assert not hasattr(connector, "_record_line_identity_mapping_conflict_issue")


# --- HIT ----------------------------------------------------------------------


def test_hit_updates_in_place_and_does_not_restamp_a_realm_complete_row():
    connector, bcli_svc, reconciliation_repo = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="1")
    direct = _stamped_row(55, "1")
    bcli_svc.read_by_qbo_identity.return_value = direct
    updated = SimpleNamespace(id=55, public_id="pub-55")
    bcli_svc.update_by_public_id.return_value = updated

    result = connector.sync_from_qbo_line(19146, "bc-pub", qbo_line, frozenset({"1"}), realm_id="realm-1")

    assert result is updated
    bcli_svc.read_by_qbo_identity.assert_called_once_with(19146, "1")
    bcli_svc.update_by_public_id.assert_called_once()
    assert bcli_svc.update_by_public_id.call_args.args == ("pub-55",)
    assert bcli_svc.update_by_public_id.call_args.kwargs["row_version"] == "rv-55"
    assert bcli_svc.update_by_public_id.call_args.kwargs["is_draft"] is False
    bcli_svc.create.assert_not_called()
    bcli_svc.repo.set_qbo_identity.assert_not_called()
    reconciliation_repo.create.assert_not_called()


def test_hit_heals_a_legacy_row_missing_its_realm_half():
    """U-293-dw's atomic-pair gap: a row found by QboId but stamped without a
    RealmId gets the realm written once (best-effort, enforce_realm_pairing)."""
    connector, bcli_svc, _ = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="1")
    bcli_svc.read_by_qbo_identity.return_value = _stamped_row(55, "1", realm_id=None)
    bcli_svc.update_by_public_id.return_value = SimpleNamespace(id=55, public_id="pub-55")

    with patch(f"{LINE_SERVICE}.stamp_line_identity_or_warn") as mock_stamp:
        connector.sync_from_qbo_line(19146, "bc-pub", qbo_line, frozenset({"1"}), realm_id="realm-1")

    mock_stamp.assert_called_once()
    assert mock_stamp.call_args.kwargs["id"] == 55
    assert mock_stamp.call_args.kwargs["qbo_id"] == "1"
    assert mock_stamp.call_args.kwargs["realm_id"] == "realm-1"
    assert mock_stamp.call_args.kwargs["enforce_realm_pairing"] is True


def test_hit_without_a_call_realm_does_not_try_to_heal():
    connector, bcli_svc, _ = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="1")
    bcli_svc.read_by_qbo_identity.return_value = _stamped_row(55, "1", realm_id=None)
    bcli_svc.update_by_public_id.return_value = SimpleNamespace(id=55, public_id="pub-55")

    with patch(f"{LINE_SERVICE}.stamp_line_identity_or_warn") as mock_stamp:
        connector.sync_from_qbo_line(19146, "bc-pub", qbo_line, frozenset({"1"}))

    mock_stamp.assert_not_called()


def test_hit_update_returning_none_raises_runtime_error_and_never_stamps():
    connector, bcli_svc, _ = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="1")
    bcli_svc.read_by_qbo_identity.return_value = _stamped_row(55, "1")
    bcli_svc.update_by_public_id.return_value = None

    with pytest.raises(RuntimeError, match="concurrent write race"):
        connector.sync_from_qbo_line(19146, "bc-pub", qbo_line, frozenset({"1"}), realm_id="realm-1")

    bcli_svc.repo.set_qbo_identity.assert_not_called()
    bcli_svc.create.assert_not_called()


# --- MISS ---------------------------------------------------------------------


def test_miss_creates_then_bare_stamps_then_returns_the_reread():
    connector, bcli_svc, reconciliation_repo = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="1", description="Materials", amount=Decimal("500"))
    bcli_svc.read_by_qbo_identity.return_value = None
    created = SimpleNamespace(id=77, public_id="pub-77")
    bcli_svc.create.return_value = created
    reread = _stamped_row(77, "1")
    bcli_svc.read_by_id.return_value = reread

    result = connector.sync_from_qbo_line(19146, "bc-pub", qbo_line, frozenset({"1"}), realm_id="realm-1")

    assert result is reread  # the re-read, not the stale in-memory candidate
    bcli_svc.create.assert_called_once()
    assert bcli_svc.create.call_args.kwargs["bill_credit_public_id"] == "bc-pub"
    assert bcli_svc.create.call_args.kwargs["description"] == "Materials"
    assert bcli_svc.create.call_args.kwargs["amount"] == Decimal("500")
    assert bcli_svc.create.call_args.kwargs["is_draft"] is False
    bcli_svc.repo.set_qbo_identity.assert_called_once_with(id=77, qbo_id="1", realm_id="realm-1")
    bcli_svc.read_by_id.assert_called_once_with(77)
    bcli_svc.update_by_public_id.assert_not_called()
    bcli_svc.delete_by_public_id.assert_not_called()
    reconciliation_repo.create.assert_not_called()


def test_miss_never_writes_a_mapping_but_does_check_for_a_readopt():
    """U-361b: unlike the mapping-table era, there is no mapping repo to write —
    but the MISS branch now DOES read read_by_bill_credit_id once, via the
    readopt-before-create check (find_stale_identity_orphan), before falling
    through to create. Superseded assertion: pre-U-361b this test asserted
    read_by_bill_credit_id was NEVER called on a miss — that's exactly the
    regression U-361b fixes (see tests/test_u361b_line_readopt_fix.py)."""
    connector, bcli_svc, _ = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="1")
    bcli_svc.read_by_qbo_identity.return_value = None
    bcli_svc.create.return_value = SimpleNamespace(id=77, public_id="pub-77")
    bcli_svc.read_by_id.return_value = _stamped_row(77, "1")

    connector.sync_from_qbo_line(19146, "bc-pub", qbo_line, frozenset({"1"}), realm_id="realm-1")

    bcli_svc.read_by_bill_credit_id.assert_called_once_with(19146)
    assert bcli_svc.read_by_qbo_identity.call_count == 2  # outer miss + re-read under lock


def test_miss_with_missing_realm_refuses_before_creating():
    connector, bcli_svc, _ = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="1")
    bcli_svc.read_by_qbo_identity.return_value = None

    with pytest.raises(RuntimeError, match="realm_id is missing"):
        connector.sync_from_qbo_line(19146, "bc-pub", qbo_line, frozenset({"1"}))  # no realm_id

    bcli_svc.create.assert_not_called()
    bcli_svc.repo.set_qbo_identity.assert_not_called()
    bcli_svc.delete_by_public_id.assert_not_called()


def test_miss_stamp_failure_rolls_back_the_fresh_line_and_reraises():
    """The U-354/U-355 identity-stamp race fix at line level: without the
    rollback, the next pull MISSes again (no mapping row to find the unstamped
    line by) and mints a duplicate."""
    connector, bcli_svc, reconciliation_repo = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="1")
    bcli_svc.read_by_qbo_identity.return_value = None
    bcli_svc.create.return_value = SimpleNamespace(id=77, public_id="pub-77")
    bcli_svc.repo.set_qbo_identity.side_effect = RuntimeError("stamp db error")

    with pytest.raises(RuntimeError, match="stamp db error"):
        connector.sync_from_qbo_line(19146, "bc-pub", qbo_line, frozenset({"1"}), realm_id="realm-1")

    bcli_svc.delete_by_public_id.assert_called_once_with("pub-77")
    reconciliation_repo.create.assert_not_called()  # rollback succeeded: nothing to record


def test_miss_stamp_that_did_not_land_rolls_back_and_reraises():
    connector, bcli_svc, _ = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="1")
    bcli_svc.read_by_qbo_identity.return_value = None
    bcli_svc.create.return_value = SimpleNamespace(id=77, public_id="pub-77")
    bcli_svc.read_by_id.return_value = _stamped_row(77, None, realm_id=None)  # sproc declined

    with pytest.raises(RuntimeError, match="identity stamp did not land"):
        connector.sync_from_qbo_line(19146, "bc-pub", qbo_line, frozenset({"1"}), realm_id="realm-1")

    bcli_svc.delete_by_public_id.assert_called_once_with("pub-77")


def test_miss_rollback_failure_records_an_orphan_line_issue_and_reraises_the_original():
    connector, bcli_svc, reconciliation_repo = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="1")
    bcli_svc.read_by_qbo_identity.return_value = None
    bcli_svc.create.return_value = SimpleNamespace(id=77, public_id="pub-77")
    bcli_svc.repo.set_qbo_identity.side_effect = RuntimeError("stamp db error")
    bcli_svc.delete_by_public_id.side_effect = RuntimeError("delete also failed")

    with pytest.raises(RuntimeError, match="stamp db error"):
        connector.sync_from_qbo_line(19146, "bc-pub", qbo_line, frozenset({"1"}), realm_id="realm-1")

    reconciliation_repo.create.assert_called_once()
    kwargs = reconciliation_repo.create.call_args.kwargs
    assert kwargs["drift_type"] == "orphan_bcli_line_item"
    assert kwargs["entity_type"] == "BillCreditLineItem"
    assert kwargs["entity_public_id"] == "pub-77"
    assert kwargs["qbo_id"] == "1"
    assert kwargs["realm_id"] == "realm-1"
    assert "BillCredit 19146" in kwargs["details"]
    assert "delete also failed" in kwargs["details"]


def test_miss_create_failure_propagates_with_nothing_to_roll_back():
    connector, bcli_svc, _ = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="1")
    bcli_svc.read_by_qbo_identity.return_value = None
    bcli_svc.create.side_effect = RuntimeError("create failed")

    with pytest.raises(RuntimeError, match="create failed"):
        connector.sync_from_qbo_line(19146, "bc-pub", qbo_line, frozenset({"1"}), realm_id="realm-1")

    bcli_svc.repo.set_qbo_identity.assert_not_called()
    bcli_svc.delete_by_public_id.assert_not_called()


def test_miss_racer_under_lock_is_updated_not_duplicated():
    connector, bcli_svc, _ = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="1")
    racer = _stamped_row(90, "1")
    bcli_svc.read_by_qbo_identity.side_effect = [None, racer]
    updated = SimpleNamespace(id=90, public_id="pub-90")
    bcli_svc.update_by_public_id.return_value = updated

    result = connector.sync_from_qbo_line(19146, "bc-pub", qbo_line, frozenset({"1"}), realm_id="realm-1")

    assert result is updated
    bcli_svc.create.assert_not_called()
    bcli_svc.repo.set_qbo_identity.assert_not_called()


# --- guards ---------------------------------------------------------------------


def test_missing_qbo_line_id_fails_closed_without_creating():
    """Pre-U-361 a line with no QBO Line.Id was still upsertable via a mapping
    keyed on the staging PK; with no mapping left, creating it would duplicate
    it on every pull — refuse instead (QBO always assigns Line.Id anyway)."""
    connector, bcli_svc, _ = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id=None)

    with pytest.raises(ValueError, match="has no QBO Line.Id"):
        connector.sync_from_qbo_line(19146, "bc-pub", qbo_line, frozenset({"1"}), realm_id="realm-1")

    bcli_svc.read_by_qbo_identity.assert_not_called()
    bcli_svc.create.assert_not_called()


def test_staging_pk_is_not_part_of_identity():
    """qbo_line.id (the qbo.VendorCreditLine staging PK) used to key the mapping;
    it is log-only now — a line with no staging id still resolves by Line.Id."""
    connector, bcli_svc, _ = _build_connector()
    qbo_line = _make_qbo_line(id=None, qbo_line_id="1")
    bcli_svc.read_by_qbo_identity.return_value = _stamped_row(55, "1")
    bcli_svc.update_by_public_id.return_value = SimpleNamespace(id=55, public_id="pub-55")

    connector.sync_from_qbo_line(19146, "bc-pub", qbo_line, frozenset({"1"}), realm_id="realm-1")

    bcli_svc.read_by_qbo_identity.assert_called_once_with(19146, "1")


def test_create_lock_key_is_parent_and_line_scoped():
    connector, bcli_svc, _ = _build_connector()
    qbo_line = _make_qbo_line(qbo_line_id="1")
    bcli_svc.read_by_qbo_identity.return_value = None
    bcli_svc.create.return_value = SimpleNamespace(id=77, public_id="pub-77")
    bcli_svc.read_by_id.return_value = _stamped_row(77, "1")
    recorded, recording_lock = _recording_lock_factory()

    with patch(LOCK_PATCH_TARGET, side_effect=recording_lock):
        connector.sync_from_qbo_line(19146, "bc-pub", qbo_line, frozenset({"1"}), realm_id="realm-1")

    assert recorded == ["qbo_dbo_line_identity_create:BillCreditLineItem:19146:1"]


# --- design §3: create-lock reachability, SHOWN not asserted -------------------


def _held_lock_tracker():
    """A qbo_app_lock stand-in that tracks the set of currently-held resources."""
    held = []

    @contextmanager
    def _lock(resource_name, timeout_ms=15000):
        held.append(resource_name)
        try:
            yield True
        finally:
            held.remove(resource_name)

    return held, _lock


def _make_qbo_vc(**overrides):
    defaults = dict(
        id=30, qbo_id="VC-99", realm_id="realm-1", vendor_ref_value="1", doc_number="VC-99",
        txn_date="2026-01-01", total_amt=Decimal("50"), private_note="note",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _build_header_connector(*, existing_bill_credit):
    bill_credit_service = Mock()
    bill_credit_service.repo = Mock()
    bill_credit_service.read_by_qbo_identity.return_value = existing_bill_credit
    line_connector = Mock()
    connector = VendorCreditBillCreditConnector(
        bill_credit_service=bill_credit_service,
        bill_credit_line_item_service=Mock(),
        vendor_service=Mock(),
        reconciliation_repo=Mock(),
        line_item_connector=line_connector,
    )
    connector._get_vendor_public_id = Mock(return_value="vendor-pub")
    return connector, bill_credit_service, line_connector


def test_header_hit_path_syncs_lines_with_no_lock_held():
    """The header connector's HIT (existing BillCredit) path calls
    sync_from_qbo_line with ZERO app locks held — so two concurrent syncs of an
    existing credit (a scheduled pull + reconciliation's missing-locally autofix,
    which holds no pull-level lock) can both MISS the same new line at once. The
    line helper's own create lock is therefore the only thing serializing that
    MISS: Option A is reachable, not just defense-in-depth."""
    existing = SimpleNamespace(id=42, public_id="bc-pub-42", row_version="rv", credit_number="VC-99")
    connector, bill_credit_service, line_connector = _build_header_connector(existing_bill_credit=existing)
    bill_credit_service.update_by_public_id.return_value = existing
    held, tracking_lock = _held_lock_tracker()
    held_at_line_sync = []
    line_connector.sync_from_qbo_line.side_effect = lambda *a, **k: held_at_line_sync.append(list(held))

    with patch(LOCK_PATCH_TARGET, side_effect=tracking_lock), patch(f"{VC_HEADER_SERVICE}.guard_lines_present"):
        connector.sync_from_qbo_vendor_credit(_make_qbo_vc(), [_make_qbo_line(qbo_line_id="1")])

    assert held_at_line_sync == [[]]  # line sync ran exactly once, with nothing held


def test_header_create_path_nests_line_sync_inside_the_header_create_lock():
    """The other direction of the same finding: on the header's MISS/CREATE path
    the line sync runs INSIDE `qbo_dbo_identity_create:BillCredit:*`, so a line
    lock taken there nests header -> line. The line helper never acquires a
    header lock, so the order is one-directional and cannot cycle."""
    connector, bill_credit_service, line_connector = _build_header_connector(existing_bill_credit=None)
    created = SimpleNamespace(id=42, public_id="bc-pub-42", row_version="rv", credit_number="VC-99")
    bill_credit_service.create.return_value = created
    bill_credit_service.read_by_id.return_value = created
    held, tracking_lock = _held_lock_tracker()
    held_at_line_sync = []
    line_connector.sync_from_qbo_line.side_effect = lambda *a, **k: held_at_line_sync.append(list(held))

    with patch(LOCK_PATCH_TARGET, side_effect=tracking_lock), patch(f"{VC_HEADER_SERVICE}.guard_lines_present"):
        connector.sync_from_qbo_vendor_credit(_make_qbo_vc(), [_make_qbo_line(qbo_line_id="1")])

    assert held_at_line_sync == [["qbo_dbo_identity_create:BillCredit:VC-99:realm-1"]]


# --- the two executed consumers in vendorcredit/business/service.py ------------


def test_stale_line_cleanup_deletes_the_staging_line_directly():
    """_upsert_vendor_credit_lines' stale-line cleanup used to delete the line's
    mapping row first (and skip the line delete if that failed). No mapping
    now: the stale staging line is deleted directly."""
    repo = Mock()
    repo.read_line_by_vendor_credit_id_and_qbo_line_id.return_value = None
    repo.read_lines_by_vendor_credit_id.return_value = [
        SimpleNamespace(id=501, qbo_line_id="1"),
        SimpleNamespace(id=502, qbo_line_id="9"),  # no longer in the QBO response
    ]
    svc = QboVendorCreditService(repo=repo)
    incoming = [
        SimpleNamespace(
            id="1", line_num=1, description="d", amount=Decimal("1"), detail_type="AccountBasedExpenseLineDetail",
            item_based_expense_line_detail=None, account_based_expense_line_detail=None,
        )
    ]

    svc._upsert_vendor_credit_lines(30, incoming)

    repo.delete_line_by_id.assert_called_once_with(502)


def test_deleted_credit_reconcile_only_deletes_header_and_staging():
    """_reconcile_deleted_vendor_credits' former Step 1 (delete the credit's line
    mappings) is gone; the destructive-progress label can only name the header."""
    repo = Mock()
    repo.read_by_realm_id.return_value = [SimpleNamespace(id=10, qbo_id="VC-1")]
    svc = QboVendorCreditService(repo=repo)
    bill_credit = SimpleNamespace(id=99, public_id="bc-pub-99")
    bill_credit_service = Mock()
    bill_credit_service.read_by_qbo_identity.return_value = bill_credit
    fake_client = Mock()
    fake_client.__enter__ = Mock(return_value=fake_client)
    fake_client.__exit__ = Mock(return_value=False)

    with patch("integrations.intuit.qbo.vendorcredit.business.service.QboVendorCreditClient", return_value=fake_client), patch(
        "integrations.intuit.qbo.base.delete_reconcile.strict_confirmed_deleted_ids", return_value={"VC-1"}
    ), patch("entities.bill_credit.business.service.BillCreditService", return_value=bill_credit_service):
        deleted = svc._reconcile_deleted_vendor_credits("realm-1")

    assert deleted == 1
    repo.read_lines_by_vendor_credit_id.assert_not_called()  # no per-line mapping walk any more
    bill_credit_service.delete_by_public_id.assert_called_once_with("bc-pub-99")
    repo.delete_by_qbo_id.assert_called_once_with("VC-1")


# --- regression: the identity-projection gap the review found -----------------
#
# Static, no-DB-required guard: the two sprocs the dbo-only fast path re-reads
# through (MISS's post-stamp read_by_id, HIT's update_by_public_id) MUST
# project QboId/RealmId, or the helper's own "did the stamp land" verification
# is fed a row that can never carry the identity it just wrote — every single
# line CREATE would then self-rollback in production (code-review finding,
# 2026-09-01: ReadBillCreditLineItemById's SELECT list omitted both columns).
# Mutation-proven: reverting either sproc's projection below makes this RED.

_BASE_SQL = Path("entities/bill_credit_line_item/sql/dbo.bill_credit_line_item.sql").read_text()


def _sproc_body(name: str) -> str:
    start = _BASE_SQL.index(f"PROCEDURE {name}\n")
    end = _BASE_SQL.index("\nGO", start)
    return _BASE_SQL[start:end]


def test_read_by_id_sproc_projects_qbo_identity_columns():
    body = _sproc_body("ReadBillCreditLineItemById")
    assert "[QboId]" in body, (
        "ReadBillCreditLineItemById must SELECT [QboId] - the dbo-only line "
        "fast path's post-stamp re-read (read_by_id) verifies the stamp landed "
        "by comparing this column; omitting it makes every CREATE self-rollback."
    )
    assert "[RealmId]" in body, "ReadBillCreditLineItemById must also SELECT [RealmId]."


def test_update_by_id_sproc_projects_qbo_identity_columns():
    body = _sproc_body("UpdateBillCreditLineItemById")
    assert "INSERTED.[QboId]" in body, (
        "UpdateBillCreditLineItemById's OUTPUT must include INSERTED.[QboId] - "
        "the HIT path's update_by_public_id return value should carry the "
        "row's real identity, not silently read back as None."
    )
    assert "INSERTED.[RealmId]" in body, "UpdateBillCreditLineItemById's OUTPUT must also include INSERTED.[RealmId]."
