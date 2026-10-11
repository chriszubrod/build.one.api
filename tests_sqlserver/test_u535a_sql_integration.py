"""U-535a: attachment extraction procedures executed against a real SQL Server.

Runs only when BUILDONE_TEST_SQLSERVER is set: a throwaway SQL Server 2022 in
READ_COMMITTED_SNAPSHOT mode, database buildone_test. The module drops and recreates the
dbo tables it owns and refuses to run against any other database.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import os
import re
from pathlib import Path

import pyodbc
import pytest

from entities.attachment.persistence import repo as repo_mod
from shared.database import call_procedure

pytestmark = pytest.mark.skipif(
    not os.environ.get("BUILDONE_TEST_SQLSERVER"),
    reason="BUILDONE_TEST_SQLSERVER not set (needs a throwaway SQL Server)",
)

_LOCAL_SERVERS = {"127.0.0.1", "localhost"}


def _server_host(conn_str: str) -> str:
    for part in conn_str.split(";"):
        key, _, value = part.partition("=")
        if key.strip().upper() == "SERVER":
            host = re.sub(r"^tcp:", "", value.strip().strip("{}"), flags=re.IGNORECASE)
            return host.split(",")[0].strip().lower()
    return ""


if os.environ.get("BUILDONE_TEST_SQLSERVER") and _server_host(os.environ["BUILDONE_TEST_SQLSERVER"]) not in _LOCAL_SERVERS:
    raise RuntimeError("tests_sqlserver refuses to run: SERVER in BUILDONE_TEST_SQLSERVER must be 127.0.0.1 or localhost")

REPO_ROOT = Path(__file__).resolve().parents[1]
ATTACHMENT_SQL = REPO_ROOT / "entities" / "attachment" / "sql" / "dbo.attachment.sql"
ADD_FIELDS_SQL = REPO_ROOT / "entities" / "attachment" / "sql" / "add_extraction_fields.sql"

# Link table -> the extraction group that enables it.
LINK_TABLES = {
    "BillLineItemAttachment": "bill",
    "BillCreditLineItemAttachment": "bill_credit",
    "ExpenseLineItemAttachment": "expense",
    "TaxpayerAttachment": "w9",
    "CertificateOfInsurance": "coi",
    "BusinessLicenseAttachment": "license",
    "ContractorsLicenseAttachment": "license",
}
PARSE_TABLES = ("BillLineItemAttachment", "BillCreditLineItemAttachment", "ExpenseLineItemAttachment")
BASE_TIME = dt.datetime(2026, 1, 1, 12, 0, 0)
BLOB_URL = "https://blob.example/f.pdf"
EXPIRED = "DATEADD(MINUTE, -30, SYSUTCDATETIME())"
FRESH = "SYSUTCDATETIME()"


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


def _attachment_create_table() -> str:
    match = re.search(r"CREATE TABLE \[dbo\]\.\[Attachment\].*?\n\);", ATTACHMENT_SQL.read_text(encoding="utf-8"), re.S)
    assert match, "CREATE TABLE [dbo].[Attachment] block not found in dbo.attachment.sql"
    return match.group(0)


def _install_schema(conn: pyodbc.Connection) -> None:
    cur = conn.cursor()
    for table in ("Attachment", *LINK_TABLES):
        cur.execute(f"IF OBJECT_ID('dbo.{table}', 'U') IS NOT NULL DROP TABLE dbo.[{table}]")
    cur.execute(_attachment_create_table())
    for table in LINK_TABLES:
        cur.execute(
            f"CREATE TABLE dbo.[{table}] (Id BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL, "
            "ParentId BIGINT NULL, AttachmentId BIGINT NOT NULL)"
        )
    conn.commit()
    for batch in _batches(ADD_FIELDS_SQL.read_text(encoding="utf-8")):
        cur.execute(batch)
    conn.commit()


@pytest.fixture(scope="module")
def conns():
    conn_str = os.environ["BUILDONE_TEST_SQLSERVER"]
    a = pyodbc.connect(conn_str, autocommit=False)
    b = pyodbc.connect(conn_str, autocommit=False)
    name = a.cursor().execute("SELECT DB_NAME()").fetchval()
    assert name == "buildone_test", f"refusing to run against database {name!r}"
    rcsi = a.cursor().execute(
        "SELECT is_read_committed_snapshot_on FROM sys.databases WHERE name = DB_NAME()"
    ).fetchval()
    assert rcsi == 1, "buildone_test must run READ_COMMITTED_SNAPSHOT ON"
    compat = a.cursor().execute(
        "SELECT compatibility_level FROM sys.databases WHERE name = DB_NAME()"
    ).fetchval()
    assert compat == 150, f"buildone_test must run at compatibility_level 150, found {compat}"
    _install_schema(a)
    b.cursor().execute("SET LOCK_TIMEOUT 5000")
    yield a, b
    a.close()
    b.close()


@pytest.fixture(autouse=True)
def _reset(conns):
    a, b = conns
    a.rollback()
    b.rollback()
    cur = a.cursor()
    for table in LINK_TABLES:
        cur.execute(f"DELETE FROM dbo.[{table}]")
    cur.execute("DELETE FROM dbo.Attachment")
    a.commit()
    yield
    a.rollback()
    b.rollback()


@contextlib.contextmanager
def _as_repo_connection(conn):
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()


@pytest.fixture
def use_connection(monkeypatch):
    def install(conn):
        monkeypatch.setattr(repo_mod, "get_connection", lambda *a, **k: _as_repo_connection(conn))

    return install


@pytest.fixture
def repo(conns, use_connection):
    use_connection(conns[0])
    return repo_mod.AttachmentRepository()


def _seed(
    conn,
    *,
    content_type="application/pdf",
    ext="pdf",
    status="pending",
    created=BASE_TIME,
    modified="expired",
    error=None,
    link=None,
):
    modified_sql = {"fresh": FRESH, "expired": EXPIRED, None: "NULL"}[modified]
    cur = conn.cursor()
    cur.execute(
        f"""
        INSERT INTO dbo.Attachment (CreatedDatetime, ModifiedDatetime, Filename, OriginalFilename,
            FileExtension, ContentType, FileSize, FileHash, BlobUrl, ExtractionStatus, ExtractionError)
        OUTPUT INSERTED.Id
        VALUES (?, {modified_sql}, 'f', 'f', ?, ?, 10, 'hash', ?, ?, ?)
        """,
        created, ext, content_type, BLOB_URL, status, error,
    )
    attachment_id = cur.fetchval()
    if link:
        _link(conn, link, attachment_id)
    return attachment_id


def _link(conn, table, attachment_id):
    conn.cursor().execute(f"INSERT INTO dbo.[{table}] (ParentId, AttachmentId) VALUES (1, ?)", attachment_id)


def _row(conn, attachment_id):
    return conn.cursor().execute(
        "SELECT ExtractionStatus, ExtractionError, ExtractedTextBlobUrl, ModifiedDatetime "
        "FROM dbo.Attachment WHERE Id = ?",
        attachment_id,
    ).fetchone()


def _expire(conn, attachment_id):
    conn.cursor().execute(f"UPDATE dbo.Attachment SET ModifiedDatetime = {EXPIRED} WHERE Id = ?", attachment_id)
    conn.commit()


def _claim_proc(conn, groups, limit):
    cur = conn.cursor()
    call_procedure(
        cursor=cur,
        name="ClaimAttachmentsForExtraction",
        params={"Groups": groups, "Limit": limit, "LeaseMinutes": 15},
    )
    return cur.fetchall()


# --- a. claim ------------------------------------------------------------------ #


@pytest.mark.parametrize("table, group", list(LINK_TABLES.items()))
def test_claim_returns_pending_pdf_linked_through_its_group(conns, repo, table, group):
    a, _ = conns
    att = _seed(a, link=table)
    a.commit()

    claimed = repo.claim_for_extraction(groups=[group], limit=10, lease_minutes=15)

    assert [c.id for c in claimed] == [att]


def test_claim_ignores_rows_whose_link_group_is_not_enabled(conns, repo):
    a, _ = conns
    _seed(a, link="BillLineItemAttachment")
    a.commit()

    assert repo.claim_for_extraction(groups=["w9", "coi"], limit=10, lease_minutes=15) == []


@pytest.mark.parametrize(
    "seed_kwargs",
    [
        pytest.param(dict(content_type="image/png", ext="png"), id="non-pdf"),
        pytest.param(dict(status="completed"), id="completed"),
        pytest.param(dict(status="failed", error="boom"), id="failed"),
        pytest.param(dict(status=None), id="never-extracted-null"),
        pytest.param(dict(status="processing", modified="fresh"), id="fresh-processing"),
    ],
)
def test_claim_skips_ineligible_rows(conns, repo, seed_kwargs):
    a, _ = conns
    _seed(a, link="BillLineItemAttachment", **seed_kwargs)
    a.commit()

    assert repo.claim_for_extraction(groups=["bill"], limit=10, lease_minutes=15) == []


def test_claim_reclaims_processing_rows_whose_lease_expired(conns, repo):
    a, _ = conns
    att = _seed(a, link="BillLineItemAttachment", status="processing", modified="expired")
    a.commit()

    claimed = repo.claim_for_extraction(groups=["bill"], limit=10, lease_minutes=15)

    assert [c.id for c in claimed] == [att]
    assert claimed[0].extraction_status == "processing"


def test_claim_orders_by_created_datetime_then_id(conns, repo):
    a, _ = conns
    late = _seed(a, link="BillLineItemAttachment", created=dt.datetime(2026, 1, 3))
    early = _seed(a, link="BillLineItemAttachment", created=dt.datetime(2026, 1, 1))
    tie = _seed(a, link="BillLineItemAttachment", created=dt.datetime(2026, 1, 1))
    a.commit()

    claimed = repo.claim_for_extraction(groups=["bill"], limit=10, lease_minutes=15)

    assert [c.id for c in claimed] == [early, tie, late]


def test_claim_respects_limit(conns, repo):
    a, _ = conns
    ids = [_seed(a, link="BillLineItemAttachment", created=dt.datetime(2026, 1, d)) for d in (1, 2, 3)]
    a.commit()

    claimed = repo.claim_for_extraction(groups=["bill"], limit=2, lease_minutes=15)

    assert [c.id for c in claimed] == ids[:2]


@pytest.mark.parametrize("limit", [None, 0, -1])
def test_claim_with_null_or_zero_limit_claims_nothing(conns, repo, limit):
    a, _ = conns
    att = _seed(a, link="BillLineItemAttachment")
    a.commit()

    assert repo.claim_for_extraction(groups=["bill"], limit=limit, lease_minutes=15) == []
    assert _row(a, att).ExtractionStatus == "pending"


def test_claimed_rows_are_processing_with_fresh_token_and_full_mapping(conns, repo):
    a, _ = conns
    att = _seed(a, link="BillLineItemAttachment")
    a.commit()

    [claimed] = repo.claim_for_extraction(groups=["bill"], limit=1, lease_minutes=15)

    assert claimed.extraction_status == "processing"
    assert claimed.blob_url == BLOB_URL
    assert claimed.file_hash == "hash"
    assert claimed.public_id and claimed.row_version
    stored = _row(a, att)
    assert stored.ExtractionStatus == "processing"
    assert stored.ModifiedDatetime == claimed.claim_modified_datetime
    age_seconds = a.cursor().execute(
        "SELECT DATEDIFF(SECOND, ModifiedDatetime, SYSUTCDATETIME()) FROM dbo.Attachment WHERE Id = ?", att
    ).fetchval()
    assert 0 <= age_seconds <= 60
    assert repo.claim_for_extraction(groups=["bill"], limit=10, lease_minutes=15) == []


@pytest.mark.parametrize("table", PARSE_TABLES)
def test_parse_flag_is_set_for_bill_bill_credit_and_expense_links(conns, repo, table):
    a, _ = conns
    _seed(a, link=table)
    a.commit()

    [claimed] = repo.claim_for_extraction(groups=[LINK_TABLES[table]], limit=1, lease_minutes=15)

    assert claimed.parse_invoice_number is True


def test_parse_flag_is_clear_when_the_attachment_is_also_a_w9(conns, repo):
    a, _ = conns
    att = _seed(a, link="BillLineItemAttachment")
    _link(a, "TaxpayerAttachment", att)
    a.commit()

    [claimed] = repo.claim_for_extraction(groups=["bill", "w9"], limit=1, lease_minutes=15)

    assert claimed.parse_invoice_number is False


@pytest.mark.parametrize("table", ["CertificateOfInsurance", "BusinessLicenseAttachment", "ContractorsLicenseAttachment", "TaxpayerAttachment"])
def test_parse_flag_is_clear_for_coi_license_and_w9_only(conns, repo, table):
    a, _ = conns
    _seed(a, link=table)
    a.commit()

    [claimed] = repo.claim_for_extraction(groups=[LINK_TABLES[table]], limit=1, lease_minutes=15)

    assert claimed.parse_invoice_number is False


def test_concurrent_claims_under_rcsi_take_different_rows_without_blocking(conns):
    a, b = conns
    ids = [_seed(a, link="BillLineItemAttachment") for _ in range(3)]
    a.commit()

    first = _claim_proc(a, "bill", 1)
    second = _claim_proc(b, "bill", 1)

    assert len(first) == 1 and len(second) == 1
    assert first[0].Id != second[0].Id
    assert {first[0].Id, second[0].Id} <= set(ids)
    a.commit()
    b.commit()


# --- b. ownership: terminal writes are guarded by the claim token ---------------- #


def test_terminal_write_with_a_stale_claim_token_changes_nothing(conns, repo):
    a, _ = conns
    att = _seed(a, link="BillLineItemAttachment")
    a.commit()
    [first] = repo.claim_for_extraction(groups=["bill"], limit=1, lease_minutes=15)
    _expire(a, att)
    [second] = repo.claim_for_extraction(groups=["bill"], limit=1, lease_minutes=15)
    assert second.claim_modified_datetime != first.claim_modified_datetime

    stale = repo.update_extraction(
        id=att,
        extraction_status="completed",
        extracted_text_blob_url="https://blob.example/stale.json",
        expected_status="processing",
        expected_modified_datetime=first.claim_modified_datetime,
    )

    assert stale is None
    row = _row(a, att)
    assert (row.ExtractionStatus, row.ExtractionError, row.ExtractedTextBlobUrl) == ("processing", None, None)

    current = repo.update_extraction(
        id=att,
        extraction_status="completed",
        extracted_text_blob_url="https://blob.example/current.json",
        expected_status="processing",
        expected_modified_datetime=second.claim_modified_datetime,
    )

    assert current.extraction_status == "completed"


def test_terminal_write_is_refused_once_the_row_left_processing(conns, repo):
    a, _ = conns
    att = _seed(a, link="BillLineItemAttachment")
    a.commit()
    [claimed] = repo.claim_for_extraction(groups=["bill"], limit=1, lease_minutes=15)
    a.cursor().execute("UPDATE dbo.Attachment SET ExtractionStatus = 'failed', ExtractionError = 'other' WHERE Id = ?", att)
    a.commit()

    result = repo.update_extraction(
        id=att,
        extraction_status="completed",
        expected_status="processing",
        expected_modified_datetime=claimed.claim_modified_datetime,
    )

    assert result is None
    row = _row(a, att)
    assert (row.ExtractionStatus, row.ExtractionError) == ("failed", "other")


def test_unguarded_update_keeps_head_error_semantics(conns, repo):
    a, _ = conns
    att = _seed(a, status="failed", error="old")
    a.commit()

    updated = repo.update_extraction(id=att, extraction_status="pending")

    assert updated.extraction_status == "pending"
    assert updated.extraction_error is None


def _invoice_number(conn, attachment_id):
    return conn.cursor().execute(
        "SELECT VendorInvoiceNumber FROM dbo.Attachment WHERE Id = ?", attachment_id
    ).fetchval()


def _link_taxpayer_with_foreign_id(conn, attachment_id):
    # The link's own Id is deliberately not the attachment id, so a subquery that resolves
    # a bare [Id] to TaxpayerAttachment.Id cannot pass by coincidence.
    cur = conn.cursor()
    cur.execute("SET IDENTITY_INSERT dbo.[TaxpayerAttachment] ON")
    cur.execute(
        "INSERT INTO dbo.[TaxpayerAttachment] (Id, ParentId, AttachmentId) VALUES (?, 1, ?)",
        attachment_id + 1000,
        attachment_id,
    )
    cur.execute("SET IDENTITY_INSERT dbo.[TaxpayerAttachment] OFF")


def test_w9_link_added_after_a_bill_claim_blocks_the_invoice_number(conns, repo):
    a, _ = conns
    att = _seed(a, link="BillLineItemAttachment")
    a.commit()
    [claimed] = repo.claim_for_extraction(groups=["bill"], limit=1, lease_minutes=15)
    assert claimed.parse_invoice_number is True
    _link_taxpayer_with_foreign_id(a, att)
    a.commit()

    written = repo.update_extraction(
        id=att,
        extraction_status="completed",
        vendor_invoice_number="INV-42",
        expected_status="processing",
        expected_modified_datetime=claimed.claim_modified_datetime,
    )

    assert written.extraction_status == "completed"
    assert _invoice_number(a, att) is None


def test_bill_claim_without_a_w9_link_stores_the_invoice_number(conns, repo):
    a, _ = conns
    att = _seed(a, link="BillLineItemAttachment")
    a.commit()
    [claimed] = repo.claim_for_extraction(groups=["bill"], limit=1, lease_minutes=15)

    repo.update_extraction(
        id=att,
        extraction_status="completed",
        vendor_invoice_number="INV-42",
        expected_status="processing",
        expected_modified_datetime=claimed.claim_modified_datetime,
    )

    assert _invoice_number(a, att) == "INV-42"


# --- c. backfill ----------------------------------------------------------------- #


def _seed_backfill(conn):
    return {
        "bill_null": _seed(conn, status=None, error="keep-null", link="BillLineItemAttachment"),
        "bill_null2": _seed(conn, status=None, link="BillLineItemAttachment"),
        "bill_failed": _seed(conn, status="failed", error="boom", link="BillLineItemAttachment"),
        "bill_completed": _seed(conn, status="completed", link="BillLineItemAttachment"),
        "bill_pending": _seed(conn, status="pending", error="x", link="BillLineItemAttachment"),
        "bill_nonpdf": _seed(conn, status=None, content_type="image/png", ext="png", link="BillLineItemAttachment"),
        "w9_null": _seed(conn, status=None, link="TaxpayerAttachment"),
        "w9_failed": _seed(conn, status="failed", error="w9-boom", link="TaxpayerAttachment"),
    }


def _statuses(conn):
    rows = conn.cursor().execute("SELECT Id, ExtractionStatus, ExtractionError FROM dbo.Attachment").fetchall()
    return {r.Id: (r.ExtractionStatus, r.ExtractionError) for r in rows}


class _CountingConnection:
    def __init__(self, conn, on_commit=None):
        self._conn = conn
        self._on_commit = on_commit
        self.commits = 0

    def cursor(self):
        return self._conn.cursor()

    def commit(self):
        self.commits += 1
        if self._on_commit:
            self._on_commit(self._conn)
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()


def test_backfill_dry_run_reports_counts_and_writes_nothing(conns, repo):
    a, _ = conns
    _seed_backfill(a)
    a.commit()
    before = _statuses(a)

    counts = repo.mark_pending_extraction_by_group(group="bill", include_failed=True, batch_size=500, dry_run=True)

    assert counts["affected"] == 0
    assert counts["never_extracted_pdfs"] == 2
    assert counts["failed_pdfs"] == 1
    assert counts["already_pending"] == 1
    assert counts["completed"] == 1
    assert _statuses(a) == before


def test_backfill_apply_marks_only_the_groups_null_pdfs_in_batches(conns, use_connection):
    a, _ = conns
    ids = _seed_backfill(a)
    a.commit()
    counting = _CountingConnection(a)
    use_connection(counting)

    result = repo_mod.AttachmentRepository().mark_pending_extraction_by_group(
        group="bill", include_failed=False, batch_size=1, dry_run=False
    )

    assert result["affected"] == 2
    # Three loop commits (two rows, then the empty batch) plus the connection's exit commit.
    assert counting.commits == 4
    states = _statuses(a)
    assert states[ids["bill_null"]] == ("pending", "keep-null")
    assert states[ids["bill_null2"]][0] == "pending"
    assert states[ids["bill_failed"]] == ("failed", "boom")
    assert states[ids["bill_completed"]][0] == "completed"
    assert states[ids["bill_pending"]] == ("pending", "x")
    assert states[ids["bill_nonpdf"]] == (None, None)
    assert states[ids["w9_null"]] == (None, None)
    assert states[ids["w9_failed"]] == ("failed", "w9-boom")


def test_include_failed_resets_only_this_groups_failed_rows_and_clears_only_their_error(conns, use_connection):
    a, _ = conns
    ids = _seed_backfill(a)
    a.commit()
    use_connection(a)

    result = repo_mod.AttachmentRepository().mark_pending_extraction_by_group(
        group="bill", include_failed=True, batch_size=500, dry_run=False
    )

    assert result["affected"] == 3
    states = _statuses(a)
    assert states[ids["bill_failed"]] == ("pending", None)
    assert states[ids["bill_null"]] == ("pending", "keep-null")
    assert states[ids["w9_failed"]] == ("failed", "w9-boom")
    assert states[ids["bill_completed"]][0] == "completed"


def test_failed_reset_is_not_starved_by_never_extracted_rows(conns, use_connection):
    a, _ = conns
    never = [_seed(a, status=None, link="BillLineItemAttachment") for _ in range(3)]
    failed = _seed(a, status="failed", error="boom", link="BillLineItemAttachment")
    a.commit()
    use_connection(a)

    result = repo_mod.AttachmentRepository().mark_pending_extraction_by_group(
        group="bill", include_failed=True, batch_size=1, dry_run=False
    )

    assert result["affected"] == 4
    states = _statuses(a)
    assert states[failed] == ("pending", None)
    assert all(states[i][0] == "pending" for i in never)


def test_backfill_does_not_repick_a_row_that_fails_again_during_the_run(conns, use_connection):
    a, _ = conns
    first = _seed(a, status="failed", error="boom", link="BillLineItemAttachment")
    second = _seed(a, status="failed", error="boom", link="BillLineItemAttachment")
    a.commit()

    def drain_fails_every_pending_row(conn):
        conn.cursor().execute(
            "UPDATE dbo.Attachment SET ExtractionStatus = 'failed', ExtractionError = 'drain-failed' "
            "WHERE ExtractionStatus = 'pending'"
        )

    counting = _CountingConnection(a, on_commit=drain_fails_every_pending_row)
    use_connection(counting)

    result = repo_mod.AttachmentRepository().mark_pending_extraction_by_group(
        group="bill", include_failed=True, batch_size=1, dry_run=False
    )

    assert result["affected"] == 1
    # Two loop commits (one reset row, then the empty batch) plus the connection's exit commit.
    assert counting.commits == 3
    states = _statuses(a)
    assert states[first] == ("failed", "drain-failed")
    assert states[second] == ("failed", "boom")


# --- d. legacy read ---------------------------------------------------------------- #


def test_read_pending_extraction_returns_pending_and_null_but_not_processing(conns):
    a, _ = conns
    pending = _seed(a, status="pending")
    never = _seed(a, status=None)
    _seed(a, status="processing", modified="fresh")
    _seed(a, status="processing", modified="expired")
    _seed(a, status="completed")
    _seed(a, status="failed", error="boom")
    a.commit()

    cur = a.cursor()
    call_procedure(cursor=cur, name="ReadAttachmentsPendingExtraction", params={})
    ids = {r.Id for r in cur.fetchall()}

    assert ids == {pending, never}
