"""U-446b — a `completed` bill refuses further edits.

A bill reaches `completed` only by finalizing, and finalizing enqueues its AP to
QBO, SharePoint, Excel and Box. Until this unit nothing stopped you editing it
afterwards: vendor, dates, bill number, total and memo were all freely editable
on a completed bill, as were its line items and attachments. Every one of those
edits moved our books without moving any of the four systems that already had
the money.

The contract is `422` + `error_code: "status_locked"`, deliberately NOT 409 —
installed iOS routes 409 to its per-service CONFLICT path (reload-and-retry,
built for optimistic-concurrency collisions) while classifying other 4xx as
terminal. Verified in `BuildOne/Services/BuildOneAPI/APIError.swift` rather than
taken from the design doc.

THE EXEMPTIONS ARE THE DANGEROUS PART. Three production flows legitimately
write to completed bills, and all run as REAL USERS, so the system-caller
marker does not cover them: invoice completion flipping `is_billed`, the KI-16
price backfill in the draw push, and the QBO pull projecting what QuickBooks
already holds. Getting any of them wrong breaks client billing or leaves the
local record permanently disagreeing with QBO. Those are tested first, on
purpose.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from shared.authz import clear_authz_context, set_authz_context
from shared.lifecycle.terminal_lock import (
    STATUS_LOCKED_PREFIX,
    StatusLockedError,
    assert_editable,
    is_system_caller,
)


@pytest.fixture(autouse=True)
def _clean_authz():
    clear_authz_context()
    yield
    clear_authz_context()


def _bill(status="completed", is_draft=False, **over):
    base = dict(
        id=55, public_id="bill-55", row_version="AAAA",
        created_datetime=None, modified_datetime=None,
        vendor_id=7, payment_term_id=None, bill_date="2026-09-01",
        due_date="2026-09-01", bill_number="INV-1", total_amount=None,
        memo=None, is_draft=is_draft, status=status,
    )
    base.update(over)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# The exemptions, first — these are what break money flows if wrong
# ---------------------------------------------------------------------------


def test_invoice_completion_may_still_flip_is_billed_on_a_completed_bill():
    """You invoice COMPLETED AP, so every line invoice completion marks belongs
    to a locked bill. Without the explicit exemption this guard would refuse the
    write and break invoice completion outright."""
    from entities.bill_line_item.business.service import BillLineItemService

    svc = BillLineItemService()
    with patch.object(svc, "_assert_parent_editable", wraps=svc._assert_parent_editable) as guard, \
         patch("entities.bill.business.service.BillService") as MockBill:
        MockBill.return_value.read_by_id.return_value = _bill()
        # exempt=True must short-circuit before any parent read
        svc._assert_parent_editable(bill_id=55, what="x", exempt=True)
    assert guard.called
    MockBill.return_value.read_by_id.assert_not_called()


def _bli_mutations_missing_the_exemption(path):
    """Calls onto a BillLineItem service's guarded mutators that are NOT exempt.

    Bound per-CALL: a file-wide `"_via_internal_pipeline" in src` stays green
    when the kwarg is dropped from some of the calls but not all, which is the
    exact shape of the bug it is supposed to catch.
    """
    import ast

    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    missing = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in {"create", "update_by_public_id", "delete_by_public_id"}:
            continue
        names = {kw.arg for kw in node.keywords}
        # A BILL-parent line write. The sibling branches in this same loop
        # write Expense / BillCredit / InvoiceLineItem lines and must NOT be
        # exempt — those entities have no terminal lock yet, so handing them
        # one would be a bypass waiting for their Phase-3 unit to land.
        if "bill_public_id" not in names or not names & {"is_billed", "price"}:
            continue
        if not any(
            kw.arg == "_via_internal_pipeline"
            and isinstance(kw.value, ast.Constant)
            and kw.value.value is True
            for kw in node.keywords
        ):
            missing.append(node.lineno)
    return missing


def test_the_invoice_completion_call_sites_actually_pass_the_exemption():
    """A guard that is exempt-able but never exempted at the call site is the
    same outage: every invoice completion would 500 on locked AP."""
    from tests.sproc_text import REPO_ROOT

    path = REPO_ROOT / "entities/invoice/business/service.py"
    missing = _bli_mutations_missing_the_exemption(path)
    assert not missing, (
        f"unexempted is_billed write(s) at {path.name}:{missing} — invoice "
        "completion writes to lines of ALREADY completed bills"
    )


def test_the_draw_push_backfill_actually_reaches_the_mutator_exempted():
    """Driven, not read. The exemption there is built into a `kwargs` dict and
    splatted, so source inspection stays green if the splat is dropped from the
    real call while the dict-building code survives."""
    from entities.invoice.business.push import _ki16_ensure_price_on_parent_lines

    line = SimpleNamespace(public_id="bli-1", row_version="AAAA", price=None, amount=10)
    with patch(
        "entities.bill_line_item.business.service.BillLineItemService"
    ) as MockSvc:
        _ki16_ensure_price_on_parent_lines("Bill", [line])

    MockSvc.return_value.update_by_public_id.assert_called_once()
    kwargs = MockSvc.return_value.update_by_public_id.call_args.kwargs
    assert kwargs.get("_via_internal_pipeline") is True, (
        f"KI-16 repairs prices on COMPLETED bills; got {sorted(kwargs)}"
    )


def test_the_draw_push_does_NOT_exempt_expense_parents():
    """Expense has no terminal lock yet — handing it a Bill-shaped exemption
    would be a bypass waiting for that unit to land."""
    from entities.invoice.business.push import _ki16_ensure_price_on_parent_lines

    line = SimpleNamespace(public_id="eli-1", row_version="AAAA", price=None, amount=10)
    with patch(
        "entities.expense_line_item.business.service.ExpenseLineItemService"
    ) as MockSvc:
        _ki16_ensure_price_on_parent_lines("Expense", [line])

    kwargs = MockSvc.return_value.update_by_public_id.call_args.kwargs
    assert "_via_internal_pipeline" not in kwargs


# ---------------------------------------------------------------------------
# The system-caller signature
# ---------------------------------------------------------------------------


def test_the_outbox_worker_boundary_marks_a_system_caller():
    """`system_authz()` is what every outbox worker and the in-process
    scheduler wrap themselves in."""
    from shared.authz import system_authz

    with system_authz():
        assert is_system_caller() is True
        assert_editable(status="completed", what="x")  # must not raise
    assert is_system_caller() is False, "the marker must not survive the block"


def test_the_cli_boundary_marks_a_system_caller():
    from scripts.sync_helper import assert_cli_system_admin

    assert_cli_system_admin()
    assert is_system_caller() is True
    assert_editable(status="completed", what="x")  # must not raise


def test_the_drain_boundary_marks_a_system_caller():
    """The HTTP drain authenticates on the shared secret, not a JWT, so it is
    the one HTTP path that legitimately asserts the marker."""
    import inspect

    from shared.api import admin

    src = inspect.getsource(admin._require_drain_secret)
    assert "is_system_context=True" in src, (
        "the drain endpoint must assert the marker or every guarded drain "
        "starts failing closed"
    )


def test_the_system_caller_signature_CANNOT_BE_FORGED_OVER_HTTP():
    """THE regression for Codex P1 #6, and the reason this reads a marker.

    The first cut of this lock tested `is_system_admin AND user_id is None`,
    on the belief that only a worker could produce that pair. It is reachable
    from an ordinary HTTP request: a valid, unexpired admin JWT whose `uid` no
    longer resolves to a User row falls through `_enrich_payload_with_authz`'s
    `if user:` and lands on exactly that pair — so a stale-but-signed human
    admin session could PUT a completed bill and be mistaken for a drain
    worker. The marker is unforgeable because `set_authz_context` defaults it
    to False, which is what the auth dependency calls.
    """
    set_authz_context(user_id=None, company_id=None, is_system_admin=True)
    assert is_system_caller() is False
    with pytest.raises(StatusLockedError):
        assert_editable(status="completed", what="its header cannot be changed")


def test_an_unresolvable_admin_jwt_no_longer_grants_system_admin_at_all():
    """Belt to the marker's braces, one layer down.

    The collision above was never the lock's to prevent — the auth layer was
    handing `@ActorIsSystemAdmin = 1` to a principal that does not exist,
    bypassing every UserCanAccess* UDF. An `isa` claim is now honoured only
    for an actor that actually resolved.
    """
    from entities.auth.business.service import _enrich_payload_with_authz

    with patch("entities.auth.business.service.UserService") as MockUser:
        MockUser.return_value.read_by_public_id.return_value = None  # deleted user
        payload = _enrich_payload_with_authz(
            {"sub": "auth-1", "uid": "user-that-no-longer-exists", "isa": True}
        )

    assert payload["user_id"] is None
    assert payload["is_system_admin"] is False, (
        "a signed `isa` claim for an unresolvable actor must not grant the "
        "system-admin bypass"
    )
    assert is_system_caller() is False


def test_a_HUMAN_system_admin_is_NOT_exempt():
    """THE assertion that makes the signature worth having.

    Chris (17) and the Claude Agent (33) are real users who are ALSO system
    admins. Exempting on `is_system_admin` alone would hand the two most active
    accounts in the system a blanket bypass of the lock — which is precisely the
    lock's purpose defeated.
    """
    set_authz_context(user_id=17, company_id=1, is_system_admin=True)
    assert is_system_caller() is False
    with pytest.raises(StatusLockedError):
        assert_editable(status="completed", what="its header cannot be changed")


def test_an_ordinary_user_is_not_exempt():
    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    assert is_system_caller() is False
    with pytest.raises(StatusLockedError):
        assert_editable(status="completed", what="x")


# ---------------------------------------------------------------------------
# What the lock actually locks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["draft", "submitted", "in_review", "approved", "declined"])
def test_every_non_terminal_state_stays_editable(status):
    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    assert_editable(status=status, what="x")  # must not raise


def test_only_completed_is_terminal():
    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    with pytest.raises(StatusLockedError):
        assert_editable(status="completed", what="x")


def test_it_falls_back_to_is_draft_for_entities_without_a_status_column():
    """BillCredit and Invoice have no Status column until their own
    Phase-3 units land, so the helper can be reused there without being wrong in
    the meantime. Expense gained a stored column in U-467."""
    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    assert_editable(status=None, is_draft=True, what="x")      # draft -> editable
    with pytest.raises(StatusLockedError):
        assert_editable(status=None, is_draft=False, what="x")  # finalized -> locked


def test_an_unknown_document_fails_OPEN():
    """Deliberate and narrow: this is a lifecycle guard, not authorization, and
    every production path reads a full row before calling it. Failing closed
    would block legitimate edits whenever a caller held a partial object."""
    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    assert_editable(status=None, is_draft=None, what="x")  # must not raise


# ---------------------------------------------------------------------------
# The HTTP contract
# ---------------------------------------------------------------------------


def test_the_error_surfaces_as_422_status_locked_not_409():
    """409 is what installed iOS routes to its optimistic-concurrency CONFLICT
    path — reload and retry. A permanent lock answered with 409 makes a queued
    edit loop or get discarded through the wrong path; 4xx-non-409 is classified
    terminal and surfaced. ProcessEngine flattens the exception to a string, so
    the message prefix is the only structure that reaches the router."""
    from shared.api.errors import ApiError, ErrorCode
    from shared.api.responses import raise_workflow_error

    err = StatusLockedError("its header cannot be changed")
    assert str(err).startswith(STATUS_LOCKED_PREFIX)

    with pytest.raises(ApiError) as exc:
        raise_workflow_error(str(err), "Failed to update bill")
    assert exc.value.status_code == 422
    assert exc.value.status_code != 409
    assert exc.value.error_code == ErrorCode.STATUS_LOCKED


def test_the_message_names_what_was_refused():
    """The detail reaches a human. "This document is completed and can no longer
    be edited" alone does not say what they tried to do."""
    assert "its line items cannot be changed" in str(
        StatusLockedError("its line items cannot be changed")
    )


