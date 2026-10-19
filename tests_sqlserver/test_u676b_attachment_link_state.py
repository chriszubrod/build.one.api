"""U-676b: dbo.ReadAttachmentLinkState against local SQL Server."""
from __future__ import annotations

import os
import re
from pathlib import Path

import pyodbc
import pytest

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
    raise RuntimeError("tests_sqlserver refuses non-local SERVER")

REPO_ROOT = Path(__file__).resolve().parents[1]
LINK_STATE_SQL = REPO_ROOT / "entities/attachment/sql/dbo.attachment_link_state.sql"


def _sqlserver_connect(conn_str: str, *, autocommit: bool = True):
    return pyodbc.connect(conn_str, autocommit=autocommit)


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


def _exec_file(cur, path: Path) -> None:
    for batch in _batches(path.read_text(encoding="utf-8")):
        cur.execute(batch)


def _drain(cur) -> None:
    while True:
        try:
            if not cur.nextset():
                break
        except pyodbc.Error:
            break


def _exec_rows(cur, sql: str, *params):
    cur.execute(sql, params)
    rows = cur.fetchall() if cur.description else []
    _drain(cur)
    return rows


def _bootstrap_link_tables(cur) -> None:
    cur.execute(
        """
        IF SCHEMA_ID('ms') IS NULL EXEC('CREATE SCHEMA ms');
        IF SCHEMA_ID('box') IS NULL EXEC('CREATE SCHEMA box');
        IF OBJECT_ID('dbo.Attachment', 'U') IS NULL
            CREATE TABLE dbo.Attachment (
                Id BIGINT PRIMARY KEY,
                CreatedByUserId BIGINT NOT NULL DEFAULT 17,
                CompanyId BIGINT NOT NULL DEFAULT 1
            );
        IF COL_LENGTH('dbo.Attachment', 'CreatedByUserId') IS NULL
            ALTER TABLE dbo.Attachment ADD CreatedByUserId BIGINT NOT NULL DEFAULT 17;
        IF COL_LENGTH('dbo.Attachment', 'CompanyId') IS NULL
            ALTER TABLE dbo.Attachment ADD CompanyId BIGINT NOT NULL DEFAULT 1;
        IF OBJECT_ID('dbo.BillLineItemAttachment', 'U') IS NULL
            CREATE TABLE dbo.BillLineItemAttachment (AttachmentId BIGINT NOT NULL);
        IF OBJECT_ID('dbo.ExpenseLineItemAttachment', 'U') IS NULL
            CREATE TABLE dbo.ExpenseLineItemAttachment (AttachmentId BIGINT NOT NULL);
        IF OBJECT_ID('dbo.BillCreditLineItemAttachment', 'U') IS NULL
            CREATE TABLE dbo.BillCreditLineItemAttachment (AttachmentId BIGINT NOT NULL);
        IF OBJECT_ID('dbo.InvoiceAttachment', 'U') IS NULL
            CREATE TABLE dbo.InvoiceAttachment (AttachmentId BIGINT NOT NULL);
        IF OBJECT_ID('dbo.InvoiceLineItemAttachment', 'U') IS NULL
            CREATE TABLE dbo.InvoiceLineItemAttachment (AttachmentId BIGINT NOT NULL);
        IF OBJECT_ID('dbo.BusinessLicenseAttachment', 'U') IS NULL
            CREATE TABLE dbo.BusinessLicenseAttachment (AttachmentId BIGINT NOT NULL);
        IF OBJECT_ID('dbo.ContractorsLicenseAttachment', 'U') IS NULL
            CREATE TABLE dbo.ContractorsLicenseAttachment (AttachmentId BIGINT NOT NULL);
        IF OBJECT_ID('dbo.AssetAttachment', 'U') IS NULL
            CREATE TABLE dbo.AssetAttachment (AttachmentId BIGINT NOT NULL);
        IF OBJECT_ID('dbo.TaxpayerAttachment', 'U') IS NULL
            CREATE TABLE dbo.TaxpayerAttachment (AttachmentId BIGINT NOT NULL);
        IF OBJECT_ID('dbo.BillTextCodingSuggestion', 'U') IS NULL
            CREATE TABLE dbo.BillTextCodingSuggestion (AttachmentId BIGINT NOT NULL);
        IF OBJECT_ID('dbo.CodingSuggestion', 'U') IS NULL
            CREATE TABLE dbo.CodingSuggestion (AttachmentId BIGINT NULL);
        IF OBJECT_ID('dbo.CertificateOfInsurance', 'U') IS NULL
            CREATE TABLE dbo.CertificateOfInsurance (AttachmentId BIGINT NULL);
        IF OBJECT_ID('ms.DriveItemAttachment', 'U') IS NULL
            CREATE TABLE ms.DriveItemAttachment (AttachmentId BIGINT NOT NULL);
        IF OBJECT_ID('box.File', 'U') IS NULL
            CREATE TABLE box.[File] (AttachmentId BIGINT NULL);
        """
    )
    _drain(cur)


