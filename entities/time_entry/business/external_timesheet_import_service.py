# Python Standard Library Imports
import csv
import io
import logging
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Optional

# Local Imports
from entities.time_entry.business.bulk_import_service import (
    TimeEntryBulkImportService,
)

logger = logging.getLogger(__name__)


REQUIRED_COLUMNS = ("worker_firstname", "worker_lastname", "work_date", "hours")
OPTIONAL_COLUMNS = ("project_name", "note")


class ExternalTimesheetImportService:
    """Phase 6c — CSV → TimeEntry/TimeLog bulk loader.

    Parses a third-party timesheet CSV (format defined in
    scripts/samples/README.md), pre-aggregates per (Worker × Project × Day)
    to dodge the Phase 4 aggregation sproc's same-key overwrite, resolves
    project names to public_ids in a single pass, and hands the rows to
    TimeEntryBulkImportService.import_rows() — which fires Phase 4
    aggregation on submit, routing to ContractLabor or EmployeeLabor
    based on User.VendorId vs User.EmployeeId.

    Run via admin endpoint OR offline CLI (scripts/import_external_timesheet.py).
    """

    def __init__(self):
        self.bulk_service = TimeEntryBulkImportService()

    # ─── parse + validate ─────────────────────────────────────────────

    def _read_csv(self, file_content: bytes) -> tuple[list[dict], list[str]]:
        """Decode + parse the CSV. Strips BOM (Excel-exported CSVs often
        prefix with \\ufeff). Returns (rows, header_columns).
        """
        text = file_content.decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        headers = [h.strip() for h in (reader.fieldnames or [])]
        rows = []
        for raw in reader:
            # Strip whitespace from every cell, normalize empty strings → None.
            cleaned = {}
            for k, v in raw.items():
                if k is None:
                    continue
                key = k.strip()
                if isinstance(v, str):
                    val = v.strip()
                    cleaned[key] = val if val else None
                else:
                    cleaned[key] = v
            rows.append(cleaned)
        return rows, headers

    def _validate_headers(self, headers: list[str]) -> Optional[str]:
        """Returns an error message if required headers are missing, else None."""
        seen = {h.lower() for h in headers}
        missing = [c for c in REQUIRED_COLUMNS if c not in seen]
        if missing:
            return (
                f"CSV is missing required column(s): {', '.join(missing)}. "
                f"Required: {', '.join(REQUIRED_COLUMNS)}. "
                f"Optional: {', '.join(OPTIONAL_COLUMNS)}."
            )
        return None

    # ─── pre-aggregation ──────────────────────────────────────────────

    def _aggregation_key(self, row: dict) -> tuple:
        """Per-row natural key — must match Phase 4 sproc's (Worker,
        Project, Day) grouping. Project is normalized to lowercase string
        so case-only differences ("HP - 6135" vs "hp - 6135") collapse to
        the same row.
        """
        return (
            (row.get("worker_firstname") or "").strip().lower(),
            (row.get("worker_lastname") or "").strip().lower(),
            (row.get("project_name") or "").strip().lower(),
            (row.get("work_date") or "").strip(),
        )

    @staticmethod
    def _coerce_hours(value) -> Decimal:
        if value is None or value == "":
            raise ValueError("hours is required")
        try:
            d = Decimal(str(value))
        except (InvalidOperation, ValueError) as e:
            raise ValueError(f"hours must be a number (got {value!r})") from e
        if d <= 0:
            raise ValueError(f"hours must be positive (got {d})")
        return d

    def _pre_aggregate(self, rows: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
        """Collapses CSV rows that share a (Worker, Project, Day) key.

        Per the Phase 4 aggregation-sproc constraint (each TimeEntry's
        aggregation OVERWRITES the labor row on natural key — see
        scripts/samples/README.md), we sum hours and concat non-empty
        notes BEFORE creating TimeEntries.

        Returns (aggregated_rows, skipped_rows, merge_groups) where:
          aggregated_rows: ready for the bulk service
          skipped_rows: per-row failures during validation (bad hours,
                        missing fields) with row_index + reason
          merge_groups: keys that had >1 source row, surfaced in the
                        response so the operator sees what got combined
        """
        groups: dict[tuple, dict] = {}
        skipped: list[dict] = []

        for idx, row in enumerate(rows):
            # Skip blank rows (DictReader sometimes emits all-None for trailing newlines).
            if not any((v not in (None, "")) for v in row.values()):
                continue

            firstname = (row.get("worker_firstname") or "").strip()
            lastname = (row.get("worker_lastname") or "").strip()
            work_date = (row.get("work_date") or "").strip()
            if not firstname or not lastname:
                skipped.append({"row_index": idx, "reason": "missing worker_firstname or worker_lastname"})
                continue
            if not work_date:
                skipped.append({"row_index": idx, "reason": "missing work_date"})
                continue

            try:
                hours = self._coerce_hours(row.get("hours"))
            except ValueError as e:
                skipped.append({"row_index": idx, "reason": str(e)})
                continue

            key = self._aggregation_key(row)
            project_name = (row.get("project_name") or "").strip() or None
            note = (row.get("note") or "").strip() or None

            existing = groups.get(key)
            if existing is None:
                groups[key] = {
                    "worker_firstname": firstname,
                    "worker_lastname": lastname,
                    "project_name": project_name,
                    "work_date": work_date,
                    "hours": hours,
                    "notes": [note] if note else [],
                    "source_row_indices": [idx],
                }
            else:
                existing["hours"] += hours
                if note:
                    existing["notes"].append(note)
                existing["source_row_indices"].append(idx)

        merge_groups = [
            {
                "key": {
                    "worker_firstname": g["worker_firstname"],
                    "worker_lastname": g["worker_lastname"],
                    "project_name": g["project_name"],
                    "work_date": g["work_date"],
                },
                "merged_source_row_indices": g["source_row_indices"],
                "total_hours": str(g["hours"]),
            }
            for g in groups.values()
            if len(g["source_row_indices"]) > 1
        ]

        aggregated = []
        for g in groups.values():
            aggregated.append({
                "worker_firstname": g["worker_firstname"],
                "worker_lastname": g["worker_lastname"],
                "project_name": g["project_name"],
                "work_date": g["work_date"],
                "hours": g["hours"],
                "note": "; ".join(g["notes"]) if g["notes"] else None,
                "source_row_indices": g["source_row_indices"],
            })

        return aggregated, skipped, merge_groups

    # ─── project resolution ───────────────────────────────────────────

    def _resolve_projects(self, project_names: set[str]) -> dict[str, Optional[str]]:
        """One-shot lookup of all distinct project names → public_id.
        Case-insensitive equal match against Project.Name. Names that don't
        match are mapped to None (TimeLog created with no project FK; the
        aggregation handles a NULL ProjectId fine).
        """
        if not project_names:
            return {}
        from entities.project.business.service import ProjectService
        all_projects = ProjectService().read_all()
        by_lower_name = {(p.name or "").strip().lower(): p.public_id for p in all_projects}
        return {name: by_lower_name.get(name.strip().lower()) for name in project_names}

    # ─── main entry point ─────────────────────────────────────────────

    def import_csv(self, file_content: bytes, *, filename: str = "upload.csv") -> dict:
        """Process a CSV upload end-to-end. Returns a structured result.

        Result shape:
            {
              "filename": str,
              "total_csv_rows": int,
              "skipped_csv_rows": [{"row_index": int, "reason": str}],
              "pre_aggregated_rows": int,
              "merge_groups": [{"key": {...}, "merged_source_row_indices": [...], "total_hours": str}],
              "unmatched_project_names": [str],
              "imported_count": int,
              "failed_count": int,
              "bulk_results": [...],      # raw per-row from TimeEntryBulkImportService
              "errors": [{"row_indices": [int], "error": str}],  # mapped back to CSV row indices
            }
        """
        result: dict = {
            "filename": filename,
            "total_csv_rows": 0,
            "skipped_csv_rows": [],
            "pre_aggregated_rows": 0,
            "merge_groups": [],
            "unmatched_project_names": [],
            "imported_count": 0,
            "failed_count": 0,
            "bulk_results": [],
            "errors": [],
        }

        # 1. Parse + validate
        try:
            rows, headers = self._read_csv(file_content)
        except Exception as exc:
            logger.exception("external_timesheet.parse.failed", extra={"filename": filename})
            result["errors"].append({"row_indices": [], "error": f"CSV parse failed: {exc}"})
            return result

        result["total_csv_rows"] = len(rows)
        header_error = self._validate_headers(headers)
        if header_error:
            result["errors"].append({"row_indices": [], "error": header_error})
            return result

        # 2. Pre-aggregate
        aggregated, skipped, merge_groups = self._pre_aggregate(rows)
        result["skipped_csv_rows"] = skipped
        result["merge_groups"] = merge_groups
        result["pre_aggregated_rows"] = len(aggregated)

        if not aggregated:
            # Nothing to import (file was entirely empty / invalid).
            return result

        # 3. Resolve project names (single pass over distinct names)
        distinct_project_names = {
            r["project_name"] for r in aggregated if r["project_name"]
        }
        project_map = self._resolve_projects(distinct_project_names)
        result["unmatched_project_names"] = sorted(
            name for name, pid in project_map.items() if pid is None
        )

        # 4. Build bulk rows + an index map so per-row failures can be
        #    traced back to the originating CSV rows.
        bulk_rows: list[dict] = []
        bulk_to_csv: list[list[int]] = []  # bulk index → list of CSV row indices
        for agg in aggregated:
            project_public_id = (
                project_map.get(agg["project_name"]) if agg["project_name"] else None
            )
            bulk_rows.append({
                "worker_firstname": agg["worker_firstname"],
                "worker_lastname": agg["worker_lastname"],
                "project_public_id": project_public_id,
                "work_date": agg["work_date"],
                "hours": str(agg["hours"]),
                "note": agg["note"],
                "submit": True,
            })
            bulk_to_csv.append(agg["source_row_indices"])

        # 5. Hand off to the bulk service
        bulk_results = self.bulk_service.import_rows(bulk_rows)
        result["bulk_results"] = bulk_results

        # 6. Aggregate counts + map errors back to CSV row indices
        for br in bulk_results:
            csv_indices = bulk_to_csv[br["row_index"]]
            if br["status"] == "failed":
                result["failed_count"] += 1
                result["errors"].append({
                    "row_indices": csv_indices,
                    "error": br.get("error", "Bulk import failed"),
                })
            else:
                result["imported_count"] += 1

        return result