# ---------------------------------------------------------------------------
# The service wiring
# ---------------------------------------------------------------------------


def test_bill_header_update_is_refused_on_a_completed_bill():
    from entities.bill.business.service import BillService

    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    svc = BillService(repo=MagicMock())
    svc.read_by_public_id = MagicMock(return_value=_bill())
    with pytest.raises(StatusLockedError, match="header"):
        svc.update_by_public_id(public_id="bill-55", row_version="AAAA", memo="tampered")


def test_the_completion_pipeline_may_still_update_the_header():
    """`_via_completion_pipeline` is the existing internal-only kwarg — the
    router's payload never includes it, so no HTTP caller can acquire it."""
    from entities.bill.business.service import BillService

    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    repo = MagicMock()
    repo.update_by_id.return_value = _bill()
    svc = BillService(repo=repo)
    svc.read_by_public_id = MagicMock(return_value=_bill())
    svc.update_by_public_id(
        public_id="bill-55", row_version="AAAA", memo="ok",
        _via_completion_pipeline=True,
    )
    repo.update_by_id.assert_called_once()


def test_a_draft_bill_is_still_freely_editable():
    from entities.bill.business.service import BillService

    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    repo = MagicMock()
    repo.update_by_id.return_value = _bill(status="draft", is_draft=True)
    svc = BillService(repo=repo)
    svc.read_by_public_id = MagicMock(return_value=_bill(status="in_review", is_draft=True))
    svc.update_by_public_id(public_id="bill-55", row_version="AAAA", memo="fine")
    repo.update_by_id.assert_called_once()


# ---------------------------------------------------------------------------
# The line-item / attachment surface, through the real service
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status,should_raise", [("completed", True), ("in_review", False)])
def test_line_item_edits_are_refused_on_a_completed_parent(status, should_raise):
    """A mutation check found this gap: every earlier test exercised the
    EXEMPTION short-circuit, so deleting the parent read entirely — making the
    guard a no-op for every non-exempt caller — left the suite green.

    This drives the real service method against a real parent lookup.
    """
    from entities.bill_line_item.business.service import BillLineItemService

    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    svc = BillLineItemService()
    with patch("entities.bill.business.service.BillService") as MockBill:
        MockBill.return_value.read_by_id.return_value = _bill(
            status=status, is_draft=(status != "completed")
        )
        if should_raise:
            with pytest.raises(StatusLockedError, match="line items"):
                svc._assert_parent_editable(bill_id=55, what="its line items cannot be changed")
        else:
            svc._assert_parent_editable(bill_id=55, what="its line items cannot be changed")
        MockBill.return_value.read_by_id.assert_called_once_with(id=55)


def test_the_guard_reads_the_parent_rather_than_trusting_the_caller():
    """A line item carries no lifecycle state of its own, so the guard has to go
    and look. Deleting that read is exactly the mutation that survived the first
    battery."""
    from entities.bill_line_item.business.service import BillLineItemService

    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    svc = BillLineItemService()
    with patch("entities.bill.business.service.BillService") as MockBill:
        MockBill.return_value.read_by_public_id.return_value = _bill()
        with pytest.raises(StatusLockedError):
            svc._assert_parent_editable(
                bill_public_id="bill-55", what="line items cannot be added to it"
            )
        MockBill.return_value.read_by_public_id.assert_called_once_with(public_id="bill-55")


def test_all_three_line_item_mutators_carry_the_guard():
    """Pinned at the source: a guard on update but not create is a hole shaped
    exactly like the bug."""
    import inspect

    from entities.bill_line_item.business.service import BillLineItemService

    for name in ("create", "update_by_public_id", "delete_by_public_id"):
        src = inspect.getsource(getattr(BillLineItemService, name))
        assert "_assert_parent_editable" in src, f"{name} is unguarded"
        assert "_via_internal_pipeline" in src, f"{name} cannot be exempted"


def test_attachment_mutators_carry_the_guard():
    import inspect

    from entities.bill_line_item_attachment.business.service import (
        BillLineItemAttachmentService,
    )

    for name in ("create", "delete_by_public_id"):
        src = inspect.getsource(getattr(BillLineItemAttachmentService, name))
        assert "_assert_parent_editable" in src, f"attachment {name} is unguarded"


# ---------------------------------------------------------------------------
# The REAL mutators, driven end to end
#
# Everything above this line either calls the helper directly or reads source
# text. Codex's review named exactly what that leaves uncovered: flipping a
# mutator to `exempt=True`, to `bill_id=None`, or to stop forwarding
# `_via_internal_pipeline` keeps every one of those tests green. These drive the
# real methods and assert on the repo, so each of those mutations goes RED.
# ---------------------------------------------------------------------------


def _line(bill_id=55, public_id="bli-1"):
    return SimpleNamespace(
        id=1, public_id=public_id, bill_id=bill_id, row_version="AAAA",
        sub_cost_code_id=None, project_id=None, description="d", quantity=1,
        rate=None, amount=None, is_billable=True, is_billed=False, markup=None,
        price=None, is_draft=True,
        # the repo reads this off the model when binding @RowVersion
        row_version_bytes=b"\x00" * 8,
    )


def _bli_service(existing=None):
    """Real BillLineItemService with a mock repo, as a non-exempt human."""
    from entities.bill_line_item.business.service import BillLineItemService

    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    svc = BillLineItemService(repo=MagicMock())
    svc.read_by_public_id = MagicMock(return_value=existing)
    return svc


def test_creating_a_line_on_a_completed_bill_is_refused_and_writes_NOTHING():
    svc = _bli_service()
    with patch("entities.bill.business.service.BillService") as MockBill:
        MockBill.return_value.read_by_public_id.return_value = _bill()
        with pytest.raises(StatusLockedError, match="added to it"):
            svc.create(bill_public_id="bill-55", description="sneaking one in")
    svc.repo.create.assert_not_called()


def test_updating_a_line_on_a_completed_bill_is_refused_and_writes_NOTHING():
    svc = _bli_service(existing=_line())
    with patch("entities.bill.business.service.BillService") as MockBill:
        MockBill.return_value.read_by_id.return_value = _bill()
        with pytest.raises(StatusLockedError, match="line items cannot be changed"):
            svc.update_by_public_id("bli-1", row_version="AAAA", amount=999)
    svc.repo.update_by_id.assert_not_called()


def test_deleting_a_line_from_a_completed_bill_is_refused_and_writes_NOTHING():
    svc = _bli_service(existing=_line())
    with patch("entities.bill.business.service.BillService") as MockBill:
        MockBill.return_value.read_by_id.return_value = _bill()
        with pytest.raises(StatusLockedError, match="deleted"):
            svc.delete_by_public_id("bli-1")
    svc.repo.delete_by_id.assert_not_called()


def test_a_line_cannot_be_MOVED_ONTO_a_completed_bill():
    """Codex P0. Guarding only the CURRENT parent let anyone re-point a line at
    a completed bill by PUTting it with that bill's public id — the destination
    was read for existence and access, never for lifecycle, so the locked bill
    silently gained a line."""
    svc = _bli_service(existing=_line(bill_id=99))  # currently on a DRAFT bill
    with patch("entities.bill.business.service.BillService") as MockBill:
        MockBill.return_value.read_by_id.return_value = _bill(
            status="in_review", is_draft=True
        )
        MockBill.return_value.read_by_public_id.return_value = _bill()  # completed
        with pytest.raises(StatusLockedError, match="moved onto it"):
            svc.update_by_public_id(
                "bli-1", row_version="AAAA", bill_public_id="bill-55"
            )
    svc.repo.update_by_id.assert_not_called()


@pytest.mark.parametrize(
    "method,kwargs",
    [
        ("create", dict(bill_public_id="bill-55", description="d")),
        ("update_by_public_id", dict(public_id="bli-1", row_version="AAAA")),
        ("delete_by_public_id", dict(public_id="bli-1")),
    ],
)
def test_each_mutator_still_lets_an_exempt_internal_pipeline_through(method, kwargs):
    """The other half: if the exemption stopped working, invoice completion and
    the QBO pull break instead.

    Asserted as the ABSENCE of the lock, not as a completed write — past the
    guard these methods do real repo work this pure-logic harness blocks, and
    that blocked call is itself proof the guard let the caller through.
    """
    svc = _bli_service(existing=_line())
    with patch("entities.bill.business.service.BillService") as MockBill:
        MockBill.return_value.read_by_id.return_value = _bill()
        MockBill.return_value.read_by_public_id.return_value = _bill()
        try:
            getattr(svc, method)(**kwargs, _via_internal_pipeline=True)
        except StatusLockedError as err:
            pytest.fail(f"the exemption stopped working for {method}: {err}")
        except Exception:
            pass  # got past the guard, then hit the no-live-DB harness block


# ---------------------------------------------------------------------------
# Bill deletion — the guard that landed in the WRONG METHOD
# ---------------------------------------------------------------------------


def _bill_service_for_delete(bill):
    """U-446c: the cascade is one sproc call, so there is nothing else to stub."""
    from entities.bill.business.service import BillService

    svc = BillService(repo=MagicMock())
    svc.read_by_public_id = MagicMock(return_value=bill)
    svc.repo.delete_cascade_by_id.return_value = bill
    return svc


