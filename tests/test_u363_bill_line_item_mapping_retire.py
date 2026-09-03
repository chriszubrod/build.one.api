"""
U-363 — retire qbo.BillLineItemBillLine (U-349 program family 10/11) and
repoint BillLineItemConnector.sync_from_qbo_bill_line onto the shared dbo-only
line primitive (base/identity_fastpath.py::run_line_identity_fastpath_dbo_only).

A CLONE of U-361's shape (tests/test_u361_bill_credit_line_item_mapping_
retire.py) — see that file's own docstring for the fuller design rationale,
which applies here unchanged. BillLineItem carries no SourceType/LinkedTxn
provenance, so none of U-362/U-362b's source-linked recognition logic applies;
this is the SIMPLE line clone. The helper's own state machine is exhaustively
pinned in tests/test_u361_line_identity_fastpath_dbo_only_helper.py; these
tests prove THIS connector's wiring plus the two new call sites this unit
introduced (a stale-line-cleanup deploy-gap bridge in 3 places, and the
reconciliation re-expression).

Covers:
  * HIT: update in place, no identity re-stamp (except the one-off realm
    self-heal for a legacy QboId-without-RealmId row, via
    stamp_line_identity_or_warn), ROWVERSION race -> RuntimeError.
  * MISS: create, then the BARE `set_qbo_identity` stamp + re-read. A missing
    realm refuses BEFORE creating; a stamp failure rolls the fresh line back
    and re-raises; a rollback that itself fails records an `orphan_bli_line_
    item` ReconciliationIssue. A readopt (stale-identity orphan, content-
    fingerprint match) is tried before ever creating.
  * `_sync_line_items` (header connector): computes `live_qbo_line_ids` once
    per bill and threads it through to every line.
  * The 3 executed consumers this unit's deploy-gap bridge protects:
    the entity delete path, `_upsert_bill_lines`' stale QboBillLine cleanup,
    and `_reconcile_deleted_bills`' Step 1.
  * The push-path (`sync_to_qbo_bill`) line-mapping replacement: a direct
    dbo-native stamp by line_num match, no more `create_mapping`.
  * dbo.ReadBillQboLinkInfo's simplification off dbo.Bill's own identity.
  * The dbo-native reconciliation re-expression:
    BILL_BILLABLE_STATUS_DRIFT_ROWS_SQL returns the SAME rows as the retired
    mapping-hop query on a shared SQLite fixture (characterization /
    equivalence).
  * Regression: the 3 sprocs the code-review-class bug found (Update/Read-
    ByPublicId/ReadsByBillId not projecting QboId/RealmId) now do.

Supersedes tests/test_u293_bill_line_item_qbo_identity_repoint.py (the
with-mapping U-293 wiring, deleted in this unit) and
tests/test_qbo_bill_line_item_mapping_exceptions.py (create_mapping()
exception handling, retired along with create_mapping() itself).
"""
import sqlite3
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

