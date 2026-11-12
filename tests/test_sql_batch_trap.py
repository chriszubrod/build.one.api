"""U-789: no statement may be swallowed into a module body by a missing GO.

A `CREATE PROCEDURE` body runs to the end of its **batch**, not to its matching
`END`. So a statement that follows the procedure in the same batch is not a
statement at all — T-SQL compiles it into the procedure body, where it executes
on every call instead of once as DDL. The failure is silent: the file applies
cleanly, the sproc works, and the index / constraint / backfill the author wrote
simply never happens.

That is the U-048 / U-053 trap. U-789 measured its cost against prod on
2026-11-12: seven live sites, and **six declared objects that do not exist in
prod at all** — `IX_Address_PublicId`, `IX_AddressType_PublicId`,
`IX_Customer_PublicId`, `IX_Role_PublicId`, `IX_VendorType_PublicId` and
`FK_VendorAddress_Vendor`, a foreign key the schema intends and the database
has never had. Four of the seven had prod *agreeing* with the trapped file,
which is the tell that the DDL had been running inside a sproc body for as long
as the file had been applied.

The loud variant is a second `CREATE PROCEDURE` in the same batch: SQL Server
raises error 111 ("CREATE PROCEDURE must be the first statement in a query
batch"), which made `dbo.address_type.sql` the one base file in the repo that
could not be applied at all. Both variants are asserted here.

This guard replaces the detector held for this purpose since U-051
(`u054_batch_trap_test_HANDOFF.py`). Scope is every `*.sql` in the repo — it was
green repo-wide when written, so there is no allowlist to erode.
"""
import re

import pytest

from tests.sql_corpus import iter_repo_sql_files
from tests.sproc_text import REPO_ROOT

SKIP_DIRS = frozenset({".venv"})

# A module declaration at the start of a line. Indentation is allowed (a few
# files indent theirs) but the keyword must open the line.
_MODULE = re.compile(
    r"^[ \t]*CREATE\s+(?:OR\s+ALTER\s+)?(PROCEDURE|PROC|FUNCTION|VIEW|TRIGGER)\b",
    re.IGNORECASE | re.MULTILINE,
)

# A statement at column 0 is top-level: everything inside a module body in this
# repo is indented. That is the discriminator — an *indented* `IF NOT EXISTS
# (SELECT ...)` is ordinary procedure logic and must not trip this guard.
_TOP_LEVEL = re.compile(
    r"^(IF|ALTER|CREATE|UPDATE|INSERT|DELETE|MERGE|GRANT|REVOKE|EXEC|EXECUTE|DROP|TRUNCATE)\b",
    re.IGNORECASE,
)


def _batches(text: str) -> list[str]:
    """Split on a lone GO, exactly as sqlcmd and scripts/run_sql.py do."""
    out: list[str] = []
    current: list[str] = []
    for line in text.split("\n"):
        if line.strip().upper() == "GO":
            out.append("\n".join(current))
            current = []
        else:
            current.append(line)
    out.append("\n".join(current))
    return out


def _swallowed(batch: str) -> list[str]:
    """Top-level statements that follow a module declaration in the same batch."""
    first = _MODULE.search(batch)
    if first is None:
        return []
    return [
        line.strip()
        for line in batch[first.end():].split("\n")
        if _TOP_LEVEL.match(line)
    ]


def _sql_files():
    return iter_repo_sql_files(REPO_ROOT, skip_dir_names=SKIP_DIRS)


def test_no_statement_is_swallowed_into_a_module_body():
    offenders = []
    for path in _sql_files():
        for index, batch in enumerate(_batches(path.read_text(encoding="utf-8"))):
            for statement in _swallowed(batch):
                offenders.append(
                    f"{path.relative_to(REPO_ROOT)} batch {index}: {statement[:100]}"
                )
    assert not offenders, (
        "a statement follows a CREATE PROCEDURE/FUNCTION/VIEW/TRIGGER in the SAME "
        "batch, so T-SQL compiles it into the module body and it never runs as a "
        "statement (the U-048/U-053 trap; U-789 found six declared objects missing "
        "from prod because of it).\n"
        "Fix: insert a lone GO between the module and the statement — never delete "
        "the statement, which is data/DDL loss dressed as a fix.\n  "
        + "\n  ".join(offenders)
    )


def test_no_two_modules_share_a_batch():
    offenders = []
    for path in _sql_files():
        for index, batch in enumerate(_batches(path.read_text(encoding="utf-8"))):
            found = _MODULE.findall(batch)
            if len(found) > 1:
                offenders.append(
                    f"{path.relative_to(REPO_ROOT)} batch {index}: {len(found)} modules"
                )
    assert not offenders, (
        "two CREATE PROCEDURE/FUNCTION/VIEW/TRIGGER declarations share one batch. "
        "SQL Server raises error 111 and run_sql.py rolls the whole file back, so "
        "the file cannot be applied at all. Insert a lone GO between them.\n  "
        + "\n  ".join(offenders)
    )


@pytest.mark.parametrize(
    "batch,expected",
    [
        # trapped: the guard runs inside DeleteFoo's body, not as DDL
        (
            "CREATE OR ALTER PROCEDURE DeleteFoo\nAS\nBEGIN\n    DELETE dbo.Foo;\nEND;\n"
            "\nIF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Foo')\nBEGIN\n"
            "    CREATE INDEX IX_Foo ON dbo.Foo (PublicId);\nEND\n",
            True,
        ),
        # safe: an INDENTED IF NOT EXISTS is ordinary procedure logic
        (
            "CREATE OR ALTER PROCEDURE UpsertFoo\nAS\nBEGIN\n"
            "    IF NOT EXISTS (SELECT 1 FROM dbo.Foo WHERE Id = @Id)\n"
            "    BEGIN\n        INSERT dbo.Foo (Id) VALUES (@Id);\n    END\nEND;\n",
            False,
        ),
        # safe: DDL with no module in the batch at all
        (
            "IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Foo')\nBEGIN\n"
            "    CREATE INDEX IX_Foo ON dbo.Foo (PublicId);\nEND\n",
            False,
        ),
    ],
)
def test_detector_is_not_vacuous(batch, expected):
    """The two tests above are only worth their green if the detector still fires.

    A guard whose pattern silently stops matching passes forever. These cases pin
    both directions: the real trapped shape is caught, and the two shapes that
    look like it are not.
    """
    assert bool(_swallowed(batch)) is expected
