"""U-801 — a completion must not acknowledge an unlogged external write.

Before this unit the `completed` state transition and the QBO outbox row were
written on two different connections, in two different transactions, several
steps apart:

    BillRepository.finalize_by_id   -> its own get_connection() -> COMMITTED
    ...file uploads, Excel sync, Box mirror...
    _enqueue_qbo_sync               -> a fresh get_connection()

and `enqueue_qbo_push` swallowed every exception, so a failed enqueue returned
`qbo_sync_queued: False` while completion still answered 200/207. The Bill was
durably `completed` with no outbox row: nothing to retry, nothing to
dead-letter, and (unlike the coding-suggestion path, which has
/admin/expense-coding/sweep-stranded) no sweeper to find it.

Why sharing one connection is sufficient — and the live probe that proves it —
is documented once, on `BillRepository.finalize_by_id`.

These tests pin, in order:
  1. a lost enqueue FAILS the completion; the two deliberate refusals do not;
  2. the connection is FORWARDED down every hop, not merely accepted;
  3. each repo USES a supplied connection instead of opening its own;
  4. `complete_bill` hands both writes the SAME connection (the whole fix);
  5. the transaction stays short — no auth query under the row lock;
  6. the two Pass-3 fixes: driver text is redacted, and the drain cannot claim
     the row mid-completion;
  7. BillCredit's deliberate carve-out stays best-effort.
"""

# Python Standard Library Imports
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

# Third-Party Imports
import pyodbc
import pytest

OUTBOX_SVC = "integrations.intuit.qbo.outbox.business.service"


def _push(**overrides):
    """Call `enqueue_qbo_push` with the boilerplate filled in."""
    from shared.completion.qbo_enqueue import enqueue_qbo_push

    auth = MagicMock()
    auth.realm_id = "123"
    kwargs = dict(
        kind="sync_bill_to_qbo",
        entity_type="Bill",
        entity_public_id="abc",
        read_auths=lambda: [auth],
        logger=MagicMock(),
        label="Bill",
    )
    kwargs.update(overrides)
    return enqueue_qbo_push(**kwargs)


def _completion_service(*, finalize_returns=..., order=None):
    """A BillService whose `complete_bill` can run with no live database.

    `order`, when given, records the sequence of interesting events so a test can
    assert on ordering.
    """
    from entities.bill.business.service import BillService

    draft = SimpleNamespace(
        id=55, public_id="pub-55", vendor_id=2, bill_number="B1",
        is_draft=True, status="approved", qbo_id=None, realm_id=None,
    )
    finalized = (
        SimpleNamespace(**{**vars(draft), "is_draft": False, "status": "completed"})
        if finalize_returns is ... else finalize_returns
    )

    repo = MagicMock()
    if order is None:
        repo.finalize_by_id.return_value = finalized
    else:
        repo.finalize_by_id.side_effect = lambda **kw: (order.append("finalize"), finalized)[1]
    repo.read_by_bill_number_and_vendor_id.return_value = None

    svc = BillService(repo=repo)
    svc.read_by_public_id = MagicMock(return_value=draft)
    svc.vendor_service = MagicMock()
    svc.vendor_service.read_by_id.return_value = SimpleNamespace(public_id="v", id=2)
    svc.bill_line_item_service = MagicMock()
    svc.bill_line_item_service.read_by_bill_id.return_value = []
    svc._assert_no_duplicate_on_complete = MagicMock()
    svc._assert_completable_lines = MagicMock()
    svc._enqueue_qbo_sync = MagicMock(return_value={"errors": []})
    svc._qbo_auth_service = MagicMock()
    if order is None:
        svc._qbo_auth_service.read_all.return_value = []
    else:
        svc._qbo_auth_service.read_all.side_effect = lambda: (order.append("read_auths"), [])[1]
    return svc, repo


# ---------------------------------------------------------------------------
# 1. Fail closed: a lost enqueue must not be downgraded to a warning
# ---------------------------------------------------------------------------

