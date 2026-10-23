"""U-723: ReadPossibleDuplicateBills against stub tables (Rules A/B/C)."""
from __future__ import annotations

import os
import re
import uuid
from decimal import Decimal
from pathlib import Path

import pyodbc
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DUPE_SQL = REPO_ROOT / "entities/bill/sql/dbo.bill_duplicate_check.sql"

pytestmark = pytest.mark.skipif(
    not os.environ.get("BUILDONE_TEST_SQLSERVER"),
    reason="BUILDONE_TEST_SQLSERVER not set",
)

_LOCAL_SERVERS = {"127.0.0.1", "localhost"}


def _server_host(conn_str: str) -> str:
    for part in conn_str.split(";"):
        key, _, value = part.partition("=")
        if key.strip().upper() == "SERVER":
            host = re.sub(r"^tcp:", "", value.strip().strip("{}"), flags=re.IGNORECASE)
            return host.split(",")[0].strip().lower()
    return ""


if os.environ.get("BUILDONE_TEST_SQLSERVER") and _server_host(
    os.environ["BUILDONE_TEST_SQLSERVER"]
) not in _LOCAL_SERVERS:
    raise RuntimeError("tests_sqlserver requires localhost SQL Server")


def _batches(sql: str) -> list[str]:
    out, cur = [], []
    for line in sql.splitlines():
        if line.strip() == "GO":
            out.append("\n".join(cur))
            cur = []
        else:
            cur.append(line)
    out.append("\n".join(cur))
    return [b for b in out if any(l.strip() and not l.strip().startswith("--") for l in b.splitlines())]


def _install_objects(cur) -> None:
    text = DUPE_SQL.read_text(encoding="utf-8")
    for batch in _batches(text):
        if "BillDuplicateOverride" in batch or "ReadPossibleDuplicateBills" in batch or "NormalizeBillNumberForRuleC" in batch:
            cur.execute(batch)


def _drop_all(cur) -> None:
    for proc in (
        "ReadPossibleDuplicateBills",
        "ReadBillDuplicateOverridesByBillId",
        "CreateBillDuplicateOverride",
    ):
        cur.execute(f"IF OBJECT_ID('dbo.{proc}', 'P') IS NOT NULL DROP PROCEDURE dbo.{proc}")
    cur.execute("IF OBJECT_ID('dbo.NormalizeBillNumberForRuleC', 'FN') IS NOT NULL DROP FUNCTION dbo.NormalizeBillNumberForRuleC")
    cur.execute("IF OBJECT_ID('dbo.BillDuplicateOverride', 'U') IS NOT NULL DROP TABLE dbo.BillDuplicateOverride")
    for tbl in (
        "dbo.ContractLabor",
        "dbo.ContractLaborLineItem",
        "dbo.BillLineItemAttachment",
        "dbo.AttachmentTextFingerprint",
        "dbo.Attachment",
        "dbo.BillLineItem",
        "dbo.Project",
        "dbo.Vendor",
        "dbo.Bill",
    ):
        cur.execute(f"IF OBJECT_ID('{tbl}', 'U') IS NOT NULL DROP TABLE {tbl}")
    cur.execute("IF OBJECT_ID('dbo.UserCanAccessBill', 'FN') IS NOT NULL DROP FUNCTION dbo.UserCanAccessBill")


def _install_schema(cur) -> None:
    cur.execute(
        """
        CREATE TABLE dbo.Vendor (Id BIGINT PRIMARY KEY, Name NVARCHAR(100));
        CREATE TABLE dbo.Project (Id BIGINT PRIMARY KEY, Name NVARCHAR(50));
        CREATE TABLE dbo.Bill (
            Id BIGINT PRIMARY KEY,
            PublicId UNIQUEIDENTIFIER NOT NULL,
            VendorId BIGINT NULL,
            BillDate DATETIME2(3) NOT NULL,
            BillNumber NVARCHAR(50) NULL,
            TotalAmount DECIMAL(18,2) NULL,
            Status NVARCHAR(32) NOT NULL,
            CreatedDatetime DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME()
        );
        CREATE TABLE dbo.BillLineItem (
            Id BIGINT PRIMARY KEY,
            BillId BIGINT NOT NULL,
            ProjectId BIGINT NULL,
            Amount DECIMAL(18,2) NULL
        );
        CREATE TABLE dbo.Attachment (Id BIGINT PRIMARY KEY);
        CREATE TABLE dbo.BillLineItemAttachment (
            Id BIGINT IDENTITY PRIMARY KEY,
            BillLineItemId BIGINT NOT NULL,
            AttachmentId BIGINT NOT NULL
        );
        CREATE TABLE dbo.AttachmentTextFingerprint (
            AttachmentId BIGINT PRIMARY KEY,
            Version NVARCHAR(16) NOT NULL,
            Fingerprint CHAR(64) NOT NULL,
            CreatedDatetime DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME()
        );
        CREATE TABLE dbo.ContractLaborLineItem (
            Id BIGINT PRIMARY KEY,
            BillLineItemId BIGINT NOT NULL
        );
        CREATE TABLE dbo.ContractLabor (
            Id BIGINT PRIMARY KEY,
            BillLineItemId BIGINT NULL
        );
        EXEC('
        CREATE FUNCTION dbo.UserCanAccessBill(@ActorUserId BIGINT, @ActorIsSystemAdmin BIT, @BillId BIGINT)
        RETURNS BIT AS BEGIN
            IF @ActorUserId IS NULL RETURN 0;
            IF @ActorIsSystemAdmin = 1 RETURN 1;
            IF @BillId = 900 RETURN 0;
            RETURN 1;
        END');
        """
    )
    _install_objects(cur)


