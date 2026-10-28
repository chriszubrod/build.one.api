"""U-701b3: per-rung intake + text suggestion columns on local SQL Server."""
from __future__ import annotations

import os
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from tests_sqlserver.fixtures.u701a_real_schema import (
    REPO_ROOT,
    _drain_cursor,
    exec_sql_file,
    u701a_real_schema_conn,
)


def _insert_attachment(cur, att_id: int) -> None:
    cur.execute(
        "SET IDENTITY_INSERT dbo.[Attachment] ON; "
        "INSERT dbo.[Attachment]([Id], [CreatedDatetime], [Filename], [OriginalFilename], "
        "[ContentType], [FileSize], [BlobUrl], [ExtractionStatus], [ExtractedTextBlobUrl], "
        "[ExtractedDatetime]) "
        "VALUES (?, SYSUTCDATETIME(), N'f.pdf', N'f.pdf', N'application/pdf', 1, "
        "N'https://b/x', N'completed', N'https://b/t', SYSUTCDATETIME()); "
        "SET IDENTITY_INSERT dbo.[Attachment] OFF;",
        (att_id,),
    )


def _insert_bill_stack(cur, *, bill_id: int = 1, att_id: int = 10) -> None:
    _insert_attachment(cur, att_id)
    cur.execute(
        f"""
        SET IDENTITY_INSERT dbo.[Bill] ON;
        INSERT dbo.[Bill](
            [Id], [CreatedDatetime], [BillDate], [DueDate], [Status], [VendorId], [CreatedByUserId]
        ) VALUES (
            {bill_id}, SYSUTCDATETIME(), SYSUTCDATETIME(), SYSUTCDATETIME(), N'draft', 1, 17
        );
        SET IDENTITY_INSERT dbo.[Bill] OFF;
        """
    )
    cur.execute(
        f"""
        SET IDENTITY_INSERT dbo.[BillLineItem] ON;
        INSERT dbo.[BillLineItem](
            [Id], [CreatedDatetime], [BillId], [SubCostCodeId], [ProjectId], [Amount], [IsDraft]
        ) VALUES (100, SYSUTCDATETIME(), {bill_id}, 1, 1, 100.00, 1);
        SET IDENTITY_INSERT dbo.[BillLineItem] OFF;
        """
    )
    cur.execute(
        """
        SET IDENTITY_INSERT dbo.[BillLineItemAttachment] ON;
        INSERT dbo.[BillLineItemAttachment](
            [Id], [CreatedDatetime], [BillLineItemId], [AttachmentId]
        ) VALUES (1000, SYSUTCDATETIME(), 100, ?);
        SET IDENTITY_INSERT dbo.[BillLineItemAttachment] OFF;
        """,
        (att_id,),
    )

pytestmark = pytest.mark.skipif(
    not os.environ.get("BUILDONE_TEST_SQLSERVER"),
    reason="BUILDONE_TEST_SQLSERVER not set",
)

INTAKE_SQL = REPO_ROOT / "entities/bill_intake_prediction/sql/dbo.bill_intake_prediction.sql"
TEXT_SQL = REPO_ROOT / "entities/coding_suggestion/sql/bill_text_coding_suggestion.sql"
VOTE_SQL = REPO_ROOT / "entities/coding_suggestion/sql/bill_coding_suggestion.sql"


def _assert_buildone_test(cur) -> None:
    cur.execute("SELECT DB_NAME()")
    assert cur.fetchone()[0] == "buildone_test"


@pytest.fixture
def u701b3_schema_conn(u701a_real_schema_conn):
    cur = u701a_real_schema_conn.cursor()
    _assert_buildone_test(cur)
    cur.execute(
        """
        IF OBJECT_ID('dbo.[User]', 'U') IS NULL
            CREATE TABLE dbo.[User](
                Id BIGINT NOT NULL PRIMARY KEY,
                IsSystemAdmin BIT NOT NULL DEFAULT 0
            );
        IF NOT EXISTS (SELECT 1 FROM dbo.[User] WHERE Id = 17)
            INSERT dbo.[User](Id, IsSystemAdmin) VALUES (17, 0);
        """
    )
    exec_sql_file(cur, VOTE_SQL)
    exec_sql_file(cur, TEXT_SQL)
    exec_sql_file(cur, INTAKE_SQL)
    exec_sql_file(cur, INTAKE_SQL)
    exec_sql_file(cur, TEXT_SQL)
    yield u701a_real_schema_conn