def test_a_failed_enqueue_raises_instead_of_reporting_a_warning():
    """The core defect. Before this unit it returned a dict and the Bill stayed
    completed with no row to drain."""
    svc = MagicMock()
    svc.return_value.enqueue.side_effect = RuntimeError("outbox insert failed")
    with patch(f"{OUTBOX_SVC}.QboOutboxService", svc):
        with pytest.raises(RuntimeError):
            _push()


def test_a_dead_letter_refusal_stays_non_fatal():
    """Deliberate refusal, not a lost write: the row EXISTS and is dead-lettered.

    A dead-lettered push may already have reached QBO, so recovery goes through
    the RequestId-preserving replay runbook, never a fresh enqueue. Failing the
    completion would lock the Bill out of completing with no self-service path,
    and would protect nothing — the record is already durable.
    """
    from integrations.intuit.qbo.outbox.business.service import (
        QboOutboxDeadLetterExistsError,
    )

    svc = MagicMock()
    svc.return_value.enqueue.side_effect = QboOutboxDeadLetterExistsError(
        entity_type="Bill", entity_public_id="abc",
        kind="sync_bill_to_qbo", dead_letter_public_id="dl-1",
    )
    with patch(f"{OUTBOX_SVC}.QboOutboxService", svc):
        result = _push()
    assert result["qbo_sync_queued"] is False
    assert result["success"] is False
    assert result["errors"]


def test_a_missing_qbo_auth_record_stays_non_fatal():
    """Unchanged: no realm to target is a configuration gap, not a lost write."""
    result = _push(read_auths=lambda: [])
    assert result["qbo_sync_queued"] is False
    assert result["success"] is False


# ---------------------------------------------------------------------------
# 2. The connection must be FORWARDED, not merely accepted
# ---------------------------------------------------------------------------
# A hop that takes `conn` and drops it type-checks and silently restores the
# two-transaction defect, so each hop's forwarding is pinned separately.

def test_the_service_threads_the_connection_to_the_insert():
    from integrations.intuit.qbo.outbox.business.service import QboOutboxService

    repo = MagicMock()
    repo.read_pending_by_entity.return_value = None
    repo.read_dead_letter_by_entity.return_value = None
    sentinel = object()

    QboOutboxService(repo=repo).enqueue(
        kind="sync_bill_to_qbo", entity_type="Bill",
        entity_public_id="abc", realm_id="r1", conn=sentinel,
    )
    assert repo.create.call_args.kwargs["conn"] is sentinel
    assert repo.read_pending_by_entity.call_args.kwargs["conn"] is sentinel
    assert repo.read_dead_letter_by_entity.call_args.kwargs["conn"] is sentinel


def test_the_service_threads_the_connection_to_the_coalesce_update():
    """Policy C coalesce is a different branch — it must share the txn too."""
    from integrations.intuit.qbo.outbox.business.service import QboOutboxService

    repo = MagicMock()
    repo.read_pending_by_entity.return_value = MagicMock(id=7, row_version="rv", public_id="ob-7")
    sentinel = object()

    QboOutboxService(repo=repo).enqueue(
        kind="sync_bill_to_qbo", entity_type="Bill",
        entity_public_id="abc", realm_id="r1", conn=sentinel,
    )
    repo.create.assert_not_called()
    assert repo.update_ready_after.call_args.kwargs["conn"] is sentinel


def test_the_push_helper_forwards_the_connection_to_the_service():
    sentinel = object()
    svc = MagicMock()
    with patch(f"{OUTBOX_SVC}.QboOutboxService", svc):
        _push(conn=sentinel)
    assert svc.return_value.enqueue.call_args.kwargs["conn"] is sentinel


# ---------------------------------------------------------------------------
# 3. Each repo must USE the supplied connection, not open its own beside it
# ---------------------------------------------------------------------------
# The last hop, and strictly stronger than checking the signature: a repo that
# takes `conn` and still calls get_connection() writes in a SECOND transaction
# while every signature and forwarding assertion stays green.

