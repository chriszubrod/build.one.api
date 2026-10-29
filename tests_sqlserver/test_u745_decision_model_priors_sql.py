"""U-745: the M0 prior sprocs + M0 columns on local SQL Server.

Seeds tests/test_u745_m0_parity.POP (the population the pure train/serve parity test feeds the
live path with) and pins ReadVendorPriorBillsForDecisionModel /
ReadSubCostCodeLabelCountsForDecisionModel to exactly the rows that test assumes.
"""
from __future__ import annotations

import os
from datetime import datetime
from decimal import Decimal

import pytest

from tests.test_u745_m0_parity import (
    BY_ID,
    POP,
    PROJECTS,
    SCCS,
    day,
    expected_label_counts,
    expected_prior_rows,
)
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

PRIORS_SQL = REPO_ROOT / "entities/coding_suggestion/sql/dbo.decision_model_priors.sql"
INTAKE_SQL = REPO_ROOT / "entities/bill_intake_prediction/sql/dbo.bill_intake_prediction.sql"
TEXT_SQL = REPO_ROOT / "entities/coding_suggestion/sql/bill_text_coding_suggestion.sql"
VOTE_SQL = REPO_ROOT / "entities/coding_suggestion/sql/bill_coding_suggestion.sql"
TOKENIZER = "u745-v1:abcd"
NOW = datetime(2026, 10, 29, 12, 0, 0)


def _seed_reference(cur) -> None:
    cur.execute(
        """
        IF NOT EXISTS (SELECT 1 FROM dbo.[User] WHERE Id = 17)
            INSERT dbo.[User](Id) VALUES (17);
        """
    )
    for vid in (5, 6, 7):
        cur.execute(
            "IF NOT EXISTS (SELECT 1 FROM dbo.[Vendor] WHERE Id = ?) "
            "INSERT dbo.[Vendor](Id, Name) VALUES (?, N'v')",
            vid, vid,
        )
    for pid in PROJECTS:
        cur.execute(
            """
            IF NOT EXISTS (SELECT 1 FROM dbo.[Project] WHERE [Id] = ?)
            BEGIN
                SET IDENTITY_INSERT dbo.[Project] ON;
                INSERT dbo.[Project]([Id], [CreatedDatetime], [Name]) VALUES (?, SYSUTCDATETIME(), N'p');
                SET IDENTITY_INSERT dbo.[Project] OFF;
            END;
            """,
            pid, pid,
        )
    for sid, (num, _holding) in SCCS.items():
        cur.execute(
            """
            IF NOT EXISTS (SELECT 1 FROM dbo.[SubCostCode] WHERE [Id] = ?)
            BEGIN
                SET IDENTITY_INSERT dbo.[SubCostCode] ON;
                INSERT dbo.[SubCostCode]([Id], [CreatedDatetime], [Number], [Name], [CostCodeId])
                VALUES (?, SYSUTCDATETIME(), ?, N's', 1);
                SET IDENTITY_INSERT dbo.[SubCostCode] OFF;
            END;
            """,
            sid, sid, num,
        )


def _seed_pop(cur) -> None:
    for b in POP:
        cur.execute(
            """
            SET IDENTITY_INSERT dbo.[Bill] ON;
            INSERT dbo.[Bill]([Id], [CreatedDatetime], [BillDate], [DueDate], [Status], [VendorId],
                              [CompanyId], [CreatedByUserId])
            VALUES (?, ?, ?, ?, ?, ?, ?, 17);
            SET IDENTITY_INSERT dbo.[Bill] OFF;
            """,
            b.bill_id, b.created, b.created, b.created, "draft" if b.draft else "completed",
            b.vendor_id, b.company_id,
        )
        for line_id, scc, project, amount in b.lines:
            cur.execute(
                """
                SET IDENTITY_INSERT dbo.[BillLineItem] ON;
                INSERT dbo.[BillLineItem]([Id], [CreatedDatetime], [BillId], [SubCostCodeId],
                                          [ProjectId], [Amount], [IsDraft])
                VALUES (?, SYSUTCDATETIME(), ?, ?, ?, ?, 0);
                SET IDENTITY_INSERT dbo.[BillLineItem] OFF;
                """,
                line_id, b.bill_id, scc, project, Decimal(amount) if amount is not None else None,
            )
        if b.pdf or b.taxpayer_pdf:
            att = b.bill_id * 10
            first_line = min(l for l, *_ in b.lines)
            cur.execute(
                """
                SET IDENTITY_INSERT dbo.[Attachment] ON;
                INSERT dbo.[Attachment]([Id], [CreatedDatetime], [Filename], [OriginalFilename],
                    [ContentType], [FileSize], [BlobUrl])
                VALUES (?, SYSUTCDATETIME(), N'f.pdf', N'f.pdf', N'application/pdf', 1, N'https://b/x');
                SET IDENTITY_INSERT dbo.[Attachment] OFF;
                INSERT dbo.[BillLineItemAttachment]([CreatedDatetime], [BillLineItemId], [AttachmentId])
                VALUES (SYSUTCDATETIME(), ?, ?);
                """,
                att, first_line, att,
            )
            cur.execute(
                "EXEC dbo.UpsertAttachmentTextTokenHash @AttachmentId=?, @TokenizerVersion=?, "
                "@TokenHashes='abc', @TokenCount=1",
                att, TOKENIZER,
            )
            _drain_cursor(cur)
            if b.taxpayer_pdf:
                cur.execute(
                    "INSERT dbo.[TaxpayerAttachment]([CreatedDatetime], [TaxpayerId], [AttachmentId]) "
                    "VALUES (SYSUTCDATETIME(), 1, ?)",
                    att,
                )


