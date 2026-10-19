"""Pure accuracy aggregation for bill intake predictions (U-676m)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Mapping, Optional, Sequence


def normalize_bill_number(value: Optional[str]) -> str:
    if not value:
        return ""
    return re.sub(r"[^0-9A-Za-z]", "", str(value)).upper()


def bill_numbers_match(pred: Optional[str], final: Optional[str]) -> bool:
    return normalize_bill_number(pred) == normalize_bill_number(final)


def totals_match(pred: Optional[Decimal], final: Optional[Decimal], *, tolerance: Decimal) -> bool:
    if pred is None or final is None:
        return False
    return abs(pred - final) <= tolerance


def _normalize_final_bill_number(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _to_decimal(value: Any) -> Optional[Decimal]:
    return Decimal(str(value)) if value is not None else None


# (label, prediction key, final key, match(pred, final, amount_tolerance)). The final
# bill number is normalized (blank → None) before any comparison.
_FIELD_SPECS: tuple[tuple[str, str, str, Callable[[Any, Any, Decimal], bool]], ...] = (
    ("vendor_id", "pred_vendor_id", "final_vendor_id", lambda p, f, _tol: p == f),
    ("bill_number_exact", "pred_bill_number", "final_bill_number", lambda p, f, _tol: p == f),
    (
        "bill_number_normalized",
        "pred_bill_number",
        "final_bill_number",
        lambda p, f, _tol: bill_numbers_match(p, f),
    ),
    ("bill_date", "pred_bill_date", "final_bill_date", lambda p, f, _tol: p == f),
    (
        "total_amount",
        "pred_total_amount",
        "final_total_amount",
        lambda p, f, tol: totals_match(_to_decimal(p), _to_decimal(f), tolerance=tol),
    ),
    ("sub_cost_code_id", "pred_sub_cost_code_id", "final_sub_cost_code_id", lambda p, f, _tol: p == f),
    ("project_id", "pred_project_id", "final_project_id", lambda p, f, _tol: p == f),
)


def _final_value(row: Mapping[str, Any], final_key: str) -> Any:
    value = row.get(final_key)
    return _normalize_final_bill_number(value) if final_key == "final_bill_number" else value


_ROW_OK_FIELD_LABELS = frozenset(
    label for label, _, _, _ in _FIELD_SPECS if label != "bill_number_exact"
)


def _row_has_any_final(row: Mapping[str, Any]) -> bool:
    return any(_final_value(row, final_key) is not None for _l, _p, final_key, _e in _FIELD_SPECS)


@dataclass(frozen=True)
class FieldAccuracy:
    label: str
    n_with_final: int
    n_match: int

    @property
    def match_rate(self) -> Optional[float]:
        if self.n_with_final <= 0:
            return None
        return self.n_match / self.n_with_final


@dataclass(frozen=True)
class AccuracyReport:
    fields: tuple[FieldAccuracy, ...]
    overall_match_rate: Optional[float]
    vendor_breakdown: tuple[tuple[str, int, int], ...]
    mean_cost_usd: Optional[Decimal]
    mean_latency_ms: Optional[float]
    n_rows: int


def aggregate_prediction_accuracy(
    rows: Sequence[Mapping[str, Any]],
    *,
    amount_tolerance: Decimal = Decimal("0.01"),
) -> AccuracyReport:
    """Aggregate match rates per field from normalized row dicts."""
    comparable_rows = [r for r in rows if _row_has_any_final(r)]
    n = len(comparable_rows)
    if n == 0:
        return AccuracyReport(
            fields=(),
            overall_match_rate=None,
            vendor_breakdown=(),
            mean_cost_usd=None,
            mean_latency_ms=None,
            n_rows=0,
        )

    field_stats = {label: [0, 0] for label, _, _, _ in _FIELD_SPECS}
    per_row_ok: list[bool] = []
    vendor_stats: dict[str, list[int]] = {}
    costs: list[Decimal] = []
    latencies: list[int] = []

    for row in comparable_rows:
        cost = row.get("cost_usd")
        if cost is not None:
            costs.append(Decimal(str(cost)))
        lat = row.get("latency_ms_total")
        if lat is not None:
            latencies.append(int(lat))

        row_field_match: dict[str, bool] = {}
        for label, pred_key, final_key, eq_fn in _FIELD_SPECS:
            final_val = _final_value(row, final_key)
            if final_val is None:
                continue
            field_stats[label][0] += 1
            matched = eq_fn(row.get(pred_key), final_val, amount_tolerance)
            if matched:
                field_stats[label][1] += 1
            row_field_match[label] = matched

        row_ok = all(row_field_match.get(label, True) for label in _ROW_OK_FIELD_LABELS)
        per_row_ok.append(row_ok)

        vname = str(row.get("vendor_name") or "Unknown")
        bucket = vendor_stats.setdefault(vname, [0, 0])
        bucket[0] += 1
        if row_ok:
            bucket[1] += 1

    fields = tuple(
        FieldAccuracy(label, stats[0], stats[1]) for label, stats in field_stats.items()
    )
    overall = sum(per_row_ok) / len(per_row_ok) if per_row_ok else None
    vendor_breakdown = tuple(
        sorted(
            ((name, stats[0], stats[1]) for name, stats in vendor_stats.items()),
            key=lambda t: (-t[1], t[0]),
        )
    )
    mean_cost = sum(costs, Decimal("0")) / Decimal(len(costs)) if costs else None
    mean_lat = sum(latencies) / len(latencies) if latencies else None
    return AccuracyReport(
        fields=fields,
        overall_match_rate=overall,
        vendor_breakdown=vendor_breakdown,
        mean_cost_usd=mean_cost,
        mean_latency_ms=mean_lat,
        n_rows=n,
    )


def format_accuracy_markdown(report: AccuracyReport) -> str:
    lines = ["# Bill intake prediction accuracy", "", f"Rows (finalized): **{report.n_rows}**", ""]
    lines.append("## Per-field match rates")
    lines.append("")
    lines.append("| Field | n (with final) | Matches | Rate |")
    lines.append("| --- | ---: | ---: | ---: |")
    for field in report.fields:
        rate = f"{field.match_rate * 100:.1f}%" if field.match_rate is not None else "—"
        lines.append(f"| {field.label} | {field.n_with_final} | {field.n_match} | {rate} |")
    lines.append("")
    if report.overall_match_rate is not None:
        lines.append(
            f"**Overall row match rate:** {report.overall_match_rate * 100:.1f}% "
            "(all compared final fields match on the same row)"
        )
        lines.append("")
    lines.append("## Top vendors (by volume)")
    lines.append("")
    lines.append("| Vendor | n | row matches |")
    lines.append("| --- | ---: | ---: |")
    for name, count, matches in report.vendor_breakdown[:20]:
        lines.append(f"| {name} | {count} | {matches} |")
    lines.append("")
    lines.append("## Cost and latency")
    lines.append("")
    if report.mean_cost_usd is not None:
        lines.append(f"- Mean cost (USD): `{report.mean_cost_usd}`")
    else:
        lines.append("- Mean cost (USD): —")
    if report.mean_latency_ms is not None:
        lines.append(f"- Mean latency (ms): `{report.mean_latency_ms:.1f}`")
    else:
        lines.append("- Mean latency (ms): —")
    lines.append("")
    return "\n".join(lines)
