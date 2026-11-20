"""U-797c1: pins for clauses that survived mutation testing (EM, 2026-11-20).

Each test names the mutant it kills. These pin existing behaviour; none changes code."""
from __future__ import annotations

import re
from types import SimpleNamespace

from shared.authz import provenance as prov
from tests.sproc_text import REPO_ROOT

SQL_PATH = REPO_ROOT / "entities" / "review" / "sql" / "dbo.review_notification.sql"
BACKFILL_PATH = REPO_ROOT / "scripts" / "migrations" / "2026_11_20_u797c1_backfill_review_notification.sql"


def _cl(project_id, work_date):
    return SimpleNamespace(Kind="contract_labor", ProjectId=project_id, WorkDate=work_date)


# --- select_notification: the missing-input guards ---------------------------------------

def test_cl_refuses_when_request_and_notification_project_are_both_missing():
    """Kills 'None project allowed': None == None must not count as the same project."""
    status, reason, _ = prov.select_notification(
        [(_cl(None, "2026-11-16"), set())], ("contract_labor", 5),
        project_id=None, work_date="2026-11-16",
    )
    assert (status, reason) == ("REFUSED", "wrong_project_or_date")


def test_cl_refuses_when_request_and_notification_date_are_both_missing():
    """Kills 'None request date allowed': two missing dates must not count as the same date."""
    status, reason, _ = prov.select_notification(
        [(_cl(7, None), set())], ("contract_labor", 5), project_id=7, work_date=None,
    )
    assert (status, reason) == ("REFUSED", "wrong_project_or_date")


def test_compact_iso_date_is_not_accepted():
    """Kills 'lax string parse': Python accepts the compact form; the binder must not."""
    assert prov._norm_work_date("20261116") is None


# --- the CREATE TABLE text carries the delete actions itself -----------------------------

def _create_table(name: str) -> str:
    text = SQL_PATH.read_text(encoding="utf-8")
    start = text.index(f"CREATE TABLE [dbo].[{name}]")
    return text[start:text.index(");", start)]


def test_create_table_review_fk_sets_null_on_delete():
    body = _create_table("ReviewNotification")
    assert re.search(r"\[FK_ReviewNotification_Review\][^\n]*ON DELETE SET NULL", body)


def test_create_table_document_fks_cascade_on_delete():
    body = _create_table("ReviewNotificationTarget")
    for fk in ("Notification", "Bill", "Expense", "BillCredit", "ContractLabor"):
        assert re.search(rf"\[FK_ReviewNotificationTarget_{fk}\][^\n]*ON DELETE CASCADE", body), fk


# --- the backfill's crew-claim filter -----------------------------------------------------

def _claims_batch() -> str:
    text = BACKFILL_PATH.read_text(encoding="utf-8")
    start = text.index("#u797c1_cl_claims")
    return text[start:start + 2500]


def test_backfill_skips_claims_with_no_work_date():
    """Kills 'NULL WorkDate claims kept' (the round-1 P1: a NULL date crashes the unique index)."""
    assert re.search(r"WHERE\s+c\.\[WorkDate\]\s+IS NOT NULL", _claims_batch())


def test_backfill_keeps_one_claim_per_project_and_date():
    """Kills 'no dedupe per project/date'."""
    batch = _claims_batch()
    assert re.search(r"PARTITION BY\s+c\.\[ProjectId\],\s*c\.\[WorkDate\]", batch)
    assert re.search(r"rn\s*=\s*1", batch)


def test_target_document_columns_have_filtered_indexes_for_cascaded_deletes():
    text = SQL_PATH.read_text(encoding="utf-8")
    for col in ("BillId", "ExpenseId", "BillCreditId", "ContractLaborId"):
        assert re.search(
            rf"^\s*CREATE INDEX IX_ReviewNotificationTarget_{col} ON \[dbo\]\.\[ReviewNotificationTarget\] "
            rf"\(\[{col}\]\) WHERE \[{col}\] IS NOT NULL;", text, re.MULTILINE), col


def test_no_fk_repair_blocks_remain():
    # The tables have never been applied anywhere; CREATE TABLE declares the delete actions once.
    assert "Guarded repair" not in SQL_PATH.read_text(encoding="utf-8")
