# Python Standard Library Imports
import json
import logging
import threading
import uuid
from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Optional

# Third-party Imports

# Local Imports
from entities.coding_suggestion.business.model import (
    IN_FLIGHT_STATUSES,
    REPOINT_CONFIRMATION_SOURCE,
    CodingSuggestion,
    VendorDominantProjectForBills,
    VendorDominantSubCostCodeForBills,
    has_written_record,
    is_repoint_pending,
)
from entities.coding_suggestion.persistence.repo import CodingSuggestionRepository
from entities.sub_cost_code.business.holding_lines import assert_no_holding_on_lines
from integrations.intuit.qbo.base.locking import qbo_app_lock
from shared.access import (
    EntityNotAccessibleError,
    assert_can_access_bill,
    assert_can_access_project,
    can_access_project,
)

logger = logging.getLogger(__name__)

# Outbox identity of the recode write for a coding suggestion (the (kind, entity_type) half of its coalesce key).
_RECODE_OUTBOX_IDENTITY = {"kind": "recode_purchase_line", "entity_type": "CodingSuggestion"}

# U-551b-f1 M1: the per-suggestion application lock. A confirm records a new revision and the QBO
# drain writes one; neither may run inside the other (an approval recorded mid-drain would be fenced
# out after the external write). The drain waits briefly (a confirm is short); a confirm waits longer
# (a drain spans a QBO GET + POST).
DRAIN_SUGGESTION_LOCK_TIMEOUT_MS = 5_000
CONFIRM_SUGGESTION_LOCK_TIMEOUT_MS = 10_000


def _canonical_public_id(public_id) -> str:
    """The identity SQL Server's ``UNIQUEIDENTIFIER`` conversion gives a string, as text.

    Lowercase hyphenated uuid; braces / ``urn:uuid:`` / whitespace stripped. SQL Server converts a
    string by its FIRST 36 characters (38 with braces) and ignores a trailing suffix, so
    ``<guid>wrong`` reads the same row as ``<guid>`` — the lock must agree (r6b Codex P1). An id that
    parses no other way (test ids) falls back to ``strip().lower()``.
    """
    text = str(public_id).strip()
    prefix = text[:38] if text.startswith("{") else text[:36]
    for candidate in (text, prefix):
        try:
            return str(uuid.UUID(candidate))
        except (ValueError, AttributeError, TypeError):
            continue
    return text.lower()


def coding_suggestion_lock_resource(public_id: str) -> str:
    """The ONE place a suggestion public id becomes its ``qbo_app_lock`` resource name — keyed on the
    CANONICAL identity (``_canonical_public_id``), so every spelling SQL maps to one row maps to one lock."""
    return f"qbo_coding_suggestion:{_canonical_public_id(public_id)}"


# U-551b-f1-f8: resources this process has a thread WAITING on inside ``qbo_app_lock``. Each waiter pins
# a pooled connection and a threadpool thread inside sp_getapplock, so only ONE per resource may wait;
# the SQL lock stays the cross-process truth.
_LOCK_WAITERS: set[str] = set()
_LOCK_WAITERS_GUARD = threading.Lock()


@contextmanager
def coding_suggestion_lock(public_id: str, *, timeout_ms: int):
    """Hold the suggestion's application lock; yields True when acquired, False on timeout.

    Looks ``qbo_app_lock`` up in this module's globals at CALL time so one patch site covers both
    holders (confirm and the outbox drain). Nothing inside the held region may take
    ``qbo_coding_suggestion:*`` or ``qbo_outbox_drain`` again (self-deadlock).

    Bounded waiters: if another thread of this process is already waiting on the same resource, yield
    False at once (the callers' existing "busy" signal: confirm -> 409 coding_suggestion_busy, the
    drain parks) instead of pinning a second connection + thread in sp_getapplock.

    Invariant: at most ONE thread of this process waits inside sp_getapplock per resource; the holder
    is not counted. Each call discards only the registration IT made, and only once (the wait ending,
    or ``finally`` if it never did) — never another thread's later registration.
    """
    resource = coding_suggestion_lock_resource(public_id)
    with _LOCK_WAITERS_GUARD:
        if resource in _LOCK_WAITERS:
            first_waiter = False
        else:
            _LOCK_WAITERS.add(resource)
            first_waiter = True
    if not first_waiter:
        yield False
        return
    registered = True
    try:
        with qbo_app_lock(resource, timeout_ms=timeout_ms) as acquired:
            # The wait is over (acquired or timed out): free the slot so the next waiter may queue
            # behind the holder, who need not be counted as a waiter. Ownership flag: after this the
            # slot may belong to ANOTHER thread, so ``finally`` must not discard it again.
            with _LOCK_WAITERS_GUARD:
                _LOCK_WAITERS.discard(resource)
                registered = False
            yield acquired
    finally:
        if registered:
            with _LOCK_WAITERS_GUARD:
                _LOCK_WAITERS.discard(resource)


def _confidence_to_float(value: Optional[Decimal]) -> Optional[float]:
    if value is None:
        return None
    return float(value)


def _contract_empty(status: str) -> dict[str, Any]:
    return {
        "status": status,
        "sub_cost_code": None,
        "project": None,
        "confidence": None,
    }


def _apply_post_redaction_contract(contract: dict[str, Any]) -> dict[str, Any]:
    project = contract.get("project")
    sub_cost_code = contract.get("sub_cost_code")
    if project is None and sub_cost_code is None:
        return _contract_empty("none")
    confidences = [
        field["confidence"]
        for field in (project, sub_cost_code)
        if field is not None and field.get("confidence") is not None
    ]
    if not confidences:
        return contract
    out = dict(contract)
    out["confidence"] = min(confidences)
    return out


def _expense_suggest_contract_needs_budget(contract: dict[str, Any]) -> bool:
    status = contract.get("status")
    if status == "suggested":
        return True
    return status == "flagged" and contract.get("sub_cost_code") is not None


def _attach_expense_budget_to_contract(
    contract: dict[str, Any],
    expense: Any,
) -> dict[str, Any]:
    if not _expense_suggest_contract_needs_budget(contract):
        return contract
    from entities.coding_suggestion.business.budget_check import evaluate_budget_for_expense

    out = dict(contract)
    expense_id = getattr(expense, "id", None)
    try:
        if expense_id is not None:
            from entities.expense_line_item.business.service import ExpenseLineItemService

            lines = ExpenseLineItemService().read_by_expense_id(expense_id=int(expense_id))
        else:
            lines = []
    except Exception as error:
        logger.warning(
            "Expense coding suggest budget failed for expense_id=%s: %s",
            expense_id,
            type(error).__name__,
        )
        out["budget"] = None
        return out
    out["budget"] = evaluate_budget_for_expense(expense, contract, lines)
    return out


def _redact_inaccessible_project_from_contract(contract: dict[str, Any]) -> dict[str, Any]:
    project = contract.get("project")
    if not project or project.get("id") is None:
        return contract
    if not can_access_project(int(project["id"])):
        redacted = dict(contract)
        redacted["project"] = None
        return _apply_post_redaction_contract(redacted)
    return contract


def _map_engine_result_to_contract(result: dict) -> dict[str, Any]:
    engine_status = result.get("status")
    if engine_status == "flagged":
        return _contract_empty("flagged")

    project = None
    project_source = result.get("project_source")
    if project_source in ("code", "address") and result.get("project_id") is not None:
        project = {
            "id": int(result["project_id"]),
            "source": "memo_code" if project_source == "code" else "memo_address",
            "confidence": _confidence_to_float(result.get("project_confidence")),
            "reason": result.get("project_reason") or "",
        }

    sub_cost_code = None
    scc_source = result.get("scc_source")
    if result.get("sub_cost_code_id") is not None and scc_source in (
        "scc_shorthand",
        "vendor_history",
    ):
        sub_cost_code = {
            "id": int(result["sub_cost_code_id"]),
            "source": "memo_shorthand" if scc_source == "scc_shorthand" else "vendor_history",
            "confidence": _confidence_to_float(result.get("scc_confidence")),
            "reason": result.get("scc_reason") or "",
        }

    if project is None and sub_cost_code is None:
        return _contract_empty("flagged")

    return {
        "status": "suggested",
        "sub_cost_code": sub_cost_code,
        "project": project,
        "confidence": _confidence_to_float(result.get("confidence")),
    }