@pytest.fixture()
def conn():
    conn_str = os.environ["BUILDONE_TEST_SQLSERVER"]
    c = pyodbc.connect(conn_str, autocommit=True)
    cur = c.cursor()
    assert cur.execute("SELECT DB_NAME()").fetchval() == "buildone_test"
    _drop_all(cur)
    _install_schema(cur)
    yield c
    _drop_all(cur)
    c.close()


def _exec_read(
    cur,
    *,
    vendor_id=10,
    bill_date="2026-10-09",
    amount="1500.00",
    bill_number="9242 Nina Ct",
    fingerprints=None,
    exclude_bill_id=500,
    project_ids="1",
    actor_user_id=1,
):
    cur.execute(
        """
        DECLARE @rc INT;
        EXEC ReadPossibleDuplicateBills
            @VendorId=?,
            @BillDate=?,
            @TotalAmount=?,
            @BillNumber=?,
            @TextFingerprints=?,
            @FingerprintVersion='u723-v1',
            @ExcludeBillId=?,
            @ProjectIds=?,
            @ActorUserId=?,
            @ActorIsSystemAdmin=0;
        """,
        vendor_id,
        bill_date,
        Decimal(amount),
        bill_number,
        fingerprints,
        exclude_bill_id,
        project_ids,
        actor_user_id,
    )
    return cur.fetchall()


def _seed_rule_a_candidate(cur, *, bill_id=100, vendor_id=10, project_id=1, cl=False):
    if cur.execute("SELECT 1 FROM dbo.Vendor WHERE Id=?", vendor_id).fetchval() is None:
        cur.execute(
            "INSERT INTO dbo.Vendor (Id, Name) VALUES (?, N'Wilson')",
            vendor_id,
        )
    if cur.execute("SELECT 1 FROM dbo.Project WHERE Id=?", project_id).fetchval() is None:
        cur.execute(
            "INSERT INTO dbo.Project (Id, Name) VALUES (?, N'HE7')",
            project_id,
        )
    cur.execute(
        """INSERT INTO dbo.Bill (Id, PublicId, VendorId, BillDate, BillNumber, TotalAmount, Status)
           VALUES (?, ?, ?, '2026-10-09', N'40781', 1500.00, 'completed')""",
        bill_id,
        uuid.uuid4(),
        vendor_id,
    )
    cur.execute(
        "INSERT INTO dbo.BillLineItem (Id, BillId, ProjectId, Amount) VALUES (?, ?, ?, 1500.00)",
        bill_id * 10,
        bill_id,
        project_id,
    )
    if cl:
        cur.execute(
            "INSERT INTO dbo.ContractLaborLineItem (Id, BillLineItemId) VALUES (?, ?)",
            bill_id,
            bill_id * 10,
        )


def test_rule_a_project_overlap_required(conn):
    """Red proof: without shared ProjectId, Rule A does not fire (sproc project-join)."""
    cur = conn.cursor()
    _seed_rule_a_candidate(cur, project_id=99)
    rows = _exec_read(cur, project_ids="1")
    assert len(rows) == 0
    rows2 = _exec_read(cur, project_ids="99")
    assert len(rows2) == 1
    assert int(rows2[0].BillId) == 100
    assert "vendor_date_amount" in rows2[0].ReasonsCsv


def test_rule_a_excludes_contract_labor(conn):
    cur = conn.cursor()
    _seed_rule_a_candidate(cur, cl=True)
    rows = _exec_read(cur, project_ids="1")
    assert len(rows) == 0


