#!/usr/bin/env python3
"""
Remove duplicate ContractLabor records, keeping one per natural key.

Duplicate key: (EmployeeName, WorkDate, JobName, TimeIn, TimeOut, Description).
Within each duplicate group we keep one entry (prefer status='billed', then lowest Id)
and delete the rest. ContractLaborLineItem rows are removed by FK CASCADE.

Usage:
    python scripts/clean_contract_labor_duplicates.py [--dry-run]

Run the stored procedure first if needed:
    See entities/contract_labor/sql/dbo.contract_labor.sql for ReadContractLaborByNaturalKey.
"""
import argparse
import sys
import os
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from entities.contract_labor.persistence.repo import ContractLaborRepository


def natural_key(entry):
    return (
        (entry.employee_name or "").strip(),
        entry.work_date or "",
        (entry.job_name or "").strip(),
        (entry.time_in or "").strip(),
        (entry.time_out or "").strip(),
        (entry.description or "").strip(),
    )


def main():
    parser = argparse.ArgumentParser(description="Remove duplicate contract labor entries")
    parser.add_argument("--dry-run", action="store_true", help="Report duplicates only, do not delete")
    args = parser.parse_args()

    repo = ContractLaborRepository()
    all_entries = repo.read_all()
    groups = defaultdict(list)
    for e in all_entries:
        groups[natural_key(e)].append(e)

    duplicates = {k: v for k, v in groups.items() if len(v) > 1}
    if not duplicates:
        print("No duplicate groups found.")
        return

    total_duplicate_rows = sum(len(v) for v in duplicates.values())
    to_delete_count = total_duplicate_rows - len(duplicates)  # one kept per group
    print(f"Found {len(duplicates)} duplicate groups ({total_duplicate_rows} rows total).")
    print(f"Would remove {to_delete_count} duplicate(s), keeping 1 per group.")
    if args.dry_run:
        for key, entries in sorted(duplicates.items(), key=lambda x: (x[0][1], x[0][0])):
            keep = min(entries, key=lambda e: (0 if e.status == "billed" else 1, e.id))
            print(f"  Key {key[:4]}...: keep Id={keep.id} (status={keep.status}), delete Ids={[e.id for e in entries if e.id != keep.id]}")
        print("Dry run. No changes made.")
        return

    deleted = 0
    for key, entries in duplicates.items():
        # Keep one: prefer billed, then lowest Id
        keep = min(entries, key=lambda e: (0 if e.status == "billed" else 1, e.id))
        for e in entries:
            if e.id == keep.id:
                continue
            try:
                repo.delete_by_id(id=e.id)
                deleted += 1
                print(f"Deleted ContractLabor Id={e.id} (employee={e.employee_name}, work_date={e.work_date})")
            except Exception as err:
                print(f"Error deleting Id={e.id}: {err}", file=sys.stderr)
    print(f"Removed {deleted} duplicate(s).")


if __name__ == "__main__":
    main()
