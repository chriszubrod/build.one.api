"""U-446b — the terminal lock: a `completed` document refuses further edits.

A bill reaches `completed` only by finalizing, and finalizing enqueues its AP to
QBO, SharePoint, Excel and Box. Editing the header afterwards changes what our
books say without changing what any of those four systems already received — so
the local record silently stops matching the money that actually moved. Until
this unit, nothing stopped it: vendor, dates, bill number, total and memo were
all freely editable on a completed bill, as were its line items and attachments.

CONTRACT: HTTP 422 with `error_code: "status_locked"`. Deliberately NOT 409.
Installed iOS routes 409 to its per-service CONFLICT path — built for optimistic
-concurrency collisions, so it reloads and retries — while `isRetriableSyncFailure`
classifies other 4xx as TERMINAL: surface it and stop
(`BuildOne/Services/BuildOneAPI/APIError.swift`). A permanent lock answered with
409 would make queued edits loop or be discarded through the wrong path.

EXEMPTIONS, and why each exists:

  * SYSTEM CALLERS. Outbox workers (`system_authz()`), CLI sync scripts
    (`assert_cli_system_admin()`) and the HTTP drain (`_require_drain_secret`)
    each assert `is_system_context=True`. `set_authz_context` defaults it to
    False, so the auth dependency clears it on every authenticated request and
    no ordinary Bill-editing route can arrive here carrying it.

    Precisely: the marker is not reachable from any route a human uses to EDIT
    a Bill. It is NOT "unreachable from HTTP" — every `POST /sync/qbo-*` route
    deliberately enters `system_authz()` while authenticated as a human holding
    QBO_SYNC.can_create, because a realm-wide pull has to read across every
    user's rows, and `/sync/qbo-bills` does reach Bill mutations.

    That is the intended contract, not a hole: see "WHAT REFUSES FURTHER EDITS
    DOES AND DOES NOT MEAN" above. A QBO pull brings the local record back into
    agreement with the system of record for money that has already moved. What
    the marker rules out is a person editing a completed document on their own
    authority — and the only way to reach it is a QBO_SYNC-gated realm pull,
    which writes what QuickBooks says and nothing a caller chooses.
    (Codex rounds 2 and 5 — earlier wording here claimed more than the code
    delivers, then described a connector set that has since grown.)

    It is NOT the pair `is_system_admin=True AND user_id is None`, which this
    unit first used and which is FORGEABLE (Codex P1 #6). A valid, unexpired
    admin JWT whose `uid` no longer resolves to a User row fell straight through
    `_enrich_payload_with_authz`'s `if user:` to
    `set_authz_context(user_id=None, is_system_admin=True)` — byte-identical to
    a worker, so a stale-but-signed human session could edit a completed bill.
    (That path now also fails closed: no resolved user, no system-admin. Belt
    and braces, because the collision was never the auth layer's to prevent.)

    Exempting on `is_system_admin` alone would be worse still — Chris (17) and
    the Claude Agent (33) are real users who are ALSO system admins, so that
    hands the two most active accounts a blanket bypass.

  * EXPLICIT INTERNAL PIPELINES. Invoice completion flips `is_billed` on the
    line items of completed bills, and it runs as a REAL USER — the signature
    above does not cover it. Those call sites pass the exemption explicitly,
    mirroring `BillService.update_by_public_id`'s existing
    `_via_completion_pipeline` precedent. An explicit kwarg is the point: it is
    greppable, and it cannot be acquired by accident.

WHAT "REFUSES FURTHER EDITS" DOES AND DOES NOT MEAN (Codex round 5, P1).
The lock governs edits made BY PEOPLE. It deliberately does not govern:

  * QBO PROJECTION AND REPAIR. A pull writes what QuickBooks already holds, and
    QBO is the system of record for AP that has shipped there — refusing it
    would leave our books permanently disagreeing with the money. So the QBO
    connectors update completed headers and lines, attach and re-attach
    evidence, and heal missing blobs, by design. Same for the attachable
    blob-heal: a completed Bill whose PDF went missing is exactly the document
    that most needs it back.
  * COMPLETION ITSELF, and invoice completion's `is_billed` flips.
  * OPERATIONAL METADATA that says nothing about what the document IS, written
    by background workers that must keep running over finalised documents:
    `Set{Bill,BillLineItem,Attachment}QboIdentity` (the QBO identity stamp),
    `UpdateAttachmentExtraction`, `Update/ConfirmAttachmentCategorization`,
    and `increment_download_count`. These are NAMED here rather than left
    unguarded-by-omission (Codex, U-446c): an exemption nobody wrote down is
    indistinguishable from a hole to the next reader, and each of these was
    independently re-discovered as a "bypass" precisely because the list did
    not exist.

    The line is what the row MEANS versus how it is indexed: blob_url,
    filename, content type, category, archive state, existence and money are
    locked; a download counter, an extraction status and an external id are not.

Those are the exemptions listed below, and they are the contract, not holes in
it. The line is: a system or internal-pipeline caller may bring the local record
back into agreement with an external system of record; nobody may change what
the document says on their own authority once it is completed.

ACCEPTED RESIDUAL #1 — THE LINE-ITEM CASCADES ARE NOT ATOMIC (Codex rounds 4-5,
P1; header half CLOSED, line-item half STILL OPEN).

CLOSED at the header level: `DeleteBillCascadeById` (U-446c) and
`DeleteExpenseCascadeById` (U-468) each run the whole cascade in ONE transaction
holding the parent under `UPDLOCK, HOLDLOCK` throughout, with attachment rows and
blobs deliberately left alone so the transaction makes no external calls. A
completion landing mid-cascade waits, then is refused outright.

STILL NOT ATOMIC at the line-item level: `ExpenseLineItemService.delete_by_public_id`
is still several transactions (link -> attachment row -> Azure blob -> legacy
mapping -> line). Every step is guarded and carries the same decision, so a
completion landing mid-cascade IS refused — but at whichever step it reached, with
the earlier steps already committed. The outcome is a partially deleted line
rather than a wrongly deleted one. Bill closed this with
`DeleteBillLineItemCascadeById`; Expense has no equivalent yet, and U-468
deliberately did not build one. Booked as its own unit.

CLOSED (was ACCEPTED RESIDUAL #2 — unordered attachment locks).
The attachment sprocs now walk their parents in a defined low->high order, so two
attachment writers sharing parents no longer deadlock. U-468 extended the walk to
a second parent kind (Expense) alongside Bill; the ordering holds across both.


path does impose low→high ordering; extending it here needs a different shape
than a join, and is booked with the unit above.

ACCEPTED RESIDUAL #3 — the check-then-write window (Codex P1 #5). The parent's
status was read in one transaction and the child row written in another — CLOSED
in this unit rather than deferred. Every mutation sproc now re-checks the parent
under `UPDLOCK, HOLDLOCK` inside the writing transaction and binds its DML to the
parent it actually locked, so the Python guard is the friendly early refusal and
the sproc is the one that cannot be raced.
"""

