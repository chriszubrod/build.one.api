"""U-701a: ReadSimilarBillsCorpusRows on real table definitions."""
from __future__ import annotations

import os

import pytest

from tests_sqlserver.fixtures.u701a_real_schema import u701a_real_schema_conn

pytestmark = pytest.mark.skipif(
    not os.environ.get("BUILDONE_TEST_SQLSERVER"),
    reason="BUILDONE_TEST_SQLSERVER not set",
)


def _seed_completed_bill(
    cur,
    *,
    bill_id: int,
    att_id: int,
    blia_id: int,
    bli_id: int,
    vendor_id: int | None = 1,
    scc_id: int = 1,
):
    cur.execute(
        """
        SET IDENTITY_INSERT dbo.[Attachment] ON;
        INSERT dbo.[Attachment](
            [Id], [CreatedDatetime], [Filename], [OriginalFilename], [ContentType],
            [FileSize], [BlobUrl]
        ) VALUES (?, SYSUTCDATETIME(), N'f.pdf', N'f.pdf', N'application/pdf', 1, N'https://b/x');
        SET IDENTITY_INSERT dbo.[Attachment] OFF;

        SET IDENTITY_INSERT dbo.[Bill] ON;
        INSERT dbo.[Bill]([Id], [CreatedDatetime], [BillDate], [DueDate], [Status], [VendorId])
        VALUES (?, SYSUTCDATETIME(), SYSUTCDATETIME(), SYSUTCDATETIME(), N'completed', ?);
        SET IDENTITY_INSERT dbo.[Bill] OFF;

        SET IDENTITY_INSERT dbo.[BillLineItem] ON;
        INSERT dbo.[BillLineItem]([Id], [CreatedDatetime], [BillId], [SubCostCodeId], [ProjectId], [Amount], [IsDraft])
        VALUES (?, SYSUTCDATETIME(), ?, ?, 1, 100.00, 0);
        SET IDENTITY_INSERT dbo.[BillLineItem] OFF;

        SET IDENTITY_INSERT dbo.[BillLineItemAttachment] ON;
        INSERT dbo.[BillLineItemAttachment]([Id], [CreatedDatetime], [BillLineItemId], [AttachmentId])
        VALUES (?, SYSUTCDATETIME(), ?, ?);
        SET IDENTITY_INSERT dbo.[BillLineItemAttachment] OFF;
        """,
        (att_id, bill_id, vendor_id, bli_id, bill_id, scc_id, blia_id, bli_id, att_id),
    )


def test_corpus_excludes_draft_and_uncoded(u701a_real_schema_conn):
    cur = u701a_real_schema_conn.cursor()
    _seed_completed_bill(cur, bill_id=10, att_id=1, blia_id=1000, bli_id=100, vendor_id=1)
    cur.execute(
        """
        SET IDENTITY_INSERT dbo.[Bill] ON;
        INSERT dbo.[Bill]([Id], [CreatedDatetime], [BillDate], [DueDate], [Status], [VendorId])
        VALUES (11, SYSUTCDATETIME(), SYSUTCDATETIME(), SYSUTCDATETIME(), N'draft', 1),
               (12, SYSUTCDATETIME(), SYSUTCDATETIME(), SYSUTCDATETIME(), N'completed', 1);
        SET IDENTITY_INSERT dbo.[Bill] OFF;
        SET IDENTITY_INSERT dbo.[Attachment] ON;
        INSERT dbo.[Attachment]([Id], [CreatedDatetime], [Filename], [OriginalFilename], [ContentType], [FileSize], [BlobUrl])
        VALUES (2, SYSUTCDATETIME(), N'f', N'f', N'application/pdf', 1, N'u'),
               (3, SYSUTCDATETIME(), N'f', N'f', N'application/pdf', 1, N'u');
        SET IDENTITY_INSERT dbo.[Attachment] OFF;
        SET IDENTITY_INSERT dbo.[BillLineItem] ON;
        INSERT dbo.[BillLineItem]([Id], [CreatedDatetime], [BillId], [SubCostCodeId], [ProjectId], [Amount], [IsDraft])
        VALUES (101, SYSUTCDATETIME(), 11, 1, 1, 100, 0),
               (102, SYSUTCDATETIME(), 12, NULL, 1, 200, 0);
        SET IDENTITY_INSERT dbo.[BillLineItem] OFF;
        SET IDENTITY_INSERT dbo.[BillLineItemAttachment] ON;
        INSERT dbo.[BillLineItemAttachment]([Id], [CreatedDatetime], [BillLineItemId], [AttachmentId])
        VALUES (1001, SYSUTCDATETIME(), 101, 2), (1002, SYSUTCDATETIME(), 102, 3);
        SET IDENTITY_INSERT dbo.[BillLineItemAttachment] OFF;
        EXEC dbo.UpsertAttachmentTextTokenHash @AttachmentId=1, @TokenizerVersion='u701a-v1:abcd', @TokenHashes='a', @TokenCount=1;
        EXEC dbo.UpsertAttachmentTextTokenHash @AttachmentId=2, @TokenizerVersion='u701a-v1:abcd', @TokenHashes='b', @TokenCount=1;
        EXEC dbo.UpsertAttachmentTextTokenHash @AttachmentId=3, @TokenizerVersion='u701a-v1:abcd', @TokenHashes='c', @TokenCount=1;
        """
    )
    cur.execute("EXEC dbo.ReadSimilarBillsCorpusRows @TokenizerVersion='u701a-v1:abcd'")
    rows = {int(r.BillId) for r in cur.fetchall()}
    assert rows == {10}


