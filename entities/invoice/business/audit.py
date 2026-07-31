"""Invoice Phase-1 draw audit (U-178) — read-only gap report."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

from entities.invoice.business.reconciliation import (
    InvoiceReconciliationService,
    _LINKED_SOURCE_TYPES,
)
from entities.invoice.business.service import InvoiceService
from entities.invoice.persistence.repo import InvoiceRepository
from entities.project.business.service import ProjectService
from integrations.box.excel.persistence.repo import BoxProjectWorkbookRepository
from integrations.box.folder.persistence.repo import BoxProjectFolderRepository
from integrations.intuit.qbo.customer.connector.project.persistence.repo import (
    CustomerProjectRepository,
)
from integrations.intuit.qbo.customer.persistence.repo import QboCustomerRepository
from integrations.sync.persistence.repo import SyncRepository, _parse_sync_last_sync
_OK_LINK_STATUSES = frozenset({"linkable", "already_linked"})
_QBO_PULL_ENTITIES = ("bill", "invoice", "purchase", "vendorcredit")
_STALE_AFTER = timedelta(minutes=30)
_AMOUNT_TOLERANCE = Decimal("0.01")
_WORKSHEET_RECONCILE_STUB = {
    "included": False,
    "hint": "GET /reconcile (Step 6) — folded into U3",
}


def _resolved_source_bindings(link_lines: list[dict]) -> list[dict]:
    """Proposed sources for linkable / already_linked lines (dry-run; FKs not on ILI yet)."""
    out: list[dict] = []
    for row in link_lines:
        status = row.get("status")
        if status not in _OK_LINK_STATUSES:
            continue
        prop = row.get("proposed") or {}
        st = prop.get("source_type")
        sid = prop.get("source_line_item_id")
        if not st or sid is None:
            continue
        if st not in _LINKED_SOURCE_TYPES:
            continue
        out.append(
            {
                "invoice_line_item_id": row["invoice_line_item_id"],
                "source_type": st,
                "source_line_item_id": int(sid),
            }
        )
    return out


def _coverage_row_for_binding(
    binding: dict,
    coverage_map: dict[tuple[str, int], dict],
) -> dict:
    key = (binding["source_type"], binding["source_line_item_id"])
    cov = coverage_map.get(key)
    if cov is None:
        attachment_count = 0
        sub_cost_code_id = None
    else:
        attachment_count = int(cov.get("attachment_count") or 0)
        sub_cost_code_id = cov.get("sub_cost_code_id")
    return {
        "invoice_line_item_id": binding["invoice_line_item_id"],
        "source_type": binding["source_type"],
        "source_line_item_id": binding["source_line_item_id"],
        "attachment_count": attachment_count,
        "sub_cost_code_id": sub_cost_code_id,
    }


def compute_coverage_gaps(
    resolved_bindings: list[dict],
    coverage_map: dict[tuple[str, int], dict],
) -> list[dict]:
    """Gap when attachment or SubCostCode missing on the resolved source line."""
    rows = [_coverage_row_for_binding(b, coverage_map) for b in resolved_bindings]
    return [
        r
        for r in rows
        if r["attachment_count"] < 1 or r["sub_cost_code_id"] is None
    ]


def detect_double_bill_pairs(lines: list[dict]) -> list[dict]:
    """
    PARTIAL first-pass screen (KI-38 / KI-41): same amount (±0.01),
    overlapping/adjacent dates, different vendor or cross-type — not the same
    underlying source line. Full attachment-text vendor-invoice-number
    cross-compare (KI-41) and multi-page attachment leak (KI-40) deferred to U5.
    """
    sourced = [_normalize_double_bill_line(row) for row in lines]
    sourced = [row for row in sourced if row is not None]
    pairs: list[dict] = []
    for i, left in enumerate(sourced):
        for right in sourced[i + 1 :]:
            if _same_source_identity(left, right):
                continue
            if not _amounts_within_tolerance(left["amount"], right["amount"]):
                continue
            if not _dates_overlap_or_adjacent(left.get("service_date"), right.get("service_date")):
                continue
            if not _different_source_identity(left, right):
                continue
            pairs.append(
                {
                    "line_a": {
                        "invoice_line_item_id": left["invoice_line_item_id"],
                        "source_type": left["source_type"],
                        "source_line_item_id": left["source_line_item_id"],
                        "vendor_id": left.get("vendor_id"),
                        "amount": str(left["amount"]),
                        "service_date": left.get("service_date"),
                    },
                    "line_b": {
                        "invoice_line_item_id": right["invoice_line_item_id"],
                        "source_type": right["source_type"],
                        "source_line_item_id": right["source_line_item_id"],
                        "vendor_id": right.get("vendor_id"),
                        "amount": str(right["amount"]),
                        "service_date": right.get("service_date"),
                    },
                }
            )
    return pairs


def assemble_audit_report(sections: dict) -> dict:
    """Collapse section flags into verdict + gap list (Phase 1 halt-once report)."""
    gaps: list[dict] = []

    if not sections.get("qbo_mapping_present"):
        gaps.append(
            {
                "class": "missing_qbo_mapping",
                "severity": "halt",
                "message": "Project has no qbo.CustomerProject mapping",
            }
        )

    for dup in sections.get("duplicate_projects") or []:
        gaps.append(
            {
                "class": "duplicate_project",
                "severity": "halt",
                "project_id": dup.get("id"),
                "name": dup.get("name"),
                "qbo_mappings": dup.get("qbo_mappings"),
            }
        )

    for stale in sections.get("staging_stale") or []:
        gaps.append(
            {
                "class": "stale_staging",
                "severity": "halt",
                "entity": stale.get("entity"),
                "last_sync_datetime": stale.get("last_sync_datetime"),
                "age_minutes": stale.get("age_minutes"),
            }
        )

    for row in sections.get("link_lines") or []:
        status = row.get("status")
        if status not in _OK_LINK_STATUSES:
            gaps.append(
                {
                    "class": "source_link",
                    "severity": "halt",
                    "invoice_line_item_id": row.get("invoice_line_item_id"),
                    "status": status,
                    "reject_reason": row.get("reject_reason"),
                }
            )

    for row in sections.get("coverage_gaps") or []:
        gaps.append(
            {
                "class": "coverage",
                "severity": "halt",
                "invoice_line_item_id": row.get("invoice_line_item_id"),
                "source_type": row.get("source_type"),
                "source_line_item_id": row.get("source_line_item_id"),
                "attachment_count": row.get("attachment_count"),
                "sub_cost_code_id": row.get("sub_cost_code_id"),
            }
        )

    for pair in sections.get("double_bill_pairs") or []:
        gaps.append(
            {
                "class": "double_bill_pair",
                "severity": "halt",
                "line_a": pair.get("line_a"),
                "line_b": pair.get("line_b"),
            }
        )

    verdict = "halt" if gaps else "clear"
    return {
        "verdict": verdict,
        "gaps": gaps,
        "lines": sections.get("lines_detail") or [],
    }


class InvoiceDrawAuditService:
    """Read-only Phase-1 audit composing existing readers into one gap report."""

    def __init__(
        self,
        invoice_service: Optional[InvoiceService] = None,
        invoice_repo: Optional[InvoiceRepository] = None,
        reconciliation_service: Optional[InvoiceReconciliationService] = None,
        project_service: Optional[ProjectService] = None,
        customer_project_repo: Optional[CustomerProjectRepository] = None,
        qbo_customer_repo: Optional[QboCustomerRepository] = None,
        sync_repo: Optional[SyncRepository] = None,
        box_workbook_repo: Optional[BoxProjectWorkbookRepository] = None,
        box_folder_repo: Optional[BoxProjectFolderRepository] = None,
    ):
        self.invoice_service = invoice_service or InvoiceService()
        self.invoice_repo = invoice_repo or InvoiceRepository()
        self.reconciliation_service = reconciliation_service or InvoiceReconciliationService(
            invoice_repo=self.invoice_repo,
            invoice_service=self.invoice_service,
        )
        self.project_service = project_service or ProjectService()
        self.customer_project_repo = customer_project_repo or CustomerProjectRepository()
        self.qbo_customer_repo = qbo_customer_repo or QboCustomerRepository()
        self.sync_repo = sync_repo or SyncRepository()
        self.box_workbook_repo = box_workbook_repo or BoxProjectWorkbookRepository()
        self.box_folder_repo = box_folder_repo or BoxProjectFolderRepository()

    def audit(self, invoice_public_id: str) -> dict:
        invoice = self.invoice_service.read_by_public_id(invoice_public_id)
        if not invoice:
            raise ValueError("invoice_not_found")

        project = None
        if invoice.project_id is not None:
            project = self.project_service.read_by_id(invoice.project_id)

        qbo_mapping = self._read_qbo_mapping(invoice.project_id)
        duplicate_projects = self._read_duplicate_projects(invoice.project_id)
        box_mappings = self._read_box_mappings(invoice.project_id)
        staging = self._read_staging_freshness()

        link_payload = self.reconciliation_service.propose_links(invoice_public_id)
        resolved_bindings = _resolved_source_bindings(link_payload["lines"])
        binding_keys = [
            (b["source_type"], b["source_line_item_id"]) for b in resolved_bindings
        ]
        coverage_map = self.invoice_repo.read_source_line_coverage(binding_keys)
        coverage_gaps = compute_coverage_gaps(resolved_bindings, coverage_map)
        coverage_rows = _coverage_rows_from_bindings(resolved_bindings, coverage_map)

        double_bill_lines = self._build_double_bill_lines(
            link_payload["lines"], resolved_bindings
        )
        double_bill_pairs = detect_double_bill_pairs(double_bill_lines)

        lines_detail = _merge_line_details(link_payload["lines"], coverage_rows)

        sections = {
            "qbo_mapping_present": qbo_mapping.get("present", False),
            "duplicate_projects": duplicate_projects,
            "staging_stale": staging.get("stale") or [],
            "link_lines": link_payload["lines"],
            "coverage_gaps": coverage_gaps,
            "double_bill_pairs": double_bill_pairs,
            "lines_detail": lines_detail,
        }
        summary = assemble_audit_report(sections)

        return {
            **summary,
            "invoice_public_id": str(invoice.public_id),
            "invoice_id": invoice.id,
            "invoice_number": invoice.invoice_number,
            "project": _project_dict(project),
            "qbo_mapping": qbo_mapping,
            "duplicate_projects": duplicate_projects,
            "box_mappings": box_mappings,
            "staging_freshness": staging,
            "source_links": {
                "lines": link_payload["lines"],
                "summary": link_payload["summary"],
            },
            "coverage": coverage_rows,
            "double_bill_pairs": double_bill_pairs,
            "worksheet_reconcile": dict(_WORKSHEET_RECONCILE_STUB),
        }

    def _read_qbo_mapping(self, project_id: Optional[int]) -> dict:
        if project_id is None:
            return {"present": False, "mapping": None, "customer": None}
        mapping = self.customer_project_repo.read_by_project_id(project_id)
        if not mapping:
            return {"present": False, "mapping": None, "customer": None}
        customer = self.qbo_customer_repo.read_by_id(mapping.qbo_customer_id)
        return {
            "present": True,
            "mapping": {
                "id": mapping.id,
                "project_id": mapping.project_id,
                "qbo_customer_id": mapping.qbo_customer_id,
            },
            "customer": (
                {
                    "qbo_id": customer.qbo_id,
                    "realm_id": customer.realm_id,
                    "display_name": customer.display_name,
                }
                if customer
                else None
            ),
        }

    def _read_duplicate_projects(self, project_id: Optional[int]) -> list[dict]:
        if project_id is None:
            return []
        rows = self.invoice_repo.read_duplicate_projects_by_project_id(project_id)
        return [
            {
                "id": row.Id,
                "name": row.Name,
                "abbreviation": getattr(row, "Abbreviation", None),
                "created_datetime": getattr(row, "CreatedDatetime", None),
                "qbo_mappings": int(getattr(row, "QboMappings", 0) or 0),
            }
            for row in rows
        ]

    def _read_box_mappings(self, project_id: Optional[int]) -> dict:
        if project_id is None:
            return {
                "workbook": None,
                "folders": {"invoices": None, "draw_requests": None},
            }
        workbook = self.box_workbook_repo.read_by_project_id(project_id)
        invoices_folder = self.box_folder_repo.read_by_project_id_and_doc_class(
            project_id, "invoices"
        )
        draw_folder = self.box_folder_repo.read_by_project_id_and_doc_class(
            project_id, "draw_requests"
        )
        return {
            "workbook": workbook,
            "folders": {
                "invoices": invoices_folder,
                "draw_requests": draw_folder,
            },
        }

    def _read_staging_freshness(self) -> dict:
        now = datetime.now(timezone.utc)
        records = self.sync_repo.read_qbo_pull_watermarks()
        by_entity = {(r.entity or "").lower(): r for r in records if r}
        rows: list[dict] = []
        stale: list[dict] = []
        for entity in _QBO_PULL_ENTITIES:
            rec = by_entity.get(entity)
            last_raw = rec.last_sync_datetime if rec else None
            parsed = _parse_sync_last_sync(last_raw)
            age_minutes: Optional[float] = None
            is_stale = True
            if parsed is not None:
                age_minutes = (now - parsed).total_seconds() / 60.0
                is_stale = age_minutes > _STALE_AFTER.total_seconds() / 60.0
            row = {
                "entity": entity,
                "last_sync_datetime": last_raw,
                "age_minutes": round(age_minutes, 1) if age_minutes is not None else None,
                "stale": is_stale,
            }
            rows.append(row)
            if is_stale:
                stale.append(row)
        return {"entities": rows, "stale": stale}

    def _build_double_bill_lines(
        self,
        link_lines: list[dict],
        resolved_bindings: list[dict],
    ) -> list[dict]:
        bindings = [
            (b["source_type"], b["source_line_item_id"]) for b in resolved_bindings
        ]
        by_ili = {row["invoice_line_item_id"]: row for row in link_lines}
        vendor_map = self.invoice_repo.read_vendor_ids_for_source_lines(bindings)
        draft: list[dict] = []
        for b in resolved_bindings:
            link_row = by_ili.get(b["invoice_line_item_id"], {})
            key = (b["source_type"], b["source_line_item_id"])
            draft.append(
                {
                    "invoice_line_item_id": b["invoice_line_item_id"],
                    "amount": link_row.get("amount"),
                    "service_date": link_row.get("service_date"),
                    "source_type": b["source_type"],
                    "source_line_item_id": b["source_line_item_id"],
                    "vendor_id": vendor_map.get(key),
                }
            )
        return draft


def _coverage_rows_from_bindings(
    resolved_bindings: list[dict],
    coverage_map: dict[tuple[str, int], dict],
) -> list[dict]:
    return [_coverage_row_for_binding(b, coverage_map) for b in resolved_bindings]


def _merge_line_details(
    link_lines: list[dict],
    coverage_rows: list[dict],
) -> list[dict]:
    coverage_by_ili = {r["invoice_line_item_id"]: r for r in coverage_rows}
    merged: list[dict] = []
    for row in link_lines:
        ili_id = row["invoice_line_item_id"]
        cov = coverage_by_ili.get(ili_id)
        merged.append(
            {
                "invoice_line_item_id": ili_id,
                "line_num": row.get("line_num"),
                "amount": str(row["amount"]) if row.get("amount") is not None else None,
                "description": row.get("description"),
                "service_date": row.get("service_date"),
                "link_status": row.get("status"),
                "link_proposed": row.get("proposed"),
                "coverage": cov,
            }
        )
    return merged


def _project_dict(project: Any) -> Optional[dict]:
    if not project:
        return None
    return {
        "id": project.id,
        "public_id": str(project.public_id) if project.public_id else None,
        "name": project.name,
        "abbreviation": getattr(project, "abbreviation", None),
    }


def _normalize_double_bill_line(row: dict) -> Optional[dict]:
    st = row.get("source_type")
    sid = row.get("source_line_item_id")
    if st not in _LINKED_SOURCE_TYPES or sid is None:
        return None
    amount = row.get("amount")
    if amount is None:
        return None
    return {
        "invoice_line_item_id": row["invoice_line_item_id"],
        "amount": Decimal(str(amount)),
        "service_date": row.get("service_date"),
        "source_type": st,
        "source_line_item_id": int(sid),
        "vendor_id": row.get("vendor_id"),
    }


def _same_source_identity(left: dict, right: dict) -> bool:
    return (
        left["source_type"] == right["source_type"]
        and left["source_line_item_id"] == right["source_line_item_id"]
    )


def _different_source_identity(left: dict, right: dict) -> bool:
    if left["source_type"] != right["source_type"]:
        return True
    lv, rv = left.get("vendor_id"), right.get("vendor_id")
    if lv is not None and rv is not None and int(lv) != int(rv):
        return True
    return False


def _amounts_within_tolerance(a: Decimal, b: Decimal) -> bool:
    return abs(a - b) <= _AMOUNT_TOLERANCE


def _dates_overlap_or_adjacent(
    left: Optional[str],
    right: Optional[str],
) -> bool:
    dl = _parse_service_date(left)
    dr = _parse_service_date(right)
    if dl is None or dr is None:
        return False
    return abs((dl - dr).days) <= 1


def _parse_service_date(value: Optional[str]) -> Optional[datetime]:
    if value is None or value == "":
        return None
    text = str(value)[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None