def test_rule_c_cross_vendor_same_number_amount_date(conn):
    """Incident shape: vendor X completed vs vendor Y draft, same number/amount/date."""
    cur = conn.cursor()
    cur.execute("INSERT INTO dbo.Vendor (Id, Name) VALUES (10, N'Wilson'), (20, N'Brooks')")
    cur.execute(
        """INSERT INTO dbo.Bill (Id, PublicId, VendorId, BillDate, BillNumber, TotalAmount, Status)
           VALUES (100, NEWID(), 10, '2026-10-09', N'9242 Nina Ct', 1500.00, 'completed')"""
    )
    cur.execute(
        """INSERT INTO dbo.Bill (Id, PublicId, VendorId, BillDate, BillNumber, TotalAmount, Status)
           VALUES (200, NEWID(), 20, '2026-10-09', N'9242 Nina Ct', 1500.00, 'draft')"""
    )
    cur.execute("INSERT INTO dbo.BillLineItem (Id, BillId, Amount) VALUES (1000, 100, 1500),(2000, 200, 1500)")
    rows = _exec_read(cur, vendor_id=20, exclude_bill_id=500, project_ids="")
    reasons = {r.ReasonsCsv for r in rows}
    assert any("same_number_amount_date" in r for r in reasons)


def test_rule_b_fingerprint_join(conn):
    cur = conn.cursor()
    fp = "a" * 64
    cur.execute("INSERT INTO dbo.Vendor (Id, Name) VALUES (1, N'V')")
    cur.execute(
        """INSERT INTO dbo.Bill (Id, PublicId, VendorId, BillDate, BillNumber, TotalAmount, Status)
           VALUES (50, NEWID(), 1, '2026-01-01', N'x', NULL, 'draft')"""
    )
    cur.execute("INSERT INTO dbo.BillLineItem (Id, BillId, Amount) VALUES (501, 50, 1)")
    cur.execute("INSERT INTO dbo.Attachment (Id) VALUES (700)")
    cur.execute(
        "INSERT INTO dbo.BillLineItemAttachment (BillLineItemId, AttachmentId) VALUES (501, 700)"
    )
    cur.execute(
        "INSERT INTO dbo.AttachmentTextFingerprint (AttachmentId, Version, Fingerprint) VALUES (700, 'u723-v1', ?)",
        fp,
    )
    rows = _exec_read(
        cur,
        vendor_id=None,
        bill_date="2026-01-01",
        amount="0",
        bill_number=None,
        fingerprints=fp,
        exclude_bill_id=None,
        project_ids="",
    )
    assert len(rows) == 1
    assert rows[0].ReasonsCsv == "same_document_text"


def test_self_exclusion_and_override(conn):
    cur = conn.cursor()
    _seed_rule_a_candidate(cur, bill_id=500, project_id=1)
    rows = _exec_read(cur, exclude_bill_id=500, project_ids="1")
    assert len(rows) == 0
    _seed_rule_a_candidate(cur, bill_id=100, project_id=1)
    cur.execute(
        """INSERT INTO dbo.BillDuplicateOverride
           (BillId, MatchedBillId, MatchReason, Stage, Disposition, ActorUserId)
           VALUES (500, 100, 'vendor_date_amount', 'create', 'confirmed_not_duplicate', 1)"""
    )
    rows2 = _exec_read(cur, exclude_bill_id=500, project_ids="1")
    assert len(rows2) == 0


def test_hidden_inaccessible_match_still_counts(conn):
    cur = conn.cursor()
    _seed_rule_a_candidate(cur, bill_id=900, project_id=1)
    rows = _exec_read(cur, project_ids="1")
    assert len(rows) == 1
    assert rows[0].CallerCanAccess is False


def test_recorded_override_does_not_suppress(conn):
    cur = conn.cursor()
    _seed_rule_a_candidate(cur, bill_id=100, project_id=1)
    cur.execute(
        """INSERT INTO dbo.BillDuplicateOverride
           (BillId, MatchedBillId, MatchReason, Stage, Disposition, ActorUserId)
           VALUES (500, 100, 'vendor_date_amount', 'create', 'recorded', 1)"""
    )
    rows = _exec_read(cur, exclude_bill_id=500, project_ids="1")
    assert len(rows) == 1


def test_confirmed_override_suppresses_both_directions(conn):
    cur = conn.cursor()
    _seed_rule_a_candidate(cur, bill_id=100, project_id=1)
    cur.execute(
        """INSERT INTO dbo.BillDuplicateOverride
           (BillId, MatchedBillId, MatchReason, Stage, Disposition, ActorUserId)
           VALUES (100, 500, 'vendor_date_amount', 'create', 'confirmed_not_duplicate', 1)"""
    )
    rows = _exec_read(cur, exclude_bill_id=500, project_ids="1")
    assert len(rows) == 0


