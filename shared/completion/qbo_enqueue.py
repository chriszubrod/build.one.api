"""
Shared QBO push enqueue for completion services (U-797c5a round 2, R1).

Bill and BillCredit both enqueue a `sync_*_to_qbo` outbox row at completion. The row is
written with the realm of the first QBO auth record. Enqueue is NOT gated: ALLOW_QBO_WRITES
is enforced at drain and in the HTTP layer, as for every other external write.

The caller injects `read_auths` (so each service keeps its own, patchable, auth accessor)
and `label` (for the log and message wording, which stays as it was for Bill).

U-801 — failure semantics changed here
--------------------------------------
This used to swallow EVERY exception and return `qbo_sync_queued: False`. Combined with the
enqueue running on its own connection, several steps after the `completed` flip had already
committed, that meant a completion could be acknowledged (200/207) with no outbox row at
all: nothing to retry, nothing to dead-letter, and — unlike the coding-suggestion path, which
has `/admin/expense-coding/sweep-stranded` — no sweeper to find it afterwards. The QBO push
was simply lost, on the money path.

`QboOutboxService`'s own docstring already said callers "invoke this from inside the same
local transaction that mutates the entity"; the Python plumbing never carried a connection,
so that contract was documentation only. It is now real: `conn` threads the completion's
transaction down to the INSERT, and a genuine write failure PROPAGATES so the state
transition rolls back. The completion fails and is retryable, rather than silently diverging
from QBO.

Two conditions deliberately stay non-fatal, because in both the system is refusing on purpose
and nothing is lost:

* **No QBO auth record** — a configuration gap. There is no realm to target, so there is no
  write to lose. Unchanged from before.
* **`QboOutboxDeadLetterExistsError`** — the row EXISTS and is dead-lettered. A dead-lettered
  push may already have reached QBO before its mapping write failed, so recovery goes through
  the RequestId-preserving replay runbook, never a fresh enqueue. Failing the completion here
  would lock the entity out of completing with no self-service path, and would not protect
  anything: the record is already durable.

Everything else — a transient DB error, a lost ROWVERSION race on the coalesce refresh, a
failed INSERT — now fails the completion. That is the point of the unit.
"""

from typing import Any, Callable, Iterable, Optional


def enqueue_qbo_push(
    *,
    kind: str,
    entity_type: str,
    entity_public_id: str,
    read_auths: Callable[[], Iterable],
    logger,
    label: str,
    conn: Optional[Any] = None,
    debounce_seconds: Optional[int] = None,
) -> dict:
    """Enqueue the QBO push for a just-completed entity.

    `conn` is the completion's connection. Supplied, the outbox INSERT joins that
    transaction and commits with the state change; omitted, it stands alone.

    `debounce_seconds` overrides the Policy C window. A caller that enqueues
    INSIDE its transaction — i.e. before the rest of its own completion work has
    run — should widen it, so the drain cannot claim the row while that work is
    still in flight. Omitted, the service's 5s default applies.

    Raises whatever the outbox write raises, except the two deliberate refusals
    documented in this module's docstring, which are returned as reported errors.
    """
    from integrations.intuit.qbo.outbox.business.service import (
        QboOutboxDeadLetterExistsError,
        QboOutboxService,
    )

    # A failure to READ the auth record is a real failure and propagates: U-215's
    # lesson was that gating the enqueue on an auth call and swallowing the result
    # dropped the push with no outbox row at all — strictly worse than any retry.
    auths = read_auths()
    realm_id = auths[0].realm_id if auths else None
    if not realm_id:
        logger.warning(
            f"Skipping QBO enqueue for {label} {entity_public_id}: no QBO auth record configured"
        )
        return {
            "success": False,
            "message": "No QBO auth record configured",
            "qbo_sync_queued": False,
            "errors": [{"step": "qbo_enqueue", "error": "No QBO auth record configured"}],
        }

    try:
        outbox_row = QboOutboxService().enqueue(
            kind=kind,
            entity_type=entity_type,
            entity_public_id=entity_public_id,
            realm_id=realm_id,
            conn=conn,
            **({} if debounce_seconds is None else {"debounce_seconds": debounce_seconds}),
        )
    except QboOutboxDeadLetterExistsError as error:
        # Deliberate refusal, not a lost write: the dead-lettered row is durable and
        # recovery is the RequestId-preserving replay. Report, do not fail completion.
        logger.warning(
            f"QBO enqueue refused for {label} {entity_public_id}: dead-lettered row exists "
            f"({error.dead_letter_public_id})"
        )
        return {
            "success": False,
            "message": f"QBO push not queued: {error}",
            "qbo_sync_queued": False,
            "errors": [{"step": "qbo_enqueue", "error": str(error)}],
        }

    logger.info(
        f"Enqueued QBO sync for {label} {entity_public_id} (outbox {outbox_row.public_id})"
    )
    return {
        "success": True,
        "message": f"QBO push queued (outbox {outbox_row.public_id})",
        "qbo_sync_queued": True,
        "outbox_public_id": outbox_row.public_id,
        "errors": [],
    }
