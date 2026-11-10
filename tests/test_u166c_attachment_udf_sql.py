"""U-166c — static guard on dbo.UserCanAccessAttachment.

The UDF lives in shared/sql/dbo.access_udfs.sql (single canonical home, U-051).
These checks are text-level: the suite has no live DB. They pin the rule set from
docs/design/u166c-attachment-access.md so a later edit cannot silently drop a
link table, the Owner/Controller clause, or the creator/admin clauses.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ACCESS_UDF_HOME = REPO_ROOT / "shared" / "sql" / "dbo.access_udfs.sql"

DEFINITION = re.compile(
    r"CREATE\s+OR\s+ALTER\s+FUNCTION\s+(?:\[?dbo\]?\.)?\[?UserCanAccessAttachment\]?\b",
    re.IGNORECASE,
)
ANY_DEFINITION = re.compile(
    r"FUNCTION\s+(?:\[?dbo\]?\.)?\[?UserCanAccessAttachment\]?\b",
    re.IGNORECASE,
)
NINE_LINK_TABLES = (
    "BillLineItemAttachment",
    "BillCreditLineItemAttachment",
    "ExpenseLineItemAttachment",
    "InvoiceAttachment",
    "InvoiceLineItemAttachment",
    "TaxpayerAttachment",
    "BusinessLicenseAttachment",
    "ContractorsLicenseAttachment",
    "AssetAttachment",
)


def _udf_block() -> str:
    text = ACCESS_UDF_HOME.read_text(encoding="utf-8")
    start = DEFINITION.search(text)
    assert start, "UserCanAccessAttachment is not defined in the shared access-UDF home"
    end = text.find("\nGO", start.end())
    assert end != -1, "UserCanAccessAttachment batch is not GO-terminated"
    return text[start.start():end]


def _sql_files():
    skip = {".venv", ".worktrees", "node_modules", ".git"}
    for path in REPO_ROOT.rglob("*.sql"):
        if any(part in skip for part in path.parts):
            continue
        yield path


def test_udf_defined_exactly_once_in_shared_home():
    text = ACCESS_UDF_HOME.read_text(encoding="utf-8")
    assert len(DEFINITION.findall(text)) == 1


def test_udf_not_defined_anywhere_else():
    offenders = [
        str(p.relative_to(REPO_ROOT))
        for p in _sql_files()
        if p != ACCESS_UDF_HOME and ANY_DEFINITION.search(p.read_text(encoding="utf-8", errors="replace"))
    ]
    assert not offenders, f"UserCanAccessAttachment redefined outside the shared home: {offenders}"


def test_udf_is_not_schemabound_and_says_why():
    block = _udf_block()
    assert "WITH SCHEMABINDING" not in block.upper()
    # The reason must sit in the comment directly above the UDF, not only in the design doc.
    text = ACCESS_UDF_HOME.read_text(encoding="utf-8")
    preamble = text[: DEFINITION.search(text).start()][-2500:]
    assert "NOT WITH SCHEMABINDING" in preamble
    assert "link tables" in preamble


def test_udf_references_all_nine_link_tables():
    block = _udf_block()
    for table in NINE_LINK_TABLES:
        assert re.search(rf"dbo\.\[?{table}\]?\b", block), f"{table} missing from UserCanAccessAttachment"


def test_udf_has_owner_and_controller_clause():
    block = _udf_block()
    assert "dbo.[UserRole]" in block or "dbo.UserRole" in block
    assert "N'Owner'" in block
    assert "N'Controller'" in block


def test_udf_has_admin_and_creator_clauses():
    block = _udf_block()
    assert "@ActorIsSystemAdmin = 1" in block
    assert "CreatedByUserId] = @ActorUserId" in block or "CreatedByUserId = @ActorUserId" in block


def test_udf_delegates_to_existing_access_udfs():
    block = _udf_block()
    for delegate in (
        "dbo.UserCanAccessBill(",
        "dbo.UserCanAccessBillCredit(",
        "dbo.UserCanAccessExpense(",
        "dbo.UserCanAccessProject(",
    ):
        assert delegate in block, f"{delegate} not called from UserCanAccessAttachment"


PROJECT_LESS_TABLES = {
    "TaxpayerAttachment",
    "BusinessLicenseAttachment",
    "ContractorsLicenseAttachment",
    "AssetAttachment",
}
PROJECT_LINKED_TABLES = {
    "BillLineItemAttachment",
    "BillCreditLineItemAttachment",
    "ExpenseLineItemAttachment",
    "InvoiceAttachment",
    "InvoiceLineItemAttachment",
}


def test_owner_controller_not_exists_branch_lists_only_project_linked_tables():
    """Branch 4 is `X OR NOT (L OR X)`, which is `X OR NOT L`. The NOT EXISTS
    therefore needs only the five project-linked tables (L). The four project-less
    tables appear only in the EXISTS (X) clauses."""
    block = _udf_block()
    start = block.index("OR NOT EXISTS (")
    not_exists_text = block[start : block.index("THEN CONVERT(BIT, 1)", start)]
    tables = set(re.findall(r"dbo\.\[(\w+)\]", not_exists_text))
    assert tables == PROJECT_LINKED_TABLES, tables
    assert not (tables & PROJECT_LESS_TABLES)


def test_project_less_tables_are_checked_by_exists_clauses():
    block = _udf_block()
    for table in PROJECT_LESS_TABLES:
        assert f"EXISTS (SELECT 1 FROM dbo.[{table}] x WHERE x.[AttachmentId] = @AttachmentId)" in block, table
