"""Live bill-coding scorecard (U-744): pure scoring of finalized intake predictions.

Input rows come from ReadBillIntakePredictionsForAccuracy (one per BillIntakePrediction,
finalized only, joined to the bill's BillTextCodingSuggestion row). No I/O here.

Scoring, per finalized row:
- code right    = PredSubCostCodeId == FinalSubCostCodeId (scored only when the final is
                  known; a null prediction counts as wrong and as an abstain).
- project right = PredProjectId == FinalProjectId, same rules.
- both right    = both of the above (scored only when both finals are known).

Output carries ids, counts and percentages only - never vendor names, bill numbers or text.
"""
from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

LIVE_CAVEAT = (
    "Live finals are anchored on the prefill: a reviewer who accepts the prefilled line "
    "makes it 'right'. These rates measure acceptance, not independent accuracy, and are "
    "not directly comparable to the backtest."
)

# docs/design/u701b/README.md, run E1 (prod, read-only, 2026-10-28): top-1 sub-cost-code
# over the same 500-bill seeded sample, abstain = wrong, rule "history if strong (>= 0.60),
# else Haiku -> Sonnet below 0.70". Labels are historical finals, not prefill-anchored.
BACKTEST_REFERENCE: dict[str, Any] = {
    "model_version": "u701b-text-v2",
    "mode_a_code_pct": 64.2,
    "mode_b_code_pct": 64.4,
    "n_bills": 500,
    "source": "docs/design/u701b/README.md (E1)",
    "measures": "top-1 sub-cost-code, abstain = wrong, historical labels",
}

HISTORY_RUNG_NOTE = (
    "History's pick is only stored when history won (CodingSource = 'history'); when it "
    "lost it is not persisted, so history is scored on its own wins only. "
    "similar_vote_legacy (pre-V2 rows) is likewise conditional on the vote answering; on "
    "V2 rows the vote runs on every decision, so a missing vote is an abstain. "
    "m0 (U-745) is the M0 decision model's shadow pick, stored on every V2 decision; a "
    "missing pick is an abstain (M0 abstains when the vote has no neighbours, and V2 rows "
    "written before U-745 carry no pick)."
)

LADDER_V2 = "u701b-text-v2"

# Stored rung *Error codes (bill_coding_decision.rung_error_code) that mean the model never
# answered: Claude budget/key gate tokens (claude_budget.pre_call_gate + transport no_key,
# with the "claude_unavailable:" prefix stripped), a skipped rung, any http_* status, and
# "error" (an unclassified provider/transport failure). "unparseable" and "not_validated"
# are a model reply that failed - those stay scored wrong.
_UNAVAILABLE_CODES = frozenset({"daily_cap", "no_key", "credit", "disabled", "skipped", "error"})


def is_availability_error(code: Optional[str]) -> bool:
    return bool(code) and (code in _UNAVAILABLE_CODES or code.startswith("http_"))


BASIS_EVERY_ROW = "every_row_where_ran"
BASIS_EVERY_V2_ROW = "every_v2_row"
BASIS_CONDITIONAL = "conditional_on_answering"

UNSET = "unset"
NO_TEXT_ROW = "no_text_row"

_DB_COLUMNS: tuple[tuple[str, str], ...] = (
    ("prediction_id", "Id"),
    ("bill_id", "BillId"),
    ("intake_source", "IntakeSource"),
    ("model_version", "ModelVersion"),
    ("coding_source", "CodingSource"),
    ("coding_escalation", "CodingEscalation"),
    ("coding_mode", "CodingMode"),
    ("pred_sub_cost_code_id", "PredSubCostCodeId"),
    ("pred_project_id", "PredProjectId"),
    ("final_sub_cost_code_id", "FinalSubCostCodeId"),
    ("final_project_id", "FinalProjectId"),
    ("finalized_datetime", "FinalizedDatetime"),
    ("created_datetime", "CreatedDatetime"),
    ("primary_model", "PrimaryModel"),
    ("primary_sub_cost_code_id", "PrimarySubCostCodeId"),
    ("primary_error", "PrimaryError"),
    ("fallback_model", "FallbackModel"),
    ("fallback_sub_cost_code_id", "FallbackSubCostCodeId"),
    ("similar_pred_sub_cost_code_id", "SimilarPredSubCostCodeId"),
    ("m0_sub_cost_code_id", "M0SubCostCodeId"),
    ("text_decision_source", "TextDecisionSource"),
    ("text_status", "TextStatus"),
    ("text_sub_cost_code_id", "TextSubCostCodeId"),
    ("text_escalate_model", "TextEscalateModel"),
    ("text_escalate_sub_cost_code_id", "TextEscalateSubCostCodeId"),
    ("text_escalate_error", "TextEscalateError"),
)


def scorecard_row_from_db(row: Any) -> dict[str, Any]:
    """Map a ReadBillIntakePredictionsForAccuracy row to the keys this module scores."""
    return {key: getattr(row, col, None) for key, col in _DB_COLUMNS}