def _outbox_repo_call(name):
    def _run(conn):
        from integrations.intuit.qbo.outbox.persistence import repo as m

        r = m.QboOutboxRepository()
        kw = dict(entity_type="Bill", entity_public_id="abc", kind="sync_bill_to_qbo")
        if name == "create":
            return r.create(realm_id="r1", request_id="rq1", conn=conn, **kw)
        if name == "update_ready_after":
            from datetime import datetime, timezone

            return r.update_ready_after(
                id=7, row_version="rv", ready_after=datetime.now(timezone.utc), conn=conn
            )
        return getattr(r, name)(conn=conn, **kw)

    return _run, "integrations.intuit.qbo.outbox.persistence.repo"


def _bill_repo_finalize():
    def _run(conn):
        from entities.bill.persistence import repo as m

        return m.BillRepository().finalize_by_id(id=55, conn=conn)

    return _run, "entities.bill.persistence.repo"


@pytest.mark.parametrize(
    "case",
    [
        _outbox_repo_call("create"),
        _outbox_repo_call("read_pending_by_entity"),
        _outbox_repo_call("read_dead_letter_by_entity"),
        _outbox_repo_call("update_ready_after"),
        _bill_repo_finalize(),
    ],
    ids=["outbox.create", "outbox.read_pending", "outbox.read_dead_letter",
         "outbox.update_ready_after", "bill.finalize_by_id"],
)
def test_the_repo_uses_the_supplied_connection(case):
    run, module_path = case
    import importlib

    module = importlib.import_module(module_path)
    conn = MagicMock(name="caller-conn")
    with patch.object(module, "get_connection") as gc:
        try:
            run(conn)
        except Exception:
            # The fake cursor cannot produce a real row. Irrelevant here — the
            # two assertions below are what make this non-vacuous.
            pass
    gc.assert_not_called()
    assert conn.cursor.called, "the supplied connection was never used"


# ---------------------------------------------------------------------------
# 4. The production invariant: complete_bill hands BOTH writes one connection
# ---------------------------------------------------------------------------

def test_completion_hands_both_writes_the_same_connection(bill_completion_conn):
    """Dropping `conn=conn` from either write in `complete_bill` leaves every
    other test in this file green while silently restoring the defect, so this
    is the test that pins the fix."""
    svc, repo = _completion_service()
    svc.complete_bill(public_id="pub-55")

    conn_used = bill_completion_conn.return_value.__enter__.return_value
    assert repo.finalize_by_id.call_args is not None, "finalize was never called"
    assert svc._enqueue_qbo_sync.call_args is not None, "enqueue was never called"
    assert repo.finalize_by_id.call_args.kwargs["conn"] is conn_used
    assert svc._enqueue_qbo_sync.call_args.kwargs["conn"] is conn_used


def test_no_row_is_enqueued_when_the_bill_was_deleted_mid_finalize(bill_completion_conn):
    """A Bill deleted between the read and the flip must not mint an outbox row."""
    svc, _ = _completion_service(finalize_returns=None)
    result = svc.complete_bill(public_id="pub-55")
    assert result["status_code"] == 404
    svc._enqueue_qbo_sync.assert_not_called()


# ---------------------------------------------------------------------------
# 5. The transaction must stay short: no auth query under the row lock
# ---------------------------------------------------------------------------

def test_the_qbo_realm_is_resolved_before_the_transaction_opens(bill_completion_conn):
    """`FinalizeBillById` locks the row WITH (UPDLOCK, HOLDLOCK), and the nested
    COMMIT means those locks live until the OUTER commit. An auth read inside the
    block would hold them across a second pooled checkout and a token
    decryption, blocking the 300ms BillEdit auto-save for that whole time."""
    from contextlib import contextmanager

    order = []
    svc, _ = _completion_service(order=order)

    @contextmanager
    def _recording():
        order.append("open_txn")
        try:
            yield MagicMock(name="completion-conn")
        finally:
            order.append("close_txn")

    bill_completion_conn.side_effect = lambda *a, **k: _recording()
    svc.complete_bill(public_id="pub-55")

    assert "read_auths" in order, "the auth record was never read"
    assert "open_txn" in order, "the transaction was never opened"
    assert order.index("read_auths") < order.index("open_txn"), (
        f"the auth read must happen before the transaction opens; got {order}"
    )