def test_intake_upsert_round_trips_ladder_columns(u701b3_schema_conn):
    cur = u701b3_schema_conn.cursor()
    _insert_attachment(cur, 10)
    att_id = 10
    cur.execute(
        """
        EXEC dbo.UpsertBillIntakePrediction
            @AttachmentId=?,
            @IntakeSource='pdf_drop',
            @ModelVersion='u701b-text-v2',
            @Status='ok',
            @PredSubCostCodeId=100,
            @CodingSource='primary',
            @CodingEscalation='low_confidence',
            @PrimaryModel='claude-haiku-5-5',
            @PrimarySubCostCodeId=100,
            @PrimaryConfidence=0.6900,
            @SimilarPredSubCostCodeId=200,
            @SimilarConfidence=0.7500,
            @SimilarNeighbourCount=3
        """,
        att_id,
    )
    _drain_cursor(cur)
    cur.execute(
        "EXEC dbo.ReadBillIntakePredictionByAttachmentId @AttachmentId=?",
        att_id,
    )
    row = cur.fetchone()
    assert row.CodingSource == "primary"
    assert row.PrimarySubCostCodeId == 100
    assert row.SimilarPredSubCostCodeId == 200
    assert row.SimilarNeighbourCount == 3


def test_text_complete_and_claim_reset(u701b3_schema_conn):
    cur = u701b3_schema_conn.cursor()
    _insert_bill_stack(cur, bill_id=1, att_id=10)
    bill_id, att_id = 1, 10
    now = datetime(2026, 5, 1, 12, 0, 0)
    cur.execute(
        """
        INSERT dbo.[BillTextCodingSuggestion](
            CreatedDatetime, ModifiedDatetime, BillId, AttachmentId,
            ExtractedDatetime, Status, ModelVersion
        ) VALUES (?, ?, ?, ?, ?, 'pending', 'u701b-text-v2')
        """,
        now,
        now,
        bill_id,
        att_id,
        now,
    )
    cur.execute(
        """
        EXEC dbo.CompleteBillTextCodingSuggestion
            @BillId=?, @AttachmentId=?, @ExtractedDatetime=?,
            @Status='done', @ModelVersion='u701b-text-v2',
            @DecisionSource='escalate', @EscalateModel='claude-sonnet-5-5',
            @EscalateSubCostCodeId=100, @EscalateConfidence=0.9000
        """,
        bill_id,
        att_id,
        now,
    )
    row = cur.fetchone()
    assert row.DecisionSource == "escalate"
    assert row.EscalateSubCostCodeId == 100

    cur.execute(
        """
        UPDATE dbo.[Bill]
        SET Status='draft', VendorId=1, CreatedDatetime=DATEADD(DAY, -1, SYSUTCDATETIME())
        WHERE Id=?
        """,
        bill_id,
    )
    cur.execute(
        """
        UPDATE dbo.[BillTextCodingSuggestion]
        SET Status='pending', ModifiedDatetime=DATEADD(HOUR, -1, SYSUTCDATETIME())
        WHERE BillId=?
        """,
        bill_id,
    )
    cur.execute("EXEC dbo.ClaimNextBillForTextCodingSuggestion")
    cur.execute(
        "SELECT DecisionSource, EscalateSubCostCodeId FROM dbo.BillTextCodingSuggestion WHERE BillId=?",
        bill_id,
    )
    reset = cur.fetchone()
    assert reset.DecisionSource is None
    assert reset.EscalateSubCostCodeId is None


def test_check_constraints_reject_bad_values(u701b3_schema_conn):
    cur = u701b3_schema_conn.cursor()
    _insert_attachment(cur, 11)
    att_id = 11
    with pytest.raises(Exception):
        cur.execute(
            """
            INSERT dbo.[BillIntakePrediction](
                CreatedDatetime, ModifiedDatetime, AttachmentId, IntakeSource,
                ModelVersion, Status, CodingSource
            ) VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), ?, 'pdf_drop', 'v', 'ok', 'bogus')
            """,
            att_id,
        )