def _as_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def filter_since(rows: Iterable[Mapping[str, Any]], since: Optional[date]) -> list[Mapping[str, Any]]:
    """Rows whose prediction was created on/after ``since`` (all rows when None)."""
    if since is None:
        return list(rows)
    out = []
    for row in rows:
        created = _as_date(row.get("created_datetime"))
        if created is not None and created >= since:
            out.append(row)
    return out


def wilson_interval(k: int, n: int, z: float = 1.96) -> Optional[tuple[float, float]]:
    """95% Wilson score interval for k successes in n trials (None when n == 0)."""
    if n <= 0:
        return None
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, center - half), min(1.0, center + half)


def _pct(x: Optional[float]) -> Optional[float]:
    return None if x is None else round(x * 100, 1)


class _Tally:
    __slots__ = ("n", "right", "abstain")

    def __init__(self) -> None:
        self.n = 0
        self.right = 0
        self.abstain = 0

    def add(self, pred: Any, final: Any) -> None:
        if final is None:
            return
        self.n += 1
        if pred is None:
            self.abstain += 1
        elif pred == final:
            self.right += 1

    def as_dict(self) -> dict[str, Any]:
        ci = wilson_interval(self.right, self.n)
        return {
            "n": self.n,
            "right": self.right,
            "abstain": self.abstain,
            "rate_pct": _pct(self.right / self.n) if self.n else None,
            "ci95_low_pct": _pct(ci[0]) if ci else None,
            "ci95_high_pct": _pct(ci[1]) if ci else None,
        }


def _is_scorable(row: Mapping[str, Any]) -> bool:
    return row.get("finalized_datetime") is not None


def _both_pred(row: Mapping[str, Any]) -> Optional[tuple[Any, Any]]:
    """(sub-cost-code, project) prediction pair, or None when either half is missing."""
    ps, pp = row.get("pred_sub_cost_code_id"), row.get("pred_project_id")
    return None if ps is None or pp is None else (ps, pp)


def _both_final(row: Mapping[str, Any]) -> Optional[tuple[Any, Any]]:
    fs, fp = row.get("final_sub_cost_code_id"), row.get("final_project_id")
    return None if fs is None or fp is None else (fs, fp)


