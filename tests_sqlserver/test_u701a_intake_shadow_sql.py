"""U-701a: BillIntakePrediction similar shadow + upsert nulling, on the REAL table definitions."""
from __future__ import annotations

import os
from decimal import Decimal

import pytest

from tests_sqlserver.fixtures.u701a_real_schema import (
    _drain_cursor,
    u701a_real_schema_conn,  # noqa: F401  (pytest fixture)
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("BUILDONE_TEST_SQLSERVER"),
    reason="BUILDONE_TEST_SQLSERVER not set",
)


def _insert_attachment(cur, att_id: int) -> None:
    cur.execute(
        "SET IDENTITY_INSERT dbo.[Attachment] ON; "
        "INSERT dbo.[Attachment]([Id], [CreatedDatetime], [Filename], [OriginalFilename], "
        "[ContentType], [FileSize], [BlobUrl]) "
        "VALUES (?, SYSUTCDATETIME(), N'f.pdf', N'f.pdf', N'application/pdf', 1, N'https://b/x'); "
        "SET IDENTITY_INSERT dbo.[Attachment] OFF;",
        (att_id,),
    )


def test_upsert_clears_similar_columns(u701a_real_schema_conn):
    """A re-read upsert nulls BOTH shadow columns, so a failed narrow write can never leave the
    previous read's shadow next to a new prediction."""
    cur = u701a_real_schema_conn.cursor()
    _insert_attachment(cur, 1)
    cur.execute(
        """
        INSERT dbo.[BillIntakePrediction] (
            [CreatedDatetime], [ModifiedDatetime], [AttachmentId], [IntakeSource], [ModelVersion], [Status],
            [SimilarPredSubCostCodeId], [SimilarConfidence]
        ) VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), 1, 'pdf_drop', 'm', 'ok', 999, 0.99);
        """
    )
    cur.execute(
        "EXEC dbo.UpsertBillIntakePrediction @AttachmentId=1, @IntakeSource='pdf_drop', "
        "@ModelVersion='m', @Status='ok'"
    )
    _drain_cursor(cur)
    cur.execute(
        "SELECT [SimilarPredSubCostCodeId], [SimilarConfidence] FROM dbo.[BillIntakePrediction] WHERE [AttachmentId]=1"
    )
    row = cur.fetchone()
    assert row.SimilarPredSubCostCodeId is None
    assert row.SimilarConfidence is None


def test_update_similar_shadow_rowcount(u701a_real_schema_conn):
    cur = u701a_real_schema_conn.cursor()
    _insert_attachment(cur, 2)
    cur.execute(
        """
        INSERT dbo.[BillIntakePrediction] (
            [CreatedDatetime], [ModifiedDatetime], [AttachmentId], [IntakeSource], [ModelVersion], [Status]
        ) VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), 2, 'pdf_drop', 'm', 'ok');
        """
    )
    cur.execute(
        "EXEC dbo.UpdateBillIntakePredictionSimilarShadow @AttachmentId=2, "
        "@SimilarPredSubCostCodeId=42, @SimilarConfidence=0.5"
    )
    assert int(cur.fetchone().RowsUpdated) == 1
    _drain_cursor(cur)
    cur.execute(
        "SELECT [SimilarPredSubCostCodeId], [SimilarConfidence] FROM dbo.[BillIntakePrediction] WHERE [AttachmentId]=2"
    )
    row = cur.fetchone()
    assert int(row.SimilarPredSubCostCodeId) == 42
    assert Decimal(str(row.SimilarConfidence)) == Decimal("0.5")
    cur.execute(
        "EXEC dbo.UpdateBillIntakePredictionSimilarShadow @AttachmentId=2, "
        "@SimilarPredSubCostCodeId=NULL, @SimilarConfidence=NULL"
    )
    assert int(cur.fetchone().RowsUpdated) == 1
    _drain_cursor(cur)
    cur.execute("SELECT [SimilarPredSubCostCodeId] FROM dbo.[BillIntakePrediction] WHERE [AttachmentId]=2")
    assert cur.fetchone()[0] is None
