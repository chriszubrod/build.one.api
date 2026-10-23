"""Deterministic budget cross-check for bill coding suggestions (U-655 / U-639 M2)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal, Optional, Sequence

from entities.budget.persistence.repo import BudgetRepository
from shared.access import assert_can_access_project
from shared.authz import current_is_system_admin, current_user_id

logger = logging.getLogger(__name__)

LOW_PROJECT_CONFIDENCE = Decimal("0.60")
FITS_HEADROOM = Decimal("0.9")

BudgetCheckValue = Optional[
    Literal["fits", "tight", "over", "unbudgeted", "no_budget", "no_project"]
]


@dataclass(frozen=True)
class BudgetCheckResult:
    check: BudgetCheckValue
    remaining: Optional[Decimal]
    evidence: dict[str, Decimal]


def _decimal_amount(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _opt_decimal(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    return Decimal(str(value))


def _line_amount(line: Any) -> Decimal:
    return _decimal_amount(getattr(line, "amount", None))


def _sum_self_in_scc(
    lines: Sequence[Any],
    *,
    project_id: int,
    sub_cost_code_id: int,
) -> Decimal:
    """Lines already coded to the target SCC on the target project (CostVariance self-exclusion)."""
    total = Decimal("0")
    for line in lines:
        if getattr(line, "project_id", None) != project_id:
            continue
        if getattr(line, "sub_cost_code_id", None) != sub_cost_code_id:
            continue
        total += _line_amount(line)
    return total


def _line_counts_toward_bill_amount(
    line: Any,
    *,
    project_id: int,
    sub_cost_code_id: int,
) -> bool:
    """Include target project/SCC or NULL placeholders; exclude other project or other SCC."""
    line_project = getattr(line, "project_id", None)
    if line_project is not None and int(line_project) != int(project_id):
        return False
    line_scc = getattr(line, "sub_cost_code_id", None)
    if line_scc is not None and int(line_scc) != int(sub_cost_code_id):
        return False
    return True


def _sum_bill_amount(
    lines: Sequence[Any],
    *,
    project_id: int,
    sub_cost_code_id: int,
) -> Decimal:
    total = Decimal("0")
    for line in lines:
        if not _line_counts_toward_bill_amount(
            line, project_id=project_id, sub_cost_code_id=sub_cost_code_id
        ):
            continue
        total += _line_amount(line)
    return total


def _base_evidence(
    project_id: Optional[int],
    sub_cost_code_id: int,
    lines: Sequence[Any],
    amount_override: Optional[Decimal],
) -> dict[str, Decimal]:
    """self_in_scc (CostVariance self-exclusion) and bill_amount (request amount, else
    this Bill's target-or-NULL project x target-or-NULL SCC lines)."""
    if project_id is None:
        self_in_scc = Decimal("0")
        bill_amount = Decimal("0")
    else:
        scope = dict(project_id=int(project_id), sub_cost_code_id=int(sub_cost_code_id))
        self_in_scc = _sum_self_in_scc(lines, **scope)
        bill_amount = _sum_bill_amount(lines, **scope)
    if amount_override is not None:
        bill_amount = _decimal_amount(amount_override)
    return {"self_in_scc": self_in_scc, "bill_amount": bill_amount}


def _variance_row_for_scc(
    variance_rows: Sequence[dict], sub_cost_code_id: int
) -> Optional[dict]:
    for row in variance_rows:
        scc_id = row.get("SubCostCodeId")
        if scc_id is not None and int(scc_id) == int(sub_cost_code_id):
            return row
    return None


def compute_budget_check(
    *,
    project_id: Optional[int],
    sub_cost_code_id: Optional[int],
    variance_rows: Sequence[dict],
    lines: Sequence[Any],
    project_confident: bool,
    amount_override: Optional[Decimal] = None,
) -> BudgetCheckResult:
    """Pure budget check over variance rows and bill line amounts (Decimal-only)."""
    if sub_cost_code_id is None:
        return BudgetCheckResult(check=None, remaining=None, evidence={})

    base_evidence = _base_evidence(project_id, int(sub_cost_code_id), lines, amount_override)
    self_in_scc = base_evidence["self_in_scc"]
    bill_amount = base_evidence["bill_amount"]

    if project_id is None or not project_confident:
        return BudgetCheckResult(
            check="no_project",
            remaining=None,
            evidence=base_evidence,
        )

    row = _variance_row_for_scc(variance_rows, int(sub_cost_code_id))
    budget_amount = _decimal_amount(row.get("BudgetAmount") if row else None)
    actual_cost = _decimal_amount(row.get("ActualCost") if row else None)
    cost_variance = _decimal_amount(row.get("CostVariance") if row else None)

    if row is None or budget_amount == 0:
        return BudgetCheckResult(
            check="unbudgeted",
            remaining=None,
            evidence={
                **base_evidence,
                "budget_amount": budget_amount,
                "actual_cost": actual_cost,
            },
        )

    remaining_pre = cost_variance + self_in_scc
    threshold_fits = remaining_pre * FITS_HEADROOM
    if bill_amount <= threshold_fits:
        check = "fits"
    elif bill_amount <= remaining_pre:
        check = "tight"
    else:
        check = "over"

    budget_remaining = remaining_pre - bill_amount
    return BudgetCheckResult(
        check=check,
        remaining=budget_remaining,
        evidence={
            **base_evidence,
            "budget_amount": budget_amount,
            "actual_cost": actual_cost,
            "remaining_pre": remaining_pre,
        },
    )


def budget_check_result_to_api_payload(
    result: BudgetCheckResult,
    *,
    project_id: Optional[int] = None,
) -> Optional[dict[str, Any]]:
    if result.check is None:
        return None
    remaining_str: Optional[str] = None
    if result.remaining is not None:
        remaining_str = f"{result.remaining:.2f}"
    # project_id (internal, as U-715's `project.id`) lets the web match a line's
    # selected project against the project this check was evaluated for.
    pid: Optional[int] = None
    if result.check != "no_project" and project_id is not None:
        pid = project_id
    return {
        "check": result.check,
        "remaining": remaining_str,
        "project_id": pid,
    }


def chosen_project_id_from_lines(lines: Sequence[Any]) -> Optional[int]:
    project_ids = {
        int(getattr(line, "project_id"))
        for line in lines
        if getattr(line, "project_id", None) is not None
    }
    if len(project_ids) == 1:
        return next(iter(project_ids))
    return None


def _resolve_expense_budget_project(
    contract: dict[str, Any],
    lines: Sequence[Any],
) -> tuple[Optional[int], bool]:
    project_field = contract.get("project")
    common = chosen_project_id_from_lines(lines)

    if project_field is not None and project_field.get("id") is not None:
        project_id = int(project_field["id"])
        confident = project_confident_for_suggestion(
            project_id,
            chosen_project_id=common,
            confidence=_opt_decimal(contract.get("confidence")),
            project_confidence=_opt_decimal(project_field.get("confidence")),
        )
        return project_id, confident

    if common is not None:
        return common, True

    return None, False


def evaluate_budget_for_expense(
    expense: Any,
    contract: dict[str, Any],
    lines: Sequence[Any],
    *,
    budget_repo: Optional[BudgetRepository] = None,
) -> Optional[dict[str, Any]]:
    """Live budget chip payload for expense coding suggest (U-719); failure-isolated."""
    if bool(getattr(expense, "is_credit", False)):
        return None

    status = contract.get("status")
    if status not in ("suggested", "flagged"):
        return None

    scc_field = contract.get("sub_cost_code")
    if not scc_field or scc_field.get("id") is None:
        return None

    expense_id = getattr(expense, "id", None)
    try:
        sub_cost_code_id = int(scc_field["id"])
        project_id, project_confident = _resolve_expense_budget_project(
            contract, lines
        )
        result = evaluate_budget_for_suggestion(
            project_id,
            sub_cost_code_id,
            lines=lines,
            project_confident=project_confident,
            budget_repo=budget_repo,
        )
        return budget_check_result_to_api_payload(result, project_id=project_id)
    except Exception as error:
        logger.warning(
            "Expense coding suggest budget failed for expense_id=%s: %s",
            expense_id,
            type(error).__name__,
        )
        return None


def evaluate_budget_for_suggestion(
    project_id: Optional[int],
    sub_cost_code_id: Optional[int],
    *,
    lines: Sequence[Any],
    project_confident: bool,
    amount_override: Optional[Decimal] = None,
    budget_repo: Optional[BudgetRepository] = None,
) -> BudgetCheckResult:
    """Load active budget + variance, with project access asserted before the read."""
    if sub_cost_code_id is None:
        return BudgetCheckResult(check=None, remaining=None, evidence={})

    if project_id is None or not project_confident:
        return compute_budget_check(
            project_id=project_id,
            sub_cost_code_id=sub_cost_code_id,
            variance_rows=[],
            lines=lines,
            project_confident=project_confident,
            amount_override=amount_override,
        )

    assert_can_access_project(int(project_id))

    repo = budget_repo or BudgetRepository()
    budget = repo.read_by_project_id(int(project_id))
    if budget is None or budget.id is None or budget.status != "active":
        return BudgetCheckResult(
            check="no_budget",
            remaining=None,
            evidence=_base_evidence(project_id, int(sub_cost_code_id), lines, amount_override),
        )

    variance_rows = repo.read_variance_by_project_id(
        int(project_id),
        budget_id=int(budget.id),
        actor_user_id=current_user_id.get(),
        actor_is_system_admin=current_is_system_admin.get(),
        sub_cost_code_id=int(sub_cost_code_id),
    )

    return compute_budget_check(
        project_id=project_id,
        sub_cost_code_id=sub_cost_code_id,
        variance_rows=variance_rows,
        lines=lines,
        project_confident=True,
        amount_override=amount_override,
    )


def project_confident_for_suggestion(
    suggestion_project_id: Optional[int],
    *,
    chosen_project_id: Optional[int],
    confidence: Optional[Decimal],
    project_confidence: Optional[Decimal] = None,
) -> bool:
    """Caller-chosen project (must match suggestion) or project confidence >= 0.60 otherwise.

    ``project_confidence`` gates the project when it differs from SCC/text confidence (U-654b).
    """
    if chosen_project_id is not None:
        return (
            suggestion_project_id is not None
            and int(chosen_project_id) == int(suggestion_project_id)
        )
    if suggestion_project_id is None:
        return False
    gate = project_confidence if project_confidence is not None else confidence
    if gate is None:
        return False
    return gate >= LOW_PROJECT_CONFIDENCE
