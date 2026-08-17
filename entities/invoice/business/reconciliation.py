"""Invoice source-linking (playbook Step 4 / U-177).

Ambiguous-match policy (U-244): resolve_link_proposals never auto-applies a guessed
link. The only tie-break it performs is one bounded positional rule (see the inline
comments in the group-resolution loop below) -- anything that doesn't come out a
clean unique match under that rule is status="ambiguous", proposed=None. The
dominant real-world ambiguous case (same-day multi-worker crew lines sharing an
identical fingerprint) is not guaranteed to resolve via that rule and remains a
known, accepted limit of this fingerprint family -- see
docs/rc_source_linking_signal_2026_08_16.md Section 2, which also motivates the
ItemRef tightening added to ProposeInvoiceSourceLinks by this same unit.

KI-35 unmapped candidates: a fingerprint match against a qbo.BillLine/PurchaseLine
row is only surfaced as a Tier-1/Tier-2 candidate when that row has a
BillLineItemBillLine/PurchaseLineExpenseLineItem mapping (ProposeInvoiceSourceLinks
requires this via INNER JOIN) -- an unmapped qbo row is never itself surfaced, and
this decision engine does not attempt to repair or backfill that mapping (KI-35
precedent: a separate scoped sync_qbo_bill.py-style re-run is the recovery path).
See _filter_candidates_ki35 for the separate DirectDbo fallback, which proposes a
distinct local match rather than repairing this one.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Optional

from entities.invoice.business.service import InvoiceService
from entities.invoice.persistence.repo import InvoiceRepository
from entities.invoice_line_item.persistence.repo import InvoiceLineItemRepository

logger = logging.getLogger(__name__)

_LINKED_SOURCE_TYPES = frozenset(
    {"BillLineItem", "ExpenseLineItem", "BillCreditLineItem"}
)


def _is_already_linked(line: dict) -> bool:
    st = line.get("source_type") or ""
    if st not in _LINKED_SOURCE_TYPES:
        return False
    if st == "BillLineItem":
        return line.get("bill_line_item_id") is not None
    if st == "ExpenseLineItem":
        return line.get("expense_line_item_id") is not None
    if st == "BillCreditLineItem":
        return line.get("bill_credit_line_item_id") is not None
    return False


def _existing_link_proposed(line: dict) -> dict:
    st = line["source_type"]
    if st == "BillLineItem":
        sid = line["bill_line_item_id"]
    elif st == "ExpenseLineItem":
        sid = line["expense_line_item_id"]
    else:
        sid = line["bill_credit_line_item_id"]
    return {
        "source_type": st,
        "source_line_item_id": sid,
        "source_project_id": line.get("source_project_id"),
        "tier": line.get("existing_tier"),
    }


def _filter_candidates_ki35(
    candidates: list[dict],
) -> list[dict]:
    """KI-35: direct-dbo Bill fallback only when no qbo staging match exists.

    See module docstring for why unmapped qbo staging rows never reach this filter.
    """
    staging = [c for c in candidates if not c.get("direct_dbo")]
    if staging:
        return staging
    return [c for c in candidates if c.get("direct_dbo")]


def _apply_cross_project_guard(
    candidates: list[dict],
    invoice_project_id: Optional[int],
) -> tuple[list[dict], list[dict]]:
    """KI-37: reject hits coded to a different project (when ProjectId is known)."""
    accepted: list[dict] = []
    rejected: list[dict] = []
    for c in candidates:
        sp = c.get("source_project_id")
        if (
            sp is not None
            and invoice_project_id is not None
            and int(sp) != int(invoice_project_id)
        ):
            rejected.append(c)
        else:
            accepted.append(c)
    return accepted, rejected


def _service_date_missing(line: dict) -> bool:
    sd = line.get("service_date")
    return sd is None or sd == ""


def resolve_link_proposals(
    lines: list[dict],
    candidates: list[dict],
    invoice_project_id: Optional[int],
) -> list[dict]:
    """
    Pure decision engine for invoice line source linkage.

    Each input line dict expects: invoice_line_item_id, line_num, amount, description,
    service_date, source_type, bill_line_item_id, expense_line_item_id,
    bill_credit_line_item_id, linked_txn_type (optional).

    Each candidate dict expects: invoice_line_item_id, tier (0-3), source_type,
    source_line_item_id, source_project_id, direct_dbo (bool, optional),
    source_line_num (optional, for tiebreak).
    """
    by_line: dict[int, list[dict]] = {}
    for c in candidates:
        ili_id = c["invoice_line_item_id"]
        by_line.setdefault(ili_id, []).append(c)

    outcome: dict[int, dict] = {}
    pending: list[tuple[dict, dict, list[dict]]] = []

    for line in lines:
        ili_id = line["invoice_line_item_id"]
        base = {
            "invoice_line_item_id": ili_id,
            "line_num": line.get("line_num"),
            "amount": line.get("amount"),
            "description": line.get("description"),
            "service_date": line.get("service_date"),
        }

        if line.get("source_type") == "EmployeeLaborLineItem":
            outcome[ili_id] = {
                **base,
                "status": "no_match",
                "proposed": None,
                "reject_reason": "employee_labor_excluded",
            }
            continue

        if _is_already_linked(line):
            outcome[ili_id] = {
                **base,
                "status": "already_linked",
                "proposed": _existing_link_proposed(line),
                "reject_reason": None,
            }
            continue

        if line.get("linked_txn_type") == "ReimburseCharge" and line.get("manual_derivative"):
            outcome[ili_id] = {
                **base,
                "status": "manual_derivative_candidate",
                "proposed": None,
                "reject_reason": None,
            }
            continue

        raw = by_line.get(ili_id, [])
        filtered = _filter_candidates_ki35(raw)
        accepted, cross_rejected = _apply_cross_project_guard(filtered, invoice_project_id)

        if cross_rejected and not accepted:
            outcome[ili_id] = {
                **base,
                "status": "cross_project_rejected",
                "proposed": None,
                "reject_reason": "source_project_mismatch",
            }
            continue

        if not accepted:
            reject_reason = (
                "missing_service_date" if _service_date_missing(line) else None
            )
            outcome[ili_id] = {
                **base,
                "status": "no_match",
                "proposed": None,
                "reject_reason": reject_reason,
            }
            continue

        pending.append((line, base, accepted))

    def _best_tier_source_key_set(accepted: list[dict]) -> frozenset[tuple[str, int]]:
        best_tier = min(c["tier"] for c in accepted)
        return frozenset(
            (c["source_type"], c["source_line_item_id"])
            for c in accepted
            if c["tier"] == best_tier
        )

    groups: dict[frozenset[tuple[str, int]], list[tuple[dict, dict, list[dict]]]] = {}
    for item in pending:
        _line, _base, accepted = item
        key_set = _best_tier_source_key_set(accepted)
        groups.setdefault(key_set, []).append(item)

    used_sources: set[tuple[str, int]] = set()

    for source_key_set, group_items in groups.items():
        group_items.sort(
            key=lambda item: (
                item[0].get("line_num") or 0,
                item[0]["invoice_line_item_id"],
            )
        )

        best_tier = min(c["tier"] for _l, _b, acc in group_items for c in acc)
        tier_pool: list[dict] = []
        for _line, _base, accepted in group_items:
            tier_pool.extend(c for c in accepted if c["tier"] == best_tier)

        by_source_key: dict[tuple[str, int], dict] = {}
        for c in tier_pool:
            sk = (c["source_type"], c["source_line_item_id"])
            if sk not in by_source_key:
                by_source_key[sk] = c

        ordered_sources = sorted(
            by_source_key.values(),
            key=lambda c: (
                c.get("source_line_num") or 0,
                c["source_type"],
                c["source_line_item_id"],
            ),
        )

        n_lines = len(group_items)
        n_sources = len(ordered_sources)

        # The one bounded positional tie-break (U-244 module docstring): only when
        # this group's line count exactly matches its distinct-source count do we
        # pair them up, by sorting both sides on LineNum and matching index-for-index.
        if n_lines == n_sources:
            for i, (line, base, _accepted) in enumerate(group_items):
                chosen = ordered_sources[i]
                source_key = (chosen["source_type"], chosen["source_line_item_id"])
                ili_id = line["invoice_line_item_id"]
                # A source already claimed by another group's pairing this call is a
                # cross-group collision -- never double-assign the same source line.
                if source_key in used_sources:
                    outcome[ili_id] = {
                        **base,
                        "status": "ambiguous",
                        "proposed": None,
                        "reject_reason": "multiple_matches",
                    }
                else:
                    used_sources.add(source_key)
                    outcome[ili_id] = {
                        **base,
                        "status": "linkable",
                        "proposed": {
                            "source_type": chosen["source_type"],
                            "source_line_item_id": chosen["source_line_item_id"],
                            "source_project_id": chosen.get("source_project_id"),
                            "tier": chosen["tier"],
                        },
                        "reject_reason": None,
                    }
        else:
            # Line count != source count (e.g. two crew lines sharing one source, or
            # one line tied between two same-tier sources) -- no positional rule
            # applies, so every line in the group is ambiguous.
            for line, base, _accepted in group_items:
                outcome[line["invoice_line_item_id"]] = {
                    **base,
                    "status": "ambiguous",
                    "proposed": None,
                    "reject_reason": "multiple_matches",
                }

    return [outcome[line["invoice_line_item_id"]] for line in lines]



class InvoiceReconciliationService:
    """Propose and apply invoice line → source line links (Step 4)."""

    def __init__(
        self,
        invoice_repo: Optional[InvoiceRepository] = None,
        invoice_line_item_repo: Optional[InvoiceLineItemRepository] = None,
        invoice_service: Optional[InvoiceService] = None,
    ):
        self.invoice_repo = invoice_repo or InvoiceRepository()
        self.invoice_line_item_repo = invoice_line_item_repo or InvoiceLineItemRepository()
        self.invoice_service = invoice_service or InvoiceService()

    def propose_links(self, invoice_public_id: str) -> dict:
        """Dry-run: fingerprint matches + statuses; no writes."""
        invoice = self.invoice_service.read_by_public_id(invoice_public_id)
        if not invoice:
            raise ValueError("invoice_not_found")

        line_rows = self.invoice_repo.read_source_link_lines(invoice.id)
        candidate_rows = self.invoice_repo.propose_invoice_source_links(invoice.id)

        lines = [_map_line_row(r) for r in line_rows]
        candidates = [_map_candidate_row(r) for r in candidate_rows]
        proposals = resolve_link_proposals(lines, candidates, invoice.project_id)

        return {
            "invoice_public_id": str(invoice.public_id),
            "invoice_id": invoice.id,
            "project_id": invoice.project_id,
            "lines": proposals,
            "summary": _summarize(proposals),
        }

    def apply_links(
        self,
        invoice_public_id: str,
        only_line_ids: Optional[list[int]] = None,
    ) -> dict:
        """Apply linkable proposals idempotently; does not mark IsBilled or call QBO."""
        proposal_payload = self.propose_links(invoice_public_id)
        project_id = proposal_payload["project_id"]

        applied: list[dict] = []
        skipped: list[dict] = []

        for row in proposal_payload["lines"]:
            ili_id = row["invoice_line_item_id"]
            if only_line_ids is not None and ili_id not in only_line_ids:
                continue

            status = row["status"]
            if status == "already_linked":
                skipped.append({**row, "apply_action": "already_linked"})
                continue
            if status != "linkable":
                skipped.append({**row, "apply_action": "skipped"})
                continue

            prop = row["proposed"]
            assert prop is not None
            # Tier-0 (direct LinkedTxn -> staged Bill/Purchase, U-186) applies through
            # the same link path as the fingerprint tiers. The RC-mediated Tier-0 arms
            # were removed from ProposeInvoiceSourceLinks (U-244) — see
            # docs/rc_source_linking_signal_2026_08_16.md.
            source_type = prop["source_type"]
            bli = eli = bcli = None
            if source_type == "BillLineItem":
                bli = prop["source_line_item_id"]
            elif source_type == "ExpenseLineItem":
                eli = prop["source_line_item_id"]
            elif source_type == "BillCreditLineItem":
                bcli = prop["source_line_item_id"]
            else:
                skipped.append({**row, "apply_action": "unsupported_source_type"})
                continue

            self.invoice_line_item_repo.link_invoice_line_item_source(
                invoice_line_item_id=ili_id,
                source_type=source_type,
                bill_line_item_id=bli,
                expense_line_item_id=eli,
                bill_credit_line_item_id=bcli,
            )

            if project_id is not None:
                self.invoice_repo.backfill_linked_source_project_id(
                    source_type=source_type,
                    source_line_item_id=prop["source_line_item_id"],
                    project_id=project_id,
                )

            applied.append({**row, "apply_action": "linked"})

        return {
            "invoice_public_id": proposal_payload["invoice_public_id"],
            "applied": applied,
            "skipped": skipped,
            "summary": {
                "applied_count": len(applied),
                "skipped_count": len(skipped),
            },
        }


def _map_line_row(row: Any) -> dict:
    amount = getattr(row, "Amount", None)
    if amount is not None:
        amount = Decimal(str(amount))
    return {
        "invoice_line_item_id": row.InvoiceLineItemId,
        "line_num": getattr(row, "LineNum", None),
        "amount": amount,
        "description": getattr(row, "Description", None),
        "service_date": getattr(row, "ServiceDate", None),
        "source_type": getattr(row, "SourceType", None),
        "bill_line_item_id": getattr(row, "BillLineItemId", None),
        "expense_line_item_id": getattr(row, "ExpenseLineItemId", None),
        "bill_credit_line_item_id": getattr(row, "BillCreditLineItemId", None),
        "source_project_id": getattr(row, "SourceProjectId", None),
        "linked_txn_type": getattr(row, "LinkedTxnType", None),
        "manual_derivative": bool(getattr(row, "ManualDerivative", False)),
        "existing_tier": getattr(row, "ExistingTier", None),
    }


def _map_candidate_row(row: Any) -> dict:
    direct = bool(getattr(row, "DirectDbo", False))
    return {
        "invoice_line_item_id": row.InvoiceLineItemId,
        "tier": int(row.Tier),
        "source_type": row.SourceType,
        "source_line_item_id": row.SourceLineItemId,
        "source_project_id": getattr(row, "SourceProjectId", None),
        "direct_dbo": direct,
        "source_line_num": getattr(row, "SourceLineNum", None),
    }


def _summarize(proposals: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for p in proposals:
        st = p["status"]
        counts[st] = counts.get(st, 0) + 1
    return counts
