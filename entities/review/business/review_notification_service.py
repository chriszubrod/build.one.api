"""
ReviewNotification record service (U-797c1).

Records one row per outbound review email (dbo.ReviewNotification) and the
documents that email covers (dbo.ReviewNotificationTarget). Writers are the
Bill / Expense / Contract Labor notification services; the MS worker stamps the
Graph ids on draft create; the reply binder (shared/authz/provenance.py) reads
it back.

Failure policy: the record is a sidecar. Writers wrap every call so a failure
here never blocks or rolls back the email. The stamp and try_* methods report
failure as a return value and never raise.
"""

import hashlib
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable, Mapping, Optional

from entities.review.persistence.review_notification_repo import ReviewNotificationRepository

logger = logging.getLogger(__name__)

# Target kind -> the dbo.ReviewNotificationTarget column it fills.
_TARGET_FIELD = {
    "bill": "BillId",
    "expense": "ExpenseId",
    "bill_credit": "BillCreditId",
    "contract_labor": "ContractLaborId",
}


def _log_failure(operation: str, error: Exception, context: str = "") -> None:
    """Log the document context and the exception CLASS only, never the message text."""
    logger.error(
        "review_notification.%s_failed context=%s error=%s",
        operation, context, type(error).__name__,
    )


def _attempt(operation: str, call, context: str) -> bool:
    """True unless the stamp call reported failure. The stamp methods log their own failures
    and return False. This catch only sees an exception that escapes them (a raising
    stand-in), so the fallback in stamp_review_notification still runs, and it is logged here
    only in that case."""
    try:
        return call() is not False
    except Exception as error:
        _log_failure(operation, error, context)
        return False


def stamp_review_notification(*, outbox_id: int, review_notification_id: Optional[int],
                              conversation_id: Optional[str],
                              internet_message_id: Optional[str],
                              draft_graph_message_id: Optional[str]) -> bool:
    """MS worker, on draft create (U-797c2a). Never raises.

    By id when the payload named the record, and falls back to the by-outbox-id stamp
    when that fails. The by-outbox-id stamp is the only path for rows enqueued before
    this deploy. Both fill NULL identity columns only. Returns True when a stamp call
    completed without raising - NOT that a row was updated (an ownership refusal or a
    released id updates zero rows and still returns True; no fallback is needed then)."""
    context = f"outbox_id={outbox_id} review_notification_id={review_notification_id}"
    try:
        service = ReviewNotificationRecordService()
    except Exception as error:
        _log_failure("stamp_service", error, context)
        return False
    if review_notification_id is not None:
        if _attempt("stamp_by_id", lambda: service.stamp_by_id(
                id=review_notification_id,
                outbox_id=outbox_id,
                conversation_id=conversation_id,
                internet_message_id=internet_message_id,
                draft_graph_message_id=draft_graph_message_id,
        ), context):
            return True
    return _attempt("stamp_from_outbox", lambda: service.stamp_from_outbox(
        outbox_id=outbox_id,
        conversation_id=conversation_id,
        internet_message_id=internet_message_id,
        draft_graph_message_id=draft_graph_message_id,
    ), context)


def _hours_text(value: Any) -> str:
    """Hours as a two-decimal Decimal string ('2.50', '8.00'). A missing value counts as 0.00.

    Normalized to two places so 8, '8.0', 8.0 and Decimal('8.00') fingerprint the same. Changing this
    definition is safe only while no fingerprint is stored (b1 not deployed)."""
    if value is None:
        return "0.00"
    return str(Decimal(str(value)).quantize(Decimal("0.01")))