def test_an_ordinary_user_cannot_delete_a_completed_bill():
    """The first cut of this unit put this guard in `complete_bill` instead —
    a scripted edit anchored on the wrong `read_by_public_id`. Delete was left
    completely unguarded AND re-completion was wrongly blocked, and every test
    stayed green because none of them drove either method."""
    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    svc = _bill_service_for_delete(_bill())
    with pytest.raises(StatusLockedError, match="cannot be deleted"):
        svc.delete_by_public_id(public_id="bill-55")
    svc.repo.delete_cascade_by_id.assert_not_called()


def test_a_system_admin_may_still_delete_a_completed_bill():
    """Deliberate escape hatch (§4.1): deleting a bill whose AP already shipped
    is destructive, but it is the only remedy for one created in error. This is
    the ONE guard that exempts on is_system_admin alone, because a human admin
    is the intended actor."""
    set_authz_context(user_id=17, company_id=1, is_system_admin=True)
    svc = _bill_service_for_delete(_bill())
    svc.delete_by_public_id(public_id="bill-55")
    svc.repo.delete_cascade_by_id.assert_called_once()


def test_a_draft_bill_is_deletable_by_anyone_who_can_reach_it():
    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    svc = _bill_service_for_delete(_bill(status="in_review", is_draft=True))
    svc.delete_by_public_id(public_id="bill-55")
    svc.repo.delete_cascade_by_id.assert_called_once()


# ---------------------------------------------------------------------------
# Completion, end to end — Step 1 locks the bill that Step 2 must still write
# ---------------------------------------------------------------------------


def test_completion_forwards_the_exemption_to_its_OWN_line_finalize():
    """Codex P0, and the sharpest test in this file.

    `complete_bill` sets the header to `completed` in Step 1, then marks each
    draft line in Step 2 — against a parent that now reads terminal. Without
    the exemption every completion with draft lines returns 207 full of
    `status_locked` errors and leaves the lines draft. Reclaim runs under
    system context and would have masked it in prod.

    It is asserted on the CALL, not on the source, because the first fix was
    inert: the flag was passed into `BillLineItemUpdate(...)`, and pydantic
    treats a leading-underscore name as a private attribute — it never became a
    field, so `model_dump()` dropped it and the mutator saw the `False`
    default. The call site read correctly and did nothing.
    """
    from entities.bill.business.service import BillService

    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    repo = MagicMock()
    repo.finalize_by_id.return_value = _bill()          # Step 1 -> completed
    repo.read_by_bill_number_and_vendor_id.return_value = None  # no duplicate
    svc = BillService(repo=repo)
    svc.read_by_public_id = MagicMock(return_value=_bill(status="in_review", is_draft=True))
    svc.vendor_service = MagicMock()
    svc.vendor_service.read_by_id.return_value = SimpleNamespace(public_id="vendor-1")
    svc.project_service = MagicMock()
    svc.bill_line_item_service = MagicMock()
    svc.bill_line_item_service.read_by_bill_id.return_value = [_line()]
    svc._qbo_auth_service = MagicMock(read_all=MagicMock(return_value=[]))

    try:
        svc.complete_bill(public_id="bill-55")
    except Exception:
        pass  # later steps reach out to blob/outbox; Step 2 is what's under test

    svc.bill_line_item_service.update_by_public_id.assert_called_once()
    kwargs = svc.bill_line_item_service.update_by_public_id.call_args.kwargs
    assert kwargs.get("_via_internal_pipeline") is True, (
        "completion must exempt its own line finalize — it is the thing doing "
        f"the completing. Got: {sorted(kwargs)}"
    )


def test_the_exemption_flag_cannot_travel_inside_a_pydantic_model():
    """Pins the trap itself, so nobody re-routes the flag through the schema.

    Underscore-prefixed names are private attributes in pydantic, silently
    dropped from both the model and `model_dump()`.
    """
    from entities.bill_line_item.api.schemas import BillLineItemUpdate

    dumped = BillLineItemUpdate(
        row_version="AAAA", bill_public_id="bill-55", description="d",
        is_draft=False, _via_internal_pipeline=True,
    ).model_dump()
    assert "_via_internal_pipeline" not in dumped, (
        "pydantic started carrying underscore fields — re-check every call "
        "site that dumps a model into a guarded mutator"
    )


def test_recompleting_an_already_completed_bill_is_not_blocked_by_the_lock():
    """The other half of the misplaced-guard P0. The stray guard landed in
    `complete_bill`, so re-completion — which the reclaim watchdog does on
    purpose with force=True — raised `status_locked` instead of being the
    idempotent no-op `FinalizeBillById` is built to be."""
    from entities.bill.business.service import BillService

    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    repo = MagicMock()
    repo.finalize_by_id.return_value = _bill()
    repo.read_by_bill_number_and_vendor_id.return_value = None  # no duplicate
    svc = BillService(repo=repo)
    svc.read_by_public_id = MagicMock(return_value=_bill())  # ALREADY completed
    svc.vendor_service = MagicMock()
    svc.vendor_service.read_by_id.return_value = SimpleNamespace(public_id="vendor-1")
    svc.project_service = MagicMock()
    svc.bill_line_item_service = MagicMock()
    svc.bill_line_item_service.read_by_bill_id.return_value = []
    svc._qbo_auth_service = MagicMock(read_all=MagicMock(return_value=[]))

    try:
        svc.complete_bill(public_id="bill-55")
    except StatusLockedError as err:
        pytest.fail(f"re-completion must stay an idempotent no-op, got: {err}")
    except Exception:
        pass
    repo.finalize_by_id.assert_called_once_with(id=55)


# ---------------------------------------------------------------------------
# The QBO pull — the exemption that runs under a HUMAN's context
# ---------------------------------------------------------------------------