def test_delete_bill_with_override_rows_succeeds(conn):
    cur = conn.cursor()
    _seed_rule_a_candidate(cur, bill_id=100, project_id=1)
    cur.execute(
        """INSERT INTO dbo.BillDuplicateOverride
           (BillId, MatchedBillId, MatchReason, Stage, Disposition, ActorUserId)
           VALUES (500, 100, 'vendor_date_amount', 'create', 'recorded', 1),
                  (100, 500, 'vendor_date_amount', 'create', 'recorded', 1)"""
    )
    cur.execute("DELETE FROM dbo.Bill WHERE Id IN (100, 500)")
    assert cur.execute("SELECT COUNT(*) FROM dbo.BillDuplicateOverride").fetchval() == 2


def test_line_sum_amount_match_when_header_differs(conn):
    cur = conn.cursor()
    _seed_rule_a_candidate(cur, bill_id=100, project_id=1)
    cur.execute("UPDATE dbo.Bill SET TotalAmount = 1400.00 WHERE Id = 100")
    rows = _exec_read(cur, amount="1500.00", project_ids="1")
    assert len(rows) == 1
    assert rows[0].TotalAmount == Decimal("1500.00")


def test_normalize_bill_number_rule_c(conn):
    cur = conn.cursor()
    cases = [
        ("\t9242 Nina Ct", "9242 nina ct"),
        ("9242\u00a0Nina Ct", "9242 nina ct"),
        ("  9242   NINA ct ", "9242 nina ct"),
    ]
    for raw, expected in cases:
        got = cur.execute("SELECT dbo.NormalizeBillNumberForRuleC(?)", raw).fetchval()
        assert got == expected
    assert cur.execute("SELECT dbo.NormalizeBillNumberForRuleC(N'QBO-123')").fetchval() is None
    assert cur.execute("SELECT dbo.NormalizeBillNumberForRuleC(N'')").fetchval() is None


def test_bill_date_datetime2_time_part_still_matches(conn):
    cur = conn.cursor()
    _seed_rule_a_candidate(cur, bill_id=100, project_id=1)
    cur.execute(
        "UPDATE dbo.Bill SET BillDate = '2026-10-09T14:30:00.000' WHERE Id = 100"
    )
    rows = _exec_read(cur, bill_date="2026-10-09", project_ids="1")
    assert len(rows) == 1


def test_null_or_zero_amount_skips_rules_a_and_c(conn):
    cur = conn.cursor()
    _seed_rule_a_candidate(cur, bill_id=100, project_id=1)
    assert len(_exec_read(cur, amount="0", project_ids="1")) == 0
    cur.execute("UPDATE dbo.Bill SET TotalAmount = NULL WHERE Id = 100")
    cur.execute("UPDATE dbo.BillLineItem SET Amount = NULL WHERE BillId = 100")
    assert len(_exec_read(cur, amount="1500.00", project_ids="1")) == 0


def test_rule_c_returns_cross_vendor_candidate_row(conn):
    cur = conn.cursor()
    cur.execute("INSERT INTO dbo.Vendor (Id, Name) VALUES (10, N'Wilson'), (20, N'Brooks')")
    cross_pid = uuid.uuid4()
    cur.execute(
        """INSERT INTO dbo.Bill (Id, PublicId, VendorId, BillDate, BillNumber, TotalAmount, Status)
           VALUES (100, NEWID(), 10, '2026-10-09', N'9242 Nina Ct', 1500.00, 'completed'),
                  (200, ?, 20, '2026-10-09', N'9242 Nina Ct', 1500.00, 'draft')""",
        cross_pid,
    )
    cur.execute(
        "INSERT INTO dbo.BillLineItem (Id, BillId, Amount) VALUES (1000, 100, 1500),(2000, 200, 1500)"
    )
    rows = _exec_read(cur, vendor_id=20, exclude_bill_id=500, project_ids="")
    cross_str = str(cross_pid).upper()
    cross_rows = [r for r in rows if str(r.PublicId).upper() == cross_str]
    assert len(cross_rows) == 1
    assert "same_number_amount_date" in cross_rows[0].ReasonsCsv


