from __future__ import annotations

from datetime import date
from typing import Any, Optional

from entities.vendor_compliance.business.validity import compute_doc_status, days_until_expiry

_STATUS_PRECEDENCE = ("missing", "expired", "incomplete", "expiring", "valid")


def _expiry_sort_key(expiry_date: Optional[str]) -> tuple[int, str]:
    """None expiry sorts lowest; otherwise lexicographic ISO date (max = best)."""
    if not expiry_date:
        return (0, "")
    return (1, expiry_date)


def _pick_best_policy(policies: list[Any]) -> Any:
    return max(policies, key=lambda p: _expiry_sort_key(getattr(p, "expiry_date", None)))


def _build_per_coverage_entry(policy: Any, *, required: bool, today: date) -> dict:
    expiry_date = getattr(policy, "expiry_date", None)
    return {
        "coverage_type": getattr(policy, "coverage_type", None),
        "required": required,
        "status": compute_doc_status(expiry_date, today),
        "expiry_date": expiry_date,
        "days_until_expiry": days_until_expiry(expiry_date, today),
        "carrier": getattr(policy, "carrier", None),
        "policy_number": getattr(policy, "policy_number", None),
        "each_occurrence": getattr(policy, "each_occurrence", None),
        "aggregate": getattr(policy, "aggregate", None),
        "policy_public_id": getattr(policy, "public_id", None),
        "certificate_public_id": getattr(policy, "certificate_public_id", None),
    }


def _worst_status(statuses: list[str]) -> str:
    rank = {s: i for i, s in enumerate(_STATUS_PRECEDENCE)}
    return min(statuses, key=lambda s: rank.get(s, len(_STATUS_PRECEDENCE)))


def resolve_coverage_map(
    required_coverage_types: list[str],
    policies: list[Any],
    today: date,
) -> dict:
    """
    Pure coverage-centric compliance resolver (no DB / service calls).
    """
    by_type: dict[str, list[Any]] = {}
    for policy in policies:
        coverage_type = getattr(policy, "coverage_type", None)
        if not coverage_type:
            continue
        by_type.setdefault(coverage_type, []).append(policy)

    best_by_type: dict[str, Any] = {
        coverage_type: _pick_best_policy(group) for coverage_type, group in by_type.items()
    }

    coverages: dict[str, dict] = {}
    required_statuses: list[str] = []

    for coverage_type in required_coverage_types:
        best = best_by_type.get(coverage_type)
        if best is None:
            coverages[coverage_type] = {
                "coverage_type": coverage_type,
                "required": True,
                "status": "missing",
            }
            required_statuses.append("missing")
        else:
            entry = _build_per_coverage_entry(best, required=True, today=today)
            coverages[coverage_type] = entry
            required_statuses.append(entry["status"])

    required_set = set(required_coverage_types)
    extra_coverages: list[dict] = []
    for coverage_type in sorted(best_by_type.keys()):
        if coverage_type in required_set:
            continue
        extra_coverages.append(
            _build_per_coverage_entry(best_by_type[coverage_type], required=False, today=today)
        )

    if required_coverage_types:
        compliant = all(s in ("valid", "expiring") for s in required_statuses)
        rollup_status = _worst_status(required_statuses)
    else:
        compliant = True
        rollup_status = "valid" if policies else "missing"

    return {
        "status": rollup_status,
        "compliant": compliant,
        "coverages": coverages,
        "extra_coverages": extra_coverages,
    }