INTAKE_LADDER_COLUMNS = [
    "SimilarPredSubCostCodeId", "SimilarConfidence", "CodingSource", "CodingEscalation",
    "PrimaryModel", "PrimarySubCostCodeId", "PrimaryProjectId", "PrimaryConfidence",
    "PrimaryError", "FallbackModel", "FallbackSubCostCodeId", "FallbackConfidence",
    "SimilarNeighbourCount",
]
TEXT_LADDER_COLUMNS = [
    "DecisionSource", "PrimaryModel", "PrimarySubCostCodeId", "PrimaryProjectId",
    "PrimaryConfidence", "PrimaryError", "EscalateModel", "EscalateSubCostCodeId",
    "EscalateProjectId", "EscalateConfidence", "EscalateError", "FallbackModel",
    "FallbackSubCostCodeId", "FallbackConfidence", "SimilarVoteSubCostCodeId",
    "SimilarVoteConfidence", "SimilarNeighbourCount", "PrimaryFromIntake", "CostUsd",
]


def _columns(cur) -> list[str]:
    return [d[0] for d in cur.description]


def test_intake_upsert_round_trips_every_ladder_column_and_reads_by_bill(u701b3_schema_conn):
    cur = u701b3_schema_conn.cursor()
    _insert_bill_stack(cur, bill_id=1, att_id=10)
    cur.execute(
        """
        EXEC dbo.UpsertBillIntakePrediction
            @AttachmentId=10, @IntakeSource='pdf_drop', @ModelVersion='u701b-text-v2',
            @Status='ok', @PredSubCostCodeId=100,
            @CodingSource='fallback', @CodingEscalation='claude_unavailable',
            @PrimaryModel='claude-haiku-5-5', @PrimarySubCostCodeId=101, @PrimaryProjectId=501,
            @PrimaryConfidence=0.6900, @PrimaryError='http_500',
            @FallbackModel='DeepSeek-V4-Flash', @FallbackSubCostCodeId=102,
            @FallbackConfidence=0.5500,
            @SimilarPredSubCostCodeId=103, @SimilarConfidence=0.7500, @SimilarNeighbourCount=4
        """
    )
    upserted = cur.fetchone()
    _drain_cursor(cur)
    expected = {
        "CodingSource": "fallback", "CodingEscalation": "claude_unavailable",
        "PrimaryModel": "claude-haiku-5-5", "PrimarySubCostCodeId": 101,
        "PrimaryProjectId": 501, "PrimaryConfidence": Decimal("0.6900"),
        "PrimaryError": "http_500", "FallbackModel": "DeepSeek-V4-Flash",
        "FallbackSubCostCodeId": 102, "FallbackConfidence": Decimal("0.5500"),
        "SimilarPredSubCostCodeId": 103, "SimilarConfidence": Decimal("0.7500"),
        "SimilarNeighbourCount": 4,
    }
    assert {k: getattr(upserted, k) for k in expected} == expected
    cur.execute("EXEC dbo.ReadBillIntakePredictionByBillId @BillId=1")
    by_bill = cur.fetchone()
    assert by_bill is not None and by_bill.BillId == 1
    assert {k: getattr(by_bill, k) for k in expected} == expected


def test_intake_empty_shapes_carry_ladder_columns(u701b3_schema_conn):
    cur = u701b3_schema_conn.cursor()
    cur.execute(
        "EXEC dbo.UpsertBillIntakePrediction @AttachmentId=987654, @IntakeSource='pdf_drop', "
        "@ModelVersion='v', @Status='ok'"
    )
    assert cur.fetchone() is None
    missing_shape = _columns(cur)
    cur.execute("EXEC dbo.ReadBillIntakePredictionByAttachmentId @AttachmentId=987654")
    assert missing_shape == _columns(cur)
    cur.execute("EXEC dbo.ReadBillIntakePredictionsForAccuracy")
    accuracy = _columns(cur)
    assert all(col in accuracy for col in INTAKE_LADDER_COLUMNS)


