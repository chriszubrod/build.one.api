"""Pure helpers + outbox enqueue for DETAILS worksheet section insert/append (U-721a)."""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SectionGroup:
    sub_cost_code: str
    rows: list[list]


@dataclass
class SectionEnqueueResult:
    synced_count: int
    errors: list


def find_insertion_row_for_subcostcode(worksheet_values: List[List[Any]], target_subcostcode: str) -> Optional[int]:
    """
    Find the row number where a new line item should be inserted based on SubCostCode.

    Logic:
    1. Find all rows where Column C matches the target SubCostCode (the "block")
    2. Within that block, find the last row that has BOTH Date (Column I) AND Vendor (Column J)
    3. Insert AFTER that last data row
    4. Fallback: if no data rows, insert two rows after the first matching row
    5. If no matching SubCostCode at all, return None (append at end)

    Args:
        worksheet_values: 2D array of cell values from the worksheet
        target_subcostcode: The SubCostCode number to match (e.g., "65.03" or "37")

    Returns:
        The 1-based row number where the new row should be inserted,
        or None if no matching SubCostCode found (append at end)
    """
    if not worksheet_values:
        return None

    matching_rows = []  # List of (excel_row, has_date_and_vendor)

    for row_index, row in enumerate(worksheet_values):
        if row_index == 0:
            continue  # Skip header row

        excel_row = row_index + 1  # 1-based

        col_c_value = row[2] if len(row) > 2 else None
        if col_c_value is None:
            continue
        col_c_str = str(col_c_value).strip()
        if not col_c_str:
            continue

        target_str = str(target_subcostcode).strip()

        # Match: exact string or numeric equivalence
        subcostcode_match = col_c_str == target_str
        if not subcostcode_match:
            try:
                subcostcode_match = float(col_c_str) == float(target_str)
            except (ValueError, TypeError):
                pass

        if subcostcode_match:
            col_i = row[8] if len(row) > 8 else None
            col_j = row[9] if len(row) > 9 else None
            has_data = (
                col_i is not None and str(col_i).strip() != ""
                and col_j is not None and str(col_j).strip() != ""
            )
            matching_rows.append((excel_row, has_data))

    if not matching_rows:
        logger.info(f"SubCostCode '{target_subcostcode}': No matching rows found, will append at end")
        return None

    # Find the last row with data (Date + Vendor)
    last_data_row = None
    for excel_row, has_data in matching_rows:
        if has_data:
            last_data_row = excel_row

    if last_data_row is not None:
        # Insert after the last data row
        logger.info(f"SubCostCode '{target_subcostcode}': inserting after last data row {last_data_row}")
        return last_data_row + 1

    # Fallback: no data rows in block, insert two rows after the first matching row
    first_match_row = matching_rows[0][0]
    logger.info(f"SubCostCode '{target_subcostcode}': no data rows, inserting two rows after first match row {first_match_row}")
    return first_match_row + 2


def find_first_row_for_subcostcode(
    worksheet_values: List[List[Any]], target_subcostcode: str
) -> Optional[int]:
    """1-based row of the first worksheet row whose column C matches target SubCostCode."""
    if not worksheet_values:
        return None

    target_str = str(target_subcostcode).strip()

    for row_index, row in enumerate(worksheet_values):
        if row_index == 0:
            continue

        col_c_value = row[2] if len(row) > 2 else None
        if col_c_value is None:
            continue
        col_c_str = str(col_c_value).strip()
        if not col_c_str:
            continue

        subcostcode_match = col_c_str == target_str
        if not subcostcode_match:
            try:
                subcostcode_match = float(col_c_str) == float(target_str)
            except (ValueError, TypeError):
                pass

        if subcostcode_match:
            return row_index + 1

    return None


def sort_insert_groups_for_excel_enqueue(
    worksheet_values: List[List[Any]], insert_groups: list
) -> None:
    """Bottom-up enqueue order: highest insertion_row first; ties by section start ASC."""
    insert_groups.sort(
        key=lambda g: (
            -g[0],
            find_first_row_for_subcostcode(worksheet_values, g[2]) or 0,
        )
    )