from entities.bill_line_item.business.service import BillLineItemService
from integrations.intuit.qbo.bill.connector.bill.business.service import BillBillConnector
from integrations.intuit.qbo.bill.connector.bill_line_item.business.service import BillLineItemConnector
from integrations.intuit.qbo.bill.business.service import QboBillService
from integrations.intuit.qbo.reconciliation.business.service import (
    BILL_BILLABLE_STATUS_DRIFT_ROWS_SQL,
    ReconciliationService,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_u304_rollback_lock import _recording_lock_factory

LOCK_PATCH_TARGET = "integrations.intuit.qbo.base.identity_fastpath.qbo_app_lock"
LINE_SERVICE = "integrations.intuit.qbo.bill.connector.bill_line_item.business.service"

# The MISS branch runs under run_line_identity_fastpath_dbo_only's create lock —
# grant it for every test in this pure-logic module (tests that need to OBSERVE
# lock traffic patch a tracking lock over this grant explicitly).
pytestmark = pytest.mark.usefixtures("grant_qbo_app_lock")


def _make_qbo_bill_line(**overrides):
    defaults = dict(
        id=42,
        qbo_bill_id=4,
        qbo_line_id="1",
        description="Lumber",
        amount=Decimal("50"),
        qty=Decimal("1"),
        unit_price=Decimal("50"),
        markup_percent=None,
        billable_status=None,
        customer_ref_value=None,
        item_ref_value=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _build_connector():
    connector = BillLineItemConnector()
    bill_svc = Mock()
    bill_svc.repo = Mock()
    bill_svc.read_by_id.return_value = SimpleNamespace(id=19146, public_id="bill-pub")
    line_svc = Mock()
    # U-361b-shape readopt step (run before every create) reads this — default
    # to "nothing to adopt" so tests unrelated to readopt exercise a clean
    # MISS/HIT without also needing to stub it themselves.
    line_svc.read_by_bill_id.return_value = []
    reconciliation_repo = Mock()
    connector.bill_service = bill_svc
    connector.bill_line_item_service = line_svc
    connector.reconciliation_repo = reconciliation_repo
    connector._get_project_public_id = Mock(return_value=None)
    return connector, bill_svc, line_svc, reconciliation_repo


def _stamped_row(line_id, qbo_line_id, realm_id="realm-1"):
    return SimpleNamespace(
        id=line_id, public_id=f"pub-{line_id}", row_version=f"rv-{line_id}",
        qbo_id=qbo_line_id, realm_id=realm_id,
    )


# --- the connector no longer knows about a mapping table ----------------------


def test_connector_has_no_mapping_repo_and_no_fingerprint_adopt():
    connector = BillLineItemConnector()
    assert not hasattr(connector, "mapping_repo")
    assert not hasattr(connector, "_find_unmapped_line_items")
    assert not hasattr(connector, "_match_by_fingerprint")
    assert not hasattr(connector, "_record_line_identity_mapping_conflict_issue")
    assert not hasattr(connector, "create_mapping")
    assert not hasattr(connector, "get_mapping_by_bill_line_item_id")
    assert not hasattr(connector, "get_mapping_by_qbo_bill_line_id")


# --- HIT ----------------------------------------------------------------------


def test_hit_updates_in_place_and_does_not_restamp_a_realm_complete_row():
    connector, bill_svc, line_svc, reconciliation_repo = _build_connector()
    qbo_line = _make_qbo_bill_line(qbo_line_id="1")
    direct = _stamped_row(55, "1")
    line_svc.read_by_qbo_identity.return_value = direct
    updated = SimpleNamespace(id=55, public_id="pub-55")
    line_svc.update_by_public_id.return_value = updated

    result = connector.sync_from_qbo_bill_line(19146, qbo_line, frozenset({"1"}), realm_id="realm-1")

    assert result is updated
    line_svc.read_by_qbo_identity.assert_called_once_with(19146, "1")
    line_svc.update_by_public_id.assert_called_once()
    assert line_svc.update_by_public_id.call_args.args == ("pub-55",)
    assert line_svc.update_by_public_id.call_args.kwargs["row_version"] == "rv-55"
    assert line_svc.update_by_public_id.call_args.kwargs["is_draft"] is False
    line_svc.create.assert_not_called()
    line_svc.repo.set_qbo_identity.assert_not_called()
    reconciliation_repo.create.assert_not_called()


def test_hit_heals_a_legacy_row_missing_its_realm_half():
    """U-293-dw's atomic-pair gap: a row found by QboId but stamped without a
    RealmId gets the realm written once (best-effort, enforce_realm_pairing)."""
    connector, bill_svc, line_svc, _ = _build_connector()
    qbo_line = _make_qbo_bill_line(qbo_line_id="1")
    line_svc.read_by_qbo_identity.return_value = _stamped_row(55, "1", realm_id=None)
    line_svc.update_by_public_id.return_value = SimpleNamespace(id=55, public_id="pub-55")

    with patch(f"{LINE_SERVICE}.stamp_line_identity_or_warn") as mock_stamp:
        connector.sync_from_qbo_bill_line(19146, qbo_line, frozenset({"1"}), realm_id="realm-1")

    mock_stamp.assert_called_once()
    assert mock_stamp.call_args.kwargs["id"] == 55
    assert mock_stamp.call_args.kwargs["qbo_id"] == "1"
    assert mock_stamp.call_args.kwargs["realm_id"] == "realm-1"
    assert mock_stamp.call_args.kwargs["enforce_realm_pairing"] is True


def test_hit_without_a_call_realm_does_not_try_to_heal():
    connector, bill_svc, line_svc, _ = _build_connector()
    qbo_line = _make_qbo_bill_line(qbo_line_id="1")
    line_svc.read_by_qbo_identity.return_value = _stamped_row(55, "1", realm_id=None)
    line_svc.update_by_public_id.return_value = SimpleNamespace(id=55, public_id="pub-55")

    with patch(f"{LINE_SERVICE}.stamp_line_identity_or_warn") as mock_stamp:
        connector.sync_from_qbo_bill_line(19146, qbo_line, frozenset({"1"}))

    mock_stamp.assert_not_called()


def test_hit_update_returning_none_raises_runtime_error_and_never_stamps():
    connector, bill_svc, line_svc, _ = _build_connector()
    qbo_line = _make_qbo_bill_line(qbo_line_id="1")
    line_svc.read_by_qbo_identity.return_value = _stamped_row(55, "1")
    line_svc.update_by_public_id.return_value = None

    with pytest.raises(RuntimeError, match="concurrent write race"):
        connector.sync_from_qbo_bill_line(19146, qbo_line, frozenset({"1"}), realm_id="realm-1")

    line_svc.repo.set_qbo_identity.assert_not_called()
    line_svc.create.assert_not_called()


# --- MISS ---------------------------------------------------------------------


def test_miss_creates_then_bare_stamps_then_returns_the_reread():
    connector, bill_svc, line_svc, reconciliation_repo = _build_connector()
    qbo_line = _make_qbo_bill_line(qbo_line_id="1", description="Materials", amount=Decimal("500"))
    line_svc.read_by_qbo_identity.return_value = None
    created = SimpleNamespace(id=77, public_id="pub-77")
    line_svc.create.return_value = created
    reread = _stamped_row(77, "1")
    line_svc.read_by_id.return_value = reread

    result = connector.sync_from_qbo_bill_line(19146, qbo_line, frozenset({"1"}), realm_id="realm-1")

    assert result is reread  # the re-read, not the stale in-memory candidate
    line_svc.create.assert_called_once()
    assert line_svc.create.call_args.kwargs["bill_public_id"] == "bill-pub"
    assert line_svc.create.call_args.kwargs["description"] == "Materials"
    assert line_svc.create.call_args.kwargs["amount"] == Decimal("500")
    assert line_svc.create.call_args.kwargs["is_draft"] is False
    line_svc.repo.set_qbo_identity.assert_called_once_with(id=77, qbo_id="1", realm_id="realm-1")
    line_svc.read_by_id.assert_called_once_with(77)
    line_svc.update_by_public_id.assert_not_called()
    line_svc.delete_by_public_id.assert_not_called()
    reconciliation_repo.create.assert_not_called()


def test_miss_never_writes_a_mapping_but_does_check_for_a_readopt():
    connector, bill_svc, line_svc, _ = _build_connector()
    qbo_line = _make_qbo_bill_line(qbo_line_id="1")
    line_svc.read_by_qbo_identity.return_value = None
    line_svc.create.return_value = SimpleNamespace(id=77, public_id="pub-77")
    line_svc.read_by_id.return_value = _stamped_row(77, "1")

    connector.sync_from_qbo_bill_line(19146, qbo_line, frozenset({"1"}), realm_id="realm-1")

    line_svc.read_by_bill_id.assert_called_once_with(19146)
    assert line_svc.read_by_qbo_identity.call_count == 2  # outer miss + re-read under lock


def test_miss_with_missing_realm_refuses_before_creating():
    connector, bill_svc, line_svc, _ = _build_connector()
    qbo_line = _make_qbo_bill_line(qbo_line_id="1")
    line_svc.read_by_qbo_identity.return_value = None

    with pytest.raises(RuntimeError, match="realm_id is missing"):
        connector.sync_from_qbo_bill_line(19146, qbo_line, frozenset({"1"}))  # no realm_id

    line_svc.create.assert_not_called()
    line_svc.repo.set_qbo_identity.assert_not_called()
    line_svc.delete_by_public_id.assert_not_called()


def test_miss_stamp_failure_rolls_back_the_fresh_line_and_reraises():
    connector, bill_svc, line_svc, reconciliation_repo = _build_connector()
    qbo_line = _make_qbo_bill_line(qbo_line_id="1")
    line_svc.read_by_qbo_identity.return_value = None
    line_svc.create.return_value = SimpleNamespace(id=77, public_id="pub-77")
    line_svc.repo.set_qbo_identity.side_effect = RuntimeError("stamp db error")

    with pytest.raises(RuntimeError, match="stamp db error"):
        connector.sync_from_qbo_bill_line(19146, qbo_line, frozenset({"1"}), realm_id="realm-1")

    line_svc.delete_by_public_id.assert_called_once_with("pub-77")
    reconciliation_repo.create.assert_not_called()  # rollback succeeded: nothing to record


def test_miss_stamp_that_did_not_land_rolls_back_and_reraises():
    connector, bill_svc, line_svc, _ = _build_connector()
    qbo_line = _make_qbo_bill_line(qbo_line_id="1")
    line_svc.read_by_qbo_identity.return_value = None
    line_svc.create.return_value = SimpleNamespace(id=77, public_id="pub-77")
    line_svc.read_by_id.return_value = _stamped_row(77, None, realm_id=None)  # sproc declined

    with pytest.raises(RuntimeError, match="identity stamp did not land"):
        connector.sync_from_qbo_bill_line(19146, qbo_line, frozenset({"1"}), realm_id="realm-1")

    line_svc.delete_by_public_id.assert_called_once_with("pub-77")


def test_miss_rollback_failure_records_an_orphan_line_issue_and_reraises_the_original():
    connector, bill_svc, line_svc, reconciliation_repo = _build_connector()
    qbo_line = _make_qbo_bill_line(qbo_line_id="1")
    line_svc.read_by_qbo_identity.return_value = None
    line_svc.create.return_value = SimpleNamespace(id=77, public_id="pub-77")
    line_svc.repo.set_qbo_identity.side_effect = RuntimeError("stamp db error")
    line_svc.delete_by_public_id.side_effect = RuntimeError("delete also failed")

    with pytest.raises(RuntimeError, match="stamp db error"):
        connector.sync_from_qbo_bill_line(19146, qbo_line, frozenset({"1"}), realm_id="realm-1")

    reconciliation_repo.create.assert_called_once()
    kwargs = reconciliation_repo.create.call_args.kwargs
    assert kwargs["drift_type"] == "orphan_bli_line_item"
    assert kwargs["entity_type"] == "BillLineItem"
    assert kwargs["entity_public_id"] == "pub-77"
    assert kwargs["qbo_id"] == "1"
    assert kwargs["realm_id"] == "realm-1"
    assert "Bill 19146" in kwargs["details"]
    assert "delete also failed" in kwargs["details"]


def test_miss_create_failure_propagates_with_nothing_to_roll_back():
    connector, bill_svc, line_svc, _ = _build_connector()
    qbo_line = _make_qbo_bill_line(qbo_line_id="1")
    line_svc.read_by_qbo_identity.return_value = None
    line_svc.create.side_effect = RuntimeError("create failed")

    with pytest.raises(RuntimeError, match="create failed"):
        connector.sync_from_qbo_bill_line(19146, qbo_line, frozenset({"1"}), realm_id="realm-1")

    line_svc.repo.set_qbo_identity.assert_not_called()
    line_svc.delete_by_public_id.assert_not_called()


def test_miss_racer_under_lock_is_updated_not_duplicated():
    connector, bill_svc, line_svc, _ = _build_connector()
    qbo_line = _make_qbo_bill_line(qbo_line_id="1")
    racer = _stamped_row(90, "1")
    line_svc.read_by_qbo_identity.side_effect = [None, racer]
    updated = SimpleNamespace(id=90, public_id="pub-90")
    line_svc.update_by_public_id.return_value = updated

    result = connector.sync_from_qbo_bill_line(19146, qbo_line, frozenset({"1"}), realm_id="realm-1")

    assert result is updated
    line_svc.create.assert_not_called()
    line_svc.repo.set_qbo_identity.assert_not_called()


# --- guards ---------------------------------------------------------------------


def test_missing_qbo_line_id_fails_closed_without_creating():
    connector, bill_svc, line_svc, _ = _build_connector()
    qbo_line = _make_qbo_bill_line(qbo_line_id=None)

    with pytest.raises(ValueError, match="has no QBO Line.Id"):
        connector.sync_from_qbo_bill_line(19146, qbo_line, frozenset({"1"}), realm_id="realm-1")

    line_svc.read_by_qbo_identity.assert_not_called()
    line_svc.create.assert_not_called()
    bill_svc.read_by_id.assert_not_called()  # fails before even resolving the parent Bill


def test_staging_pk_is_not_part_of_identity():
    """qbo_bill_line.id (the qbo.BillLine staging PK) used to key the mapping;
    it is log-only now — a line with no staging id still resolves by Line.Id."""
    connector, bill_svc, line_svc, _ = _build_connector()
    qbo_line = _make_qbo_bill_line(id=None, qbo_line_id="1")
    line_svc.read_by_qbo_identity.return_value = _stamped_row(55, "1")
    line_svc.update_by_public_id.return_value = SimpleNamespace(id=55, public_id="pub-55")

    connector.sync_from_qbo_bill_line(19146, qbo_line, frozenset({"1"}), realm_id="realm-1")

    line_svc.read_by_qbo_identity.assert_called_once_with(19146, "1")


def test_create_lock_key_is_parent_and_line_scoped():
    connector, bill_svc, line_svc, _ = _build_connector()
    qbo_line = _make_qbo_bill_line(qbo_line_id="1")
    line_svc.read_by_qbo_identity.return_value = None
    line_svc.create.return_value = SimpleNamespace(id=77, public_id="pub-77")
    line_svc.read_by_id.return_value = _stamped_row(77, "1")
    recorded, recording_lock = _recording_lock_factory()

    with patch(LOCK_PATCH_TARGET, side_effect=recording_lock):
        connector.sync_from_qbo_bill_line(19146, qbo_line, frozenset({"1"}), realm_id="realm-1")

    assert recorded == ["qbo_dbo_line_identity_create:BillLineItem:19146:1"]


# --- entity service: update_by_public_id concurrent-delete contract -------------


def test_update_by_public_id_returns_none_on_concurrent_delete_not_attribute_error():
    """Regression: pre-fix, `existing.row_version = ...` and every field-set
    below it sat OUTSIDE any `if not existing: return None` guard — a `None`
    `existing` (row deleted between read and this call) fell through to
    `self.repo.update_by_id(existing)`, which immediately raises AttributeError
    on `existing.id` deep in the persistence layer, re-wrapped as an opaque
    DatabaseOperationError. The connector's own `_apply_line_fields` (this
    file's HIT-branch tests) documents and relies on a clean `None` return
    here to raise the well-classified `raise_concurrent_write_race` instead —
    that contract only holds if this method actually returns None."""
    repo = Mock()
    svc = BillLineItemService(repo=repo)
    with patch.object(svc, "read_by_public_id", return_value=None):
        result = svc.update_by_public_id("bli-pub", row_version="rv", description="x")

    assert result is None
    repo.update_by_id.assert_not_called()


# --- header connector: live_qbo_line_ids computation + threading ----------------


def test_sync_line_items_computes_live_qbo_line_ids_once_and_threads_to_every_line():
    bill_service = Mock()
    connector = BillBillConnector(bill_service=bill_service)
    line_connector = Mock()
    seen_calls = []
    line_connector.sync_from_qbo_bill_line.side_effect = (
        lambda bill_id, line, live_ids, realm_id=None: seen_calls.append((bill_id, line.qbo_line_id, live_ids, realm_id))
    )

    lines = [
        _make_qbo_bill_line(qbo_line_id="1"),
        _make_qbo_bill_line(qbo_line_id="2"),
        _make_qbo_bill_line(qbo_line_id=None),  # excluded from the live set, still synced
    ]

    with patch(f"{LINE_SERVICE}.BillLineItemConnector", return_value=line_connector):
        connector._sync_line_items(19146, lines, "realm-1")

    assert len(seen_calls) == 3
    for bill_id, _qbo_line_id, live_ids, realm_id in seen_calls:
        assert bill_id == 19146
        assert live_ids == frozenset({"1", "2"})
        assert realm_id == "realm-1"


def test_sync_line_items_raises_when_any_line_fails():
    bill_service = Mock()
    connector = BillBillConnector(bill_service=bill_service)
    line_connector = Mock()
    line_connector.sync_from_qbo_bill_line.side_effect = [None, RuntimeError("boom")]

    with patch(f"{LINE_SERVICE}.BillLineItemConnector", return_value=line_connector):
        with pytest.raises(RuntimeError, match=r"1 of 2 bill line\(s\) failed"):
            connector._sync_line_items(
                19146, [_make_qbo_bill_line(qbo_line_id="1"), _make_qbo_bill_line(qbo_line_id="2")], "realm-1",
            )


# --- deploy-gap bridge (3 call sites) --------------------------------------------


def test_bridge_is_object_id_guarded_and_scoped_by_qbo_bill_line_id():
    from integrations.intuit.qbo.bill.business.service import (
        _clear_legacy_bill_line_item_bill_line_mapping_by_qbo_line_id,
    )

    mock_cursor = Mock()
    mock_conn = Mock()
    mock_conn.__enter__ = Mock(return_value=mock_conn)
    mock_conn.__exit__ = Mock(return_value=False)
    mock_conn.cursor.return_value = mock_cursor

    with patch("shared.database.get_connection", return_value=mock_conn):
        _clear_legacy_bill_line_item_bill_line_mapping_by_qbo_line_id(600)

    sql_text = mock_cursor.execute.call_args.args[0]
    assert "OBJECT_ID" in sql_text
    assert "qbo.BillLineItemBillLine" in sql_text or "[BillLineItemBillLine]" in sql_text
    assert "QboBillLineId" in sql_text
    assert mock_cursor.execute.call_args.args[1] == (600,)


def test_bridge_failure_is_swallowed_best_effort():
    from integrations.intuit.qbo.bill.business.service import (
        _clear_legacy_bill_line_item_bill_line_mapping_by_qbo_line_id,
    )

    with patch("shared.database.get_connection", side_effect=RuntimeError("connection reset")):
        _clear_legacy_bill_line_item_bill_line_mapping_by_qbo_line_id(600)  # must not raise


def test_upsert_bill_lines_stale_cleanup_clears_bridge_before_deleting_staging_line():
    repo = Mock()
    line_repo = Mock()
    line_repo.read_by_qbo_bill_id.return_value = [
        SimpleNamespace(id=501, qbo_line_id="1"),
        SimpleNamespace(id=502, qbo_line_id="9"),  # no longer in the QBO response
    ]
    svc = QboBillService(repo=repo, line_repo=line_repo)

    call_order = []
    with patch(
        "integrations.intuit.qbo.bill.business.service._clear_legacy_bill_line_item_bill_line_mapping_by_qbo_line_id",
        side_effect=lambda qbo_bill_line_id: call_order.append(("bridge", qbo_bill_line_id)),
    ):
        line_repo.delete_by_id.side_effect = lambda lid: call_order.append(("delete", lid))
        svc._upsert_bill_lines(88, [
            SimpleNamespace(
                id="1", line_num=1, description="d", amount=Decimal("1"), detail_type="AccountBasedExpenseLineDetail",
                item_based_expense_line_detail=None, account_based_expense_line_detail=None,
            )
        ])

    assert call_order == [("bridge", 502), ("delete", 502)]


def test_reconcile_deleted_bills_step1_clears_bridge_per_staging_line():
    repo = Mock()
    local = SimpleNamespace(qbo_id="42", id=1, realm_id="realm-1")
    repo.read_by_realm_id.return_value = [local]
    line_repo = Mock()
    line_repo.read_by_qbo_bill_id.return_value = [
        SimpleNamespace(id=901, qbo_line_id="1"),
        SimpleNamespace(id=902, qbo_line_id="2"),
    ]
    svc = QboBillService(repo=repo, line_repo=line_repo)

    bridge_calls = []
    with patch(
        "integrations.intuit.qbo.bill.business.service.QboBillClient"
    ), patch(
        "integrations.intuit.qbo.base.delete_reconcile.strict_confirmed_deleted_ids",
        return_value={"42"},
    ), patch(
        "integrations.intuit.qbo.bill.business.service._clear_legacy_bill_line_item_bill_line_mapping_by_qbo_line_id",
        side_effect=lambda qbo_bill_line_id: bridge_calls.append(qbo_bill_line_id),
    ), patch(
        "entities.bill.business.service.BillService"
    ) as bill_svc_cls:
        bill_svc_cls.return_value.read_by_qbo_identity.return_value = None
        deleted = svc._reconcile_deleted_bills("realm-1")

    assert deleted == 1
    assert bridge_calls == [901, 902]


# --- push-path (sync_to_qbo_bill) line-mapping replacement -----------------------
#
# The real push path is exercised end-to-end (not reimplemented in miniature)
# in tests/test_u239_qbo_push_retry_idempotency.py::
# test_sync_to_qbo_bill_first_create_stores_local_mirror_and_lines — updated in
# this unit to assert `bill_line_item_service.repo.set_qbo_identity` is called
# by line_num match instead of the retired `create_mapping`.


# --- dbo.ReadBillQboLinkInfo simplification --------------------------------------


_BILL_SQL = Path("entities/bill/sql/dbo.bill.sql").read_text()


def _sproc_body(sql_text: str, name: str) -> str:
    start = sql_text.index(f"PROCEDURE {name}")
    end = sql_text.index("\nGO", start)
    return sql_text[start:end]


def test_read_bill_qbo_link_info_no_longer_walks_the_line_mapping():
    body = _sproc_body(_BILL_SQL, "dbo.ReadBillQboLinkInfo")
    assert "BillLineItemBillLine" not in body
    assert "BillLineItem" not in body
    assert "FROM dbo.[Bill]" in body or "FROM dbo.Bill" in body
    assert "QboId" in body and "QboRealmId" in body


# ---------------------------------------------------------------------------
# Reconciliation re-expression: BILL_BILLABLE_STATUS_DRIFT_ROWS_SQL vs the
# retired qbo.BillLineItemBillLine mapping-hop query, on a shared SQLite
# fixture (characterization / equivalence) — mirrors U-356's
# INVOICE_DRAW_ROWS_SQL precedent exactly.
# ---------------------------------------------------------------------------

_LEGACY_MAPPING_HOP_SQL = """
    SELECT
        b.Id AS BillId,
        CAST(b.PublicId AS NVARCHAR(50)) AS BillPublicId,
        b.QboId AS QboBillId,
        b.BillNumber,
        bli.Id AS BillLineItemId,
        bli.Amount AS LineAmount,
        inv.InvoiceNumber AS InvoiceNumber
    FROM dbo.BillLineItem bli
    JOIN dbo.Bill b ON b.Id = bli.BillId
    JOIN qbo.BillLineItemBillLine map ON map.BillLineItemId = bli.Id
    JOIN qbo.BillLine ql ON ql.Id = map.QboBillLineId
    JOIN qbo.Bill qb ON qb.Id = ql.QboBillId AND qb.RealmId = b.RealmId
    LEFT JOIN dbo.InvoiceLineItem ili ON ili.BillLineItemId = bli.Id
    LEFT JOIN dbo.Invoice inv ON inv.Id = ili.InvoiceId
    WHERE bli.IsBilled = 1
      AND ql.BillableStatus = 'Billable'
      AND b.RealmId = ?
      AND b.QboId IS NOT NULL
"""


def _fixture_db():
    """SQLite stand-in with `dbo`/`qbo` ATTACHed as schema names so the
    production SQL text runs unmodified. Representative corpus:

      bli 11  Bill 1 (Q1,R1), IsBilled=1, QboId='1' -> matches ql 5001 (Billable)
      bli 12  Bill 2 (Q2,R1), IsBilled=1, QboId='2' -> matches ql 5002 (Billable)
      bli 13  Bill 3 (Q3,R2), IsBilled=1, QboId='1' -> OTHER realm; matches its
              OWN bill's ql 5003 (Billable) when queried AT realm R2
      bli 14  Bill 4 (Q4,R1), IsBilled=0            -> excluded (not billed)
      bli 15  Bill 5 (Q5,R1), IsBilled=1, QboId='1' -> its OWN match, ql 5005,
              is NotBillable, so it's excluded — but ql 5001/5004/5006 (Bills
              1/4/6, ALL realm R1) also carry QboLineId='1' and ARE Billable;
              a join that isn't scoped to bli 15's OWN parent qbo.Bill (105)
              would incorrectly pick one of those up and flip this row IN.
      bli 16  Bill 6 (Q6,R1), IsBilled=1, QboId=NULL -> excluded (dbo-native:
              no identity to resolve the QBO line through at all)
    """
    conn = sqlite3.connect(":memory:")
    conn.execute("ATTACH DATABASE ':memory:' AS dbo")
    conn.execute("ATTACH DATABASE ':memory:' AS qbo")
    conn.executescript(
        """
        CREATE TABLE dbo.Bill(Id INTEGER PRIMARY KEY, PublicId TEXT, QboId TEXT, RealmId TEXT, BillNumber TEXT);
        CREATE TABLE dbo.BillLineItem(Id INTEGER PRIMARY KEY, BillId INTEGER, Amount REAL, IsBilled INTEGER, QboId TEXT);
        CREATE TABLE qbo.Bill(Id INTEGER PRIMARY KEY, QboId TEXT, RealmId TEXT);
        CREATE TABLE qbo.BillLine(Id INTEGER PRIMARY KEY, QboBillId INTEGER, QboLineId TEXT, BillableStatus TEXT);
        CREATE TABLE qbo.BillLineItemBillLine(Id INTEGER PRIMARY KEY, BillLineItemId INTEGER, QboBillLineId INTEGER);
        CREATE TABLE dbo.InvoiceLineItem(Id INTEGER PRIMARY KEY, BillLineItemId INTEGER, InvoiceId INTEGER);
        CREATE TABLE dbo.Invoice(Id INTEGER PRIMARY KEY, InvoiceNumber TEXT);

        INSERT INTO dbo.Bill VALUES
          (1, 'p1', 'Q1', 'R1', 'B-1'),
          (2, 'p2', 'Q2', 'R1', 'B-2'),
          (3, 'p3', 'Q3', 'R2', 'B-3'),
          (4, 'p4', 'Q4', 'R1', 'B-4'),
          (5, 'p5', 'Q5', 'R1', 'B-5'),
          (6, 'p6', 'Q6', 'R1', 'B-6');
        INSERT INTO dbo.BillLineItem VALUES
          (11, 1, 100.00, 1, '1'),
          (12, 2, 200.00, 1, '2'),
          (13, 3, 300.00, 1, '1'),
          (14, 4, 400.00, 0, '1'),
          (15, 5, 500.00, 1, '1'),
          (16, 6, 600.00, 1, NULL);
        INSERT INTO qbo.Bill VALUES (101, 'Q1', 'R1'), (102, 'Q2', 'R1'), (103, 'Q3', 'R2'),
                                    (104, 'Q4', 'R1'), (105, 'Q5', 'R1'), (106, 'Q6', 'R1');
        INSERT INTO qbo.BillLine VALUES
          (5001, 101, '1', 'Billable'),
          (5002, 102, '2', 'Billable'),
          (5003, 103, '1', 'Billable'),
          (5004, 104, '1', 'Billable'),
          (5005, 105, '1', 'NotBillable'),
          (5006, 106, '1', 'Billable');
        INSERT INTO qbo.BillLineItemBillLine VALUES (1, 11, 5001), (2, 12, 5002), (3, 13, 5003),
                                                     (4, 14, 5004), (5, 15, 5005);
        INSERT INTO dbo.Invoice VALUES (900, 'INV-900');
        INSERT INTO dbo.InvoiceLineItem VALUES (9001, 11, 900);
        """
    )
    return conn


def _rows(conn, sql, realm_id):
    return sorted(conn.execute(sql, (realm_id,)).fetchall())


def test_dbo_native_drift_rows_match_legacy_mapping_hop_on_representative_fixture():
    conn = _fixture_db()
    for realm in ("R1", "R2", "R-NONE"):
        assert _rows(conn, BILL_BILLABLE_STATUS_DRIFT_ROWS_SQL, realm) == _rows(conn, _LEGACY_MAPPING_HOP_SQL, realm)

    rows = _rows(conn, BILL_BILLABLE_STATUS_DRIFT_ROWS_SQL, "R1")
    # (BillId, BillPublicId, QboBillId, BillNumber, BillLineItemId, LineAmount, InvoiceNumber)
    assert (1, "p1", "Q1", "B-1", 11, 100.0, "INV-900") in rows
    assert (2, "p2", "Q2", "B-2", 12, 200.0, None) in rows
    assert [r[0] for r in rows] == [1, 2]
    assert len(rows) == 2  # bli 14 (unbilled), 15 (see next test), 16 (no QboId) all excluded


def test_dbo_native_drift_rows_never_cross_matches_reused_line_id_across_bills():
    """The parent-scoping guard this re-expression's own design note calls
    out: qbo.BillLine's QboLineId is only unique WITHIN its parent qbo.Bill —
    4 different qbo.Bill rows in realm R1 (101, 104, 105, 106) all carry a
    line with QboLineId='1'. bli 15's OWN match (ql 5005, under its actual
    parent qb 105) is NotBillable, so it must NOT appear in the drift result —
    but 3 of those 4 SAME-QboLineId siblings (ql 5001/5004/5006, under
    DIFFERENT bills) ARE Billable. A join missing the `ql.QboBillId = qb.Id`
    parent-scope (bare `ql.QboLineId = bli.QboId`) would pick one of those up
    instead and incorrectly flip bli 15 into the drift result."""
    conn = _fixture_db()
    rows_r1 = _rows(conn, BILL_BILLABLE_STATUS_DRIFT_ROWS_SQL, "R1")
    assert 5 not in {r[0] for r in rows_r1}  # Bill 5 / bli 15 must not appear
    assert rows_r1 == _rows(conn, _LEGACY_MAPPING_HOP_SQL, "R1")  # legacy hop agrees


def test_bill_billable_status_drift_sql_is_mapping_free_and_parent_scoped():
    assert "qbo.BillLineItemBillLine" not in BILL_BILLABLE_STATUS_DRIFT_ROWS_SQL
    assert "JOIN qbo.Bill qb ON qb.QboId = b.QboId AND qb.RealmId = b.RealmId" in BILL_BILLABLE_STATUS_DRIFT_ROWS_SQL
    assert "JOIN qbo.BillLine ql ON ql.QboBillId = qb.Id AND ql.QboLineId = bli.QboId" in BILL_BILLABLE_STATUS_DRIFT_ROWS_SQL
    assert "WHERE bli.IsBilled = 1" in BILL_BILLABLE_STATUS_DRIFT_ROWS_SQL
    assert "AND bli.QboId IS NOT NULL" in BILL_BILLABLE_STATUS_DRIFT_ROWS_SQL


def test_reconcile_bill_billable_status_drift_executes_the_module_level_row_source():
    """The constant the equivalence test exercises must be what production
    actually runs — guards against the two drifting apart."""
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    conn_ctx = MagicMock()
    conn_ctx.__enter__.return_value.cursor.return_value = cursor
    service = ReconciliationService(repo=Mock())

    with patch("shared.database.get_connection", return_value=conn_ctx):
        result = service._reconcile_bill_billable_status_drift(realm_id="realm-1", run_id="run-1")

    cursor.execute.assert_called_once_with(BILL_BILLABLE_STATUS_DRIFT_ROWS_SQL, "realm-1")
    assert result["errors"] == 0 and result["flagged"] == 0


# --- regression: the sproc identity-projection gap the map found ---------------
#
# Static, no-DB-required guard: ReadBillLineItemByPublicId / ReadBillLineItemsByBillId
# / UpdateBillLineItemById MUST project QboId/RealmId, or any future caller that
# trusts their return value's identity fields silently gets None instead of the
# row's real (already-stamped) identity — the exact bug class U-361/U-362's own
# code review caught elsewhere in this program, found here by inspection before
# any code shipped. Mutation-proven: reverting any projection below makes this RED.

_BASE_SQL = Path("entities/bill_line_item/sql/dbo.bill_line_item.sql").read_text()


def _bli_sproc_body(name: str) -> str:
    return _sproc_body(_BASE_SQL, name)


def test_read_by_public_id_sproc_projects_qbo_identity_columns():
    body = _bli_sproc_body("ReadBillLineItemByPublicId")
    assert "[QboId]" in body
    assert "[RealmId]" in body


def test_read_by_bill_id_sproc_projects_qbo_identity_columns():
    body = _bli_sproc_body("ReadBillLineItemsByBillId")
    assert "[QboId]" in body
    assert "[RealmId]" in body


def test_update_by_id_sproc_projects_qbo_identity_columns():
    body = _bli_sproc_body("UpdateBillLineItemById")
    assert "INSERTED.[QboId]" in body, (
        "UpdateBillLineItemById's OUTPUT must include INSERTED.[QboId] - the HIT "
        "path's update_by_public_id return value should carry the row's real "
        "identity, not silently read back as None."
    )
    assert "INSERTED.[RealmId]" in body, "UpdateBillLineItemById's OUTPUT must also include INSERTED.[RealmId]."


def test_read_by_id_sproc_still_projects_qbo_identity_columns_no_regression():
    """ReadBillLineItemById already projected both columns pre-U-363 — pin it
    so a future edit can't silently drop them."""
    body = _bli_sproc_body("ReadBillLineItemById")
    assert "[QboId]" in body
    assert "[RealmId]" in body