def test_text_complete_sets_every_column_and_claim_resets_them(u701b3_schema_conn):
    cur = u701b3_schema_conn.cursor()
    _insert_bill_stack(cur, bill_id=1, att_id=10)
    now = datetime(2026, 5, 1, 12, 0, 0)
    cur.execute(
        """
        INSERT dbo.[BillTextCodingSuggestion](
            CreatedDatetime, ModifiedDatetime, BillId, AttachmentId,
            ExtractedDatetime, Status, ModelVersion
        ) VALUES (?, ?, 1, 10, ?, 'pending', 'u654b-text-v1')
        """,
        now, now, now,
    )
    cur.execute(
        """
        EXEC dbo.CompleteBillTextCodingSuggestion
            @BillId=1, @AttachmentId=10, @ExtractedDatetime=?, @Status='done',
            @ModelVersion='u701b-text-v2', @SubCostCodeId=1, @RecordCall=1,
            @DecisionSource='escalate',
            @PrimaryModel='claude-haiku-5-5', @PrimarySubCostCodeId=101, @PrimaryProjectId=501,
            @PrimaryConfidence=0.6000, @PrimaryError=NULL,
            @EscalateModel='claude-sonnet-5-5', @EscalateSubCostCodeId=102,
            @EscalateProjectId=502, @EscalateConfidence=0.9000, @EscalateError='http_500',
            @FallbackModel='DeepSeek-V4-Flash', @FallbackSubCostCodeId=103,
            @FallbackConfidence=0.5500, @SimilarVoteSubCostCodeId=104,
            @SimilarVoteConfidence=0.4000, @SimilarNeighbourCount=5,
            @PrimaryFromIntake=1, @CostUsd=0.001234
        """,
        now,
    )
    row = cur.fetchone()
    expected = {
        "DecisionSource": "escalate", "PrimaryModel": "claude-haiku-5-5",
        "PrimarySubCostCodeId": 101, "PrimaryProjectId": 501,
        "PrimaryConfidence": Decimal("0.6000"), "PrimaryError": None,
        "EscalateModel": "claude-sonnet-5-5", "EscalateSubCostCodeId": 102,
        "EscalateProjectId": 502, "EscalateConfidence": Decimal("0.9000"),
        "EscalateError": "http_500", "FallbackModel": "DeepSeek-V4-Flash",
        "FallbackSubCostCodeId": 103, "FallbackConfidence": Decimal("0.5500"),
        "SimilarVoteSubCostCodeId": 104, "SimilarVoteConfidence": Decimal("0.4000"),
        "SimilarNeighbourCount": 5, "PrimaryFromIntake": True,
        "CostUsd": Decimal("0.001234"),
    }
    assert {k: getattr(row, k) for k in expected} == expected
    assert row.ModelVersion == "u701b-text-v2"

    cur.execute("EXEC dbo.ReadBillTextCodingSuggestionByBillId @BillId=1")
    read = cur.fetchone()
    assert {k: getattr(read, k) for k in expected} == expected

    cur.execute(
        "UPDATE dbo.[Bill] SET Status='draft', VendorId=1, "
        "CreatedDatetime=DATEADD(DAY, -1, SYSUTCDATETIME()) WHERE Id=1"
    )
    cur.execute(
        "UPDATE dbo.[BillTextCodingSuggestion] SET Status='pending', "
        "ModifiedDatetime=DATEADD(HOUR, -1, SYSUTCDATETIME()) WHERE BillId=1"
    )
    cur.execute("EXEC dbo.ClaimNextBillForTextCodingSuggestion")
    _drain_cursor(cur)
    cur.execute(
        "SELECT " + ", ".join(TEXT_LADDER_COLUMNS) + ", ModelVersion "
        "FROM dbo.BillTextCodingSuggestion WHERE BillId=1"
    )
    reset = cur.fetchone()
    assert all(getattr(reset, col) is None for col in TEXT_LADDER_COLUMNS)
    # Flag-off claim placeholder is unchanged; the V2 path writes its version on Complete.
    cur.execute(
        "SELECT OBJECT_DEFINITION(OBJECT_ID('dbo.ClaimNextBillForTextCodingSuggestion'))"
    )
    definition = cur.fetchone()[0]
    assert "N'u654b-text-v1'" in definition and "u701b-text-v2" not in definition


def test_text_complete_empty_shape_matches_the_read(u701b3_schema_conn):
    cur = u701b3_schema_conn.cursor()
    cur.execute(
        "EXEC dbo.CompleteBillTextCodingSuggestion @BillId=987654, @AttachmentId=1, "
        "@ExtractedDatetime='2026-05-01', @Status='done', @ModelVersion='v'"
    )
    assert cur.fetchone() is None
    empty_shape = _columns(cur)
    cur.execute("EXEC dbo.ReadBillTextCodingSuggestionByBillId @BillId=987654")
    assert empty_shape == _columns(cur)
    assert all(col in empty_shape for col in TEXT_LADDER_COLUMNS)


