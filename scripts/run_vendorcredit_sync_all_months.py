#!/usr/bin/env python3
"""Run QBO VendorCredit sync for each month (2022-01 through 2026-01) with up to 6 in parallel."""
import os
import subprocess
import sys
from calendar import monthrange
from concurrent.futures import ProcessPoolExecutor, as_completed

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def run_month(year_month):
    y, m = year_month
    start = f"{y}-{m:02d}-01"
    _, last = monthrange(y, m)
    end = f"{y}-{m:02d}-{last:02d}"
    r = subprocess.run(
        [sys.executable, "scripts/sync_qbo_vendorcredit.py", "--start-date", start, "--end-date", end, "--skip-sync-update"],
        cwd=ROOT,
        capture_output=False,
    )
    return (year_month, r.returncode)


def main():
    months = []
    for y in range(2022, 2027):
        for m in range(1, 13):
            if y == 2026 and m > 1:
                break
            months.append((y, m))

    completed = 0
    failed = []
    with ProcessPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(run_month, ym): ym for ym in months}
        for fut in as_completed(futures):
            ym, code = fut.result()
            completed += 1
            status = "OK" if code == 0 else "FAILED"
            print(f"[{completed}/{len(months)}] {ym[0]}-{ym[1]:02d} {status}")
            if code != 0:
                failed.append(f"{ym[0]}-{ym[1]:02d}")

    if failed:
        print(f"Failed months: {failed}")
        sys.exit(1)
    print("All months completed successfully.")


if __name__ == "__main__":
    main()
