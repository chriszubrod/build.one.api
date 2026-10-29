"""U-744: ReadBillIntakePredictionsForAccuracy additive columns + BillTextCodingSuggestion join."""
from __future__ import annotations

import os
from datetime import datetime

import pytest

from tests_sqlserver.fixtures.u701a_real_schema import (
    REPO_ROOT,
    _drain_cursor,
    exec_sql_file,
    u701a_real_schema_conn,  # noqa: F401 (fixture)
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("BUILDONE_TEST_SQLSERVER"),
    reason="BUILDONE_TEST_SQLSERVER not set",
)

INTAKE_SQL = REPO_ROOT / "entities/bill_intake_prediction/sql/dbo.bill_intake_prediction.sql"
TEXT_SQL = REPO_ROOT / "entities/coding_suggestion/sql/bill_text_coding_suggestion.sql"
VOTE_SQL = REPO_ROOT / "entities/coding_suggestion/sql/bill_coding_suggestion.sql"

NEW_COLUMNS = (
    "ModelVersion", "CodingMode", "CodingChosenProjectId", "FinalizedDatetime", "CreatedDatetime",
    "TextDecisionSource", "TextStatus", "TextModelVersion", "TextSubCostCodeId", "TextProjectId",
    "TextEscalateModel", "TextEscalateSubCostCodeId", "TextEscalateProjectId",
    "TextEscalateConfidence", "TextEscalateError",
    # U-745 (appended after the U-744 block).
    "M0SubCostCodeId", "M0Confidence", "TextM0SubCostCodeId", "TextM0Confidence",
)
# The pre-U-744 column order, which must be unchanged (prefix of the new shape).
OLD_COLUMNS = (
    "Id", "BillId", "IntakeSource", "Status", "PredVendorId", "PredBillNumber", "PredBillDate",
    "PredTotalAmount", "PredSubCostCodeId", "PredProjectId", "FinalVendorId", "FinalBillNumber",
    "FinalBillDate", "FinalTotalAmount", "FinalSubCostCodeId", "FinalProjectId", "CostUsd",
    "LatencyMsTotal", "SimilarPredSubCostCodeId", "SimilarConfidence", "CodingSource",
    "CodingEscalation", "PrimaryModel", "PrimarySubCostCodeId", "PrimaryProjectId",
    "PrimaryConfidence", "PrimaryError", "FallbackModel", "FallbackSubCostCodeId",
    "FallbackConfidence", "SimilarNeighbourCount", "VendorName",
)

NOW = datetime(2026, 10, 29, 12, 0, 0)


@pytest.fixture
def conn(u701a_real_schema_conn):  # noqa: F811
    cur = u701a_real_schema_conn.cursor()
    cur.execute("SELECT DB_NAME()")
    assert cur.fetchone()[0] == "buildone_test"
    cur.execute(
        """
        IF OBJECT_ID('dbo.[User]', 'U') IS NULL
            CREATE TABLE dbo.[User](Id BIGINT NOT NULL PRIMARY KEY, IsSystemAdmin BIT NOT NULL DEFAULT 0);
        IF NOT EXISTS (SELECT 1 FROM dbo.[User] WHERE Id = 17)
            INSERT dbo.[User](Id, IsSystemAdmin) VALUES (17, 0);
        """
    )
    exec_sql_file(cur, VOTE_SQL)
    exec_sql_file(cur, TEXT_SQL)
    exec_sql_file(cur, INTAKE_SQL)
    exec_sql_file(cur, INTAKE_SQL)  # idempotent re-apply
    _drain_cursor(cur)
    yield u701a_real_schema_conn


def _prediction(cur, *, att_id: int, bill_id, finalized: bool, mode: str = "A") -> None:
    cur.execute(
        """
        INSERT dbo.[BillIntakePrediction](
            CreatedDatetime, ModifiedDatetime, AttachmentId, BillId, IntakeSource, ModelVersion,
            Status, PredSubCostCodeId, PredProjectId, CodingMode, CodingChosenProjectId,
            CodingSource, FinalSubCostCodeId, FinalProjectId, FinalizedDatetime
        ) VALUES (?, ?, ?, ?, 'pdf_drop', 'u701b-text-v2', 'ok', 100, 5, ?, 5,
                  'primary', 100, 5, ?)
        """,
        NOW, NOW, att_id, bill_id, mode, NOW if finalized else None,
    )


def _bill_and_attachment(cur, *, bill_id: int, att_id: int) -> None:
    cur.execute(
        """
        IF NOT EXISTS (SELECT 1 FROM dbo.[Attachment] WHERE [Id] = ?)
        BEGIN
            SET IDENTITY_INSERT dbo.[Attachment] ON;
            INSERT dbo.[Attachment]([Id], [CreatedDatetime], [Filename], [OriginalFilename],
                [ContentType], [FileSize], [BlobUrl], [ExtractionStatus], [ExtractedTextBlobUrl],
                [ExtractedDatetime])
            VALUES (?, SYSUTCDATETIME(), N'f.pdf', N'f.pdf', N'application/pdf', 1,
                N'https://b/x', N'completed', N'https://b/t', SYSUTCDATETIME());
            SET IDENTITY_INSERT dbo.[Attachment] OFF;
        END;
        IF NOT EXISTS (SELECT 1 FROM dbo.[Bill] WHERE [Id] = ?)
        BEGIN
            SET IDENTITY_INSERT dbo.[Bill] ON;
            INSERT dbo.[Bill]([Id], [CreatedDatetime], [BillDate], [DueDate], [Status],
                [VendorId], [CreatedByUserId])
            VALUES (?, SYSUTCDATETIME(), SYSUTCDATETIME(), SYSUTCDATETIME(), N'draft', 1, 17);
            SET IDENTITY_INSERT dbo.[Bill] OFF;
        END;
        """,
        att_id, att_id, bill_id, bill_id,
    )