def test_bill_coding_actor_returns_creator_company_and_total(u701b3_schema_conn):
    cur = u701b3_schema_conn.cursor()
    _insert_bill_stack(cur, bill_id=1, att_id=10)
    cur.execute("UPDATE dbo.[Bill] SET TotalAmount=-125.50, CompanyId=1 WHERE Id=1")
    cur.execute("UPDATE dbo.[User] SET IsSystemAdmin=1 WHERE Id=17")
    cur.execute("EXEC dbo.ReadBillCodingActorByBillId @BillId=1")
    row = cur.fetchone()
    assert (row.CreatedByUserId, row.IsSystemAdmin, row.CompanyId, row.TotalAmount) == (
        17, 1, 1, Decimal("-125.50")
    )
    cur.execute("EXEC dbo.ReadBillCodingActorByBillId @BillId=987654")
    assert cur.fetchone() is None


@pytest.mark.parametrize(
    "table,column,value",
    [
        ("BillIntakePrediction", "CodingEscalation", "bogus"),
        ("BillTextCodingSuggestion", "DecisionSource", "bogus"),
    ],
)
def test_more_check_constraints_reject_bad_values(u701b3_schema_conn, table, column, value):
    cur = u701b3_schema_conn.cursor()
    cur.execute(
        "SELECT name FROM sys.check_constraints WHERE parent_object_id = OBJECT_ID(?)",
        f"dbo.{table}",
    )
    names = {r[0] for r in cur.fetchall()}
    assert f"CK_{table}_{column}" in names
    _insert_bill_stack(cur, bill_id=1, att_id=10)
    if table == "BillIntakePrediction":
        sql = (
            "INSERT dbo.[BillIntakePrediction](CreatedDatetime, ModifiedDatetime, AttachmentId, "
            "IntakeSource, ModelVersion, Status, CodingEscalation) "
            "VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), 10, 'pdf_drop', 'v', 'ok', ?)"
        )
    else:
        sql = (
            "INSERT dbo.[BillTextCodingSuggestion](CreatedDatetime, ModifiedDatetime, BillId, "
            "AttachmentId, ExtractedDatetime, Status, ModelVersion, DecisionSource) "
            "VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), 1, 10, SYSUTCDATETIME(), 'pending', 'v', ?)"
        )
    with pytest.raises(Exception, match="CHECK"):
        cur.execute(sql, value)


def test_similar_shadow_never_overwrites_a_v2_row(u701b3_schema_conn):
    cur = u701b3_schema_conn.cursor()
    _insert_attachment(cur, 20)
    _insert_attachment(cur, 21)
    cur.execute(
        "EXEC dbo.UpsertBillIntakePrediction @AttachmentId=20, @IntakeSource='pdf_drop', "
        "@ModelVersion='u701b-text-v2', @Status='ok', @CodingSource='primary', "
        "@SimilarPredSubCostCodeId=103, @SimilarConfidence=0.7500, @SimilarNeighbourCount=4"
    )
    _drain_cursor(cur)
    cur.execute(
        "EXEC dbo.UpsertBillIntakePrediction @AttachmentId=21, @IntakeSource='pdf_drop', "
        "@ModelVersion='u654b-text-v1', @Status='ok'"
    )
    _drain_cursor(cur)
    for att_id in (20, 21):
        cur.execute(
            "EXEC dbo.UpdateBillIntakePredictionSimilarShadow @AttachmentId=?, "
            "@SimilarPredSubCostCodeId=999, @SimilarConfidence=0.9900",
            att_id,
        )
        _drain_cursor(cur)
    cur.execute(
        "SELECT AttachmentId, SimilarPredSubCostCodeId, SimilarConfidence "
        "FROM dbo.BillIntakePrediction WHERE AttachmentId IN (20, 21) ORDER BY AttachmentId"
    )
    v2, legacy = cur.fetchall()
    assert (v2.SimilarPredSubCostCodeId, v2.SimilarConfidence) == (103, Decimal("0.7500"))
    assert (legacy.SimilarPredSubCostCodeId, legacy.SimilarConfidence) == (999, Decimal("0.9900"))