@pytest.fixture
def conn(u701a_real_schema_conn):  # noqa: F811
    cur = u701a_real_schema_conn.cursor()
    cur.execute("SELECT DB_NAME()")
    assert cur.fetchone()[0] == "buildone_test"
    exec_sql_file(cur, VOTE_SQL)
    exec_sql_file(cur, TEXT_SQL)
    exec_sql_file(cur, TEXT_SQL)  # idempotent re-apply
    exec_sql_file(cur, INTAKE_SQL)
    exec_sql_file(cur, INTAKE_SQL)
    exec_sql_file(cur, PRIORS_SQL)
    exec_sql_file(cur, PRIORS_SQL)
    _drain_cursor(cur)
    _seed_reference(cur)
    _seed_pop(cur)
    yield u701a_real_schema_conn


def _priors(cur, *, company=1, vendor=5, as_of, exclude=None, tokenizer=TOKENIZER):
    cur.execute(
        "EXEC dbo.ReadVendorPriorBillsForDecisionModel @CompanyId=?, @VendorId=?, @AsOf=?, "
        "@ExcludeBillId=?, @TokenizerVersion=?",
        company, vendor, as_of, exclude, tokenizer,
    )
    return cur.fetchall()


def _as_tuples(rows):
    return [
        (int(r.BillId), r.CreatedDatetime, int(r.DominantSubCostCodeId),
         None if r.DominantProjectId is None else int(r.DominantProjectId),
         Decimal(str(r.TotalAbsAmount)), bool(r.HasPdf))
        for r in rows
    ]


@pytest.mark.parametrize(
    "vendor,as_of,exclude,company",
    [
        (5, day(10), 109, 1),   # the parity query: self, equal-time and later bills out
        (5, day(12), None, 1),  # no exclusion; every labelled vendor-5 bill so far
        (5, day(12), 105, 1),
        (6, day(10), None, 1),
        (7, day(12), 401, 1),   # cold vendor
        (5, day(12), None, 2),  # company filter
    ],
)
def test_prior_rows_match_the_parity_population(conn, vendor, as_of, exclude, company):
    cur = conn.cursor()
    got = _as_tuples(_priors(cur, company=company, vendor=vendor, as_of=as_of, exclude=exclude))
    want = _as_tuples(expected_prior_rows(vendor_id=vendor, as_of=as_of, exclude_bill_id=exclude,
                                          company_id=company))
    assert got == want


def test_prior_rule_details(conn):
    cur = conn.cursor()
    rows = {int(r.BillId): r for r in _priors(cur, as_of=day(10), exclude=109)}
    assert sorted(rows) == [101, 102, 103, 104, 105, 108]  # 106 draft, 107 uncoded, 110 equal time
    assert (rows[104].DominantSubCostCodeId, rows[104].DominantProjectId) == (3, 501)
    assert (rows[105].DominantSubCostCodeId, rows[105].DominantProjectId) == (1, 501)
    assert Decimal(str(rows[105].TotalAbsAmount)) == Decimal("80.00")
    assert Decimal(str(rows[108].TotalAbsAmount)) == Decimal("150.00")
    assert [bool(rows[i].HasPdf) for i in (101, 102, 103, 104, 105, 108)] == [
        True, True, False, True, False, False  # 108: TaxpayerAttachment never counts
    ]
    # Strictly earlier: a bill created exactly at as_of is out; one tick later it is in.
    ids = {int(r.BillId) for r in _priors(cur, as_of=BY_ID[110].created)}
    assert 110 not in ids and 109 not in ids
    ids = {int(r.BillId) for r in _priors(cur, as_of=datetime(2026, 1, 11, 9, 0, 0, 1000))}
    assert {109, 110} <= ids