def test_corpus_excludes_null_vendor_bill(u701a_real_schema_conn):
    cur = u701a_real_schema_conn.cursor()
    _seed_completed_bill(cur, bill_id=20, att_id=4, blia_id=2000, bli_id=200, vendor_id=None)
    cur.execute(
        """
        EXEC dbo.UpsertAttachmentTextTokenHash @AttachmentId=4, @TokenizerVersion='u701a-v1:abcd',
            @TokenHashes='z', @TokenCount=1;
        """
    )
    cur.execute("EXEC dbo.ReadSimilarBillsCorpusRows @TokenizerVersion='u701a-v1:abcd'")
    assert cur.fetchall() == []


def test_earliest_blia_with_hash_skips_earlier_without_hash(u701a_real_schema_conn):
    cur = u701a_real_schema_conn.cursor()
    cur.execute(
        """
        SET IDENTITY_INSERT dbo.[Attachment] ON;
        INSERT dbo.[Attachment]([Id], [CreatedDatetime], [Filename], [OriginalFilename], [ContentType], [FileSize], [BlobUrl])
        VALUES (1, SYSUTCDATETIME(), N'f', N'f', N'application/pdf', 1, N'u'),
               (2, SYSUTCDATETIME(), N'f', N'f', N'application/pdf', 1, N'u');
        SET IDENTITY_INSERT dbo.[Attachment] OFF;
        SET IDENTITY_INSERT dbo.[Bill] ON;
        INSERT dbo.[Bill]([Id], [CreatedDatetime], [BillDate], [DueDate], [Status], [VendorId])
        VALUES (30, SYSUTCDATETIME(), SYSUTCDATETIME(), SYSUTCDATETIME(), N'completed', 1);
        SET IDENTITY_INSERT dbo.[Bill] OFF;
        SET IDENTITY_INSERT dbo.[BillLineItem] ON;
        INSERT dbo.[BillLineItem]([Id], [CreatedDatetime], [BillId], [SubCostCodeId], [ProjectId], [Amount], [IsDraft])
        VALUES (300, SYSUTCDATETIME(), 30, 1, 1, 100, 0), (301, SYSUTCDATETIME(), 30, 1, 1, 50, 0);
        SET IDENTITY_INSERT dbo.[BillLineItem] OFF;
        SET IDENTITY_INSERT dbo.[BillLineItemAttachment] ON;
        INSERT dbo.[BillLineItemAttachment]([Id], [CreatedDatetime], [BillLineItemId], [AttachmentId])
        VALUES (3000, SYSUTCDATETIME(), 300, 1), (3001, SYSUTCDATETIME(), 301, 2);
        SET IDENTITY_INSERT dbo.[BillLineItemAttachment] OFF;
        EXEC dbo.UpsertAttachmentTextTokenHash @AttachmentId=2, @TokenizerVersion='u701a-v1:abcd', @TokenHashes='z', @TokenCount=1;
        """
    )
    cur.execute("EXEC dbo.ReadSimilarBillsCorpusRows @TokenizerVersion='u701a-v1:abcd'")
    row = cur.fetchone()
    assert row is not None
    assert int(row.AttachmentId) == 2