def _suggestion(cur, *, bill_id: int, att_id: int, decision: str, escalate_scc) -> None:
    _bill_and_attachment(cur, bill_id=bill_id, att_id=att_id)
    cur.execute(
        """
        INSERT dbo.[BillTextCodingSuggestion](
            CreatedDatetime, ModifiedDatetime, BillId, AttachmentId, ExtractedDatetime, Status,
            ModelVersion, SubCostCodeId, ProjectId, DecisionSource, EscalateModel,
            EscalateSubCostCodeId, EscalateProjectId, EscalateConfidence, EscalateError
        ) VALUES (?, ?, ?, ?, ?, 'done', 'u701b-text-v2', 1, 1, ?, 'claude-sonnet',
                  ?, 5, 0.8100, ?)
        """,
        NOW, NOW, bill_id, att_id, NOW, decision, escalate_scc,
        None if escalate_scc is not None else "daily_cap",
    )


def _read(cur):
    cur.execute("EXEC dbo.ReadBillIntakePredictionsForAccuracy")
    cols = [d[0] for d in cur.description]
    return cols, cur.fetchall()


def test_columns_appended_and_old_order_kept(conn):
    cur = conn.cursor()
    cols, _ = _read(cur)
    assert tuple(cols[: len(OLD_COLUMNS)]) == OLD_COLUMNS
    assert tuple(cols[len(OLD_COLUMNS):]) == NEW_COLUMNS


def test_escalate_join_and_finalized_only(conn):
    cur = conn.cursor()
    _prediction(cur, att_id=10, bill_id=1, finalized=True, mode="B")
    _prediction(cur, att_id=11, bill_id=2, finalized=False)  # not finalized: excluded
    _prediction(cur, att_id=12, bill_id=None, finalized=True)  # unbound: no text row
    _suggestion(cur, bill_id=1, att_id=10, decision="escalate", escalate_scc=101)
    _suggestion(cur, bill_id=2, att_id=11, decision="primary", escalate_scc=None)
    _, rows = _read(cur)
    by_att = {r.Id: r for r in rows}
    assert len(rows) == 2
    bound = next(r for r in rows if r.BillId == 1)
    assert bound.TextDecisionSource == "escalate"
    assert bound.TextEscalateSubCostCodeId == 101
    assert bound.TextEscalateModel == "claude-sonnet"
    assert bound.TextEscalateError is None
    assert bound.TextStatus == "done"
    assert bound.ModelVersion == "u701b-text-v2"
    assert bound.CodingMode == "B"
    assert bound.CodingChosenProjectId == 5
    assert bound.FinalizedDatetime is not None and bound.CreatedDatetime is not None
    unbound = next(r for r in rows if r.BillId is None)
    assert unbound.TextDecisionSource is None and unbound.TextStatus is None
    assert all(r.FinalizedDatetime is not None for r in by_att.values())


def test_one_row_per_prediction_even_with_multiple_suggestion_rows(conn):
    cur = conn.cursor()
    # Relax the unique index inside this rolled-back transaction to simulate a duplicate.
    cur.execute("DROP INDEX [UQ_BillTextCodingSuggestion_BillId] ON dbo.[BillTextCodingSuggestion]")
    _prediction(cur, att_id=10, bill_id=1, finalized=True)
    _suggestion(cur, bill_id=1, att_id=10, decision="primary", escalate_scc=None)
    _suggestion(cur, bill_id=1, att_id=10, decision="escalate", escalate_scc=102)
    _, rows = _read(cur)
    assert len(rows) == 1
    # Deterministic: the newest (highest Id) suggestion row wins.
    assert rows[0].TextDecisionSource == "escalate"
    assert rows[0].TextEscalateSubCostCodeId == 102


def test_two_predictions_on_one_bill_share_its_text_row(conn):
    cur = conn.cursor()
    _prediction(cur, att_id=10, bill_id=1, finalized=True)
    _prediction(cur, att_id=11, bill_id=1, finalized=True)
    _suggestion(cur, bill_id=1, att_id=10, decision="history", escalate_scc=None)
    _, rows = _read(cur)
    assert len(rows) == 2
    assert {r.TextDecisionSource for r in rows} == {"history"}


def test_escalate_error_surfaces(conn):
    cur = conn.cursor()
    _prediction(cur, att_id=10, bill_id=1, finalized=True)
    _suggestion(cur, bill_id=1, att_id=10, decision="primary", escalate_scc=None)
    _, rows = _read(cur)
    assert len(rows) == 1
    assert rows[0].TextEscalateSubCostCodeId is None
    assert rows[0].TextEscalateError == "daily_cap"