def test_has_pdf_needs_the_current_tokenizer(conn):
    cur = conn.cursor()
    for version in (None, "other-version"):
        rows = _priors(cur, as_of=day(10), exclude=109, tokenizer=version)
        assert rows and not any(bool(r.HasPdf) for r in rows)


def test_label_counts(conn):
    cur = conn.cursor()
    for company in (1, 2):
        cur.execute("EXEC dbo.ReadSubCostCodeLabelCountsForDecisionModel @CompanyId=?", company)
        got = {int(r.SubCostCodeId): int(r.BillCount) for r in cur.fetchall()}
        assert got == expected_label_counts(company)
    assert expected_label_counts(1) == {1: 5, 2: 4, 3: 1, 7: 1, 10: 1}


# ---------------------------------------------------------------- M0 columns


def _cols(cur, table):
    cur.execute(
        "SELECT c.name, t.name AS type_name, c.precision, c.scale FROM sys.columns c "
        "JOIN sys.types t ON t.user_type_id = c.user_type_id WHERE c.object_id = OBJECT_ID(?)",
        table,
    )
    return {r.name: (r.type_name, r.precision, r.scale) for r in cur.fetchall()}


def test_m0_columns_mirror_the_vote_columns(conn):
    cur = conn.cursor()
    intake = _cols(cur, "dbo.BillIntakePrediction")
    assert intake["M0SubCostCodeId"] == intake["SimilarPredSubCostCodeId"]
    assert intake["M0Confidence"] == intake["SimilarConfidence"] == ("decimal", 5, 4)
    text = _cols(cur, "dbo.BillTextCodingSuggestion")
    assert text["M0SubCostCodeId"] == text["SimilarVoteSubCostCodeId"]
    assert text["M0Confidence"] == text["SimilarVoteConfidence"] == ("decimal", 5, 4)


def test_old_check_constraints_are_upgraded_to_allow_m0(conn):
    cur = conn.cursor()
    cur.execute(
        """
        ALTER TABLE dbo.[BillIntakePrediction] DROP CONSTRAINT [CK_BillIntakePrediction_CodingSource];
        ALTER TABLE dbo.[BillIntakePrediction] ADD CONSTRAINT [CK_BillIntakePrediction_CodingSource]
            CHECK ([CodingSource] IS NULL OR [CodingSource] IN
                   ('history', 'primary', 'fallback', 'similar_vote', 'none'));
        ALTER TABLE dbo.[BillTextCodingSuggestion] DROP CONSTRAINT [CK_BillTextCodingSuggestion_DecisionSource];
        ALTER TABLE dbo.[BillTextCodingSuggestion] ADD CONSTRAINT [CK_BillTextCodingSuggestion_DecisionSource]
            CHECK ([DecisionSource] IS NULL OR [DecisionSource] IN
                   ('history', 'primary', 'escalate', 'fallback', 'similar_vote', 'reused_intake', 'none'));
        """
    )
    exec_sql_file(cur, TEXT_SQL)
    exec_sql_file(cur, INTAKE_SQL)
    _drain_cursor(cur)
    cur.execute(
        "SELECT name, definition FROM sys.check_constraints WHERE name IN "
        "('CK_BillIntakePrediction_CodingSource', 'CK_BillTextCodingSuggestion_DecisionSource')"
    )
    defs = {r.name: r.definition for r in cur.fetchall()}
    assert len(defs) == 2 and all("'m0'" in d for d in defs.values())
    assert "'similar_vote'" in defs["CK_BillIntakePrediction_CodingSource"]
    assert "'reused_intake'" in defs["CK_BillTextCodingSuggestion_DecisionSource"]