def test_the_qbo_pull_exempts_every_line_mutation_it_makes():
    """Codex P1 #4.

    The scheduler drives QBO pulls through the drain, which asserts system
    context — so the lock would never fire there and the gap stays invisible.
    But `POST /sync/qbo-bills` runs the IDENTICAL connector under
    `require_module_api(Modules.QBO_SYNC, "can_create")`: a real, human-
    authenticated caller. Against a bill already completed locally, the pull's
    create/update/delete would be refused and the local record would drift
    permanently from the system that owns the money.

    Bound per-CALL rather than by counting a substring: a file-wide
    `"_via_internal_pipeline" in src` stays green if the kwarg is dropped from
    two of the three calls, which is exactly the shape of the original bug.
    """
    import ast

    from tests.sproc_text import REPO_ROOT

    path = (
        REPO_ROOT
        / "integrations/intuit/qbo/bill/connector/bill_line_item/business/service.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))

    guarded = {"create", "update_by_public_id", "delete_by_public_id"}
    found: dict[str, bool] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in guarded:
            continue
        # ...only calls onto the BillLineItem service, not some other object
        target = node.func.value
        if not (isinstance(target, ast.Attribute) and target.attr == "bill_line_item_service"):
            continue
        exempted = any(
            kw.arg == "_via_internal_pipeline"
            and isinstance(kw.value, ast.Constant)
            and kw.value.value is True
            for kw in node.keywords
        )
        found[node.func.attr] = found.get(node.func.attr, True) and exempted

    assert set(found) == guarded, (
        f"expected the QBO pull to mutate lines via all of {sorted(guarded)}; "
        f"found {sorted(found)} — did a call move or get renamed?"
    )
    unexempted = sorted(name for name, ok in found.items() if not ok)
    assert not unexempted, (
        f"the QBO pull's {unexempted} call(s) would be refused on a completed "
        "bill when triggered from POST /sync/qbo-bills"
    )


def test_an_ordinary_set_authz_context_CLEARS_a_marker_left_by_a_worker():
    """The reset is what makes the marker unforgeable, and nothing else proved
    it: the stale-JWT test starts from a cleared context, so dropping
    `current_is_system_context.set(...)` from `set_authz_context` would leave it
    green. This starts from a system context on purpose."""
    from shared.authz import current_is_system_context, system_authz

    with system_authz():
        assert current_is_system_context.get() is True
        # ...the auth dependency running inside that same context
        set_authz_context(user_id=17, company_id=1, is_system_admin=True)
        assert current_is_system_context.get() is False, (
            "set_authz_context must clear the marker by default or a worker's "
            "context becomes inheritable"
        )
        assert is_system_caller() is False
        with pytest.raises(StatusLockedError):
            assert_editable(status="completed", what="x")


# ---------------------------------------------------------------------------
# create_bill's duplicate path — a write to a completed Bill's header
# ---------------------------------------------------------------------------


def _create_service(existing):
    from entities.bill.business.service import BillService

    repo = MagicMock()
    repo.read_by_bill_number_and_vendor_id.return_value = existing
    svc = BillService(repo=repo)
    return svc, repo


def _post_create(svc, vendor_public_id="vendor-1"):
    """POST /create/bill, carrying a client-supplied source-email id."""
    with patch("entities.bill.business.service.VendorService") as MockVendor, \
         patch("entities.email_message.business.service.EmailMessageService") as MockEmail:
        MockVendor.return_value.read_by_public_id.return_value = SimpleNamespace(id=7)
        MockEmail.return_value.read_by_public_id.return_value = SimpleNamespace(id=123)
        try:
            svc.create(
                vendor_public_id=vendor_public_id,
                bill_number="INV-1",
                bill_date="2026-09-01",
                due_date="2026-09-01",
                is_draft=True,
                # Orthogonal to what is under test: every Bill needs a PDF
                # from creation, so the duplicate path is only reachable
                # once one exists. Skipping it keeps the test on the guard.
                require_attachment=False,
                source_email_message_public_id="email-1",
            )
        except Exception as err:
            return err
    return None


def test_posting_a_duplicate_does_not_stamp_a_completed_bills_source_email():
    """Codex round 2, P1. The opportunistic backfill WRITES `SourceEmailMessageId`
    and bumps `ModifiedDatetime` on the matched Bill, and the email id is
    client-supplied — so posting a duplicate of a completed Bill edited a locked
    document while returning the ordinary duplicate error."""
    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    svc, repo = _create_service(_bill())  # completed
    err = _post_create(svc)
    repo.link_source_email_message.assert_not_called()
    assert err is not None and "already exists" in str(err), (
        "the duplicate error is still the right answer — only the write is "
        f"dropped. Got: {err!r}"
    )
    assert "completed" in str(err), "say why the link was left alone"


def test_posting_a_duplicate_of_a_DRAFT_bill_still_backfills_the_link():
    """The backfill exists to preserve the email dedup trail. Over-blocking it
    would silently break intake."""
    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    svc, repo = _create_service(_bill(status="in_review", is_draft=True))
    _post_create(svc)
    repo.link_source_email_message.assert_called_once()


def test_not_even_a_system_caller_backfills_onto_a_completed_bill():
    """Corrected from the opposite assertion (Codex round 5, P3).

    An earlier cut exempted system callers here. The sproc's
    `AND [Status] <> 'completed'` applies to everyone, so that exemption only
    sent them down a path that wrote nothing and then reported "already has a
    source email linked" — a message describing something that did not happen.
    A completed Bill has already shipped its AP; a late dedup stamp protects
    nothing.
    """
    from shared.authz import system_authz

    svc, repo = _create_service(_bill())
    with system_authz():
        err = _post_create(svc)
    repo.link_source_email_message.assert_not_called()
    assert err is not None and "completed" in str(err)


# ===========================================================================
# ROUND 3 — the four items Codex's round-2 review left open, folded in
# ===========================================================================


def _executable(sql: str) -> str:
    """SQL with comments stripped.

    Asserting against raw sproc text has produced a false green FOUR times in
    this unit alone: the prose in a comment satisfies the assertion while the
    executable predicate is gone. Every SQL assertion below goes through here.
    """
    return "\n".join(line.split("--")[0] for line in sql.splitlines())


# ---------------------------------------------------------------------------
# 1. The in-transaction guard (Codex round 1, P1 #5 — the TOCTOU)
# ---------------------------------------------------------------------------

_GUARDED_SPROCS = [
    ("entities/bill_line_item/sql/dbo.bill_line_item.sql", "CreateBillLineItem"),
    ("entities/bill_line_item/sql/dbo.bill_line_item.sql", "UpdateBillLineItemById"),
    ("entities/bill_line_item/sql/dbo.bill_line_item.sql", "DeleteBillLineItemById"),
    ("entities/bill_line_item_attachment/sql/dbo.bill_line_item_attachment.sql",
     "CreateBillLineItemAttachment"),
    ("entities/bill_line_item_attachment/sql/dbo.bill_line_item_attachment.sql",
     "DeleteBillLineItemAttachmentById"),
]


@pytest.mark.parametrize("sql_rel,proc", _GUARDED_SPROCS)
def test_every_child_mutation_sproc_checks_the_parent_under_UPDLOCK(sql_rel, proc):
    """The Python guard reads the parent in one transaction and writes the child
    in another. Under RCSI each statement takes its own snapshot, so a line edit
    racing a completion passes the guard on a `draft` snapshot and commits after
    the bill finalizes. Only a locked re-check inside the writing transaction
    closes it — and UPDLOCK is the load-bearing word: a plain SELECT would read
    the same stale snapshot the Python guard just did."""
    from tests.sproc_text import REPO_ROOT, sproc_body

    body = _executable(sproc_body(REPO_ROOT / sql_rel, proc))

    assert "UPDLOCK" in body, f"{proc} reads the parent without locking it"
    assert "HOLDLOCK" in body, f"{proc} must hold the lock to the commit"
    assert "'completed'" in body, f"{proc} does not test the terminal state"
    assert "@AllowTerminalParent = 0" in body, f"{proc} cannot be exempted"
    assert "STATUS_LOCKED:" in body, (
        f"{proc} must RAISERROR the sentinel the repo maps back to a typed error"
    )


@pytest.mark.parametrize("sql_rel,proc", _GUARDED_SPROCS)
def test_a_refusal_commits_before_it_raises(sql_rel, proc):
    """NEVER ROLLBACK inside a sproc. pyodbc runs autocommit-off, so an in-proc
    rollback zeroes the implicit outer transaction and SQL Server raises 266
    ("Transaction count after EXECUTE...") — which would surface as a confusing
    500 instead of the refusal. CLAUDE.md's 2026-06-11 rule."""
    from tests.sproc_text import REPO_ROOT, sproc_body

    body = _executable(sproc_body(REPO_ROOT / sql_rel, proc))
    assert "ROLLBACK" not in body.upper(), f"{proc} rolls back inside the sproc"

    commit_at = body.index("COMMIT TRANSACTION")
    raise_at = body.index("RAISERROR")
    assert commit_at < raise_at, (
        f"{proc} must COMMIT the untouched transaction BEFORE raising"
    )


@pytest.mark.parametrize("sql_rel,proc", _GUARDED_SPROCS)
def test_every_guarded_sproc_sets_nocount(sql_rel, proc):
    """The guard runs assignment SELECTs before the DML. With NOCOUNT off each
    emits a row-count token that pyodbc surfaces as the FIRST result, and
    `cursor.fetchone()` then raises "No results. Previous SQL was not a query"
    instead of returning the OUTPUT row — so every one of these writes would
    break. Single-statement `INSERT ... OUTPUT` sprocs survive without it by
    accident; none of these is single-statement any more."""
    from tests.sproc_text import REPO_ROOT, sproc_body

    body = _executable(sproc_body(REPO_ROOT / sql_rel, proc))
    assert "SET NOCOUNT ON" in body, f"{proc} will break pyodbc's fetchone"


@pytest.mark.parametrize("sql_rel,proc", _GUARDED_SPROCS)
def test_the_new_param_is_optional_and_now_fails_CLOSED(sql_rel, proc):
    """U-446c flipped the default from `= 1` to `= 0`.

    U-446b shipped it permissive deliberately: that was the only default that
    made the SQL safe to apply either side of ITS OWN deploy, because `= 0`
    would have refused completion's own Step-2 line finalize while the previous
    image was still serving — turning every completion with draft lines into a
    207 until the deploy landed, unhealable by the reclaim watchdog. The cost
    was that a call site which forgot the param silently lost the guard.

    That window is closed — U-446b is live and every caller passes the param —
    so the trapdoor is gone: an omission now refuses the write instead of
    quietly skipping the check. The param stays OPTIONAL so the signature is
    still additive for any caller that binds positionally.
    """
    from tests.sproc_text import REPO_ROOT, sproc_params

    params = sproc_params(REPO_ROOT / sql_rel, proc)
    assert "@AllowTerminalParent BIT = 0" in params, (
        f"{proc}: the param must be optional AND fail closed"
    )
    assert "@AllowTerminalParent BIT = 1" not in params, (
        f"{proc}: a permissive default is a silent trapdoor — U-446c removed it"
    )


def test_the_update_sproc_guards_BOTH_the_current_and_the_target_parent():
    """Same hole the Python layer had: guarding only the current parent lets a
    line be re-pointed ONTO a completed bill."""
    from tests.sproc_text import REPO_ROOT, sproc_body

    body = _executable(
        sproc_body(
            REPO_ROOT / "entities/bill_line_item/sql/dbo.bill_line_item.sql",
            "UpdateBillLineItemById",
        )
    )
    # Both ids must reach the locked check, and they must be acquired in a
    # deterministic order (low id first) so two opposite moves cannot deadlock.
    assert "@LoBillId" in body and "@HiBillId" in body, (
        "the two parents must be locked in a total order, not by a bare IN (...)"
    )
    assert "@CurrentBillId <= @BillId" in body, "ascending-id ordering is the point"
    assert body.count("WITH (UPDLOCK, HOLDLOCK)") >= 2, (
        "the target parent must be locked and checked alongside the current one"
    )


# ---------------------------------------------------------------------------
# ...and the Python side of it
# ---------------------------------------------------------------------------


def test_the_sproc_sentinel_becomes_a_typed_error_not_a_500():
    """Without the mapping the refusal reaches `map_database_error` and surfaces
    as a generic 500 — indistinguishable from a real database fault, and routed
    nowhere near the 422 `status_locked` contract."""
    from shared.lifecycle.terminal_lock import reraise_if_sproc_status_locked

    with pytest.raises(StatusLockedError, match="line items"):
        reraise_if_sproc_status_locked(
            Exception("[42000] STATUS_LOCKED: line items cannot be added to a completed Bill."),
            what="its line items cannot be changed",
        )
    # An unrelated database error must pass straight through untouched.
    reraise_if_sproc_status_locked(Exception("deadlock victim"), what="x")


@pytest.mark.parametrize(
    "exempt,system,expected",
    [(False, False, False), (True, False, True), (False, True, True), (True, True, True)],
)
def test_is_exempt_is_the_or_of_pipeline_and_system(exempt, system, expected):
    from shared.authz import system_authz
    from shared.lifecycle.terminal_lock import is_exempt

    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    if system:
        with system_authz():
            assert is_exempt(exempt) is expected
    else:
        assert is_exempt(exempt) is expected


def _repo_param_capture(module_path, cls_name):
    """Drive a repo method with call_procedure mocked, returning its params."""
    import importlib

    mod = importlib.import_module(module_path)
    return mod, getattr(mod, cls_name)


@pytest.mark.parametrize(
    "method,kwargs,expected",
    [
        ("create", dict(bill_public_id="bill-55", description="d"), 0),
        ("update_by_public_id", dict(public_id="bli-1", row_version="AAAA"), 0),
        ("delete_by_public_id", dict(public_id="bli-1"), 0),
    ],
)
def test_the_repo_always_sends_the_flag_and_sends_0_for_a_human(method, kwargs, expected):
    """The sproc default is permissive, so an omitted param is a SILENT loss of
    the guard — there is no error to notice. This is the pin for that."""
    from entities.bill_line_item.business.service import BillLineItemService

    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    svc = BillLineItemService()
    svc.read_by_public_id = MagicMock(return_value=_line())
    captured = {}

    def _capture(*, cursor, name, params):
        captured.update(params)
        raise RuntimeError("stop after the call")

    with patch("entities.bill_line_item.persistence.repo.call_procedure", _capture), \
         patch("entities.bill_line_item.persistence.repo.get_connection"), \
         patch("entities.bill.business.service.BillService") as MockBill, \
         patch("entities.bill_line_item.business.service.BillService") as MockBill2, \
         patch("entities.invoice_line_item.persistence.repo.InvoiceLineItemRepository"), \
         patch("entities.contract_labor.persistence.repo.ContractLaborRepository") as MockCl:
        draft = _bill(status="in_review", is_draft=True)
        for m in (MockBill, MockBill2):
            m.return_value.read_by_id.return_value = draft
            m.return_value.read_by_public_id.return_value = draft
        MockCl.return_value.read_by_bill_line_item_id.return_value = []
        try:
            getattr(svc, method)(**kwargs)
        except Exception:
            pass

    assert "AllowTerminalParent" in captured, (
        f"{method} did not send the flag — the sproc defaults permissive, so "
        "this drops the in-transaction guard with no error anywhere"
    )
    assert captured["AllowTerminalParent"] == expected


def test_an_exempt_caller_sends_1():
    from entities.bill_line_item.business.service import BillLineItemService

    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    svc = BillLineItemService()
    svc.read_by_public_id = MagicMock(return_value=_line())
    captured = {}

    def _capture(*, cursor, name, params):
        captured.update(params)
        raise RuntimeError("stop")

    with patch("entities.bill_line_item.persistence.repo.call_procedure", _capture), \
         patch("entities.bill_line_item.persistence.repo.get_connection"), \
         patch("entities.bill.business.service.BillService") as MockBill, \
         patch("entities.bill_line_item.business.service.BillService") as MockBill2:
        for m in (MockBill, MockBill2):
            m.return_value.read_by_id.return_value = _bill()
            m.return_value.read_by_public_id.return_value = _bill()
        try:
            svc.update_by_public_id("bli-1", row_version="AAAA", _via_internal_pipeline=True)
        except Exception:
            pass

    assert captured.get("AllowTerminalParent") == 1


# ---------------------------------------------------------------------------
# 2. The Attachment itself — not just the link row (Codex round 2, P1)
# ---------------------------------------------------------------------------


def _attachment_service(completed_parents: int):
    from entities.attachment.business.service import AttachmentService

    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    svc = AttachmentService(repo=MagicMock())
    svc.read_by_public_id = MagicMock(
        return_value=SimpleNamespace(
            id=9, public_id="att-9", row_version="AAAA", blob_url="https://b/x.pdf",
            filename="x.pdf", is_archived=False,
        )
    )
    patcher = patch(
        "entities.bill_line_item_attachment.persistence.repo.BillLineItemAttachmentRepository"
    )
    MockRepo = patcher.start()
    MockRepo.return_value.count_completed_bills_by_attachment_id.return_value = completed_parents
    return svc, patcher


@pytest.mark.parametrize(
    "method,kwargs",
    [
        ("update_by_public_id", dict(public_id="att-9", row_version="AAAA", blob_url="https://evil/swap.pdf")),
        ("archive", dict(public_id="att-9")),
        ("unarchive", dict(public_id="att-9")),
        ("delete_by_public_id", dict(public_id="att-9")),
    ],
)
def test_a_completed_bills_evidence_file_cannot_be_touched(method, kwargs):
    """The link row was guarded; the FILE it points at was not.

    A caller holding plain ATTACHMENTS permissions could repoint `blob_url` at
    different bytes, rename it, or archive it — for the exact PDF the AP was
    approved from, on a bill whose money already reached QBO, SharePoint,
    Excel and Box.
    """
    svc, patcher = _attachment_service(completed_parents=1)
    try:
        with pytest.raises(StatusLockedError, match="attachments"):
            getattr(svc, method)(**kwargs)
    finally:
        patcher.stop()
    svc.repo.update_by_id.assert_not_called()
    svc.repo.delete_by_id.assert_not_called()


@pytest.mark.parametrize(
    "method,kwargs",
    [
        ("update_by_public_id", dict(public_id="att-9", row_version="AAAA", filename="renamed.pdf")),
        ("archive", dict(public_id="att-9")),
        ("delete_by_public_id", dict(public_id="att-9")),
    ],
)
def test_an_attachment_on_no_completed_bill_stays_freely_editable(method, kwargs):
    """Most attachments are not bill evidence at all — expense receipts, invoice
    packets, contract-labor logs. Over-blocking here would break all of them."""
    svc, patcher = _attachment_service(completed_parents=0)
    try:
        getattr(svc, method)(**kwargs)  # must not raise
    finally:
        patcher.stop()


def test_completions_own_blob_rename_is_still_allowed():
    """Step 1 of completion has already set the header to `completed`, so by the
    time `_rename_invoice_blob_on_complete` runs, its own bill is terminal. It
    IS the completion; without the exemption the invoice blob would keep its
    nested name forever."""
    svc, patcher = _attachment_service(completed_parents=3)
    try:
        svc.update_by_public_id(
            public_id="att-9", row_version="AAAA", blob_url="https://b/renamed.pdf",
            _via_internal_pipeline=True,
        )
    finally:
        patcher.stop()
    svc.repo.update_by_id.assert_called_once()


def test_the_completion_rename_call_site_passes_the_exemption():
    """Pinned at the call, not the source: dropping it breaks every completion
    whose invoice blob still sits under a nested name."""
    import ast

    from tests.sproc_text import REPO_ROOT

    tree = ast.parse((REPO_ROOT / "entities/bill/business/service.py").read_text())
    renames = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "update_by_public_id"
        and any(kw.arg == "original_filename" for kw in node.keywords)
    ]
    assert len(renames) == 1, f"expected one blob-rename call, found {len(renames)}"
    assert any(
        kw.arg == "_via_internal_pipeline"
        and isinstance(kw.value, ast.Constant)
        and kw.value.value is True
        for kw in renames[0].keywords
    ), "completion's blob rename must exempt itself"


