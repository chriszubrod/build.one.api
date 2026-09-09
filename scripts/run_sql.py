#!/usr/bin/env python3
"""
Simple script to run SQL files against the database.

Usage (run from the repo root, /Users/chris/Applications/build.one/build.one.api):
    ./.venv/bin/python scripts/run_sql.py path/to/file.sql

Both halves matter:
  - `./.venv/bin/python`, not `python` (absent on macOS) and not `python3`
    (the system 3.9, not this project's 3.11 — the same trap
    tests/conftest.py::pytest_configure fails fast on).
  - Run from the repo root. config.py sets `env_file=".env"`, which
    pydantic-settings resolves against the CURRENT WORKING DIRECTORY, so
    invoking this from the build.one umbrella silently loads no .env and the
    DB connection fails on empty credentials rather than on a clear path error.
"""
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.database import get_connection


def run_sql_file(file_path: str):
    """Run a SQL file against the database."""
    print(f"Reading SQL file: {file_path}")

    with open(file_path, 'r') as f:
        sql_content = f.read()

    # Split on GO statements (SQL Server batch separator)
    batches = []
    current_batch = []

    for line in sql_content.split('\n'):
        stripped = line.strip().upper()
        if stripped == 'GO':
            if current_batch:
                batches.append('\n'.join(current_batch))
                current_batch = []
        else:
            current_batch.append(line)

    # Don't forget the last batch
    if current_batch:
        batches.append('\n'.join(current_batch))

    print(f"Found {len(batches)} SQL batches to execute")

    with get_connection() as conn:
        cursor = conn.cursor()

        for i, batch in enumerate(batches, 1):
            # Skip empty batches
            if not batch.strip():
                continue

            try:
                print(f"Executing batch {i}/{len(batches)}...")
                cursor.execute(batch)
                # Consume any results
                while cursor.nextset():
                    pass
            except Exception as e:
                print(f"Error in batch {i}: {e}")
                print(f"Batch content (first 200 chars): {batch[:200]}...")
                raise

        print("All batches executed successfully!")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: ./.venv/bin/python scripts/run_sql.py <path_to_sql_file>")
        print("       (run from the repo root — .env is resolved against the CWD)")
        sys.exit(1)

    sql_file = sys.argv[1]
    if not os.path.exists(sql_file):
        print(f"Error: File not found: {sql_file}")
        sys.exit(1)

    run_sql_file(sql_file)