def test_multiple_reasons_aggregate_one_row(conn):
    cur = conn.cursor()
    fp = "b" * 64
    cur.execute("INSERT INTO dbo.Vendor (Id, Name) VALUES (10, N'V')")
    cur.execute(
        """INSERT INTO dbo.Bill (Id, PublicId, VendorId, BillDate, BillNumber, TotalAmount, Status)
           VALUES (100, NEWID(), 10, '2026-10-09', N'9242 Nina Ct', 1500.00, 'draft')"""
    )
    cur.execute(
        "INSERT INTO dbo.BillLineItem (Id, BillId, ProjectId, Amount) VALUES (1000, 100, 1, 1500)"
    )
    cur.execute("INSERT INTO dbo.Project (Id, Name) VALUES (1, N'HE7')")
    cur.execute("INSERT INTO dbo.Attachment (Id) VALUES (701)")
    cur.execute(
        "INSERT INTO dbo.BillLineItemAttachment (BillLineItemId, AttachmentId) VALUES (1000, 701)"
    )
    cur.execute(
        "INSERT INTO dbo.AttachmentTextFingerprint (AttachmentId, Version, Fingerprint) VALUES (701, 'u723-v1', ?)",
        fp,
    )
    rows = _exec_read(
        cur,
        bill_number="9242 Nina Ct",
        fingerprints=fp,
        project_ids="1",
    )
    assert len(rows) == 1
    reasons = set(rows[0].ReasonsCsv.split(","))
    assert "vendor_date_amount" in reasons
    assert "same_number_amount_date" in reasons
    assert "same_document_text" in reasons


def test_null_actor_cannot_access(conn):
    cur = conn.cursor()
    _seed_rule_a_candidate(cur, bill_id=100, project_id=1)
    rows = _exec_read(cur, project_ids="1", actor_user_id=None)
    assert len(rows) == 1
    assert rows[0].CallerCanAccess is False


def test_fingerprint_version_mismatch_skips_rule_b(conn):
    cur = conn.cursor()
    fp = "c" * 64
    cur.execute("INSERT INTO dbo.Vendor (Id, Name) VALUES (1, N'V')")
    cur.execute(
        """INSERT INTO dbo.Bill (Id, PublicId, VendorId, BillDate, BillNumber, TotalAmount, Status)
           VALUES (50, NEWID(), 1, '2026-01-01', N'x', 1.00, 'draft')"""
    )
    cur.execute("INSERT INTO dbo.BillLineItem (Id, BillId, Amount) VALUES (501, 50, 1)")
    cur.execute("INSERT INTO dbo.Attachment (Id) VALUES (702)")
    cur.execute(
        "INSERT INTO dbo.BillLineItemAttachment (BillLineItemId, AttachmentId) VALUES (501, 702)"
    )
    cur.execute(
        "INSERT INTO dbo.AttachmentTextFingerprint (AttachmentId, Version, Fingerprint) VALUES (702, 'other-version', ?)",
        fp,
    )
    rows = _exec_read(
        cur,
        vendor_id=None,
        bill_date="2026-01-01",
        amount="1.00",
        bill_number=None,
        fingerprints=fp,
        exclude_bill_id=None,
        project_ids="",
    )
    assert len(rows) == 0


def test_legacy_contract_labor_header_link_excluded(conn):
    cur = conn.cursor()
    _seed_rule_a_candidate(cur, bill_id=100, project_id=1)
    cur.execute(
        "INSERT INTO dbo.ContractLabor (Id, BillLineItemId) VALUES (1, 1000)"
    )
    rows = _exec_read(cur, project_ids="1")
    assert len(rows) == 0


def test_create_override_skips_identical_insert(conn):
    cur = conn.cursor()
    cur.execute("INSERT INTO dbo.Vendor (Id, Name) VALUES (1, N'V')")
    cur.execute(
        """INSERT INTO dbo.Bill (Id, PublicId, VendorId, BillDate, BillNumber, TotalAmount, Status)
           VALUES (1, NEWID(), 1, '2026-01-01', N'a', 1, 'draft'),
                  (2, NEWID(), 1, '2026-01-01', N'b', 1, 'draft')"""
    )
    cur.execute(
        """EXEC CreateBillDuplicateOverride
            @BillId=1, @MatchedBillId=2, @MatchReason='vendor_date_amount',
            @Stage='create', @Disposition='recorded', @ActorUserId=1"""
    )
    cur.execute(
        """EXEC CreateBillDuplicateOverride
            @BillId=1, @MatchedBillId=2, @MatchReason='vendor_date_amount',
            @Stage='create', @Disposition='recorded', @ActorUserId=1"""
    )
    count = cur.execute(
        "SELECT COUNT(*) FROM dbo.BillDuplicateOverride WHERE BillId=1 AND MatchedBillId=2"
    ).fetchval()
    assert count == 1