# ---------------------------------------------------------------------------
# 6. Pass-3 fixes
# ---------------------------------------------------------------------------

def test_driver_text_is_redacted_out_of_the_completion_result(bill_completion_conn):
    """Pass 3 F1. Completion now owns the connection, so a raw pyodbc.Error from
    the checkout or the COMMIT reaches the handler without having passed through
    `map_database_error`. That dict is persisted by `set_completion_result` and
    served by GET /get/bill/{public_id}/completion-result to any BILLS-module
    user, so driver and credential text must not survive into it."""
    svc, _ = _completion_service()
    bill_completion_conn.side_effect = pyodbc.OperationalError(
        "08001",
        "[Microsoft][ODBC Driver 18 for SQL Server]Login failed for user 'builduser'. (18456)",
    )

    result = svc.complete_bill(public_id="pub-55")

    assert result["status_code"] == 500
    assert result["bill_finalized"] is False
    blob = f"{result['message']} {result['errors']}"
    for secret in ("Login failed", "builduser", "ODBC Driver 18", "Microsoft"):
        assert secret not in blob, f"{secret!r} leaked into the completion result: {blob}"


def test_the_completion_enqueue_widens_the_policy_c_window():
    """Pass 3 F2. The row is now written BEFORE completion's SharePoint / Excel /
    Box tail, so the drain must not be eligible to claim it while that work is
    still in flight. At the 5s default a drain tick could push against a blob
    Step 2b has not renamed yet and burn a retry."""
    from entities.bill.business.service import (
        _COMPLETION_ENQUEUE_DEBOUNCE_SECONDS,
        BillService,
    )

    assert _COMPLETION_ENQUEUE_DEBOUNCE_SECONDS > 5, "must exceed the 5s default"

    svc = BillService(repo=MagicMock())
    auth = MagicMock()
    auth.realm_id = "r1"
    outbox = MagicMock()
    with patch(f"{OUTBOX_SVC}.QboOutboxService", outbox):
        svc._enqueue_qbo_sync(
            bill=SimpleNamespace(public_id="pub-55"), conn=object(), auths=[auth]
        )
    assert (
        outbox.return_value.enqueue.call_args.kwargs["debounce_seconds"]
        == _COMPLETION_ENQUEUE_DEBOUNCE_SECONDS
    )


# ---------------------------------------------------------------------------
# 7. BillCredit's carve-out stays best-effort, and cannot spread by accident
# ---------------------------------------------------------------------------

def test_bill_credit_enqueue_failure_is_reported_not_raised():
    """BillCredit deliberately keeps the pre-U-801 shape: it finalizes through the
    generic `update_by_public_id` in a ROWVERSION retry loop rather than one
    idempotent flip, so it has no shared transaction to roll back, and its call
    site sits outside any handler. Letting the new exception escape would answer
    500 on a credit that IS already committed — worse than today's 207. If this
    test ever starts failing, BillCredit has become atomic and the carve-out
    (and this test) should go with it.
    """
    from entities.bill_credit.business.complete_service import BillCreditCompleteService

    svc = BillCreditCompleteService.__new__(BillCreditCompleteService)
    svc._qbo_auth_service = MagicMock()
    svc._qbo_auth_service.read_all.side_effect = RuntimeError("auth read failed")

    result = svc._enqueue_qbo_sync(bill_credit=SimpleNamespace(public_id="bc-7", qbo_id=None))

    assert result["qbo_sync_queued"] is False
    assert result["success"] is False
    assert result["errors"][0]["step"] == "qbo_enqueue"