def _score(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    code, project, both = _Tally(), _Tally(), _Tally()
    for row in rows:
        code.add(row.get("pred_sub_cost_code_id"), row.get("final_sub_cost_code_id"))
        project.add(row.get("pred_project_id"), row.get("final_project_id"))
        both.add(_both_pred(row), _both_final(row))
    return {"code": code.as_dict(), "project": project.as_dict(), "both": both.as_dict()}


def _group(
    rows: Sequence[Mapping[str, Any]],
    key_fn: Callable[[Mapping[str, Any]], str],
    scorer: Callable[[Sequence[Mapping[str, Any]]], dict[str, Any]] = _score,
) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        buckets.setdefault(key_fn(row), []).append(row)
    return {k: scorer(buckets[k]) for k in sorted(buckets)}


def _key(field: str, missing: str = UNSET) -> Callable[[Mapping[str, Any]], str]:
    def fn(row: Mapping[str, Any]) -> str:
        value = row.get(field)
        return missing if value is None or value == "" else str(value)

    return fn


Attempted = Callable[[Mapping[str, Any]], bool]


def _ran(model_key: str, scc_key: str) -> Attempted:
    """The rung left a model or an answer on the row."""
    return lambda r: r.get(model_key) is not None or r.get(scc_key) is not None


def _is_v2(row: Mapping[str, Any]) -> bool:
    return row.get("model_version") == LADDER_V2


# (rung, attempted(row), rung's sub-cost-code key, rung's error key, basis). An attempted
# rung with no answer is an abstain (wrong) - unless its error code is an availability
# failure, which is excluded from n and counted as "unavailable".
# The V2 ladder runs the filtered similar vote on every decision, so on V2 rows a missing
# vote is an abstain. Legacy rows only stored a vote when it answered (conditional rate).
_RUNGS: tuple[tuple[str, Attempted, str, Optional[str], str], ...] = (
    ("primary", _ran("primary_model", "primary_sub_cost_code_id"),
     "primary_sub_cost_code_id", "primary_error", BASIS_EVERY_ROW),
    ("escalate", _ran("text_escalate_model", "text_escalate_sub_cost_code_id"),
     "text_escalate_sub_cost_code_id", "text_escalate_error", BASIS_EVERY_ROW),
    ("fallback", _ran("fallback_model", "fallback_sub_cost_code_id"),
     "fallback_sub_cost_code_id", None, BASIS_EVERY_ROW),
    ("similar_vote", _is_v2, "similar_pred_sub_cost_code_id", None, BASIS_EVERY_V2_ROW),
    ("m0", _is_v2, "m0_sub_cost_code_id", None, BASIS_EVERY_V2_ROW),
    ("similar_vote_legacy",
     lambda r: not _is_v2(r) and r.get("similar_pred_sub_cost_code_id") is not None,
     "similar_pred_sub_cost_code_id", None, BASIS_CONDITIONAL),
    ("history", lambda r: r.get("coding_source") == "history",
     "pred_sub_cost_code_id", None, BASIS_CONDITIONAL),
    ("text_tick_decision", lambda r: r.get("text_status") == "done",
     "text_sub_cost_code_id", None, BASIS_EVERY_ROW),
)


def _rungs(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    out = {}
    for name, attempted, scc_key, error_key, basis in _RUNGS:
        tally, unavailable = _Tally(), 0
        for row in rows:
            if not attempted(row):
                continue
            pred, final = row.get(scc_key), row.get("final_sub_cost_code_id")
            if (
                pred is None
                and final is not None
                and error_key
                and is_availability_error(row.get(error_key))
            ):
                unavailable += 1
                continue
            tally.add(pred, final)
        out[name] = {**tally.as_dict(), "unavailable": unavailable, "basis": basis}
    return out


def build_coding_scorecard(
    rows: Iterable[Mapping[str, Any]], *, since: Optional[date] = None
) -> dict[str, Any]:
    """JSON-ready scorecard over finalized rows (optionally created on/after ``since``)."""
    scorable = [r for r in filter_since(rows, since) if _is_scorable(r)]
    return {
        "since": since.isoformat() if since else None,
        "n_finalized": len(scorable),
        "overall": _score(scorable),
        "by_model_version": _group(scorable, _key("model_version")),
        "by_coding_source": _group(scorable, _key("coding_source")),
        "by_coding_mode": _group(scorable, _key("coding_mode")),
        "by_intake_source": _group(scorable, _key("intake_source")),
        "by_text_decision_source": _group(scorable, _key("text_decision_source", NO_TEXT_ROW)),
        "rungs": _rungs(scorable),
        "rungs_by_model_version": _group(scorable, _key("model_version"), _rungs),
        "backtest_reference": dict(BACKTEST_REFERENCE),
        "history_rung_note": HISTORY_RUNG_NOTE,
        "caveat": LIVE_CAVEAT,
    }


def _fmt_metric(m: Mapping[str, Any]) -> str:
    if not m["n"]:
        return "— (n=0)"
    return f"{m['rate_pct']:.1f}% ({m['right']}/{m['n']}, CI {m['ci95_low_pct']:.1f}–{m['ci95_high_pct']:.1f})"


def _group_table(title: str, groups: Mapping[str, Mapping[str, Any]]) -> list[str]:
    lines = [f"### {title}", "", "| Group | Code right | Project right | Both right | Abstain (code) |",
             "| --- | ---: | ---: | ---: | ---: |"]
    for key, g in groups.items():
        lines.append(
            f"| {key} | {_fmt_metric(g['code'])} | {_fmt_metric(g['project'])} | "
            f"{_fmt_metric(g['both'])} | {g['code']['abstain']} |"
        )
    lines.append("")
    return lines


def _rung_table(title: str, rungs: Mapping[str, Mapping[str, Any]]) -> list[str]:
    lines = [f"### {title}", "", "| Rung | Code right | Abstain | Unavailable (excluded) | Basis |",
             "| --- | ---: | ---: | ---: | --- |"]
    for name, m in rungs.items():
        basis = m["basis"].replace("_", " ")
        lines.append(f"| {name} | {_fmt_metric(m)} | {m['abstain']} | {m['unavailable']} | {basis} |")
    lines.append("")
    return lines


def format_coding_scorecard_markdown(card: Mapping[str, Any]) -> str:
    ref = card["backtest_reference"]
    lines = ["## Live coding scorecard (U-744)", ""]
    lines.append(f"Finalized predictions scored: **{card['n_finalized']}**"
                 + (f" (created since {card['since']})" if card.get("since") else ""))
    lines.append("")
    lines.append(f"> {card['caveat']}")
    lines.append("")
    lines.append(
        f"Backtest reference ({ref['model_version']}, {ref['source']}): Mode A "
        f"{ref['mode_a_code_pct']}% / Mode B {ref['mode_b_code_pct']}% — {ref['measures']}, "
        f"n={ref['n_bills']}."
    )
    lines.append("")
    lines += _group_table("Overall", {"all": card["overall"]})
    lines += _group_table("By model version", card["by_model_version"])
    lines += _group_table("By coding source (intake)", card["by_coding_source"])
    lines += _group_table("By coding mode", card["by_coding_mode"])
    lines += _group_table("By intake source", card["by_intake_source"])
    lines += _group_table("By text-tier decision source", card["by_text_decision_source"])
    lines += _rung_table("Per-rung counterfactuals (code right, where the rung ran)", card["rungs"])
    for version, rungs in card["rungs_by_model_version"].items():
        lines += _rung_table(f"Per-rung counterfactuals — {version}", rungs)
    lines.append(f"_{card['history_rung_note']}_")
    lines.append("")
    return "\n".join(lines)