def group_rows_by_subcostcode(keyed_rows: Iterable[tuple[str, list]]) -> list[SectionGroup]:
    """Group rows by SubCostCode number in first-seen order (batch paths ②③⑤)."""
    groups: dict[str, list[list]] = {}
    order: list[str] = []
    for scc_number, row in keyed_rows:
        if scc_number not in groups:
            groups[scc_number] = []
            order.append(scc_number)
        groups[scc_number].append(row)
    return [SectionGroup(sub_cost_code=scc, rows=groups[scc]) for scc in order]


def batch_entity_public_id(project_id: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"ms-excel-batch:{project_id}"))


def plan_section_inserts(
    worksheet_values: List[List[Any]],
    groups: list[SectionGroup],
    *,
    log_no_match: bool,
) -> Tuple[list, list]:
    """Pure: resolve insertion rows, sort insert groups, collect append rows."""
    insert_groups: list = []
    rows_to_append: list = []

    for group in groups:
        group_rows = group.rows
        if not group_rows:
            continue

        sub_cost_code_number = group.sub_cost_code
        insertion_row = None
        if sub_cost_code_number and worksheet_values:
            insertion_row = find_insertion_row_for_subcostcode(
                worksheet_values=worksheet_values,
                target_subcostcode=sub_cost_code_number,
            )

        if insertion_row:
            insert_groups.append((insertion_row, group_rows, sub_cost_code_number))
        else:
            if log_no_match:
                logger.info(
                    f"SubCostCode {sub_cost_code_number or 'None'}: no match found, will append at end"
                )
            rows_to_append.extend(group_rows)

    sort_insert_groups_for_excel_enqueue(worksheet_values, insert_groups)
    return insert_groups, rows_to_append


def enqueue_section_inserts(
    *,
    worksheet_values: List[List[Any]],
    groups: list[SectionGroup],
    entity_type: str,
    entity_public_id: str,
    drive_id: str,
    item_id: str,
    worksheet_name: str,
    batch: bool,
    log_no_match: bool = False,
) -> SectionEnqueueResult:
    insert_groups, rows_to_append = plan_section_inserts(
        worksheet_values, groups, log_no_match=log_no_match
    )

    from integrations.ms.outbox.business.service import MsOutboxService

    ms_outbox = MsOutboxService()
    synced_count = 0
    errors: list = []

    for insertion_row, group_rows, sub_cost_code_number in insert_groups:
        queued = ms_outbox.enqueue_excel_insert(
            entity_type=entity_type,
            entity_public_id=entity_public_id,
            drive_id=drive_id,
            item_id=item_id,
            worksheet_name=worksheet_name,
            row_index=insertion_row,
            values=group_rows,
            session_id=None,
        )
        if queued is not None:
            synced_count += len(group_rows)
            if batch:
                logger.info(
                    f"Queued batch Excel insert of {len(group_rows)} row(s) at row "
                    f"{insertion_row} for SubCostCode {sub_cost_code_number} "
                    f"(outbox {queued.public_id})"
                )
            else:
                logger.info(
                    f"Queued Excel insert of {len(group_rows)} row(s) at row "
                    f"{insertion_row} for SubCostCode {sub_cost_code_number} "
                    f"(outbox {queued.public_id})"
                )
        else:
            errors.append({
                "sub_cost_code": sub_cost_code_number,
                "error": "Excel insert enqueue refused (ALLOW_MS_WRITES=false or enqueue failure)"
            })

    if rows_to_append:
        if batch:
            logger.info(f"Queuing batch append of {len(rows_to_append)} row(s) to end of worksheet")
        else:
            logger.info(f"Queuing append of {len(rows_to_append)} row(s) to end of worksheet")
        queued = ms_outbox.enqueue_excel_append(
            entity_type=entity_type,
            entity_public_id=entity_public_id,
            drive_id=drive_id,
            item_id=item_id,
            worksheet_name=worksheet_name,
            values=rows_to_append,
            session_id=None,
        )
        if queued is not None:
            synced_count += len(rows_to_append)
            if batch:
                logger.info(
                    f"Queued batch Excel append of {len(rows_to_append)} row(s) "
                    f"(outbox {queued.public_id})"
                )
            else:
                logger.info(
                    f"Queued Excel append of {len(rows_to_append)} row(s) "
                    f"(outbox {queued.public_id})"
                )
        else:
            errors.append({
                "error": "Excel append enqueue refused (ALLOW_MS_WRITES=false or enqueue failure)"
            })

    return SectionEnqueueResult(synced_count=synced_count, errors=errors)