def _map_stored_item_to_contract(item: "CodingSuggestion") -> dict[str, Any]:
    from entities.coding_suggestion.business.suggestion_service import (
        SOURCE_PROJECT_ADDRESS,
        SOURCE_PROJECT_CODE,
        SOURCE_SCC_SHORTHAND,
        SOURCE_VENDOR_HISTORY,
        split_suggestion_reason,
    )

    if item.status == "flagged":
        return _contract_empty("flagged")

    if item.status not in ("pending", "suggested"):
        return _contract_empty("none")

    if item.status == "pending" and not item.suggestion_source:
        return _contract_empty("none")

    tokens = set(filter(None, (item.suggestion_source or "").split("+")))
    overall = _confidence_to_float(item.suggestion_confidence)
    project_reason, scc_reason = split_suggestion_reason(item.suggestion_reason)

    project = None
    if item.suggested_project_id is not None and tokens.intersection(
        {SOURCE_PROJECT_CODE, SOURCE_PROJECT_ADDRESS}
    ):
        if SOURCE_PROJECT_CODE in tokens:
            src = "memo_code"
        else:
            src = "memo_address"
        project = {
            "id": int(item.suggested_project_id),
            "source": src,
            "confidence": overall,
            "reason": project_reason or "",
        }

    sub_cost_code = None
    if item.suggested_sub_cost_code_id is not None:
        if SOURCE_SCC_SHORTHAND in tokens:
            scc_api_source = "memo_shorthand"
        elif SOURCE_VENDOR_HISTORY in tokens:
            scc_api_source = "vendor_history"
        else:
            scc_api_source = None
        if scc_api_source:
            sub_cost_code = {
                "id": int(item.suggested_sub_cost_code_id),
                "source": scc_api_source,
                "confidence": overall,
                "reason": scc_reason or "",
            }

    return {
        "status": "suggested",
        "sub_cost_code": sub_cost_code,
        "project": project,
        "confidence": overall,
    }