from typing import Optional

from shared.authz import current_is_system_context

# The router maps this exact prefix to 422 + `status_locked`. ProcessEngine
# folds a service exception into `{"error": str(e)}`, so the message is the only
# structure that survives to the HTTP layer (same mechanism as U-444's
# `review_status_shape`).
from shared.api.responses import STATUS_LOCKED_PREFIX  # noqa: F401  (re-exported)

TERMINAL_STATUS = "completed"


class StatusLockedError(PermissionError):
    """An edit refused because the parent document is in a terminal state."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"{STATUS_LOCKED_PREFIX}{reason}")


def is_system_caller() -> bool:
    """True for outbox workers, CLI sync and the drain endpoint — never for a
    human, however privileged, and never for any HTTP request. See the module
    docstring for why this reads a positive marker rather than inferring one."""
    return current_is_system_context.get()


# The prefix the mutation sprocs RAISERROR with when their in-transaction guard
# refuses. pyodbc surfaces it as the exception message, so it is the only thing
# that survives back to Python (same constraint as STATUS_LOCKED_PREFIX on the
# HTTP side).
SPROC_STATUS_LOCKED_TOKEN = "STATUS_LOCKED:"


def is_exempt(exempt: bool = False) -> bool:
    """Whether this caller skips the lock: an explicit internal pipeline, or a
    non-HTTP system boundary. The value the repo layer sends to the sprocs as
    `@AllowTerminalParent`."""
    return bool(exempt) or is_system_caller()


def reraise_if_sproc_status_locked(error: Exception, *, what: str) -> None:
    """Turn a sproc's RAISERROR back into the typed error the routers map to 422.

    Without this the refusal reaches `map_database_error` and surfaces as a
    generic 500 — indistinguishable from a real database fault, and routed
    nowhere near the `status_locked` contract.
    """
    if SPROC_STATUS_LOCKED_TOKEN in str(error):
        raise StatusLockedError(what) from error


def is_terminal(status: Optional[str], *, is_draft: Optional[bool] = None) -> bool:
    """Whether a document is in its terminal state.

    Prefers the canonical `status`; falls back to `is_draft` for the entities
    whose Phase-3 unit has not landed yet (bill_credit, invoice), so
    this helper can be reused there without being wrong in the meantime.
    """
    if status is not None:
        return status == TERMINAL_STATUS
    # Both unknown -> NOT terminal, i.e. the guard fails OPEN. That is
    # deliberate and narrow: this is a lifecycle guard, not an authorization
    # one, and every production path reads a full row from the database before
    # calling it. Failing closed would instead block legitimate edits whenever a
    # caller held a partially-populated object.
    return is_draft is False


def assert_editable(
    *,
    status: Optional[str],
    is_draft: Optional[bool] = None,
    what: str,
    exempt: bool = False,
) -> None:
    """Raise `StatusLockedError` if the document is terminal and nothing exempts
    this caller.

    `what` names the attempted edit and lands in the message the user sees, so
    make it read as a sentence: "its line items cannot be changed".
    """
    if exempt or is_system_caller():
        return
    if is_terminal(status, is_draft=is_draft):
        raise StatusLockedError(what)