def test_the_delete_route_never_destroys_the_blob_of_a_refused_delete():
    """THE ordering bug, and its second round.

    The route deleted from Azure first and the database second, and
    FK_BillLineItemAttachment_Attachment is NO ACTION — so on a completed Bill's
    evidence the old order destroyed the file, then failed the row delete on the
    FK and returned 500. The document survived in name only.

    A preflight check alone did NOT fix it: the check ran in its own
    transaction, so a completion committing between the check and the Azure call
    still destroyed the bytes. The order is now inverted — the row goes first,
    decided by the sproc's in-transaction guard — so a refusal reaches the
    client with nothing destroyed.
    """
    import entities.attachment.api.router as att_router
    from shared.api.errors import ApiError, ErrorCode

    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    attachment = SimpleNamespace(
        id=9, public_id="att-9", row_version="AAAA", blob_url="https://b/x.pdf"
    )
    with patch.object(att_router, "service") as MockSvc, \
         patch.object(att_router, "AzureBlobStorage") as MockStorage:
        MockSvc.read_by_public_id.return_value = attachment
        MockSvc.delete_by_public_id.side_effect = StatusLockedError(
            "its attachments cannot be deleted"
        )
        with pytest.raises(ApiError) as exc:
            att_router.delete_attachment_by_public_id_router(
                public_id="att-9", current_user={}
            )

    MockStorage.assert_not_called()
    assert exc.value.status_code == 422
    assert exc.value.status_code != 409
    assert exc.value.error_code == ErrorCode.STATUS_LOCKED


def test_the_delete_route_removes_the_row_BEFORE_the_blob():
    """Pins the order itself, not just the refusal path — restoring the old
    blob-first order would make the test above pass again by accident."""
    import entities.attachment.api.router as att_router

    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    attachment = SimpleNamespace(
        id=9, public_id="att-9", row_version="AAAA", blob_url="https://b/x.pdf"
    )
    order = []
    with patch.object(att_router, "service") as MockSvc, \
         patch.object(att_router, "AzureBlobStorage") as MockStorage:
        MockSvc.read_by_public_id.return_value = attachment
        MockSvc.delete_by_public_id.side_effect = lambda **_: (
            order.append("row"), SimpleNamespace(to_dict=lambda: {})
        )[1]
        MockStorage.return_value.delete_file.side_effect = lambda *_: order.append("blob")
        att_router.delete_attachment_by_public_id_router(
            public_id="att-9", current_user={}
        )

    assert order == ["row", "blob"], (
        f"the guarded row delete must decide before anything is destroyed: {order}"
    )


# ---------------------------------------------------------------------------
# 3. The Contract-Labor PDF endpoint (Codex round 2, P1)
# ---------------------------------------------------------------------------


def _cl_pdf_service(bill):
    from entities.contract_labor.business.pdf_service import ContractLaborPDFService

    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    svc = ContractLaborPDFService()
    svc.bill_service = MagicMock()
    svc.bill_service.read_by_public_id.return_value = bill
    svc.vendor_service = MagicMock()
    svc.vendor_service.read_by_id.return_value = SimpleNamespace(id=7, name="Acme")
    svc.bill_line_item_service = MagicMock()
    svc.bill_line_item_service.read_by_bill_id.return_value = [_line()]
    svc.bill_line_item_attachment_service = MagicMock()
    svc.bill_line_item_attachment_service.read_by_bill_line_item_id.return_value = None
    return svc


def test_the_cl_pdf_endpoint_refuses_before_uploading_anything():
    """It used to upload the PDF and create its Attachment row FIRST, and only
    then hit the link guard — leaving an orphan blob and an orphan Attachment
    behind on every single attempt."""
    import entities.contract_labor.business.pdf_service as pdf_mod

    svc = _cl_pdf_service(_bill(bill_number="INV-1"))  # completed
    with patch.object(pdf_mod, "AzureBlobStorage") as MockStorage:
        with pytest.raises(StatusLockedError, match="time-log PDFs"):
            svc.generate_pdfs_for_bill(bill_public_id="bill-55")
    MockStorage.assert_not_called()
    svc.bill_line_item_attachment_service.create.assert_not_called()


def test_the_cl_pdf_endpoint_answers_422_not_200_and_not_500():
    """The broad handler turned the refusal into `{"success": false}` with HTTP
    200 — which every client reads as "nothing went wrong, nothing to retry"."""
    import entities.contract_labor.api.router as cl_router
    from shared.api.errors import ApiError, ErrorCode

    with patch.object(cl_router, "StatusLockedError", StatusLockedError), \
         patch(
            "entities.contract_labor.business.pdf_service.ContractLaborPDFService"
         ) as MockSvc:
        MockSvc.return_value.generate_pdfs_for_bill.side_effect = StatusLockedError(
            "time-log PDFs cannot be generated for it"
        )
        import asyncio
        with pytest.raises(ApiError) as exc:
            asyncio.run(
                cl_router.generate_pdfs_for_bill(bill_public_id="bill-55", current_user={})
            )
    assert exc.value.status_code == 422
    assert exc.value.status_code != 409
    assert exc.value.error_code == ErrorCode.STATUS_LOCKED


def test_the_cl_batch_sweep_SKIPS_a_completed_bill_instead_of_aborting():
    """Different semantics on purpose, and driven through the REAL sweep.

    The single-bill endpoint raises so its caller gets a 422; a sweep over many
    bills must not lose every remaining bill because one of them is closed. An
    earlier draft of this test rebuilt the loop body instead of calling the
    method, which would have stayed green no matter what the sweep did.
    """
    from entities.contract_labor.business.pdf_service import ContractLaborPDFService

    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    svc = ContractLaborPDFService()
    svc.repo = MagicMock()
    svc.repo.read_by_status.return_value = [SimpleNamespace(id=1, bill_line_item_id=11, vendor_id=7)]
    svc.line_item_repo = MagicMock()
    svc.line_item_repo.read_by_contract_labor_id.return_value = [
        SimpleNamespace(bill_line_item_id=12)
    ]
    svc.bill_line_item_service = MagicMock()
    svc.bill_line_item_service.read_by_id.side_effect = lambda id: SimpleNamespace(
        id=id, bill_id=100 + id
    )
    svc.bill_service = MagicMock()
    svc.bill_service.read_by_id.side_effect = lambda id: SimpleNamespace(
        id=id, public_id=f"bill-{id}"
    )

    seen = []

    def _per_bill(*, bill_public_id):
        seen.append(bill_public_id)
        if bill_public_id == "bill-111":
            raise StatusLockedError("time-log PDFs cannot be generated for it")
        return {"pdfs_generated": 2, "errors": []}

    svc.generate_pdfs_for_bill = MagicMock(side_effect=_per_bill)

    result = svc.generate_pdfs_for_billed_entries()

    assert len(seen) == 2, f"the sweep aborted after the locked bill: {seen}"
    assert result["pdfs_generated"] == 2, "the open bill must still be processed"
    assert any("bill-111" in e for e in result["errors"]), (
        "the skip must be RECORDED — a bill that can never get its time log is "
        "something the operator needs to see"
    )