def _attachment_with_extraction(cur, att_id: int) -> None:
    cur.execute(
        """
        SET IDENTITY_INSERT dbo.[Attachment] ON;
        INSERT dbo.[Attachment]([Id], [CreatedDatetime], [Filename], [OriginalFilename],
            [ContentType], [FileSize], [BlobUrl], [ExtractionStatus], [ExtractedTextBlobUrl],
            [ExtractedDatetime])
        VALUES (?, SYSUTCDATETIME(), N'f.pdf', N'f.pdf', N'application/pdf', 1, N'https://b/x',
                N'completed', N'https://b/t', ?);
        SET IDENTITY_INSERT dbo.[Attachment] OFF;
        """,
        att_id, NOW,
    )


def test_intake_upsert_reads_and_accuracy_round_trip_m0(conn):
    cur = conn.cursor()
    _attachment_with_extraction(cur, 9001)
    cur.execute(
        "EXEC dbo.UpsertBillIntakePrediction @AttachmentId=9001, @IntakeSource='pdf_drop', "
        "@ModelVersion='u701b-text-v2', @Status='ok', @CodingSource='m0', "
        "@SimilarPredSubCostCodeId=2, @SimilarConfidence=0.6, @M0SubCostCodeId=3, @M0Confidence=0.71234"
    )
    row = cur.fetchone()
    _drain_cursor(cur)
    assert (int(row.M0SubCostCodeId), row.M0Confidence) == (3, Decimal("0.7123"))
    assert row.CodingSource == "m0"
    cur.execute("EXEC dbo.ReadBillIntakePredictionByAttachmentId @AttachmentId=9001")
    row = cur.fetchone()
    assert (int(row.M0SubCostCodeId), row.M0Confidence) == (3, Decimal("0.7123"))
    # Re-upsert without M0 params clears them (UPDATE sets every column).
    cur.execute(
        "EXEC dbo.UpsertBillIntakePrediction @AttachmentId=9001, @IntakeSource='pdf_drop', "
        "@ModelVersion='u701b-text-v2', @Status='ok'"
    )
    row = cur.fetchone()
    _drain_cursor(cur)
    assert row.M0SubCostCodeId is None and row.M0Confidence is None

    # Finalized prediction bound to bill 101 + its text row -> the accuracy read.
    cur.execute(
        """
        UPDATE dbo.[BillIntakePrediction]
        SET [BillId] = 101, [FinalizedDatetime] = ?, [FinalSubCostCodeId] = 1,
            [M0SubCostCodeId] = 3, [M0Confidence] = 0.5
        WHERE [AttachmentId] = 9001
        """,
        NOW,
    )
    cur.execute(
        """
        INSERT dbo.[BillTextCodingSuggestion](CreatedDatetime, ModifiedDatetime, BillId, AttachmentId,
            ExtractedDatetime, Status, ModelVersion)
        VALUES (?, ?, 101, 9001, ?, 'pending', 'u701b-text-v2')
        """,
        NOW, NOW, NOW,
    )
    cur.execute(
        "EXEC dbo.CompleteBillTextCodingSuggestion @BillId=101, @AttachmentId=9001, "
        "@ExtractedDatetime=?, @Status='done', @ModelVersion='u701b-text-v2', "
        "@DecisionSource='m0', @SubCostCodeId=1, @M0SubCostCodeId=1, @M0Confidence=0.9",
        NOW,
    )
    row = cur.fetchone()
    _drain_cursor(cur)
    assert row.DecisionSource == "m0"
    assert (int(row.M0SubCostCodeId), row.M0Confidence) == (1, Decimal("0.9000"))
    cur.execute("EXEC dbo.ReadBillTextCodingSuggestionByBillId @BillId=101")
    row = cur.fetchone()
    assert (int(row.M0SubCostCodeId), row.M0Confidence) == (1, Decimal("0.9000"))

    cur.execute("EXEC dbo.ReadBillIntakePredictionsForAccuracy")
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    # Appended after the U-744 columns: ordinal readers unaffected.
    assert cols[-4:] == ["M0SubCostCodeId", "M0Confidence", "TextM0SubCostCodeId", "TextM0Confidence"]
    assert cols.index("TextEscalateError") == len(cols) - 5
    r = next(r for r in rows if r.BillId == 101)
    assert (int(r.M0SubCostCodeId), r.M0Confidence) == (3, Decimal("0.5000"))
    assert (int(r.TextM0SubCostCodeId), r.TextM0Confidence) == (1, Decimal("0.9000"))
