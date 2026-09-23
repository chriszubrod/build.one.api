#!/usr/bin/env python3
"""U-507 DEPLOY PREFLIGHT — read-only. MUST REPORT ZERO ROWS BEFORE U-507 DEPLOYS.

    ./.venv/bin/python scripts/migrations/u507_preflight_padded_qbo_ids.py

WHY THIS IS PYTHON AND NOT SQL. The first version of this preflight was T-SQL
using `QboId <> LTRIM(RTRIM(QboId))`. That was WRONG, and wrong in the direction
that matters -- it reported clean while the hazard was live. Measured against
the live server:

    normalize_qbo_id('\\xa0900')  -> '900'     SQL predicate: MISSED   (NBSP)
    normalize_qbo_id('\\u3000900')-> '900'     SQL predicate: MISSED   (ideographic space)
    normalize_qbo_id('900 ')     -> '900'     SQL predicate: MISSED   (SQL Server's
                                              comparison ignores TRAILING spaces)
    normalize_qbo_id(' 900')     -> '900'     SQL predicate: FLAGGED  (1 of 4)

Python's `str.strip()` removes every character where `str.isspace()` is true --
NBSP, U+1680, U+2000-U+200A, U+202F, U+3000 and more. SQL Server's no-argument
LTRIM/RTRIM removes ASCII space only. Any T-SQL predicate is a TRANSLATION of
the Python rule and can drift from it. This script calls `normalize_qbo_id`
itself, so the check is equivalent by construction rather than by care.

WHAT IT GUARDS. U-507's validator does not only reject a blank QBO id -- it also
NORMALIZES a non-blank one. If a stored id is non-canonical, the post-deploy pull
reads it normalized, its exact (QboId, RealmId) lookup MISSES, and a second
staging row plus a second downstream identity is minted. Deletion reconciliation
normalizes both sides and will not clean the stale one up.

Covers the six staging tables AND the four dbo identity carriers -- the first
SQL version omitted the dbo half even though the same measurement covered it.

IF THIS REPORTS ANY ROW: do not deploy. Canonicalize that row and its downstream
identity first, re-run, then deploy.
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from integrations.intuit.qbo.base.ids import normalize_qbo_id
from shared.database import get_connection

# The table list is DERIVED, never hand-curated.
#
# It was hand-curated once. The first version listed six staging tables; review
# pointed out it omitted the four dbo identity carriers its own measurement had
# covered, so it became ten; review then found an eleventh (dbo.PaymentTerm,
# which the Term connector stamps via run_identity_fastpath_dbo_only). Asking
# the schema directly found 32 tables carrying a QboId column -- the curated
# list was missing 22 of them.
#
# A hand-maintained inventory of "everywhere QBO identity lives" is wrong by
# construction in a schema this size, and a preflight that silently checks the
# wrong subset is the same false-assurance failure as the T-SQL predicate this
# script replaced. So: ask INFORMATION_SCHEMA.
#
# BASE TABLE only -- views (dbo.vw_*) are projections of tables already covered,
# and a non-canonical value in one is a symptom, not a separate site.


def identity_tables(cursor):
    """Every base table carrying a QboId column, asked of the schema."""
    cursor.execute(
        """
        SELECT c.TABLE_SCHEMA + '.' + c.TABLE_NAME
        FROM INFORMATION_SCHEMA.COLUMNS c
        JOIN INFORMATION_SCHEMA.TABLES t
          ON  t.TABLE_SCHEMA = c.TABLE_SCHEMA
          AND t.TABLE_NAME   = c.TABLE_NAME
        WHERE c.COLUMN_NAME = 'QboId'
          AND t.TABLE_TYPE  = 'BASE TABLE'
        ORDER BY 1
        """
    )
    return [r[0] for r in cursor.fetchall()]


def find_non_canonical(cursor=None):
    """Rows whose stored id is not what normalize_qbo_id would produce.

    Returns (offenders, tables_checked). `cursor` is injectable so the rule can
    be exercised in tests against fake rows -- the previous test asserted a
    LOCAL COPY of this predicate, which would have passed even if this function
    were bypassed entirely.
    """
    def _scan(cur):
        tables = identity_tables(cur)
        found = []
        for table in tables:
            cur.execute(f"SELECT Id, [QboId] FROM {table} WHERE [QboId] IS NOT NULL")
            for row_id, value in cur.fetchall():
                if normalize_qbo_id(value) != value:
                    found.append((table, row_id, value))
        return found, tables

    if cursor is not None:
        return _scan(cursor)
    with get_connection() as conn:
        return _scan(conn.cursor())


def main() -> int:
    offenders, tables = find_non_canonical()
    print(f"U-507 preflight — checked {len(tables)} tables (derived from the schema)")
    if not offenders:
        print("  CLEAN: every stored QBO id is already canonical. Safe to deploy U-507.")
        return 0
    print(f"  ⛔ {len(offenders)} NON-CANONICAL id(s) — DO NOT DEPLOY:")
    for table, row_id, value in offenders:
        print(f"     {table} Id={row_id}  stored={value!r}  normalizes_to={normalize_qbo_id(value)!r}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