_TEST_ATTACHMENT_IDS = tuple(range(900_001, 900_030))


def _cleanup_test_rows(cur) -> None:
    for aid in _TEST_ATTACHMENT_IDS:
        cur.execute(
            f"""
            DELETE FROM box.[File] WHERE AttachmentId = {aid};
            DELETE FROM ms.DriveItemAttachment WHERE AttachmentId = {aid};
            DELETE FROM dbo.CertificateOfInsurance WHERE AttachmentId = {aid};
            DELETE FROM dbo.CodingSuggestion WHERE AttachmentId = {aid};
            DELETE FROM dbo.BillTextCodingSuggestion WHERE AttachmentId = {aid};
            DELETE FROM dbo.TaxpayerAttachment WHERE AttachmentId = {aid};
            DELETE FROM dbo.AssetAttachment WHERE AttachmentId = {aid};
            DELETE FROM dbo.ContractorsLicenseAttachment WHERE AttachmentId = {aid};
            DELETE FROM dbo.BusinessLicenseAttachment WHERE AttachmentId = {aid};
            DELETE FROM dbo.InvoiceLineItemAttachment WHERE AttachmentId = {aid};
            DELETE FROM dbo.InvoiceAttachment WHERE AttachmentId = {aid};
            DELETE FROM dbo.BillCreditLineItemAttachment WHERE AttachmentId = {aid};
            DELETE FROM dbo.ExpenseLineItemAttachment WHERE AttachmentId = {aid};
            DELETE FROM dbo.BillLineItemAttachment WHERE AttachmentId = {aid};
            DELETE FROM dbo.Attachment WHERE Id = {aid};
            """
        )
    _drain(cur)


@pytest.fixture
def conn():
    conn_str = os.environ["BUILDONE_TEST_SQLSERVER"]
    c = _sqlserver_connect(conn_str)
    cur = c.cursor()
    _bootstrap_link_tables(cur)
    _exec_file(cur, LINK_STATE_SQL)
    _cleanup_test_rows(cur)
    yield c
    _cleanup_test_rows(cur)
    c.close()


def _link_state(
    cur,
    attachment_id: int,
    *,
    actor_user_id: int = 17,
    company_id: int = 1,
    is_system_admin: bool = False,
):
    rows = _exec_rows(
        cur,
        "EXEC dbo.ReadAttachmentLinkState @AttachmentId=?, @ActorUserId=?, @CompanyId=?, @IsSystemAdmin=?",
        attachment_id,
        actor_user_id,
        company_id,
        1 if is_system_admin else 0,
    )
    assert len(rows) == 1
    return bool(rows[0].IsLinked), bool(rows[0].IsTaxpayer)


def test_unlinked_non_taxpayer(conn):
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO dbo.Attachment (Id, CreatedByUserId, CompanyId) VALUES (900001, 17, 1), (900002, 17, 1)"
    )
    linked, taxpayer = _link_state(cur, 900001)
    assert linked is False
    assert taxpayer is False


def test_bill_line_linked(conn):
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO dbo.Attachment (Id, CreatedByUserId, CompanyId) VALUES (900005, 17, 1)"
    )
    cur.execute(
        "INSERT INTO dbo.BillLineItemAttachment (AttachmentId) VALUES (900005)"
    )
    linked, taxpayer = _link_state(cur, 900005)
    assert linked is True
    assert taxpayer is False