def cl_lines_fingerprint(pairs: Iterable[tuple[Any, Any]]) -> str:
    """SHA-256 hex over one crew member's lines: the sorted 'lineItemId:hours' strings, joined by newlines.

    The one definition: U-797b1 writes it into the claim snapshot, U-797b2 recomputes it at apply time to
    refuse an apply when the lines changed since the email. Order-independent by the sort."""
    items = sorted(f"{line_id}:{_hours_text(hours)}" for line_id, hours in pairs)
    return hashlib.sha256("\n".join(items).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CrewTargetSnapshot:
    """What the CL crew email showed for one crew member, plus the fingerprint of their lines then (U-797b1).

    suggested_sub_cost_code_id / suggestion_id / suggestion_source are NULL when the email showed no code.
    suggestion_may_split is the split note as shown (NULL when no completed row was shown).
    """

    contract_labor_id: int
    suggested_sub_cost_code_id: Optional[int]
    suggestion_id: Optional[int]
    suggestion_source: Optional[str]
    suggestion_may_split: Optional[bool]
    lines_fingerprint: Optional[str]


def target_pairs(target_rows: Iterable, notification_id) -> set:
    """(kind, id) pairs for the targets of one notification."""
    pairs: set = set()
    for t in target_rows:
        if getattr(t, "ReviewNotificationId", None) != notification_id:
            continue
        for kind, field in _TARGET_FIELD.items():
            value = getattr(t, field, None)
            if value is not None:
                pairs.add((kind, int(value)))
    return pairs


class ReviewNotificationRecordService:
    """Writes and reads the ReviewNotification record."""

    def __init__(self, repo: Optional[ReviewNotificationRepository] = None):
        self.repo = repo or ReviewNotificationRepository()

    # ------------------------------------------------------------------
    # Writers
    # ------------------------------------------------------------------
    def record_for_review(self, *, kind: str, review_id: int, targets, subject: Optional[str]):
        """One notification for a per-record email (bill / expense / bill_credit).

        `targets` is a list of (kind, id). Returns the notification row."""
        row = self.repo.create(
            kind=kind,
            review_id=review_id,
            subject=subject,
            project_id=None,
            work_date=None,
        )
        if row is None:
            return None
        for target_kind, target_id in targets:
            self._add_target(row.Id, target_kind, target_id)
        return row

    def claim_for_crew(self, *, project_id: int, work_date, subject: Optional[str],
                       contract_labor_ids: Iterable[int],
                       snapshots: Optional[Mapping[int, CrewTargetSnapshot]] = None):
        """The Contract Labor (project, date) claim, with one target per crew member.

        snapshots (U-797b1) maps a ContractLabor id to what the email showed for it; each target stores it.
        Returns None when the claim already exists (no targets are written).
        If a target write fails, the claim is released so a retry can run."""
        row = self.repo.create(
            kind="contract_labor",
            review_id=None,
            subject=subject,
            project_id=project_id,
            work_date=work_date,
        )
        if row is None:
            return None
        try:
            for cl_id in contract_labor_ids:
                snap = snapshots.get(cl_id) if snapshots else None
                self._add_target(row.Id, "contract_labor", cl_id, snapshot=snap)
        except Exception:
            self.release(row.Id)
            raise
        return row

    def attach_outbox(self, id: int, outbox_id: int) -> None:
        self.repo.set_outbox(id=id, outbox_id=outbox_id)

    def release(self, id: int) -> None:
        self.repo.delete(id=id)

    # ------------------------------------------------------------------
    # Never-raise wrappers. Each logs the context and the exception CLASS only,
    # and returns None / False. The email is never blocked or un-sent by the record.
    # ------------------------------------------------------------------
    def try_record_for_review(self, *, kind: str, review_id: int, targets, subject: Optional[str],
                              context: str = ""):
        try:
            return self.record_for_review(kind=kind, review_id=review_id, targets=targets, subject=subject)
        except Exception as error:
            _log_failure("record_for_review", error, context)
            return None

    def try_claim_for_crew(self, *, project_id: int, work_date, subject: Optional[str],
                           contract_labor_ids: Iterable[int],
                           snapshots: Optional[Mapping[int, CrewTargetSnapshot]] = None,
                           context: str = ""):
        try:
            return self.claim_for_crew(project_id=project_id, work_date=work_date, subject=subject,
                                       contract_labor_ids=contract_labor_ids, snapshots=snapshots)
        except Exception as error:
            _log_failure("claim_for_crew", error, context)
            return None

    def try_attach_outbox(self, id: int, outbox_id: int, context: str = "") -> bool:
        try:
            self.attach_outbox(id, outbox_id)
            return True
        except Exception as error:
            _log_failure("attach_outbox", error, context)
            return False

    def try_release(self, id: int, context: str = "") -> bool:
        try:
            self.release(id)
            return True
        except Exception as error:
            _log_failure("release", error, context)
            return False

    # ------------------------------------------------------------------
    # Stamps. Each returns True when the call completed, False when it failed.
    # ------------------------------------------------------------------
    def stamp_from_outbox(self, *, outbox_id: int, conversation_id: Optional[str],
                          internet_message_id: Optional[str],
                          draft_graph_message_id: Optional[str]) -> bool:
        """Legacy rows (enqueued before U-797c2a): stamp by OutboxId. Never raises."""
        try:
            self.repo.stamp_from_outbox(
                outbox_id=outbox_id,
                conversation_id=conversation_id,
                internet_message_id=internet_message_id,
                draft_graph_message_id=draft_graph_message_id,
            )
            return True
        except Exception as error:
            _log_failure("stamp_from_outbox", error, f"outbox_id={outbox_id}")
            return False

    def stamp_by_id(self, *, id: int, outbox_id: int, conversation_id: Optional[str],
                    internet_message_id: Optional[str],
                    draft_graph_message_id: Optional[str]) -> bool:
        """U-797c2a: stamp the row the payload names, owned by this outbox row. Never raises."""
        try:
            self.repo.stamp_by_id(
                id=id,
                outbox_id=outbox_id,
                conversation_id=conversation_id,
                internet_message_id=internet_message_id,
                draft_graph_message_id=draft_graph_message_id,
            )
            return True
        except Exception as error:
            _log_failure("stamp_by_id", error, f"review_notification_id={id} outbox_id={outbox_id}")
            return False

    # ------------------------------------------------------------------
    # Readers
    # ------------------------------------------------------------------
    def lookup_for_reply(self, conversation_id: Optional[str]) -> list:
        """Every notification on this exact ConversationId, with its target pairs.

        Returns [(notification_row, {(kind, id), ...}), ...]. Blank input is []
        with no database call. The caller decides what 0, 1 or 2+ mean."""
        conv = conversation_id if isinstance(conversation_id, str) else ""
        if not conv.strip():
            return []
        notifications, targets = self.repo.find_for_reply(conversation_id=conv)
        return [(n, target_pairs(targets, n.Id)) for n in notifications]

    def contract_labor_record_exists(self, *, project_id: int, work_date) -> bool:
        """U-747: True when the day's contract_labor ReviewNotification exists for this project and work date."""
        return self.repo.read_contract_labor_record_id(project_id=project_id, work_date=work_date) is not None

    def read_crew_snapshots(self, notification_id: int) -> dict[int, CrewTargetSnapshot]:
        """U-797b1: the snapshot of each crew member's target on one CL notification, keyed by ContractLabor id."""
        rows = self.repo.read_target_snapshots(review_notification_id=notification_id)
        out: dict[int, CrewTargetSnapshot] = {}
        for r in rows:
            cl_id = int(r.ContractLaborId)
            out[cl_id] = CrewTargetSnapshot(
                contract_labor_id=cl_id,
                suggested_sub_cost_code_id=int(r.SuggestedSubCostCodeId) if r.SuggestedSubCostCodeId is not None else None,
                suggestion_id=int(r.SuggestionId) if r.SuggestionId is not None else None,
                suggestion_source=r.SuggestionSource,
                suggestion_may_split=bool(r.SuggestionMaySplit) if r.SuggestionMaySplit is not None else None,
                lines_fingerprint=r.LinesFingerprint,
            )
        return out

    def _add_target(self, notification_id: int, kind: str, target_id: int,
                    snapshot: Optional[CrewTargetSnapshot] = None):
        field = _TARGET_FIELD[kind]
        extra: dict[str, Any] = {}
        if snapshot is not None:
            extra = dict(
                with_snapshot=True,
                suggested_sub_cost_code_id=snapshot.suggested_sub_cost_code_id,
                suggestion_id=snapshot.suggestion_id,
                suggestion_source=snapshot.suggestion_source,
                suggestion_may_split=snapshot.suggestion_may_split,
                lines_fingerprint=snapshot.lines_fingerprint,
            )
        return self.repo.add_target(
            review_notification_id=notification_id,
            bill_id=target_id if field == "BillId" else None,
            expense_id=target_id if field == "ExpenseId" else None,
            bill_credit_id=target_id if field == "BillCreditId" else None,
            contract_labor_id=target_id if field == "ContractLaborId" else None,
            **extra,
        )
