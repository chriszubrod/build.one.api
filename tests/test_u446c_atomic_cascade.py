"""U-446c — deleting is ONE transaction, and the lock's default fails closed.

Three residuals U-446b documented rather than fixed.

1. THE CASCADES WERE N TRANSACTIONS. `BillService.delete_by_public_id` ran
   attachment links, then each line item (with its own dependent cleanup), then
   Review rows, then MsMessageBill links, then the header — each committing on
   its own. U-446b made every step refuse a completed parent and gave them all
   the same decision, so a completion landing mid-cascade WAS refused — but at
   whichever step it reached, leaving the earlier ones committed. That is a
   partially deleted document, and it applied to ordinary draft deletes racing a
   completion, not just the admin escape hatch.

   It is fixable at all because the cascade makes no external calls: Attachment
   rows and their Azure blobs are deliberately left alone, so there is nothing
   that has to live outside a transaction.

2. `@AllowTerminalParent` DEFAULTED PERMISSIVE. U-446b shipped `= 1` because it
   was the only default that made the SQL safe to apply either side of its own
   deploy. That window is closed, so the trapdoor goes: an omission now refuses
   instead of silently skipping the guard.

3. THE ATTACHMENT SPROCS LOCKED BILLS UNORDERED. Two writers sharing Bills could
   take the U locks in opposing order and deadlock.

A LIVE 547, verified against prod and fixed here on the way: the standalone line
delete never cleared its BillLineItemAttachment link, whose FK is NO ACTION —
so `DELETE /delete/bill_line_item/{id}` on any line with an attachment failed
outright. Only the bill-level cascade cleared it first, which is why it only bit
that one path.

CORRECTED FROM THIS UNIT'S OWN GATE 1: it also claimed ReviewEntry was never
cleared and that 2 live bills would 547. That was wrong — `DeleteReviewsByBillId`
deletes ReviewEntry behind its own OBJECT_ID guard, which the Python cascade
called. The new sproc deletes both to PRESERVE that, not to fix anything.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from shared.authz import clear_authz_context, set_authz_context
from shared.lifecycle.terminal_lock import StatusLockedError


@pytest.fixture(autouse=True)
def _clean_authz():
    clear_authz_context()
    yield
    clear_authz_context()


def _executable(sql: str) -> str:
    """SQL with comments stripped — prose in a comment has satisfied an
    assertion in this family five times now."""
    return "\n".join(line.split("--")[0] for line in sql.splitlines())


def _body(rel, proc):
    from tests.sproc_text import REPO_ROOT, sproc_body

    return _executable(sproc_body(REPO_ROOT / rel, proc))


BILL_SQL = "entities/bill/sql/dbo.bill.sql"
BLI_SQL = "entities/bill_line_item/sql/dbo.bill_line_item.sql"
ATT_SQL = "entities/attachment/sql/dbo.attachment.sql"


# ---------------------------------------------------------------------------
# 1. One transaction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rel,proc", [(BILL_SQL, "DeleteBillCascadeById"), (BLI_SQL, "DeleteBillLineItemCascadeById")]
)
def test_the_cascade_is_a_single_transaction(rel, proc):
    body = _body(rel, proc)
    assert body.count("BEGIN TRANSACTION") == 1, f"{proc} must open exactly one transaction"
    assert "ROLLBACK" not in body.upper(), (
        "never ROLLBACK inside a sproc — pyodbc runs autocommit-off and the "
        "in-proc rollback zeroes the outer transaction (SQL error 266)"
    )
    assert "SET NOCOUNT ON" in body


@pytest.mark.parametrize(
    "rel,proc", [(BILL_SQL, "DeleteBillCascadeById"), (BLI_SQL, "DeleteBillLineItemCascadeById")]
)
def test_the_parent_is_locked_before_any_child_is_touched(rel, proc):
    """The lock is the whole point: it is what serializes the cascade against
    FinalizeBillById, so a completion either loses the race outright or is
    refused with nothing destroyed."""
    body = _body(rel, proc)
    lock_at = body.index("WITH (UPDLOCK, HOLDLOCK)")
    first_write = min(
        i for i in (body.find("DELETE FROM dbo.[BillLineItemAttachment]"),
                    body.find("DELETE FROM dbo.[InvoiceLineItem]"),
                    body.find("UPDATE dbo.[ContractLabor]"))
        if i != -1
    )
    assert lock_at < first_write, f"{proc} writes before it locks the parent"


@pytest.mark.parametrize(
    "rel,proc,refusal",
    [
        (BILL_SQL, "DeleteBillCascadeById", "a completed Bill cannot be deleted"),
        (BLI_SQL, "DeleteBillLineItemCascadeById", "line items of a completed Bill cannot be deleted"),
    ],
)
def test_the_cascade_refuses_a_completed_parent_and_commits_before_raising(rel, proc, refusal):
    body = _body(rel, proc)
    assert "@AllowTerminalParent = 0" in body
    assert refusal in body
    assert body.index("COMMIT TRANSACTION") < body.index("RAISERROR")


def test_the_line_cascades_child_cleanup_is_bound_to_the_locked_parent():
    """Codex P0, and the sharpest failure in this unit.

    @ParentBillId comes from a SNAPSHOT read, so the line can be MOVED between
    that read and the lock. Only the final DELETE was bound to the locked
    parent, so the child cleanup — links, invoice rows, provenance, the
    ContractLabor FK — would have destroyed rows belonging to a line that now
    lives on a DIFFERENT bill, possibly a completed one, while the delete itself
    matched nothing and the caller saw a bare "not found".

    One re-check under the lock is sufficient: once this transaction holds
    @ParentBillId's lock AND the line is confirmed on it, the line cannot move
    again, because any mover must take that same lock.
    """
    body = _body(BLI_SQL, "DeleteBillLineItemCascadeById")
    assert "@StillOnLockedParent" in body, "the cleanup is not re-checked under the lock"
    assert "DECLARE @StillOnLockedParent BIT = 0;" in body, (
        "the flag must start FALSE — initialised to 1 it stays true when the "
        "re-check matches nothing, which is precisely the moved-line case it "
        "exists to catch"
    )
    recheck = body.index("SELECT @StillOnLockedParent = 1")
    guard = body.index("IF @StillOnLockedParent = 1")
    assert body.index("WITH (UPDLOCK, HOLDLOCK)") < recheck, (
        "the re-check must happen AFTER the lock, or it reads the same stale "
        "snapshot it is meant to defend against"
    )
    for destructive in ("DELETE FROM dbo.[BillLineItemAttachment]",
                        "DELETE FROM dbo.[InvoiceLineItem]",
                        "UPDATE dbo.[ContractLabor]",
                        "DELETE FROM qbo.[BillLineItemBillLine]"):
        assert body.index(destructive) > guard, (
            f"{destructive} runs outside the @StillOnLockedParent guard"
        )


def test_the_attachment_walk_validates_the_set_it_locked():
    """Codex P0 — and this unit's own regression.

    The join that was replaced gave no acquisition ORDER (deadlock) but DID give
    set STABILITY: one statement locks every row it scans. A plain ascending
    walk buys the order and loses the stability — move a line from Bill 30 to
    Bill 5 after 10 is locked, complete Bill 5, and `> @PrevBillId` skips it
    entirely.

    So the walk must be followed by a check that every currently-linked Bill is
    one we hold, and repeat if not.
    """
    for proc in ("UpdateAttachmentById", "DeleteAttachmentById"):
        body = _body(ATT_SQL, proc)
        assert "@LockedBills" in body, f"{proc} does not track what it locked"
        assert "EXCEPT" in body, (
            f"{proc} never validates that the linked set is a subset of the "
            "locked set — an ascending walk alone can skip a Bill"
        )
        assert "@Passes" in body, f"{proc} has no re-walk when the set moved"
        # the completed check must read the LOCKED set, not a fresh query...
        assert "INNER JOIN @LockedBills" in body, (
            f"{proc} must decide from the Bills it actually holds"
        )
        # ...INTERSECTED with the Bills still linked (Codex P1). @LockedBills is
        # a coverage SUPERSET — validation proves `linked ⊆ locked`, not
        # equality — so a Bill whose last link was removed while we walked stays
        # in the set. Deciding on the raw set refuses 422 `status_locked` for a
        # Bill this file is no longer evidence for: a permanent answer to a
        # transient state, which a retry would contradict.
        assert "blia.[AttachmentId] = @Id" in body.split("@LockedCompleted", 1)[1], (
            f"{proc} decides over Bills it merely LOCKED rather than Bills the "
            "attachment is still linked to"
        )


def test_the_review_entry_delete_keeps_its_compatibility_guard():
    """Codex P2. ReviewEntry is decommissioned; `DeleteReviewsByBillId` — the
    sproc this cascade replaced — deletes it only when the table still exists.
    Dropping that guard turns the cascade from a no-op into a hard runtime
    failure the day the table is dropped."""
    body = _body(BILL_SQL, "DeleteBillCascadeById")
    assert "OBJECT_ID('dbo.ReviewEntry', 'U') IS NOT NULL" in body, (
        "the decommissioned-table guard was lost"
    )
    assert body.index("OBJECT_ID('dbo.ReviewEntry'") < body.index("DELETE FROM dbo.[ReviewEntry]")


def test_the_bill_cascade_clears_every_fk_that_points_at_it():
    """Verified against sys.foreign_keys, not assumed. ContractLaborLineItem is
    SET_NULL and MsMessageBill is CASCADE, so neither needs a step — and the
    MsMessageBill step the Python cascade ran was redundant twice over."""
    body = _body(BILL_SQL, "DeleteBillCascadeById")
    for table in ("dbo.[BillLineItemAttachment]", "dbo.[InvoiceLineItem]",
                  "dbo.[BillLineItem]", "dbo.[ReviewEntry]", "dbo.[Review]", "dbo.[Bill]"):
        assert f"DELETE FROM {table}" in body, f"{table} is never cleared"
    assert "UPDATE dbo.[ContractLabor]" in body, "ContractLabor.BillLineItemId must be NULLed"
    assert "MsMessageBill" not in body, (
        "that FK is CASCADE — an explicit delete is redundant and misleading"
    )
    # innermost FK first, or the delete trips a REFERENCE constraint
    assert body.index("DELETE FROM dbo.[BillLineItemAttachment]") < body.index("DELETE FROM dbo.[BillLineItem]")
    assert body.index("DELETE FROM dbo.[InvoiceLineItem]") < body.index("DELETE FROM dbo.[BillLineItem]")
    assert body.index("DELETE FROM dbo.[BillLineItem]") < body.index("DELETE FROM dbo.[Bill]")


def test_the_line_cascade_clears_the_attachment_link_that_used_to_547():
    """THE live bug. `FK_BillLineItemAttachment_BillLineItem` is NO ACTION and
    the standalone path never cleared the link, so deleting any line with an
    attachment failed outright — confirmed against prod, not inferred."""
    body = _body(BLI_SQL, "DeleteBillLineItemCascadeById")
    assert "DELETE FROM dbo.[BillLineItemAttachment] WHERE [BillLineItemId] = @Id;" in body
    assert body.index("dbo.[BillLineItemAttachment]") < body.index("DELETE FROM dbo.[BillLineItem]")


@pytest.mark.parametrize(
    "rel,proc", [(BILL_SQL, "DeleteBillCascadeById"), (BLI_SQL, "DeleteBillLineItemCascadeById")]
)
def test_the_legacy_qbo_bridge_moved_into_the_sproc_still_guarded(rel, proc):
    """qbo.BillLineItemBillLine is already dropped in prod, but its FK was NO
    ACTION — so wherever it still exists the delete would 547 without this. The
    OBJECT_ID guard is what makes the dropped case a plain no-op."""
    body = _body(rel, proc)
    assert "OBJECT_ID('qbo.BillLineItemBillLine')" in body
    assert body.index("OBJECT_ID('qbo.BillLineItemBillLine')") < body.index("DELETE FROM dbo.[BillLineItem]")


@pytest.mark.parametrize(
    "rel,proc,scope",
    [
        (BILL_SQL, "DeleteBillCascadeById",
         "IN (SELECT [Id] FROM dbo.[BillLineItem] WHERE [BillId] = @Id)"),
        (BLI_SQL, "DeleteBillLineItemCascadeById", "= @Id"),
    ],
)
def test_the_invoice_lines_own_children_are_cleared_first(rel, proc, scope):
    """Found by rehearsing against real data, not by any test here.

    Replacing `InvoiceLineItemRepository.delete_by_bill_line_item_id` with a raw
    DELETE silently dropped what the sproc behind it did:
    InvoiceLineItem has two NO ACTION children of its own, and
    InvoiceLineItemSourceProvenance has ~30k live rows. The first rehearsal
    cascade hit 547 on FK_InvoiceLineItemSourceProvenance_InvoiceLineItem.

    This is the removed-behaviour trap in its purest form — a repo call looks
    like one statement and is three.
    """
    body = _body(rel, proc)
    for child in ("dbo.[InvoiceLineItemAttachment]", "dbo.[InvoiceLineItemSourceProvenance]"):
        assert child in body, f"{proc} never clears {child} — the delete will 547"
        assert body.index(child) < body.index("DELETE FROM dbo.[InvoiceLineItem]"), (
            f"{proc} must clear {child} BEFORE the invoice lines it points at"
        )
    assert scope in body


def test_the_closure_of_tables_the_bill_cascade_clears_is_complete():
    """Pins the transitive FK closure computed from sys.foreign_keys, so a new
    NO ACTION child added to any of these tables shows up as a failing test
    rather than a 547 in production.

    NOT in the list, deliberately: MsMessageBill (CASCADE),
    ContractLaborLineItem (SET_NULL), and ContractLabor's own children — the
    cascade NULLs ContractLabor.BillLineItemId rather than deleting the row, so
    nothing below ContractLabor is reachable from here.
    """
    body = _body(BILL_SQL, "DeleteBillCascadeById")
    must_clear = [
        "dbo.[BillLineItemAttachment]",
        "dbo.[InvoiceLineItemAttachment]",
        "dbo.[InvoiceLineItemSourceProvenance]",
        "dbo.[InvoiceLineItem]",
        "dbo.[BillLineItem]",
        "dbo.[ReviewEntry]",
        "dbo.[Review]",
        "dbo.[Bill]",
    ]
    for t in must_clear:
        assert t in body, f"{t} is in the FK closure but never cleared"
    assert "UPDATE dbo.[ContractLabor]" in body


def test_attachment_rows_and_blobs_survive_the_cascade():
    """Only the LINK goes. This is what keeps the cascade free of external calls
    — and so able to be one transaction at all."""
    for rel, proc in ((BILL_SQL, "DeleteBillCascadeById"), (BLI_SQL, "DeleteBillLineItemCascadeById")):
        body = _body(rel, proc)
        assert "DELETE FROM dbo.[Attachment]" not in body, (
            f"{proc} must not delete Attachment rows — the blob would be orphaned "
            "and the cascade would need an external call to avoid it"
        )


# ---------------------------------------------------------------------------
# ...and the Python side
# ---------------------------------------------------------------------------


def test_the_bill_service_makes_ONE_repo_call():
    """~90 lines across N transactions collapsed to one. A second repo call here
    would be a step back outside the transaction."""
    from entities.bill.business.service import BillService

    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    svc = BillService(repo=MagicMock())
    bill = SimpleNamespace(id=55, public_id="bill-55", status="in_review", is_draft=True)
    svc.read_by_public_id = MagicMock(return_value=bill)
    svc.repo.delete_cascade_by_id.return_value = bill

    assert svc.delete_by_public_id(public_id="bill-55") is bill
    svc.repo.delete_cascade_by_id.assert_called_once_with(55, allow_terminal_parent=False)
    svc.repo.delete_by_id.assert_not_called()


def test_the_admin_decision_reaches_the_transaction():
    """§4.1 keeps the completed-bill delete for admins deliberately; the sproc
    only honours it if the flag actually arrives."""
    from shared.authz import system_authz
    from entities.bill.business.service import BillService

    svc = BillService(repo=MagicMock())
    bill = SimpleNamespace(id=55, public_id="bill-55", status="completed", is_draft=False)
    svc.read_by_public_id = MagicMock(return_value=bill)
    svc.repo.delete_cascade_by_id.return_value = bill

    with system_authz():
        svc.delete_by_public_id(public_id="bill-55")
    svc.repo.delete_cascade_by_id.assert_called_once_with(55, allow_terminal_parent=True)


def test_a_non_admin_is_still_refused_before_the_cascade_runs():
    from entities.bill.business.service import BillService

    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    svc = BillService(repo=MagicMock())
    svc.read_by_public_id = MagicMock(
        return_value=SimpleNamespace(id=55, public_id="bill-55", status="completed", is_draft=False)
    )
    with pytest.raises(StatusLockedError):
        svc.delete_by_public_id(public_id="bill-55")
    svc.repo.delete_cascade_by_id.assert_not_called()


def test_the_line_service_no_longer_commits_cleanup_of_its_own():
    """The invoice-line delete, the ContractLabor FK clear and the legacy
    mapping clear each used to commit BEFORE the guarded line delete — so a
    completion in that gap produced `status_locked` on a line whose dependents
    were already gone."""
    import inspect

    from entities.bill_line_item.business.service import BillLineItemService

    src = inspect.getsource(BillLineItemService.delete_by_public_id)
    for gone in ("InvoiceLineItemRepository", "ContractLaborRepository",
                 "_clear_legacy_bill_line_item_bill_line_mapping", "get_connection"):
        assert gone not in src, f"{gone} still runs in its own transaction here"
    assert "delete_cascade_by_id" in src


@pytest.mark.parametrize(
    "repo_mod,repo_cls,method",
    [
        ("entities.bill.persistence.repo", "BillRepository", "delete_cascade_by_id"),
        ("entities.bill_line_item.persistence.repo", "BillLineItemRepository", "delete_cascade_by_id"),
    ],
)
def test_the_cascade_repos_send_the_flag_and_map_the_sentinel(repo_mod, repo_cls, method):
    import importlib

    mod = importlib.import_module(repo_mod)
    captured = {}

    def _capture(*, cursor, name, params):
        captured.update(params)
        raise Exception("[42000] STATUS_LOCKED: refused by the in-transaction guard.")

    with patch.object(mod, "call_procedure", _capture), patch.object(mod, "get_connection"):
        with pytest.raises(StatusLockedError):
            getattr(getattr(mod, repo_cls)(), method)(1, allow_terminal_parent=False)

    assert captured.get("AllowTerminalParent") == 0


# ---------------------------------------------------------------------------
# 3. Ordered Bill locks in the attachment sprocs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("proc", ["UpdateAttachmentById", "DeleteAttachmentById"])
def test_the_attachment_sprocs_lock_bills_in_ascending_order(proc):
    """A join locked every linked Bill at once and guaranteed no acquisition
    order, so two attachment writers sharing Bills could deadlock. An ascending
    walk is a total order every writer agrees on."""
    body = _body(ATT_SQL, proc)
    assert "ORDER BY li.[BillId]" in body, "the walk must be ordered"
    assert "li.[BillId] > @PrevBillId" in body, "and must advance strictly upward"
    assert "INNER JOIN dbo.[Bill] b WITH (UPDLOCK, HOLDLOCK)" not in body, (
        "the unordered set-lock join is what this replaced"
    )
    # the Attachment row is still the common first lock every writer takes
    assert body.index("FROM dbo.[Attachment] WITH (UPDLOCK, HOLDLOCK)") < body.index("ORDER BY li.[BillId]")
    # ...and ordering alone is not the whole contract — see the set-validation
    # test above, which is the half that ordering cost us.


# ---------------------------------------------------------------------------
# 2. The fail-closed default — across EVERY sproc that carries the param
# ---------------------------------------------------------------------------


def test_no_guarded_sproc_anywhere_still_defaults_permissive():
    """DERIVED from the SQL, not a hand-kept list.

    The first version of this check lived in U-446b's file against five named
    sprocs, and silently covered neither the Bill nor the Attachment pair — a
    mutation flipping those back to `= 1` stayed green. Scanning for the param
    means a sproc that GAINS the guard later is covered the day it does.
    """
    import re

    from tests.sproc_text import REPO_ROOT

    permissive, guarded = [], []
    for path in sorted((REPO_ROOT / "entities").rglob("dbo.*.sql")):
        raw = path.read_text(encoding="utf-8")
        # Strip `--` comments before scanning (U-454). These read RAW text, so
        # a sproc whose COMMENT quotes the permissive form -- while explaining
        # why it must never be used -- was reported as a permissive default.
        # Prose cannot skip a guard; only executable SQL can. Splitting per
        # line preserves the line structure, so reported line numbers stay
        # correct.
        text = "\n".join(line.split("--")[0] for line in raw.splitlines())
        if "@AllowTerminalParent" not in text:
            continue
        for m in re.finditer(r"@AllowTerminalParent\s+BIT\s*=\s*([01])", text):
            (permissive if m.group(1) == "1" else guarded).append(
                f"{path.name}:{text[:m.start()].count(chr(10)) + 1}"
            )

    assert guarded, "no guarded sproc found at all — the scan is broken"
    assert not permissive, (
        "a permissive default is a silent trapdoor: the sproc skips its guard "
        f"and nothing errors. Found at {permissive}"
    )
    # sanity: the count should cover every sproc the lock guards
    assert len(guarded) >= 11, (
        f"expected at least 11 guarded sprocs, found {len(guarded)} — did one "
        "lose its param entirely?"
    )


@pytest.mark.parametrize(
    "rel,proc", [(BILL_SQL, "DeleteBillCascadeById"), (BLI_SQL, "DeleteBillLineItemCascadeById")]
)
def test_a_missing_row_yields_an_EMPTY_result_set_not_no_result_set(rel, proc):
    """The pyodbc trap, caught by driving the sproc rather than reading it.

    Both cascades first had an `IF <missing> BEGIN COMMIT; RETURN; END` guard.
    That returns NO result set, and `cursor.fetchone()` raises "No results.
    Previous SQL was not a query" on it — so deleting a row that vanished
    concurrently surfaced as a confusing error instead of None. It is reachable:
    the service reads first, but the row can go in between.

    The fix removed the special case rather than patching it. With no early
    return every statement is a natural no-op when nothing matches, and the
    final DELETE's OUTPUT clause yields an empty result set on its own.

    Only the RETURN on the LOCKED path may stand — that one is preceded by a
    RAISERROR, which pyodbc surfaces as an exception either way.
    """
    body = _body(rel, proc)
    returns = body.count("RETURN;")
    raisers = body.count("RAISERROR")
    assert returns == raisers, (
        f"{proc} has {returns} RETURN(s) but {raisers} RAISERROR(s) — a RETURN "
        "that is not a refusal leaves pyodbc with no result set to fetch"
    )
    assert "IF @Status IS NULL" not in body and "IF @ParentBillId IS NULL" not in body, (
        "the missing-row early return is what produced no result set at all"
    )


def test_every_guard_LOCKS_unconditionally_and_only_REFUSES_conditionally():
    """The deepest error in this unit, and the one worth a standing test.

    `@AllowTerminalParent` says whether a caller may WRITE to a completed
    parent. It must never decide whether to take the LOCK. When the lock sat
    inside `IF @AllowTerminalParent = 0 BEGIN ... END`, an exempt writer took no
    lock at all — so it was invisible to every other transaction, and the
    serialization every one of these guards rests on quietly disappeared for
    exactly the callers that mutate most.

    That made the cascades' re-check unsound: an exempt mover could reparent a
    line out from under a cascade that was holding its parent, and the cascade
    would then destroy the children of a line living on a completed bill.

    Shape: `IF @AllowTerminalParent = 0` may only ever appear as part of a
    COMPOUND condition on a value already computed under the lock — never as a
    block opener wrapping the lock itself.
    """
    from tests.sproc_text import REPO_ROOT

    offenders = []
    for path in sorted((REPO_ROOT / "entities").rglob("dbo.*.sql")):
        raw = path.read_text(encoding="utf-8")
        # Strip `--` comments before scanning (U-454). These read RAW text, so
        # a sproc whose COMMENT quotes the permissive form -- while explaining
        # why it must never be used -- was reported as a permissive default.
        # Prose cannot skip a guard; only executable SQL can. Splitting per
        # line preserves the line structure, so reported line numbers stay
        # correct.
        text = "\n".join(line.split("--")[0] for line in raw.splitlines())
        if "@AllowTerminalParent" not in text:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            code = line.split("--")[0]
            if "IF @AllowTerminalParent = 0" in code and " AND " not in code:
                offenders.append(f"{path.name}:{n}")

    assert not offenders, (
        "`IF @AllowTerminalParent = 0` used as a block opener at "
        f"{offenders} — that wraps the lock in the exemption, so an exempt "
        "writer takes no lock and serializes against nothing"
    )


@pytest.mark.parametrize(
    "rel,proc",
    [
        (BLI_SQL, "CreateBillLineItem"),
        (BLI_SQL, "UpdateBillLineItemById"),
        (BLI_SQL, "DeleteBillLineItemById"),
        (BILL_SQL, "UpdateBillById"),
        (BILL_SQL, "DeleteBillById"),
        (ATT_SQL, "UpdateAttachmentById"),
        (ATT_SQL, "DeleteAttachmentById"),
    ],
)
def test_the_lock_precedes_the_exemption_test_in_every_guarded_sproc(rel, proc):
    """Per-sproc form of the rule above: the UPDLOCK has to be acquired before
    anything consults the exemption."""
    body = _body(rel, proc)
    lock_at = body.index("WITH (UPDLOCK, HOLDLOCK)")
    exempt_at = body.index("@AllowTerminalParent = 0")
    assert lock_at < exempt_at, (
        f"{proc} consults the exemption before taking its lock — an exempt "
        "caller would skip the lock entirely"
    )


def test_the_status_transition_sproc_cannot_reopen_a_completed_bill():
    """Codex P1. `TransitionBillStatus` takes its allowed source states from the
    CALLER, so `@FromStatuses = 'completed'` would move a finalised Bill back
    out of the terminal state — undoing a status whose AP already reached QBO,
    SharePoint, Excel and Box. No repository calls it that way; the fence makes
    it uncallable that way at all."""
    body = _body(BILL_SQL, "TransitionBillStatus")
    assert "AND [Status] <> 'completed'" in body, (
        "the terminal state must be fenced in the UPDATE predicate, not left to "
        "whatever @FromStatuses the caller supplies"
    )


def test_the_project_backfill_joins_the_lock_protocol():
    """Codex P0. It writes dbo.BillLineItem, so it belongs to the protocol —
    lock unconditionally, refuse conditionally — even though `ProjectId IS NULL`
    already bounded it to a one-way fill that could never CHANGE an allocation.
    The guard is what stops a future non-system caller acquiring that reach."""
    body = _body("entities/invoice/sql/dbo.invoice.sql", "BackfillLinkedSourceProjectId")
    assert "WITH (UPDLOCK, HOLDLOCK)" in body
    assert "STATUS_LOCKED" in body
    assert body.index("WITH (UPDLOCK, HOLDLOCK)") < body.index("@AllowTerminalParent = 0"), (
        "the lock must precede the exemption test"
    )
    assert "[ProjectId] IS NULL" in body, (
        "the one-way-fill predicate is what bounds this to repair; do not drop it"
    )


def test_the_contract_NAMES_its_operational_metadata_exemptions():
    """An exemption nobody wrote down is indistinguishable from a hole.

    Each of these was independently re-discovered as a "bypass" by review,
    because the contract described the QBO and completion exemptions but left
    the metadata writers implicit.
    """
    from shared.lifecycle import terminal_lock

    doc = terminal_lock.__doc__
    assert "OPERATIONAL METADATA" in doc
    for named in ("QboIdentity", "UpdateAttachmentExtraction",
                  "increment_download_count"):
        assert named in doc, f"{named} is exempt in practice but unnamed in the contract"