def test_one_attachment_two_bills(u701a_real_schema_conn):
    cur = u701a_real_schema_conn.cursor()
    cur.execute(
        """
        SET IDENTITY_INSERT dbo.[Attachment] ON;
        INSERT dbo.[Attachment]([Id], [CreatedDatetime], [Filename], [OriginalFilename], [ContentType], [FileSize], [BlobUrl])
        VALUES (1, SYSUTCDATETIME(), N'f', N'f', N'application/pdf', 1, N'u');
        SET IDENTITY_INSERT dbo.[Attachment] OFF;
        SET IDENTITY_INSERT dbo.[Bill] ON;
        INSERT dbo.[Bill]([Id], [CreatedDatetime], [BillDate], [DueDate], [Status], [VendorId])
        VALUES (40, SYSUTCDATETIME(), SYSUTCDATETIME(), SYSUTCDATETIME(), N'completed', 1),
               (41, SYSUTCDATETIME(), SYSUTCDATETIME(), SYSUTCDATETIME(), N'completed', 1);
        SET IDENTITY_INSERT dbo.[Bill] OFF;
        SET IDENTITY_INSERT dbo.[BillLineItem] ON;
        INSERT dbo.[BillLineItem]([Id], [CreatedDatetime], [BillId], [SubCostCodeId], [ProjectId], [Amount], [IsDraft])
        VALUES (400, SYSUTCDATETIME(), 40, 1, 1, 100, 0), (401, SYSUTCDATETIME(), 41, 1, 1, 100, 0);
        SET IDENTITY_INSERT dbo.[BillLineItem] OFF;
        SET IDENTITY_INSERT dbo.[BillLineItemAttachment] ON;
        INSERT dbo.[BillLineItemAttachment]([Id], [CreatedDatetime], [BillLineItemId], [AttachmentId])
        VALUES (4000, SYSUTCDATETIME(), 400, 1), (4001, SYSUTCDATETIME(), 401, 1);
        SET IDENTITY_INSERT dbo.[BillLineItemAttachment] OFF;
        EXEC dbo.UpsertAttachmentTextTokenHash @AttachmentId=1, @TokenizerVersion='u701a-v1:abcd', @TokenHashes='x', @TokenCount=1;
        """
    )
    cur.execute("EXEC dbo.ReadSimilarBillsCorpusRows @TokenizerVersion='u701a-v1:abcd'")
    rows = cur.fetchall()
    assert {int(r.BillId) for r in rows} == {40, 41}
    assert all(int(r.AttachmentId) == 1 for r in rows)


def test_old_key_version_hash_never_picks_the_attachment(u701a_real_schema_conn):
    """After a key rotation the earliest attachment's old-version hash is ignored; the later
    attachment with a CURRENT-version hash is chosen. With no current hash the bill is absent."""
    cur = u701a_real_schema_conn.cursor()
    cur.execute(
        """
        SET IDENTITY_INSERT dbo.[Attachment] ON;
        INSERT dbo.[Attachment]([Id], [CreatedDatetime], [Filename], [OriginalFilename], [ContentType], [FileSize], [BlobUrl])
        VALUES (1, SYSUTCDATETIME(), N'f', N'f', N'application/pdf', 1, N'u'),
               (2, SYSUTCDATETIME(), N'f', N'f', N'application/pdf', 1, N'u'),
               (3, SYSUTCDATETIME(), N'f', N'f', N'application/pdf', 1, N'u');
        SET IDENTITY_INSERT dbo.[Attachment] OFF;
        SET IDENTITY_INSERT dbo.[Bill] ON;
        INSERT dbo.[Bill]([Id], [CreatedDatetime], [BillDate], [DueDate], [Status], [VendorId])
        VALUES (50, SYSUTCDATETIME(), SYSUTCDATETIME(), SYSUTCDATETIME(), N'completed', 1),
               (51, SYSUTCDATETIME(), SYSUTCDATETIME(), SYSUTCDATETIME(), N'completed', 1);
        SET IDENTITY_INSERT dbo.[Bill] OFF;
        SET IDENTITY_INSERT dbo.[BillLineItem] ON;
        INSERT dbo.[BillLineItem]([Id], [CreatedDatetime], [BillId], [SubCostCodeId], [ProjectId], [Amount], [IsDraft])
        VALUES (500, SYSUTCDATETIME(), 50, 1, 1, 100, 0), (501, SYSUTCDATETIME(), 50, 1, 1, 50, 0),
               (510, SYSUTCDATETIME(), 51, 1, 1, 100, 0);
        SET IDENTITY_INSERT dbo.[BillLineItem] OFF;
        SET IDENTITY_INSERT dbo.[BillLineItemAttachment] ON;
        INSERT dbo.[BillLineItemAttachment]([Id], [CreatedDatetime], [BillLineItemId], [AttachmentId])
        VALUES (5000, SYSUTCDATETIME(), 500, 1), (5001, SYSUTCDATETIME(), 501, 2),
               (5100, SYSUTCDATETIME(), 510, 3);
        SET IDENTITY_INSERT dbo.[BillLineItemAttachment] OFF;
        EXEC dbo.UpsertAttachmentTextTokenHash @AttachmentId=1, @TokenizerVersion='u701a-v1:0ld0', @TokenHashes='a', @TokenCount=1;
        EXEC dbo.UpsertAttachmentTextTokenHash @AttachmentId=2, @TokenizerVersion='u701a-v1:abcd', @TokenHashes='b', @TokenCount=1;
        EXEC dbo.UpsertAttachmentTextTokenHash @AttachmentId=3, @TokenizerVersion='u701a-v1:0ld0', @TokenHashes='c', @TokenCount=1;
        """
    )
    cur.execute("EXEC dbo.ReadSimilarBillsCorpusRows @TokenizerVersion='u701a-v1:abcd'")
    rows = {int(r.BillId): int(r.AttachmentId) for r in cur.fetchall()}
    assert rows == {50: 2}