class CodingSuggestionService:
    """Business layer for expense coding queue instrumentation rows."""

    def __init__(self):
        self.repo = CodingSuggestionRepository()

    def upsert_for_bill(
        self,
        *,
        bill_id: int,
        vendor_id: Optional[int] = None,
        suggested_project_id: Optional[int] = None,
        suggested_sub_cost_code_id: Optional[int] = None,
        suggestion_source: Optional[str] = None,
        suggestion_reason: Optional[str] = None,
        suggestion_confidence: Optional[Decimal] = None,
        features_json: Optional[str] = None,
        model_version: Optional[str] = None,
        prefill_state: Optional[str] = None,
        budget_check: Optional[str] = None,
        budget_remaining: Optional[Decimal] = None,
        created_by_user_id: Optional[int] = None,
        replace: bool = False,
    ) -> Optional[CodingSuggestion]:
        assert_can_access_bill(bill_id)
        return self.repo.upsert_for_bill(
            bill_id=bill_id,
            vendor_id=vendor_id,
            suggested_project_id=suggested_project_id,
            suggested_sub_cost_code_id=suggested_sub_cost_code_id,
            suggestion_source=suggestion_source,
            suggestion_reason=suggestion_reason,
            suggestion_confidence=suggestion_confidence,
            features_json=features_json,
            model_version=model_version,
            prefill_state=prefill_state,
            budget_check=budget_check,
            budget_remaining=budget_remaining,
            created_by_user_id=created_by_user_id,
            replace=replace,
        )

    def read_by_bill_id(self, bill_id: int) -> Optional[CodingSuggestion]:
        assert_can_access_bill(bill_id)
        return self.repo.read_by_bill_id(bill_id)

    def read_vendor_dominant_sub_cost_code_for_bills(
        self,
        vendor_id: int,
        *,
        project_id: Optional[int] = None,
        as_of=None,
        exclude_bill_id: Optional[int] = None,
    ) -> Optional[VendorDominantSubCostCodeForBills]:
        # A caller-supplied project must be one the actor can see. Vendor-wide history
        # (no project) is an aggregate: one SCC id plus counts, no project identity.
        assert_can_access_project(project_id)
        return self.repo.read_vendor_dominant_sub_cost_code_for_bills(
            vendor_id,
            project_id=project_id,
            as_of=as_of,
            exclude_bill_id=exclude_bill_id,
        )

    def read_vendor_dominant_project_for_bills(
        self,
        vendor_id: int,
        *,
        as_of=None,
        exclude_bill_id: Optional[int] = None,
    ) -> Optional[VendorDominantProjectForBills]:
        result = self.repo.read_vendor_dominant_project_for_bills(
            vendor_id,
            as_of=as_of,
            exclude_bill_id=exclude_bill_id,
        )
        # Never reveal a predicted project (or its vendor association) the actor
        # cannot access: suggest nothing instead. Admin/system callers bypass.
        if result is not None:
            try:
                assert_can_access_project(result.top_project_id)
            except EntityNotAccessibleError:
                return None
        return result

    def upsert_from_queue(
        self,
        *,
        qbo_purchase_id: int,
        qbo_purchase_qbo_id: str,
        realm_id: str,
        vendor_qbo_id: Optional[str] = None,
        expense_id: Optional[int] = None,
        created_by_user_id: Optional[int] = None,
    ) -> Optional[CodingSuggestion]:
        vendor_id = self._resolve_vendor_id(vendor_qbo_id, realm_id=realm_id)
        if expense_id is None:
            expense_id = self._resolve_expense_id_for_purchase(
                qbo_purchase_qbo_id=qbo_purchase_qbo_id,
                realm_id=realm_id,
            )
        return self.repo.upsert_from_queue(
            qbo_purchase_id=qbo_purchase_id,
            qbo_purchase_qbo_id=qbo_purchase_qbo_id,
            realm_id=realm_id,
            vendor_id=vendor_id,
            expense_id=expense_id,
            created_by_user_id=created_by_user_id,
        )

    def ensure_for_expense(
        self,
        *,
        expense_id: int,
        qbo_id: str,
        realm_id: str,
        created_by_user_id: Optional[int] = None,
        qbo_purchase: Optional[Any] = None,
    ) -> bool:
        """Seed this Expense's suggestion when its QBO Purchase still has an uncategorized line.

        Returns True when the upsert ran. It is a MERGE on (RealmId, QboPurchaseQboId), so a
        repeated or concurrent call never duplicates a row or resets its status (U-652a).
        """
        if qbo_purchase is None:
            from integrations.intuit.qbo.purchase.persistence.repo import QboPurchaseRepository

            qbo_purchase = QboPurchaseRepository().read_by_qbo_id_and_realm_id(qbo_id, realm_id)
        if qbo_purchase is None or qbo_purchase.id is None:
            return False
        if self._count_uncategorized_purchase_lines(realm_id=realm_id, qbo_purchase_qbo_id=qbo_id) < 1:
            return False
        self.upsert_from_queue(
            qbo_purchase_id=int(qbo_purchase.id),
            qbo_purchase_qbo_id=qbo_id,
            realm_id=realm_id,
            vendor_qbo_id=qbo_purchase.entity_ref_value,
            expense_id=expense_id,
            created_by_user_id=created_by_user_id,
        )
        return True

    def suggest_for_expense(
        self,
        *,
        expense_public_id: str,
        user_id: int,
        engine: Optional[Any] = None,
    ) -> Optional[dict[str, Any]]:
        """On-demand coding suggestion for one draft expense (U-715).

        Returns None when the expense does not exist (caller maps to 404).
        Never writes dbo.Expense / dbo.ExpenseLineItem.
        """
        from entities.expense.business.service import ExpenseService

        expense = ExpenseService().read_by_public_id(public_id=expense_public_id)
        if expense is None:
            return None

        if not expense.is_draft:
            return _contract_empty("not_draft")

        items: list = []
        if expense.id is not None:
            states = self.repo.read_state_by_expense_ids([int(expense.id)])
            items = states.get(int(expense.id)) or []

        qbo_purchase = None
        if expense.qbo_id and expense.realm_id:
            from integrations.intuit.qbo.purchase.persistence.repo import QboPurchaseRepository

            qbo_purchase = QboPurchaseRepository().read_by_qbo_id_and_realm_id(
                expense.qbo_id,
                expense.realm_id,
            )

        if (
            not items
            and expense.qbo_id
            and expense.realm_id
            and expense.id is not None
            and self.repo.read_exists_for_purchase(
                realm_id=expense.realm_id,
                qbo_purchase_qbo_id=expense.qbo_id,
            )
            is None
        ):
            try:
                self.ensure_for_expense(
                    expense_id=int(expense.id),
                    qbo_id=expense.qbo_id,
                    realm_id=expense.realm_id,
                    created_by_user_id=user_id,
                    qbo_purchase=qbo_purchase,
                )
            except Exception as error:
                logger.warning(
                    "Expense coding suggest seed failed for expense_id=%s: %s",
                    expense.id,
                    type(error).__name__,
                )
            states = self.repo.read_state_by_expense_ids([int(expense.id)])
            items = states.get(int(expense.id)) or []

        coding_row: Optional[CodingSuggestion] = None
        if items:
            public_id = items[0].get("public_id")
            if public_id:
                coding_row = self.read_by_public_id(str(public_id))

        if coding_row is not None and self._expense_suggestion_is_materialized(coding_row):
            contract = _redact_inaccessible_project_from_contract(
                _map_stored_item_to_contract(coding_row)
            )
            return _attach_expense_budget_to_contract(contract, expense)

        suggestion_engine = engine or self._default_suggestion_engine()
        item_shape, realm_id = self._engine_item_for_expense(
            expense, coding_row, qbo_purchase=qbo_purchase
        )
        engine_result = suggestion_engine.suggest_for_item(item_shape, realm_id=realm_id)

        if (
            coding_row is not None
            and coding_row.public_id
            and coding_row.status == "pending"
        ):
            stored = suggestion_engine.persist_result(
                public_id=coding_row.public_id,
                result=engine_result,
                sync_token=getattr(item_shape, "sync_token", None),
                only_from_pending_like=True,
            )
            if stored is None:
                stored = self.read_by_public_id(coding_row.public_id)
            if stored is not None:
                contract = _redact_inaccessible_project_from_contract(
                    _map_stored_item_to_contract(stored)
                )
                return _attach_expense_budget_to_contract(contract, expense)

        contract = _redact_inaccessible_project_from_contract(
            _map_engine_result_to_contract(engine_result)
        )
        return _attach_expense_budget_to_contract(contract, expense)

    @staticmethod
    def _default_suggestion_engine():
        from entities.coding_suggestion.business.suggestion_service import CodingSuggestionEngine

        return CodingSuggestionEngine(coding_item_service=CodingSuggestionService())

    @staticmethod
    def _expense_suggestion_is_materialized(item: CodingSuggestion) -> bool:
        if item.status in ("suggested", "flagged"):
            return True
        return bool(item.suggestion_source)

    def _engine_item_for_expense(
        self,
        expense: Any,
        coding_row: Optional[CodingSuggestion],
        *,
        qbo_purchase: Optional[Any] = None,
    ) -> tuple[Any, Optional[str]]:
        private_note: Optional[str] = None
        sync_token: Optional[str] = None
        vendor_id = coding_row.vendor_id if coding_row and coding_row.vendor_id else expense.vendor_id
        realm_id = expense.realm_id

        if qbo_purchase is None and expense.qbo_id and expense.realm_id:
            from integrations.intuit.qbo.purchase.persistence.repo import QboPurchaseRepository

            qbo_purchase = QboPurchaseRepository().read_by_qbo_id_and_realm_id(
                expense.qbo_id,
                expense.realm_id,
            )
        if qbo_purchase is not None:
            private_note = qbo_purchase.private_note
            sync_token = qbo_purchase.sync_token

        item = SimpleNamespace(
            public_id=coding_row.public_id if coding_row else None,
            vendor_id=vendor_id,
            sync_token=sync_token,
        )
        if private_note:
            item.private_note = private_note
        else:
            item.memo_text = expense.memo

        return item, realm_id

    @staticmethod
    def _resolve_expense_id_for_purchase(
        *, qbo_purchase_qbo_id: str, realm_id: str
    ) -> Optional[int]:
        try:
            from entities.expense.business.service import ExpenseService

            expense = ExpenseService().read_by_qbo_identity(qbo_purchase_qbo_id, realm_id)
            return int(expense.id) if expense and expense.id is not None else None
        except Exception as error:
            logger.warning(
                "Could not resolve expense for purchase %s: %s",
                qbo_purchase_qbo_id,
                error,
            )
            return None

    def read_by_public_id(self, public_id: str) -> Optional[CodingSuggestion]:
        return self.repo.read_by_public_id(public_id)

    def record_suggestion(
        self,
        *,
        public_id: str,
        suggested_project_id: Optional[int] = None,
        suggested_sub_cost_code_id: Optional[int] = None,
        suggested_description: Optional[str] = None,
        suggestion_source: Optional[str] = None,
        suggestion_reason: Optional[str] = None,
        suggestion_confidence: Optional[Decimal] = None,
        sync_token_at_suggest: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Optional[CodingSuggestion]:
        return self.repo.record_suggestion(
            public_id=public_id,
            suggested_project_id=suggested_project_id,
            suggested_sub_cost_code_id=suggested_sub_cost_code_id,
            suggested_description=suggested_description,
            suggestion_source=suggestion_source,
            suggestion_reason=suggestion_reason,
            suggestion_confidence=suggestion_confidence,
            sync_token_at_suggest=sync_token_at_suggest,
            status=status,
        )

    def record_flag(
        self,
        public_id: str,
        reason: str,
        modified_by_user_id: Optional[int] = None,
        only_from_pending_like: bool = False,
    ) -> Optional[CodingSuggestion]:
        return self.repo.record_flag(
            public_id=public_id,
            reason=reason,
            modified_by_user_id=modified_by_user_id,
            only_from_pending_like=only_from_pending_like,
        )

    def claim(
        self,
        public_id: str,
        user_id: int,
        reclaim_after_seconds: int = 900,
    ) -> Optional[CodingSuggestion]:
        return self.repo.claim(
            public_id=public_id,
            user_id=user_id,
            reclaim_after_seconds=reclaim_after_seconds,
        )

    def release(self, public_id: str, user_id: int) -> Optional[CodingSuggestion]:
        return self.repo.release(public_id=public_id, user_id=user_id)

    def record_confirmation(
        self,
        *,
        public_id: str,
        confirmed_project_id: Optional[int] = None,
        confirmed_sub_cost_code_id: Optional[int] = None,
        confirmed_description: Optional[str] = None,
        was_overridden: Optional[bool] = None,
        confirmed_by_user_id: Optional[int] = None,
        expected_sync_token: Optional[str] = None,
        status: Optional[str] = None,
        confirmed_mixed_codes: Optional[bool] = None,
        prefill_state: Optional[str] = None,
        confirmation_source: Optional[str] = None,
        repoint_targets_json: Optional[str] = None,
        expected_confirmation_seq: Optional[int] = None,
    ) -> Optional[CodingSuggestion]:
        return self.repo.record_confirmation(
            public_id=public_id,
            confirmed_project_id=confirmed_project_id,
            confirmed_sub_cost_code_id=confirmed_sub_cost_code_id,
            confirmed_description=confirmed_description,
            was_overridden=was_overridden,
            confirmed_by_user_id=confirmed_by_user_id,
            expected_sync_token=expected_sync_token,
            status=status,
            confirmed_mixed_codes=confirmed_mixed_codes,
            prefill_state=prefill_state,
            confirmation_source=confirmation_source,
            repoint_targets_json=repoint_targets_json,
            expected_confirmation_seq=expected_confirmation_seq,
        )

    def mark_enqueued(
        self, public_id: str, expected_confirmation_seq: Optional[int] = None
    ) -> Optional[CodingSuggestion]:
        return self.repo.mark_enqueued(public_id, expected_confirmation_seq=expected_confirmation_seq)

    @staticmethod
    def _repoint_target_key(target: dict) -> tuple:
        return (
            str(target.get("qbo_line_id")),
            target.get("sub_cost_code_id"),
            target.get("project_id"),
            target.get("description"),
            target.get("repoint_from_item_ref_value"),
        )

    @staticmethod
    def _repoint_targets_stale(item: CodingSuggestion, targets: list, targets_token: Optional[str]) -> bool:
        """R4-B: targets are valid only against the ownership snapshot they were computed from."""
        if targets_token is None or targets_token != item.written_sync_token:
            return True
        written_by_line = {str(e.get("qbo_line_id")): e for e in item.written_lines}
        for target in targets:
            written = written_by_line.get(str(target.get("qbo_line_id")))
            if written is None or written.get("item_ref_value") != target.get("repoint_from_item_ref_value"):
                return True
        return False

    def mark_written(
        self,
        public_id: str,
        sync_token: Optional[str] = None,
        expected_confirmation_seq: Optional[int] = None,
        written_lines_json: Optional[str] = None,
    ) -> Optional[CodingSuggestion]:
        return self.repo.mark_written(
            public_id,
            sync_token=sync_token,
            expected_confirmation_seq=expected_confirmation_seq,
            written_lines_json=written_lines_json,
        )

    def mark_changed_in_qbo(
        self,
        public_id: str,
        expected_confirmation_seq: Optional[int] = None,
    ) -> Optional[CodingSuggestion]:
        return self.repo.mark_changed_in_qbo(
            public_id,
            expected_confirmation_seq=expected_confirmation_seq,
        )

    _DEFAULT_EXTERNALLY_RESOLVED_REASON = (
        "recoded outside the cockpit; staging line replaced"
    )

    def reconcile_externally_resolved(self, dry_run: bool = True) -> dict:
        candidates = self.repo.read_externally_resolved_candidates()
        if dry_run:
            return {"candidates": len(candidates), "items": candidates}

        marked = 0
        skipped = 0
        for item in candidates:
            result = self.repo.mark_resolved_externally(
                item["public_id"],
                self._DEFAULT_EXTERNALLY_RESOLVED_REASON,
            )
            if result is not None:
                marked += 1
            else:
                skipped += 1
        return {"marked": marked, "skipped": skipped}

    # ------------------------------------------------------------------
    # U-551b-f1-f5: recovery of a lost enqueue (timer) and of an `error` repoint (system admin)
    # ------------------------------------------------------------------

    _RECODE_OUTBOX_KIND = "recode_purchase_line"
    _RECODE_OUTBOX_ENTITY_TYPE = "CodingSuggestion"  # what confirm / _enqueue_recode_and_mark enqueue with

    def _has_recode_dead_letter(self, public_id: str) -> bool:
        """A dead-lettered recode may already have reached QBO: nothing re-enqueues behind one (an
        operator replays it). Reads the outbox repo directly (the service exposes no read)."""
        from integrations.intuit.qbo.outbox.business.service import QboOutboxService

        return bool(
            QboOutboxService().repo.read_dead_letter_by_entity(
                kind=self._RECODE_OUTBOX_KIND,
                entity_type=self._RECODE_OUTBOX_ENTITY_TYPE,
                entity_public_id=public_id,
            )
        )

    def sweep_stranded_confirmed(
        self,
        *,
        dry_run: bool = True,
        older_than_seconds: int = 300,
        limit: int = 100,
        record_required_since=None,
    ) -> dict:
        """Re-enqueue ``confirmed`` rows whose enqueue was lost (the approval was given; only the enqueue
        step died). Gated like confirm: gate off + apply = ZERO writes. NEVER touches an ``error`` row
        (a deterministic error would loop every tick) - those are only REPORTED under ``recovery``.
        Each row is re-read under the suggestion lock and re-checked (status 'confirmed', seq unchanged)
        before ``_enqueue_recode_and_mark``; a dead letter blocks it."""
        from integrations.intuit.qbo.base.client import recode_write_gate_reason
        from integrations.intuit.qbo.base.errors import CodingSuggestionBusyError

        gate = recode_write_gate_reason()
        candidates = self.repo.read_stranded_confirmed(older_than_seconds=older_than_seconds, limit=limit)
        recovery_rows = self.repo.read_recovery_candidates(record_required_since=record_required_since, limit=limit)
        result: dict[str, Any] = {
            "gate": gate,
            "candidates": len(candidates),
            "enqueued": 0,
            "dead_letter_pending": [],
            "busy": [],
            "moved": [],
            "errors": [],
            "would_enqueue": [],
            "recovery": {
                "error_repoint": [r["public_id"] for r in recovery_rows if r.get("category") == "error_repoint"],
                "written_without_record": [
                    r["public_id"] for r in recovery_rows if r.get("category") == "written_without_record"
                ],
            },
        }
        if gate is not None and not dry_run:
            result.update({"status": "skipped", "reason": gate})
            self._log_sweep(result)
            return result

        for candidate in candidates:
            public_id = candidate["public_id"]
            try:
                if candidate.get("has_dead_letter"):
                    result["dead_letter_pending"].append(public_id)
                    continue
                if dry_run:
                    result["would_enqueue"].append(public_id)
                    continue
                with coding_suggestion_lock(public_id, timeout_ms=CONFIRM_SUGGESTION_LOCK_TIMEOUT_MS) as acquired:
                    if not acquired:
                        result["busy"].append(public_id)
                        continue
                    item = self.read_by_public_id(public_id)
                    if (
                        item is None
                        or item.status != "confirmed"
                        or item.confirmation_seq != candidate.get("confirmation_seq")
                    ):
                        result["moved"].append(public_id)
                        continue
                    seq = item.confirmation_seq
                    if self._enqueue_recode_and_mark(public_id, item.realm_id or candidate.get("realm_id"), seq):
                        result["enqueued"] += 1
                    else:
                        result["moved"].append(public_id)
            except CodingSuggestionBusyError:
                result["busy"].append(public_id)
            except Exception as error:
                result["errors"].append({"id": public_id, "error": type(error).__name__})
        result["status"] = "dry_run" if dry_run else "ok"
        self._log_sweep(result)
        return result

    @staticmethod
    def _log_sweep(result: dict) -> None:
        recovery = result["recovery"]
        flagged = (
            result.get("status") == "skipped" or result["enqueued"] or result["would_enqueue"]
            or any(result[k] for k in ("dead_letter_pending", "busy", "moved", "errors"))
            or any(recovery.values())
        )
        message = (
            "coding_suggestion.sweep_stranded status=%s gate=%s candidates=%d enqueued=%d would_enqueue=%s "
            "dead_letter_pending=%s busy=%s moved=%s errors=%s error_repoint=%s written_without_record=%s"
        )
        args = (
            result.get("status"), result["gate"], result["candidates"], result["enqueued"],
            result["would_enqueue"], result["dead_letter_pending"], result["busy"], result["moved"],
            result["errors"], recovery["error_repoint"], recovery["written_without_record"],
        )
        (logger.warning if flagged else logger.info)(message, *args)

    def redrive_error_repoint(self, public_id: str) -> dict:
        """System-admin re-drive of a repoint the drain marked ``error``: re-record the SAME approved
        revision (the row's own ``repoint_targets_json``) as a NEW ConfirmationSeq, fenced on the seq read
        here, then enqueue; the drain's token fence writes it or marks ``changed_in_qbo``. Whole body under
        the suggestion lock. A first-write ``error`` is ``not_a_repoint`` (the cockpit confirm recovers it)."""
        from integrations.intuit.qbo.base.client import recode_write_gate_reason

        gate_reason = recode_write_gate_reason()
        if gate_reason is not None:
            return {"status": "writes_disabled", "reason": gate_reason}

        with coding_suggestion_lock(public_id, timeout_ms=CONFIRM_SUGGESTION_LOCK_TIMEOUT_MS) as acquired:
            if not acquired:
                logger.warning("coding_suggestion.lock_busy public_id=%s holder=redrive", public_id)
                return {"status": "busy"}
            item = self.read_by_public_id(public_id)
            if item is None:
                return {"status": "not_found"}
            if item.status != "error":
                return {"status": "not_error"}
            if item.confirmation_source != REPOINT_CONFIRMATION_SOURCE:
                return {"status": "not_a_repoint"}
            if not item.repoint_targets:
                return {"status": "no_repoint_targets"}
            if self._has_recode_dead_letter(public_id):
                return {"status": "dead_letter_pending"}

            recorded = self.record_confirmation(
                public_id=public_id,
                confirmed_project_id=item.confirmed_project_id,
                confirmed_sub_cost_code_id=item.confirmed_sub_cost_code_id,
                confirmed_description=item.confirmed_description,
                expected_sync_token=item.written_sync_token,  # as confirm(repoint=True): the drain fences on OUR write's token
                status="confirmed",
                confirmation_source=REPOINT_CONFIRMATION_SOURCE,
                repoint_targets_json=item.repoint_targets_json,
                expected_confirmation_seq=item.confirmation_seq,
            )
            if recorded is None:
                logger.warning("coding_suggestion.redrive_revision_lost public_id=%s", public_id)
                return {"status": "moved"}
            new_seq = getattr(recorded, "confirmation_seq", None)
            if not self._enqueue_recode_and_mark(public_id, item.realm_id, new_seq):
                return {"status": "moved"}
            logger.warning("coding_suggestion.redrive_enqueued public_id=%s confirmation_seq=%s", public_id, new_seq)
            return {"status": "enqueued", "confirmation_seq": new_seq}

    def adopt_written_record(self, public_id: str, *, record_required_since=None) -> dict:
        """U-551b-f1-f3b ADOPT: give a record-less ``written`` row (a first write that crashed between the QBO
        POST and the mark) the written record it lacks, after GET-verifying the live Purchase. System-admin
        action; NEVER writes to QBO (one read-only GET). Whole body under the suggestion lock; the external-write
        gate is checked FIRST (a record write is a mark). All-or-nothing: every locally coded line must be
        ItemBased on the live purchase carrying the item (and customer) of the CONFIRMED coding (single-target
        first write) or of the local line (multi-line review_submit; post-pull residual), else a named refusal
        and zero writes. The adopted token is the GET's (the document that CONTAINS our lines), never
        ``SyncTokenAtSuggest``. Persist is fenced on the ConfirmationSeq read here (a revision since -> ``moved``)."""
        from datetime import datetime

        from integrations.intuit.qbo.base.client import recode_write_gate_reason

        gate_reason = recode_write_gate_reason()
        if gate_reason is not None:
            return {"status": "writes_disabled", "reason": gate_reason}

        with coding_suggestion_lock(public_id, timeout_ms=CONFIRM_SUGGESTION_LOCK_TIMEOUT_MS) as acquired:
            if not acquired:
                logger.warning("coding_suggestion.lock_busy public_id=%s holder=adopt", public_id)
                return {"status": "busy"}
            item = self.read_by_public_id(public_id)
            if item is None:
                return {"status": "not_found"}
            if item.status != "written":
                return {"status": "not_written"}
            if item.written_sync_token is not None or item.written_lines_json is not None:
                return {"status": "has_record"}
            if item.confirmation_source not in ("review_submit", "confirm_endpoint"):
                return {"status": "not_a_first_write"}
            written_at = item.written_at
            if isinstance(written_at, str):
                try:
                    written_at = datetime.fromisoformat(written_at)
                except ValueError:
                    written_at = None
            if written_at is None or record_required_since is None or written_at < record_required_since:
                return {"status": "legacy"}  # unknown / pre-record age: cannot tell a crash gap from legacy
            if item.confirmation_seq is None:
                return {"status": "moved"}  # nothing to fence the persist on: refuse here, never reach the sproc THROW
            if not (item.expense_id and item.qbo_purchase_qbo_id and item.realm_id):
                return {"status": "line_not_recoded"}  # nothing to verify against

            from integrations.intuit.qbo.base.cost_code_resolver import resolve_qbo_item_ref
            from integrations.intuit.qbo.purchase.connector.expense.business.service import (
                PurchaseExpenseConnector,
            )
            from entities.expense_line_item.business.service import ExpenseLineItemService
            from shared.lifecycle.completion_gate import HoldingCodeRefused

            locals_ = [
                line for line in ExpenseLineItemService().read_by_expense_id(item.expense_id)
                if getattr(line, "qbo_id", None)
            ]
            # Mode selection. The adopt CANNOT recover which lines were on the placeholder at write time
            # (the drain wrote `item.confirmed_*` to the placeholder PurchaseLine(s); the 15-min purchase
            # pull has since overwritten the local lines). So whenever a confirmed coding exists we match
            # the live lines against IT first: it under-claims at worst (a later repoint of an unclaimed
            # line is refused `repoint_target_not_ours`, the same outcome as leaving the row), whereas
            # multi-line mode may over-claim pre-categorized lines - the operator must verify EVERY
            # recorded line was on the placeholder before our write.
            has_confirmed = bool(item.confirmed_sub_cost_code_id)
            if item.confirmation_source == "confirm_endpoint" and not has_confirmed:
                return {"status": "no_confirmed_coding"}

            connector = PurchaseExpenseConnector()
            raw = connector.get_purchase_raw(realm_id=item.realm_id, qbo_purchase_qbo_id=item.qbo_purchase_qbo_id)
            if raw.get("SyncToken") is None:
                return {"status": "no_live_token"}
            live_sync_token = str(raw.get("SyncToken"))
            live_by_id = {str(line.get("Id")): line for line in (raw.get("Line") or [])}

            def _resolve(scc):
                try:
                    return resolve_qbo_item_ref(scc, item.realm_id), None
                except HoldingCodeRefused:
                    return None, {"status": "line_holding"}

            def _carries(live, ref, customer_ref):
                detail = (live or {}).get("ItemBasedExpenseLineDetail") or {}
                return (
                    live is not None
                    and live.get("DetailType") == "ItemBasedExpenseLineDetail"
                    and (detail.get("ItemRef") or {}).get("value") == ref.value
                    and (customer_ref is None or (detail.get("CustomerRef") or {}).get("value") == customer_ref.value)
                )

            written_lines: list[dict] = []
            if has_confirmed:
                ref, refusal = _resolve(item.confirmed_sub_cost_code_id)
                if refusal:
                    return refusal
                if ref is None:
                    return {"status": "line_unmapped"}
                customer_ref = None
                if item.confirmed_project_id:
                    customer_ref = connector._get_qbo_customer_ref(item.confirmed_project_id)
                    if customer_ref is None:
                        return {"status": "line_not_recoded"}
                matches = [
                    str(local.qbo_id) for local in locals_
                    if _carries(live_by_id.get(str(local.qbo_id)), ref, customer_ref)
                ]
                if len(matches) > 1:
                    return {"status": "ambiguous_lines"}
                if matches:
                    written_lines.append(
                        {
                            "qbo_line_id": matches[0],
                            "item_ref_value": ref.value,
                            "sub_cost_code_id": int(item.confirmed_sub_cost_code_id),
                        }
                    )
                elif item.confirmation_source == "confirm_endpoint" or len(locals_) == 1:
                    # A confirm_endpoint write, or a review_submit with a SINGLE linked line, wrote the
                    # confirmed coding to exactly one line: zero matches means it is not there any more
                    # (a human recoded it) — refuse; never fall back to the local coding, which mirrors QBO.
                    if any(str(local.qbo_id) not in live_by_id for local in locals_):
                        return {"status": "line_missing"}
                    return {"status": "line_not_recoded"}
            if not written_lines:
                # review_submit with no confirmed scc, or a MULTI-line review_submit none of whose lines
                # carries the confirmed coding: multi-line mode. The per-line coding lives only in the local lines. RESIDUAL: after a
                # purchase pull the local coding mirrors QBO, so this proves self-consistency, not that the
                # lines were ours (pre-categorized lines may be over-claimed) - the operator must confirm
                # by eye that every recorded line was on the placeholder (see the runbook).
                for local in locals_:
                    scc = getattr(local, "sub_cost_code_id", None)
                    if not scc:
                        continue  # no local coding to verify against: skipped, not adopted
                    ref, refusal = _resolve(scc)
                    if refusal:
                        return refusal
                    if ref is None:
                        return {"status": "line_unmapped"}
                    live = live_by_id.get(str(local.qbo_id))
                    if live is None:
                        return {"status": "line_missing"}
                    customer_ref = None
                    project_id = getattr(local, "project_id", None)
                    if project_id:
                        customer_ref = connector._get_qbo_customer_ref(project_id)
                        if customer_ref is None:
                            return {"status": "line_not_recoded"}
                    if not _carries(live, ref, customer_ref):
                        return {"status": "line_not_recoded"}
                    written_lines.append(
                        {"qbo_line_id": str(local.qbo_id), "item_ref_value": ref.value, "sub_cost_code_id": int(scc)}
                    )
            if not written_lines:
                return {"status": "line_not_recoded"}

            record_json = json.dumps(written_lines)
            adopted = self.repo.adopt_written_record(
                public_id, live_sync_token, record_json, item.confirmation_seq
            )
            if adopted is None:
                logger.warning("coding_suggestion.adopt_lost public_id=%s", public_id)
                return {"status": "moved"}
            logger.warning(
                "coding_suggestion.adopt_written_record public_id=%s lines=%s", public_id, len(written_lines)
            )
            return {"status": "adopted", "lines": len(written_lines), "written_sync_token": live_sync_token}

    def mark_error(
        self,
        public_id: str,
        write_error: Optional[str] = None,
        expected_confirmation_seq: Optional[int] = None,
    ) -> Optional[CodingSuggestion]:
        return self.repo.mark_error(
            public_id,
            write_error=write_error,
            expected_confirmation_seq=expected_confirmation_seq,
        )

    def confirm(
        self,
        *,
        public_id: str,
        project_id: int,
        sub_cost_code_id: int,
        description: Optional[str],
        was_overridden: bool,
        user_id: int,
        confirmed_mixed_codes: Optional[bool] = None,
        prefill_state: Optional[str] = None,
        allow_multi_line: bool = False,
        repoint: bool = False,
        repoint_targets: Optional[list] = None,
        repoint_targets_token: Optional[str] = None,
        expected_confirmation_seq: Optional[int] = None,
        lock_held: bool = False,
    ) -> dict:
        """Confirm a suggestion and enqueue its QBO recode.

        ``repoint`` (U-551b-f1, completion only) re-confirms a ``written`` suggestion so the
        drain repoints OUR written lines whose item diverged from the local coding; it is
        fenced on ``written_sync_token`` and refused without a written record. It also REQUIRES
        ``repoint_targets`` (the coding completion approved, persisted so a later purchase pull cannot
        erase it before the drain). Re-confirming a stranded ``confirmed`` repoint is a RECOVERY: it
        never calls ``record_confirmation`` (no seq bump; the persisted targets are the pending work),
        only re-enqueues. R4-B: ``repoint_targets`` are refused (``repoint_targets_stale``) unless
        ``repoint_targets_token`` equals the current ``written_sync_token`` and every target is one of
        our written lines carrying the item it was computed from. R4-C: for a ``confirmed``/``enqueued``
        repoint, fresh targets EQUAL to the persisted ones are recovery / ``already_enqueued``;
        DIFFERENT ones are a NEW revision (record_confirmation + enqueue). Fresh targets absent
        (nothing diverges any more) keeps the old recovery of the persisted work.

        U-551b-f2 status guard (NON-repoint only, before any resolver call), by the item's status:
        ``written`` -> ``already_written``; ``resolved_externally`` -> ``resolved_externally``;
        ``confirmed``/``enqueued`` with the repoint source -> ``repoint_pending`` (completion owns it);
        ``enqueued`` (first write) -> ``already_enqueued``. A ``confirmed`` first write is the crash window
        between ``record_confirmation`` and the enqueue: if the caller's (project, sub_cost_code) EQUALS the
        persisted coding it is a RECOVERY (never ``record_confirmation``, no seq bump: enqueue +
        ``mark_enqueued`` fenced on the item's seq; ``None`` -> ``already_terminal``, else ``enqueued`` +
        ``recovered``); a DIFFERENT coding is a NEW revision recorded through the normal path, but its
        ``record_confirmation`` is fenced on the seq this confirm read (a drain or another confirm that moved
        the row in between makes the write miss -> ``not_recorded``). A row that still HOLDS a written record
        (``written_sync_token`` + ``written_lines``, e.g. a repoint that bounced to ``changed_in_qbo`` /
        ``error``) is never a first-write target -> ``already_written``. pending / suggested / flagged
        and a recordless changed_in_qbo / error are unchanged (a first confirm or a re-review). Every refusal
        is zero-write EXCEPT ``already_terminal``, which is returned AFTER the recovery enqueue (the enqueue
        coalesces into the pending row and the drain honours the fenced mark, so it is harmless).

        ``allow_multi_line`` is set ONLY by the review-submit path, where every
        uncategorized line carries its own ExpenseLineItem coding and the worker writes
        each line with it. The confirm endpoint leaves it False: one document-level code
        cannot express per-line coding, so a multi-line purchase is refused (U-652).

        U-551b-f1 revision model: the whole confirm runs under the per-suggestion application lock
        (``coding_suggestion_lock``; a drain holds it across its QBO write), so no revision is recorded
        while a drain is in flight; a held lock returns ``{"status": "busy"}`` with zero writes.
        ``expected_confirmation_seq`` is REQUIRED for ``repoint=True`` (``invalid`` without it): the
        approval is bound to the revision the completion probe READ, and an item that has moved on
        returns ``repoint_revision_lost`` before any write. Non-repoint callers pass nothing (unguarded).

        ``lock_held=True`` is a CALLER CONTRACT (not verifiable here): the caller already holds
        ``coding_suggestion_lock(public_id)`` and confirm must NOT acquire it again (self-deadlock). The
        gate and revision-required checks still run. The only production caller is expense completion,
        which holds the lock across its probe -> confirm so the revision it read cannot move in between.
        """
        from integrations.intuit.qbo.base.client import recode_write_gate_reason

        # U-058: gate-off must reject with zero DB writes — a silently recorded
        # 'confirmed' looked successful to the operator but never reached QBO.
        gate_reason = recode_write_gate_reason()
        if gate_reason is not None:
            return {"status": "writes_disabled", "reason": gate_reason}

        if repoint and expected_confirmation_seq is None:
            return {"status": "invalid", "reason": "revision required"}

        kwargs = dict(
            public_id=public_id,
            project_id=project_id,
            sub_cost_code_id=sub_cost_code_id,
            description=description,
            was_overridden=was_overridden,
            user_id=user_id,
            confirmed_mixed_codes=confirmed_mixed_codes,
            prefill_state=prefill_state,
            allow_multi_line=allow_multi_line,
            repoint=repoint,
            repoint_targets=repoint_targets,
            repoint_targets_token=repoint_targets_token,
            expected_confirmation_seq=expected_confirmation_seq,
        )
        if lock_held:
            return self._confirm_under_lock(**kwargs)
        with coding_suggestion_lock(public_id, timeout_ms=CONFIRM_SUGGESTION_LOCK_TIMEOUT_MS) as acquired:
            if not acquired:
                logger.warning("coding_suggestion.lock_busy public_id=%s holder=confirm", public_id)
                return {"status": "busy"}
            return self._confirm_under_lock(**kwargs)

    def _enqueue_recode_and_mark(self, public_id: str, realm_id, expected_confirmation_seq) -> bool:
        """Recovery tail shared by the first-write and repoint branches: enqueue the recode, then mark the
        suggestion enqueued fenced on ``expected_confirmation_seq``. ``False`` = the mark was fenced out."""
        from integrations.intuit.qbo.outbox.business.service import QboOutboxService

        outbox = QboOutboxService()
        outbox.enqueue(
            **_RECODE_OUTBOX_IDENTITY,
            entity_public_id=public_id,
            realm_id=realm_id,
        )
        return self.mark_enqueued(public_id, expected_confirmation_seq=expected_confirmation_seq) is not None

    def _non_repoint_status_guard(
        self, item: CodingSuggestion, public_id: str, project_id, sub_cost_code_id
    ) -> Optional[dict]:
        """U-551b-f2 status guard for a non-repoint confirm; ``None`` = proceed (pending / suggested /
        flagged / a recordless changed_in_qbo / error, or a ``confirmed`` first write whose caller supplies a
        DIFFERENT coding: ``_confirm_under_lock`` then records a new revision fenced on this item's seq).

        ``confirmed`` first write = crash window between record_confirmation and enqueue. Caller's
        ``(project_id, sub_cost_code_id)`` EQUAL to the persisted coding -> RECOVERY (no record_confirmation,
        no seq bump; enqueue + mark fenced on the seq; a fenced-out mark -> ``already_terminal`` AFTER the
        enqueue, harmless: the enqueue coalesces and the drain honours the fenced mark). DIFFERENT -> ``None``.
        A row that still holds ANY half of a written record is ``already_written`` - checked BEFORE the
        first-write recovery (only a completion repoint changes a line we wrote).
        Every other refusal is zero-write."""
        status = item.status
        if status == "written":
            return {"status": "already_written"}
        if status == "resolved_externally":
            return {"status": "resolved_externally"}
        if is_repoint_pending(status, getattr(item, "confirmation_source", None)):
            return {"status": "repoint_pending"}  # completion owns a repoint; this caller must not touch it
        if has_written_record(item):
            # F5 (r3: BEFORE the first-write recovery, and EITHER half of the record counts): a row our drain
            # has written is never a first-write target again - a first write never sets the token, and
            # MarkCodingSuggestionWritten CASE-preserves it, so the token alone proves our write landed.
            return {"status": "already_written"}
        if status in IN_FLIGHT_STATUSES:
            if status == "enqueued":
                return {"status": "already_enqueued"}
            same_coding = (project_id, sub_cost_code_id) == (
                getattr(item, "confirmed_project_id", None),
                getattr(item, "confirmed_sub_cost_code_id", None),
            )
            if same_coding:
                if not self._enqueue_recode_and_mark(
                    public_id, item.realm_id, getattr(item, "confirmation_seq", None)
                ):
                    return {"status": "already_terminal"}
                return {"status": "enqueued", "enqueued": True, "recovered": True}
        return None

    def _confirm_under_lock(
        self,
        *,
        public_id: str,
        project_id: int,
        sub_cost_code_id: int,
        description: Optional[str],
        was_overridden: bool,
        user_id: int,
        confirmed_mixed_codes: Optional[bool],
        prefill_state: Optional[str],
        allow_multi_line: bool,
        repoint: bool,
        repoint_targets: Optional[list],
        repoint_targets_token: Optional[str],
        expected_confirmation_seq: Optional[int],
    ) -> dict:
        item = self.read_by_public_id(public_id)
        if item is None:
            return {"status": "not_found"}

        if not repoint:
            # U-551b-f2: a NON-repoint confirm is only a first confirm / re-review / recovery. Any other
            # status is refused with ZERO writes and BEFORE any resolver call (a confirm on a ``written``
            # suggestion would record seq+1, find no placeholder line and drive the suggestion to ``error``).
            guard = self._non_repoint_status_guard(item, public_id, project_id, sub_cost_code_id)
            if guard is not None:
                return guard

        if repoint and getattr(item, "confirmation_seq", None) != expected_confirmation_seq:
            # I1: the approval belongs to the revision the probe read; this item has moved on.
            logger.warning("coding_suggestion.repoint_revision_lost public_id=%s", public_id)
            return {"status": "repoint_revision_lost"}

        pending_repoint = (
            repoint
            and is_repoint_pending(item.status, getattr(item, "confirmation_source", None))
        )  # recovery / in-flight: the persisted targets are the work, no coding args are needed
        if (not project_id or not sub_cost_code_id) and not pending_repoint:
            return {
                "status": "invalid",
                "reason": "project and sub_cost_code required",
            }

        from integrations.intuit.qbo.base.cost_code_resolver import resolve_qbo_item_ref

        qbo_item_ref = None if pending_repoint else resolve_qbo_item_ref(sub_cost_code_id, realm_id=item.realm_id)
        if qbo_item_ref is None and not pending_repoint:
            return {
                "status": "mapping_missing",
                "reason": "SubCostCode has no QBO Item mapping",
            }

        repoint_targets_json: Optional[str] = None
        if repoint:
            written = item.written_lines
            # A repoint stranded between confirm and enqueue (status 'confirmed', our source) is
            # recovered; one already enqueued is in flight. Anything else must be 'written'.
            replacing = False
            if is_repoint_pending(item.status, getattr(item, "confirmation_source", None)):
                # ``None`` = the probe's recompute failed (recover the persisted work as-is); ``[]`` =
                # recomputed and nothing diverges any more (an EMPTY revision, R4b-0).
                if repoint_targets is not None:
                    if self._repoint_targets_stale(item, repoint_targets, repoint_targets_token):
                        return {"status": "repoint_targets_stale"}
                    fresh = {self._repoint_target_key(t) for t in repoint_targets}
                    persisted = {self._repoint_target_key(t) for t in (item.repoint_targets or [])}
                    replacing = fresh != persisted
                if replacing:
                    if repoint_targets and (not project_id or not sub_cost_code_id):
                        return {"status": "invalid", "reason": "project and sub_cost_code required"}
                    logger.warning(
                        "coding_suggestion.repoint.targets_replaced public_id=%s", public_id
                    )
                elif item.status == "enqueued":
                    return {"status": "already_enqueued"}
                else:
                    if not self._enqueue_recode_and_mark(public_id, item.realm_id, expected_confirmation_seq):
                        return {"status": "already_terminal"}
                    return {"status": "enqueued", "enqueued": True, "recovered": True}
            elif item.status != "written":
                return {"status": "not_written"}
            if not item.written_sync_token or not written:
                return {"status": "no_written_record"}
            if not repoint_targets and not replacing:
                return {"status": "no_repoint_targets"}
            if not replacing and self._repoint_targets_stale(item, repoint_targets, repoint_targets_token):
                return {"status": "repoint_targets_stale"}
            repoint_targets_json = json.dumps(repoint_targets)
        else:
            uncategorized_count = self._count_uncategorized_purchase_lines(
                realm_id=item.realm_id or "",
                qbo_purchase_qbo_id=item.qbo_purchase_qbo_id or "",
            )
            refusal = self._uncategorized_count_refusal(
                uncategorized_count=uncategorized_count,
                allow_multi_line=allow_multi_line,
            )
            if refusal is not None:
                return refusal

        expense_id = getattr(item, "expense_id", None) or self._resolve_expense_id_for_purchase(
            qbo_purchase_qbo_id=item.qbo_purchase_qbo_id or "",
            realm_id=item.realm_id or "",
        )
        if expense_id is not None:
            from entities.expense_line_item.business.service import (
                ExpenseLineItemService,
            )
            from integrations.intuit.qbo.base.ids import coerce_id

            eli_svc = ExpenseLineItemService()
            elis = eli_svc.read_by_expense_id(expense_id=expense_id)
            recoded_line_ids: set[int] = set()
            if repoint:
                written_qbo_line_ids = {str(e.get("qbo_line_id")) for e in written if e.get("qbo_line_id")}
                recoded_line_ids = {
                    int(li.id)
                    for li in elis
                    if getattr(li, "id", None) is not None
                    and str(getattr(li, "qbo_id", None)) in written_qbo_line_ids
                }
            else:
                uncategorized_qbo_line_ids = self._uncategorized_staging_qbo_line_ids(
                    realm_id=item.realm_id or "",
                    qbo_purchase_qbo_id=item.qbo_purchase_qbo_id or "",
                )
                if uncategorized_count == 1 and len(uncategorized_qbo_line_ids) == 1:
                    local = eli_svc.read_by_qbo_identity(
                        coerce_id(expense_id), uncategorized_qbo_line_ids[0]
                    )
                    if local is not None and local.id is not None:
                        recoded_line_ids.add(int(local.id))
                elif allow_multi_line and uncategorized_qbo_line_ids:
                    for qbo_line_id in uncategorized_qbo_line_ids:
                        local = eli_svc.read_by_qbo_identity(
                            coerce_id(expense_id), qbo_line_id
                        )
                        if local is not None and local.id is not None:
                            recoded_line_ids.add(int(local.id))
            other_lines = [
                li
                for li in elis
                if getattr(li, "id", None) not in recoded_line_ids
            ]
            assert_no_holding_on_lines(
                other_lines,
                message=(
                    "This expense cannot be recoded while any line remains on the suspense "
                    "sub cost code. Recode those lines first."
                ),
            )

        expected_sync_token = None
        if repoint:
            expected_sync_token = item.written_sync_token  # drain fences on OUR write's token
        else:
            try:
                from integrations.intuit.qbo.purchase.persistence.repo import QboPurchaseRepository

                qbo_purchase = QboPurchaseRepository().read_by_id(item.qbo_purchase_id)
                if qbo_purchase and qbo_purchase.sync_token:
                    expected_sync_token = qbo_purchase.sync_token
            except Exception as error:
                logger.warning(
                    "Could not snapshot sync token for coding suggestion %s: %s",
                    public_id,
                    error,
                )

        # U-551b-f1-f10: refuse a dead-lettered target BEFORE recording a revision enqueue() would refuse.
        from integrations.intuit.qbo.outbox.business.service import QboOutboxService

        outbox = QboOutboxService()
        outbox.find_coalesce_target_or_raise_dead_letter(
            **_RECODE_OUTBOX_IDENTITY,
            entity_public_id=public_id,
            realm_id=item.realm_id,
        )

        recorded = self.record_confirmation(
            public_id=public_id,
            confirmed_project_id=project_id,
            confirmed_sub_cost_code_id=sub_cost_code_id,
            confirmed_description=description,
            was_overridden=was_overridden,
            confirmed_by_user_id=user_id,
            expected_sync_token=expected_sync_token,
            status="confirmed",
            confirmed_mixed_codes=confirmed_mixed_codes,
            prefill_state=prefill_state,
            # The worker writes per-line coding only for a review-submit confirmation.
            # Repoint: record_confirmation has no status guard and bumps ConfirmationSeq, so the
            # terminal marks of the PREVIOUS write (seq n) cannot land on this one (seq n+1).
            confirmation_source=(
                REPOINT_CONFIRMATION_SOURCE
                if repoint
                else "review_submit" if allow_multi_line else "confirm_endpoint"
            ),
            repoint_targets_json=repoint_targets_json,
            # R4b-1 / I1: the repoint write lands only on the revision the CALLER's probe read (a lost race = no row).
            # F2: a ``confirmed`` first write re-recorded with a DIFFERENT coding is fenced on the seq read
            # above, so a drain / other confirm that moved the row in between makes the write miss.
            expected_confirmation_seq=(
                expected_confirmation_seq
                if repoint
                else getattr(item, "confirmation_seq", None) if item.status == "confirmed" else None
            ),
        )
        if recorded is None:
            if repoint:
                logger.warning("coding_suggestion.repoint_revision_lost public_id=%s", public_id)
                return {"status": "repoint_revision_lost"}
            return {"status": "not_recorded"}

        outbox.enqueue(
            **_RECODE_OUTBOX_IDENTITY,
            entity_public_id=public_id,
            realm_id=item.realm_id,
        )
        seq = getattr(recorded, "confirmation_seq", None)
        self.mark_enqueued(public_id, expected_confirmation_seq=seq if isinstance(seq, int) else None)
        return {"status": "enqueued", "enqueued": True}

    def is_multi_line_purchase(self, item: CodingSuggestion) -> bool:
        """True when the suggestion's QBO purchase has 2+ uncategorized lines (read-only)."""
        return self._count_uncategorized_purchase_lines(
            realm_id=item.realm_id or "",
            qbo_purchase_qbo_id=item.qbo_purchase_qbo_id or "",
        ) > 1

    @staticmethod
    def _uncategorized_count_refusal(*, uncategorized_count: int, allow_multi_line: bool) -> Optional[dict]:
        """U-652d3 F2: a confirm with no genuine placeholder line can only no-op, so it is refused before
        any write as ``nothing_to_recode`` (router: 409 with the status as detail, like its siblings)."""
        if uncategorized_count == 0:
            return {"status": "nothing_to_recode"}
        if uncategorized_count > 1 and not allow_multi_line:
            return {
                "status": "multi_line_unsupported",
                "reason": "multi_line_purchase_confirm_unsupported",
            }
        return None

    @staticmethod
    def _uncategorized_staging_qbo_line_ids(
        *, realm_id: str, qbo_purchase_qbo_id: str
    ) -> list[str]:
        if not realm_id or not qbo_purchase_qbo_id:
            return []
        from shared.database import get_connection

        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT pl.[QboLineId]
                FROM [qbo].[PurchaseLine] pl
                INNER JOIN [qbo].[Purchase] p ON pl.[QboPurchaseId] = p.[Id]
                WHERE p.[QboId] = ?
                  AND p.[RealmId] = ?
                  AND pl.[IsUncodedPlaceholder] = 1
                ORDER BY pl.[QboLineId]
                """,
                qbo_purchase_qbo_id,
                realm_id,
            )
            return [str(r[0]) for r in cursor.fetchall() if r[0] is not None]

    @staticmethod
    def _count_uncategorized_purchase_lines(
        *, realm_id: str, qbo_purchase_qbo_id: str
    ) -> int:
        if not realm_id or not qbo_purchase_qbo_id:
            return 0
        from shared.database import get_connection

        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM [qbo].[PurchaseLine] pl
                INNER JOIN [qbo].[Purchase] p ON pl.[QboPurchaseId] = p.[Id]
                WHERE p.[QboId] = ?
                  AND p.[RealmId] = ?
                  AND pl.[IsUncodedPlaceholder] = 1
                """,
                qbo_purchase_qbo_id,
                realm_id,
            )
            row = cursor.fetchone()
            return int(row[0]) if row and row[0] is not None else 0

    def to_api_dict(self, item: CodingSuggestion) -> dict:
        # Plain row dict (legacy line-id aliases retired in U-652d2).
        return item.to_dict()

    # One of FIVE near-identical dbo-first/legacy-fallback vendor-ref resolvers
    # (U-284v): this one, BillBillConnector._get_vendor_public_id (pull) +
    # _get_qbo_vendor_ref (push), PurchaseExpenseConnector._get_vendor_public_id,
    # VendorCreditBillCreditConnector._get_vendor_public_id. Hand-copied
    # deliberately, mirroring _get_project_public_id's own precedent — see
    # TODO.md's U-005[reuse] entry before adding a 6th copy or consolidating.
    def _resolve_vendor_id(
        self,
        vendor_qbo_id: Optional[str],
        *,
        realm_id: Optional[str] = None,
    ) -> Optional[int]:
        if not vendor_qbo_id:
            return None
        try:
            from entities.vendor.business.service import VendorService
            from integrations.intuit.qbo.base.identity_consistency import verify_identity_dbo_only

            vendor_service = VendorService()

            # U-313: dbo.Vendor's native QboId/RealmId is the SOLE identity
            # store for Vendor (Wave 5 "trust dbo alone" — qbo.VendorVendor no
            # longer has a writer, see docs/design/wave5.md). No legacy hop
            # left to fall back to on a miss (removed; it had no data source
            # left either).
            direct_vendor = vendor_service.read_by_qbo_identity(vendor_qbo_id, realm_id)
            if direct_vendor:
                verified_qbo_id = verify_identity_dbo_only(
                    direct_vendor, read_direct_by_qbo_identity=vendor_service.read_by_qbo_identity,
                )
                if verified_qbo_id:
                    return direct_vendor.id

            return None
        except Exception as error:
            logger.warning(f"Could not resolve vendor for QBO id {vendor_qbo_id}: {error}")
            return None