def test_taxpayer_linked_and_flag(conn):
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO dbo.Attachment (Id, CreatedByUserId, CompanyId) VALUES (900007, 17, 1)"
    )
    cur.execute("INSERT INTO dbo.TaxpayerAttachment (AttachmentId) VALUES (900007)")
    linked, taxpayer = _link_state(cur, 900007)
    assert linked is True
    assert taxpayer is True


def test_coding_suggestion_linked(conn):
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO dbo.Attachment (Id, CreatedByUserId, CompanyId) VALUES (900011, 17, 1)"
    )
    cur.execute("INSERT INTO dbo.CodingSuggestion (AttachmentId) VALUES (900011)")
    linked, taxpayer = _link_state(cur, 900011)
    assert linked is True
    assert taxpayer is False


def test_certificate_of_insurance_linked(conn):
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO dbo.Attachment (Id, CreatedByUserId, CompanyId) VALUES (900012, 17, 1)"
    )
    cur.execute("INSERT INTO dbo.CertificateOfInsurance (AttachmentId) VALUES (900012)")
    linked, taxpayer = _link_state(cur, 900012)
    assert linked is True
    assert taxpayer is False


def test_box_file_linked(conn):
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO dbo.Attachment (Id, CreatedByUserId, CompanyId) VALUES (900013, 17, 1)"
    )
    cur.execute("INSERT INTO box.[File] (AttachmentId) VALUES (900013)")
    linked, taxpayer = _link_state(cur, 900013)
    assert linked is True
    assert taxpayer is False


def test_non_owner_not_readable(conn):
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO dbo.Attachment (Id, CreatedByUserId, CompanyId) VALUES (900020, 17, 1)"
    )
    linked, _ = _link_state(cur, 900020, actor_user_id=99, company_id=1)
    assert linked is True


def test_admin_bypasses_ownership(conn):
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO dbo.Attachment (Id, CreatedByUserId, CompanyId) VALUES (900021, 17, 1)"
    )
    linked, _ = _link_state(cur, 900021, actor_user_id=99, is_system_admin=True)
    assert linked is False


def test_wrong_company_not_readable(conn):
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO dbo.Attachment (Id, CreatedByUserId, CompanyId) VALUES (900022, 17, 1)"
    )
    linked, _ = _link_state(cur, 900022, actor_user_id=17, company_id=2)
    assert linked is True


def test_reapply_sql_idempotent(conn):
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO dbo.Attachment (Id, CreatedByUserId, CompanyId) VALUES (900009, 17, 1)"
    )
    _exec_file(cur, LINK_STATE_SQL)
    linked, taxpayer = _link_state(cur, 900009)
    assert linked is False and taxpayer is False


def _is_owned(cur, attachment_id: int, **kw) -> bool:
    rows = _exec_rows(
        cur,
        "EXEC dbo.ReadAttachmentLinkState @AttachmentId=?, @ActorUserId=?, @CompanyId=?, @IsSystemAdmin=?",
        attachment_id,
        kw.get("actor_user_id", 17),
        kw.get("company_id", 1),
        1 if kw.get("is_system_admin") else 0,
    )
    return bool(rows[0].IsOwned)


def test_u676p_is_owned_separates_mine_but_linked_from_not_mine(conn):
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO dbo.Attachment (Id, CreatedByUserId, CompanyId) VALUES (900030, 17, 1)"
    )
    cur.execute("INSERT INTO dbo.BillLineItemAttachment (AttachmentId) VALUES (900030)")
    # Mine and linked: IsLinked=1 and IsOwned=1.
    assert _link_state(cur, 900030)[0] is True
    assert _is_owned(cur, 900030) is True
    # Other user, other company: not owned (IsLinked also 1, as before).
    assert _is_owned(cur, 900030, actor_user_id=99) is False
    assert _is_owned(cur, 900030, company_id=2) is False
    # System admin owns everything; NULL actor/company owns nothing.
    assert _is_owned(cur, 900030, actor_user_id=99, is_system_admin=True) is True
    assert _is_owned(cur, 900030, actor_user_id=None) is False
    assert _is_owned(cur, 900030, company_id=None) is False