def test_taxpayer_linked_attachment_never_picks_the_bill(u701a_real_schema_conn):
    """An attachment hashed BEFORE it was linked as a taxpayer document (W-9) is never used: the
    bill falls through to its next hashed attachment, or is absent when it has none."""
    cur = u701a_real_schema_conn.cursor()
    cur.execute(
        """
        SET IDENTITY_INSERT dbo.[Attachment] ON;
        INSERT dbo.[Attachment]([Id], [CreatedDatetime], [Filename], [OriginalFilename], [ContentType], [FileSize], [BlobUrl])
        VALUES (1, SYSUTCDATETIME(), N'f', N'f', N'application/pdf', 1, N'u'),
               (2, SYSUTCDATETIME(), N'f', N'f', N'application/pdf', 1, N'u'),
               (3, SYSUTCDATETIME(), N'f', N'f', N'application/pdf', 1, N'u');
        SET IDENTITY_INSERT dbo.[Attachment] OFF;
        SET IDENTITY_INSERT dbo.[Bill] ON;
        INSERT dbo.[Bill]([Id], [CreatedDatetime], [BillDate], [DueDate], [Status], [VendorId])
        VALUES (60, SYSUTCDATETIME(), SYSUTCDATETIME(), SYSUTCDATETIME(), N'completed', 1),
               (61, SYSUTCDATETIME(), SYSUTCDATETIME(), SYSUTCDATETIME(), N'completed', 1);
        SET IDENTITY_INSERT dbo.[Bill] OFF;
        SET IDENTITY_INSERT dbo.[BillLineItem] ON;
        INSERT dbo.[BillLineItem]([Id], [CreatedDatetime], [BillId], [SubCostCodeId], [ProjectId], [Amount], [IsDraft])
        VALUES (600, SYSUTCDATETIME(), 60, 1, 1, 100, 0), (601, SYSUTCDATETIME(), 60, 1, 1, 50, 0),
               (610, SYSUTCDATETIME(), 61, 1, 1, 100, 0);
        SET IDENTITY_INSERT dbo.[BillLineItem] OFF;
        SET IDENTITY_INSERT dbo.[BillLineItemAttachment] ON;
        INSERT dbo.[BillLineItemAttachment]([Id], [CreatedDatetime], [BillLineItemId], [AttachmentId])
        VALUES (6000, SYSUTCDATETIME(), 600, 1), (6001, SYSUTCDATETIME(), 601, 2),
               (6100, SYSUTCDATETIME(), 610, 3);
        SET IDENTITY_INSERT dbo.[BillLineItemAttachment] OFF;
        EXEC dbo.UpsertAttachmentTextTokenHash @AttachmentId=1, @TokenizerVersion='u701a-v1:abcd', @TokenHashes='a', @TokenCount=1;
        EXEC dbo.UpsertAttachmentTextTokenHash @AttachmentId=2, @TokenizerVersion='u701a-v1:abcd', @TokenHashes='b', @TokenCount=1;
        EXEC dbo.UpsertAttachmentTextTokenHash @AttachmentId=3, @TokenizerVersion='u701a-v1:abcd', @TokenHashes='c', @TokenCount=1;
        INSERT dbo.[TaxpayerAttachment]([CreatedDatetime], [TaxpayerId], [AttachmentId])
        VALUES (SYSUTCDATETIME(), 1, 1), (SYSUTCDATETIME(), 1, 3);
        """
    )
    cur.execute("EXEC dbo.ReadSimilarBillsCorpusRows @TokenizerVersion='u701a-v1:abcd'")
    rows = {int(r.BillId): int(r.AttachmentId) for r in cur.fetchall()}
    assert rows == {60: 2}