# ---------------------------------------------------------------------------
# 4. Every QBO sync route asserts system intent
# ---------------------------------------------------------------------------


def test_every_qbo_sync_route_declares_system_intent():
    """A QBO pull spans all users' rows by design. Under the requesting user's
    authz the connector's access-scoped lookups return None and it drives
    DUPLICATE creation / mapping deletion — vendor and customer say exactly that
    in their own docstrings, and only those two did it. The other seven ran the
    same connectors under a human's row scope."""
    import ast

    from tests.sproc_text import REPO_ROOT

    offenders = []
    for path in sorted((REPO_ROOT / "integrations/intuit/qbo").glob("*/api/router.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("sync_qbo_"):
                continue
            calls_connector = any(
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == "sync_from_qbo"
                for n in ast.walk(node)
            )
            if not calls_connector:
                continue  # invoice: the pull is disabled, there is nothing to wrap
            wrapped = any(
                isinstance(n, ast.With)
                and any(
                    isinstance(item.context_expr, ast.Call)
                    and getattr(item.context_expr.func, "id", None) == "system_authz"
                    for item in n.items
                )
                for n in ast.walk(node)
            )
            if not wrapped:
                offenders.append(f"{path.parent.parent.name}:{node.name}")

    assert not offenders, (
        "these QBO sync routes run the connector under the requesting user's "
        f"row scope: {offenders}"
    )


# ---------------------------------------------------------------------------
# Gaps the round-3 mutation battery found (each of these went GREEN first)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql_rel,proc,must_contain",
    [
        ("entities/bill_line_item/sql/dbo.bill_line_item.sql", "CreateBillLineItem",
         "WHERE [Id] = @BillId AND [Status] = 'completed'"),
        ("entities/bill_line_item/sql/dbo.bill_line_item.sql", "UpdateBillLineItemById",
         "SELECT @CurrentBillId = [BillId] FROM dbo.[BillLineItem] WHERE [Id] = @Id"),
        ("entities/bill_line_item/sql/dbo.bill_line_item.sql", "DeleteBillLineItemById",
         "SELECT @ParentBillId = [BillId] FROM dbo.[BillLineItem] WHERE [Id] = @Id"),
        ("entities/bill_line_item_attachment/sql/dbo.bill_line_item_attachment.sql",
         "CreateBillLineItemAttachment", "WHERE li.[Id] = @BillLineItemId"),
        ("entities/bill_line_item_attachment/sql/dbo.bill_line_item_attachment.sql",
         "DeleteBillLineItemAttachmentById", "WHERE blia.[Id] = @Id"),
    ],
)
def test_each_guard_resolves_the_parent_from_the_right_key(sql_rel, proc, must_contain):
    """The generic UPDLOCK/'completed' assertions above all stayed green when
    the DELETE sproc's parent lookup was deleted outright, leaving its guard
    comparing against an undeclared variable. Each sproc has to be pinned to the
    key it actually resolves its parent through."""
    from tests.sproc_text import REPO_ROOT, sproc_body

    body = _executable(sproc_body(REPO_ROOT / sql_rel, proc))
    assert must_contain in body, f"{proc} no longer resolves its parent correctly"


@pytest.mark.parametrize(
    "repo_mod,repo_cls,method,args",
    [
        ("entities.bill_line_item.persistence.repo", "BillLineItemRepository",
         "delete_by_id", (1,)),
        ("entities.bill_line_item_attachment.persistence.repo",
         "BillLineItemAttachmentRepository", "delete_by_id", (1,)),
    ],
)
def test_a_sprocs_refusal_reaches_python_as_a_typed_error(repo_mod, repo_cls, method, args):
    """The helper was tested in isolation; nothing proved the REPOS call it.

    Without the mapping the refusal falls through to `map_database_error` and
    surfaces as a generic 500 — indistinguishable from a real database fault,
    and nowhere near the 422 `status_locked` contract the clients depend on.
    """
    import importlib

    mod = importlib.import_module(repo_mod)
    repo = getattr(mod, repo_cls)()

    def _raise(*a, **k):
        raise Exception("[42000] [SQL Server]STATUS_LOCKED: the line items of a completed Bill cannot be deleted. (50000)")

    with patch.object(mod, "call_procedure", _raise), patch.object(mod, "get_connection"):
        with pytest.raises(StatusLockedError):
            getattr(repo, method)(*args, allow_terminal_parent=False)


def test_the_attachment_link_service_also_sends_the_flag_for_a_human():
    """The BLI service was pinned; its attachment-link sibling was not, and
    hardcoding `allow_terminal_parent=True` there left the suite green."""
    from entities.bill_line_item_attachment.business.service import (
        BillLineItemAttachmentService,
    )

    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    svc = BillLineItemAttachmentService(repo=MagicMock())
    svc.repo.read_by_public_id.return_value = SimpleNamespace(id=5, bill_line_item_id=11)
    svc.read_by_public_id = MagicMock(return_value=SimpleNamespace(id=5))
    captured = {}

    def _capture(*, cursor, name, params):
        captured.update(params)
        raise RuntimeError("stop")

    with patch("entities.bill_line_item.business.service.BillLineItemService") as MockBli, \
         patch("entities.bill_line_item_attachment.business.service.BillLineItemService") as MockBli2:
        for m in (MockBli, MockBli2):
            m.return_value.read_by_id.return_value = _line()
        svc.repo.delete_by_id = MagicMock()
        svc.delete_by_public_id(public_id="blia-5")

    assert svc.repo.delete_by_id.call_args.kwargs.get("allow_terminal_parent") is False, (
        "an ordinary user delete must leave the in-transaction guard ON"
    )


def test_a_human_triggered_qbo_sync_now_attributes_rows_to_the_system_user():
    """Pins a real, deliberate behaviour change (Codex round 3, P2).

    `system_authz()` clears `current_user_id`, and the create sprocs COALESCE a
    NULL actor to 17. So a human with QBO_SYNC.can_create who triggers a pull no
    longer has the imported rows attributed to them — they are attributed to the
    system user, exactly as the scheduler-driven pull already did. That is the
    intended "system import" semantics (the same pull should not produce
    different audit rows depending on which endpoint drove it), but it IS a
    change, and it should fail loudly if someone reverts the wrap expecting the
    old attribution back.
    """
    from shared.authz import current_user_id, system_authz

    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    assert current_user_id.get() == 20
    with system_authz():
        assert current_user_id.get() is None, (
            "a QBO pull runs as the system, so CreatedByUserId falls back to 17"
        )
    assert current_user_id.get() == 20, "and the human's context is restored after"


# ===========================================================================
# Round-3 fixes — every one of these went GREEN in the first mutation battery
# ===========================================================================


def _ast_of(rel):
    import ast

    from tests.sproc_text import REPO_ROOT

    return ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8"))


def _calls_in(tree, attr):
    import ast

    return [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == attr
    ]


def _has_true_kwarg(call, name):
    import ast

    return any(
        kw.arg == name and isinstance(kw.value, ast.Constant) and kw.value.value is True
        for kw in call.keywords
    )


def test_the_bill_cascade_carries_ONE_conditional_decision_not_a_blanket_exemption():
    """Codex round 4, P1 — and the first cut of this test asserted the bug.

    The cascade exempted every child unconditionally, reasoning that the header
    guard had already decided. That is only true for an ADMIN: for everyone else
    the header check passed because the bill was a DRAFT, so a completion
    landing mid-cascade met no resistance at all and a non-admin could remove a
    completed bill's links, lines and header without one refusal.

    Every step must carry the SAME condition — then a mid-cascade completion is
    refused at the first child with nothing destroyed, while an admin's
    deliberate deletion still goes through.
    """
    import ast

    tree = _ast_of("entities/bill/business/service.py")
    target = next(
        n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == "delete_by_public_id"
    )

    def _is_cascade_exempt(kw):
        return isinstance(kw.value, ast.Name) and kw.value.id == "cascade_exempt"

    flags = [
        kw for n in ast.walk(target) if isinstance(n, ast.Call)
        for kw in n.keywords
        if kw.arg in ("_via_internal_pipeline", "allow_terminal_parent", "exempt")
    ]
    # U-446c collapsed the cascade into ONE sproc call, so there are now exactly
    # two flag sites — the early Python refusal and the call that carries the
    # same decision into the transaction. Fewer places to get wrong is the
    # point; what still matters is that neither of them is a hardcoded True.
    assert len(flags) == 2, (
        f"expected the header guard + the cascade call to carry a flag, "
        f"found {len(flags)}"
    )
    blanket = [
        kw.arg for kw in flags
        if isinstance(kw.value, ast.Constant) and kw.value.value is True
    ]
    assert not blanket, (
        f"blanket exemption(s) back in the cascade: {blanket}. Every step must "
        "reuse `cascade_exempt` so a non-admin's delete is refused at the first "
        "child when the bill completes mid-cascade"
    )
    assert all(_is_cascade_exempt(kw) for kw in flags), (
        "every step must reuse the SAME decision variable"
    )


def test_the_contract_labor_rebuild_keeps_the_sproc_guard_on():
    """It reaches the repo DIRECTLY, skipping the service guard — and the sproc
    default is permissive, so omitting the flag silently drops the last line of
    defence. This is the concrete case that proves the default is a bridge and
    not a safety boundary."""
    import ast

    tree = _ast_of("entities/contract_labor/business/bill_service.py")
    # BOTH directions (Codex round 4, P3): the first cut of this test searched
    # only `delete_by_id`, and the file's direct `BillRepository.update_by_id`
    # kept the permissive default unnoticed.
    direct = [
        c for name in ("delete_by_id", "update_by_id")
        for c in _calls_in(tree, name)
        if isinstance(c.func.value, ast.Attribute) and c.func.value.attr == "repo"
    ]
    assert len(direct) >= 2, (
        f"expected direct repo delete AND update calls, found {len(direct)}"
    )
    for call in direct:
        assert any(
            kw.arg == "allow_terminal_parent"
            and isinstance(kw.value, ast.Constant)
            and kw.value.value is False
            for kw in call.keywords
        ), "a direct repo call must opt IN to the guard explicitly"


@pytest.mark.parametrize(
    "sql_rel,proc,must_contain,why",
    [
        ("entities/bill_line_item/sql/dbo.bill_line_item.sql", "DeleteBillLineItemById",
         "AND (@AllowTerminalParent = 1 OR [BillId] = @ParentBillId)",
         "the DELETE must be bound to the parent it locked, or a line moved "
         "between the snapshot read and the write is deleted off a completed Bill"),
        ("entities/bill_line_item_attachment/sql/dbo.bill_line_item_attachment.sql",
         "CreateBillLineItemAttachment", "li.[BillId] = @ParentBillId",
         "the INSERT must be bound to the parent it locked"),
        ("entities/bill_line_item_attachment/sql/dbo.bill_line_item_attachment.sql",
         "DeleteBillLineItemAttachmentById", "li.[BillId] = @ParentBillId",
         "the DELETE must be bound to the parent it locked"),
        ("entities/bill/sql/dbo.bill.sql", "UpdateBillById",
         "RAISERROR('STATUS_LOCKED: a completed Bill cannot be edited.', 16, 1)",
         "a completion winning the header race must yield 422 status_locked, not "
         "the 409 a bare row-version miss produces — iOS routes 409 to "
         "reload-and-retry, so a permanent refusal there loops"),
        ("entities/attachment/sql/dbo.attachment.sql", "UpdateAttachmentById",
         "STATUS_LOCKED: this file is evidence for a completed Bill.",
         "the Python check runs in a separate transaction; only this one is "
         "serialized against completion"),
        ("entities/attachment/sql/dbo.attachment.sql", "DeleteAttachmentById",
         "STATUS_LOCKED: this file is evidence for a completed Bill.",
         "same, for the destructive direction"),
    ],
)
def test_the_write_is_bound_to_what_the_guard_actually_checked(sql_rel, proc, must_contain, why):
    from tests.sproc_text import REPO_ROOT, sproc_body

    body = _executable(sproc_body(REPO_ROOT / sql_rel, proc))
    assert must_contain in body, f"{proc}: {why}"


def test_the_two_parent_locks_are_taken_in_a_total_order():
    """A bare `IN (@a, @b)` guarantees no acquisition order, so A->B and B->A
    moves could take the two U locks in opposing order and deadlock. Ascending
    id is a total order every writer agrees on."""
    from tests.sproc_text import REPO_ROOT, sproc_body

    body = _executable(
        sproc_body(
            REPO_ROOT / "entities/bill_line_item/sql/dbo.bill_line_item.sql",
            "UpdateBillLineItemById",
        )
    )
    assert "WHERE [Id] = @LoBillId" in body and "WHERE [Id] = @HiBillId" in body, (
        "each parent must be locked by its own single-id seek, low id first"
    )
    assert "IN (@LoBillId, @HiBillId)" not in body and "IN (@CurrentBillId" not in body, (
        "a set-based IN (...) seek reintroduces the undefined acquisition order"
    )
    assert body.index("@LoBillId AND [Status]") < body.index("@HiBillId AND [Id] <>"), (
        "low must be locked before high"
    )


@pytest.mark.parametrize(
    "repo_mod,repo_cls,method,args,build",
    [
        ("entities.bill.persistence.repo", "BillRepository", "update_by_id", (), "bill"),
        ("entities.attachment.persistence.repo", "AttachmentRepository", "delete_by_id", (1,), None),
        ("entities.attachment.persistence.repo", "AttachmentRepository", "update_by_id", (), "att"),
    ],
)
def test_these_repos_send_the_flag_and_map_the_sentinel(repo_mod, repo_cls, method, args, build):
    """Two properties at once, because the same call proves both: the param has
    to reach the sproc (its default is permissive, so an omission is silent),
    and the sproc's RAISERROR has to come back as a typed error rather than a
    generic 500."""
    import importlib

    mod = importlib.import_module(repo_mod)
    repo = getattr(mod, repo_cls)()
    if build == "bill":
        args = (SimpleNamespace(
            id=1, row_version_bytes=b"\x00" * 8, vendor_id=7, payment_term_id=None,
            bill_date="2026-09-01", due_date="2026-09-01", bill_number="INV-1",
            total_amount=None, memo=None, is_draft=False,
        ),)
    elif build == "att":
        args = (SimpleNamespace(
            id=9, row_version_bytes=b"\x00" * 8, filename="x.pdf",
            original_filename="x.pdf", file_extension="pdf",
            content_type="application/pdf", file_size=1, file_hash="h",
            blob_url="u", description=None, category=None, tags=None,
            is_archived=False, status=None, expiration_date=None, storage_tier=None,
        ),)

    captured = {}

    def _capture(*, cursor, name, params):
        captured.update(params)
        raise Exception("[42000] STATUS_LOCKED: refused by the in-transaction guard.")

    with patch.object(mod, "call_procedure", _capture), patch.object(mod, "get_connection"):
        with pytest.raises(StatusLockedError):
            getattr(repo, method)(*args, allow_terminal_parent=False)

    assert captured.get("AllowTerminalParent") == 0, (
        f"{repo_cls}.{method} did not send the flag — the sproc defaults "
        "permissive, so this drops the guard with nothing to notice"
    )


@pytest.mark.parametrize(
    "rel,func",
    [
        ("entities/invoice/business/service.py", "delete_by_public_id"),
        ("entities/expense/business/service.py", "delete_by_public_id"),
        ("entities/bill_credit/business/service.py", "delete_by_public_id"),
        ("entities/invoice_line_item/business/service.py", "delete_by_public_id"),
    ],
)
def test_every_cascade_removes_the_row_BEFORE_it_destroys_the_blob(rel, func):
    """An attachment can be a completed Bill's evidence too (BLIA multi-split
    linking), and these cascades used to delete the blob first.

    An earlier cut of this guard asked a PRE-CHECK question before touching
    Azure. That was not enough and this test said so wrongly: the check is a
    COUNT in its own transaction, so a Bill completing between the check and the
    Azure call still destroyed the bytes. Only ordering the guarded row delete
    first makes the refusal authoritative — nothing is destroyed when it fires.
    """
    import ast

    tree = _ast_of(rel)
    target = next(
        (n for n in ast.walk(tree)
         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == func),
        None,
    )
    assert target is not None, f"{rel}:{func} moved"

    destroys = [n.lineno for n in _calls_in(target, "delete_file")]
    if not destroys:
        pytest.skip("this cascade no longer deletes blobs directly")

    removes = [
        n.lineno for n in _calls_in(target, "delete_by_public_id")
        if any(kw.arg == "public_id" for kw in n.keywords)
    ]
    assert removes, f"{rel}:{func} destroys blobs without ever deleting the row"
    assert min(removes) < min(destroys), (
        f"{rel}:{func} deletes the blob before the guarded row delete has "
        "decided — a completed Bill's evidence can be destroyed and the "
        "refusal then arrives too late to matter"
    )

    # ...and the refusal must be caught, or one frozen attachment aborts the
    # whole cascade and leaves the rest of the parent half-deleted.
    handlers = [
        h for n in ast.walk(target) if isinstance(n, ast.Try) for h in n.handlers
        if h.type is not None
        and "StatusLockedError" in ast.dump(h.type)
    ]
    assert handlers, (
        f"{rel}:{func} must catch StatusLockedError and keep going — a frozen "
        "attachment is a skip, not a failure of the whole cascade"
    )


# ===========================================================================
# Round-4 fixes
# ===========================================================================


@pytest.mark.parametrize(
    "sql_rel,proc,must_contain,why",
    [
        ("entities/bill/sql/dbo.bill.sql", "DeleteBillById",
         "RAISERROR('STATUS_LOCKED: a completed Bill cannot be deleted.', 16, 1)",
         "the service decides before a multi-step cascade, in another "
         "transaction; only the locked decision here cannot be raced"),
        ("entities/bill/sql/dbo.bill.sql", "LinkBillSourceEmailMessage",
         "AND [Status] <> 'completed'",
         "POST /create/bill's duplicate path stamps SourceEmailMessageId from a "
         "CLIENT-SUPPLIED id; the Python status check runs in a different "
         "transaction, so a completion in between edited a locked header"),
        ("entities/attachment/sql/dbo.attachment.sql", "UpdateAttachmentById",
         "FROM dbo.[Attachment] WITH (UPDLOCK, HOLDLOCK)",
         "locking only the Bills linked RIGHT NOW leaves a second transaction "
         "free to link this file to a draft Bill and complete it mid-flight — "
         "adding a BLIA row does not touch Attachment.RowVersion"),
        ("entities/attachment/sql/dbo.attachment.sql", "DeleteAttachmentById",
         "FROM dbo.[Attachment] WITH (UPDLOCK, HOLDLOCK)",
         "same, for the destructive direction"),
        ("entities/bill_line_item_attachment/sql/dbo.bill_line_item_attachment.sql",
         "CreateBillLineItemAttachment",
         "FROM dbo.[Attachment] WITH (UPDLOCK, HOLDLOCK)",
         "the link-create must take the SAME attachment lock, or it is not "
         "serialized against the attachment writes it would invalidate"),
    ],
)
def test_round4_sproc_guards(sql_rel, proc, must_contain, why):
    from tests.sproc_text import REPO_ROOT, sproc_body

    body = _executable(sproc_body(REPO_ROOT / sql_rel, proc))
    assert must_contain in body, f"{proc}: {why}"


def test_the_bill_repo_sends_the_delete_flag_and_maps_the_sentinel():
    from entities.bill.persistence.repo import BillRepository
    import entities.bill.persistence.repo as mod

    captured = {}

    def _capture(*, cursor, name, params):
        captured.update(params)
        raise Exception("[42000] STATUS_LOCKED: a completed Bill cannot be deleted.")

    with patch.object(mod, "call_procedure", _capture), patch.object(mod, "get_connection"):
        with pytest.raises(StatusLockedError):
            BillRepository().delete_by_id(1, allow_terminal_parent=False)
    assert captured.get("AllowTerminalParent") == 0


def test_a_lost_reparent_race_surfaces_as_422_not_409_or_404():
    """The in-transaction guards make a wrong write impossible by matching ZERO
    rows — so the loser of the race saw "not found", a row-version 409, or a
    bare repo failure. 409 is the worst: iOS routes it to reload-and-retry, so a
    permanent refusal delivered that way loops forever."""
    from entities.bill_line_item.business.service import BillLineItemService

    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    svc = BillLineItemService(repo=MagicMock())
    svc.read_by_public_id = MagicMock(return_value=_line())
    svc.repo.delete_cascade_by_id.return_value = None  # bound DELETE matched nothing

    with patch("entities.bill.business.service.BillService") as MockBill, \
         patch("entities.bill_line_item.business.service.BillService") as MockBill2, \
         patch("entities.invoice_line_item.persistence.repo.InvoiceLineItemRepository"), \
         patch("entities.contract_labor.persistence.repo.ContractLaborRepository") as MockCl:
        MockCl.return_value.read_by_bill_line_item_id.return_value = []
        # the guard passed on the way in (draft), then the parent completed
        draft, done = _bill(status="in_review", is_draft=True), _bill()
        for m in (MockBill, MockBill2):
            m.return_value.read_by_id.side_effect = [draft, done]
        with pytest.raises(StatusLockedError, match="deleted"):
            svc.delete_by_public_id("bli-1")


def test_a_normal_repo_failure_is_not_relabelled_as_status_locked():
    """The re-assert runs on the failure path, so it must not swallow or rename
    genuine faults — a deadlock victim is not a locked document."""
    from entities.bill_line_item.business.service import BillLineItemService

    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    svc = BillLineItemService(repo=MagicMock())
    svc.read_by_public_id = MagicMock(return_value=_line())
    svc.repo.update_by_id.side_effect = RuntimeError("deadlock victim")

    with patch("entities.bill.business.service.BillService") as MockBill, \
         patch("entities.bill_line_item.business.service.BillService") as MockBill2:
        draft = _bill(status="in_review", is_draft=True)
        for m in (MockBill, MockBill2):
            m.return_value.read_by_id.return_value = draft
            m.return_value.read_by_public_id.return_value = draft
        with pytest.raises(RuntimeError, match="deadlock victim"):
            svc.update_by_public_id("bli-1", row_version="AAAA", amount=5)


# ===========================================================================
# Round-5 fixes
# ===========================================================================


@pytest.mark.parametrize(
    "rel,func",
    [
        ("entities/expense/business/service.py", "delete_by_public_id"),
        ("entities/bill_credit/business/service.py", "delete_by_public_id"),
        ("entities/expense_line_item/business/service.py", "delete_by_public_id"),
    ],
)
def test_the_join_row_goes_before_the_attachment_it_points_at(rel, func):
    """FK_*Attachment_Attachment is NO ACTION, so the Attachment delete fails
    while its own join row still stands — and the failure is swallowed. These
    cascades therefore destroyed the blob and left the Attachment row orphaned
    on every run, which predates this unit; inverting to row-first without
    fixing it turned that into leaking BOTH.

    Order must be: join row, then the guarded Attachment row, then the blob.
    """
    import ast

    tree = _ast_of(rel)
    target = next(
        n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == func
    )
    links = [
        n.lineno for n in _calls_in(target, "delete_by_id")
        if "attachment" in ast.dump(n.func.value).lower()
    ]
    rows = [
        n.lineno for n in _calls_in(target, "delete_by_public_id")
        if any(kw.arg == "public_id" for kw in n.keywords)
    ]
    blobs = [n.lineno for n in _calls_in(target, "delete_file")]
    assert links and rows, f"{rel}:{func} — expected both a link and a row delete"
    assert min(links) < min(rows), (
        f"{rel}:{func} deletes the Attachment while its join row still stands — "
        "the FK refuses and the row is orphaned"
    )
    if blobs:
        assert min(rows) < min(blobs), (
            f"{rel}:{func} destroys the blob before the guarded row delete decides"
        )

    # Source ORDER alone cannot see a step disabled in place — the call stays
    # where it is and simply never runs. A constant `if` test is how that
    # happens, and it has no legitimate use in a cascade, so reject it outright.
    dead = [
        n.lineno for n in ast.walk(target)
        if isinstance(n, ast.If) and isinstance(n.test, ast.Constant)
    ]
    assert not dead, (
        f"{rel}:{func} has constant-test branch(es) at {dead} — a cascade step "
        "disabled in place still reads as correctly ordered"
    )


def test_the_reassert_does_not_relabel_a_transient_fault():
    """Re-asserting on ANY exception meant a deadlock victim or a dropped
    connection came back as `status_locked` whenever the bill happened to
    complete in the meantime — a transient fault reported as a permanent
    refusal, which is the opposite of what the client should act on."""
    import inspect

    from entities.bill_line_item.business.service import BillLineItemService

    src = inspect.getsource(BillLineItemService.update_by_public_id)
    assert "except DatabaseConcurrencyError:" in src, (
        "the reassert must be narrowed to the outcome a lost terminal race "
        "actually produces"
    )
    assert "except Exception:" not in src.split("_reassert_after_a_lost_write")[0][-400:], (
        "a bare `except Exception` around the reassert masks real errors"
    )


def test_the_attachment_link_delete_names_a_lost_race():
    """Its line-item sibling reasserts; this one returned a bare None, so a
    refusal read to the caller as "not found"."""
    import inspect

    from entities.bill_line_item_attachment.business.service import (
        BillLineItemAttachmentService,
    )

    src = inspect.getsource(BillLineItemAttachmentService.delete_by_public_id)
    assert "_assert_parent_editable" in src.split("deleted = self.repo.delete_by_id")[1], (
        "a zero-row delete must be re-checked against the parent"
    )


def test_the_contract_states_what_it_does_not_cover():
    """The lock lets QBO projection write to completed documents by design. That
    is the contract and has to be written down as such — an undocumented
    exemption is indistinguishable from a bypass to the next reader."""
    from shared.lifecycle import terminal_lock

    doc = terminal_lock.__doc__
    assert "DOES AND DOES NOT MEAN" in doc
    assert "QBO PROJECTION AND REPAIR" in doc
    assert "ACCEPTED RESIDUAL #1" in doc and "NOT ATOMIC" in doc, (
        "the non-atomic cascade is a known, booked residual — it must stay "
        "written down rather than quietly assumed fixed"
    )


def test_the_expense_line_item_cascade_orders_its_deletes_for_real():
    """Driven, not read.

    The static order tests above cannot see a guard turned into a dead branch —
    the calls stay where they are in the source while never running. This one
    records what actually happens.
    """
    from entities.expense_line_item.business.service import ExpenseLineItemService

    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    svc = ExpenseLineItemService(repo=MagicMock())
    svc.read_by_public_id = MagicMock(
        return_value=SimpleNamespace(id=3, public_id="eli-3", expense_id=1, row_version="A")
    )
    order = []
    att = SimpleNamespace(id=9, public_id="att-9", blob_url="https://b/x.pdf")

    with patch(
        "entities.expense_line_item_attachment.persistence.repo.ExpenseLineItemAttachmentRepository"
    ) as MockLinkRepo, patch(
        "entities.attachment.business.service.AttachmentService"
    ) as MockAtt, patch("shared.storage.AzureBlobStorage") as MockStorage:
        MockLinkRepo.return_value.read_by_expense_line_item_id.return_value = SimpleNamespace(
            id=5, attachment_id=9
        )
        MockLinkRepo.return_value.delete_by_id.side_effect = lambda **_: order.append("link")
        MockAtt.return_value.read_by_id.return_value = att
        MockAtt.return_value.delete_by_public_id.side_effect = lambda **_: (
            order.append("row"), att
        )[1]
        MockStorage.return_value.delete_file.side_effect = lambda *_: order.append("blob")
        svc.delete_by_public_id("eli-3")

    assert order == ["link", "row", "blob"], (
        "the join row must go first (the FK is NO ACTION), then the guarded "
        f"Attachment row, then the blob. Got: {order}"
    )


def test_the_expense_line_item_cascade_keeps_frozen_evidence():
    """And the refusal must stop the blob, not just the row."""
    from entities.expense_line_item.business.service import ExpenseLineItemService

    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    svc = ExpenseLineItemService(repo=MagicMock())
    svc.read_by_public_id = MagicMock(
        return_value=SimpleNamespace(id=3, public_id="eli-3", expense_id=1, row_version="A")
    )
    with patch(
        "entities.expense_line_item_attachment.persistence.repo.ExpenseLineItemAttachmentRepository"
    ) as MockLinkRepo, patch(
        "entities.attachment.business.service.AttachmentService"
    ) as MockAtt, patch("shared.storage.AzureBlobStorage") as MockStorage:
        MockLinkRepo.return_value.read_by_expense_line_item_id.return_value = SimpleNamespace(
            id=5, attachment_id=9
        )
        MockAtt.return_value.read_by_id.return_value = SimpleNamespace(
            id=9, public_id="att-9", blob_url="https://b/x.pdf"
        )
        MockAtt.return_value.delete_by_public_id.side_effect = StatusLockedError(
            "its attachments cannot be deleted"
        )
        svc.delete_by_public_id("eli-3")

    MockStorage.return_value.delete_file.assert_not_called()


def test_the_attachment_link_delete_raises_on_a_lost_race_for_real():
    """The source-shape test could not see the guard turned into a dead branch."""
    import functools

    from entities.bill_line_item.business.service import BillLineItemService as RealBLI
    from entities.bill_line_item_attachment.business.service import (
        BillLineItemAttachmentService,
    )

    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    svc = BillLineItemAttachmentService(repo=MagicMock())
    svc.repo.read_by_public_id.return_value = SimpleNamespace(id=5, bill_line_item_id=11)
    svc.read_by_public_id = MagicMock(return_value=SimpleNamespace(id=5))
    svc.repo.delete_by_id.return_value = None  # the bound DELETE matched nothing

    with patch("entities.bill_line_item.business.service.BillLineItemService") as MockBli, \
         patch("entities.bill_line_item_attachment.business.service.BillLineItemService") as MockBli2, \
         patch("entities.bill.business.service.BillService") as MockBill:
        for m in (MockBli, MockBli2):
            m.return_value.read_by_id.return_value = _line()
            # the REAL guard on the mock (captured BEFORE the patch), so this
            # exercises the actual check rather than a mock of it
            m.return_value._assert_parent_editable = functools.partial(
                RealBLI._assert_parent_editable, m.return_value
            )
        # DRAFT on the way in, COMPLETED by the time the write lands — the only
        # sequence that isolates the post-delete reassert from the upfront
        # guard. With both reading `completed` the upfront guard raises and this
        # test passes no matter what the reassert does.
        MockBill.return_value.read_by_id.side_effect = [
            _bill(status="in_review", is_draft=True),
            _bill(),
        ]
        with pytest.raises(StatusLockedError, match="attachments"):
            svc.delete_by_public_id(public_id="blia-5")
