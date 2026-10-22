"""U-701a: backfill candidate query against real Attachment schema (no IsTaxpayerDocument column)."""
from __future__ import annotations

import os
from datetime import datetime

import pytest

from scripts import u701a_backfill_token_hashes as bf
from tests_sqlserver.fixtures.u701a_real_schema import _drain_cursor, u701a_real_schema_conn

pytestmark = pytest.mark.skipif(
    not os.environ.get("BUILDONE_TEST_SQLSERVER"),
    reason="BUILDONE_TEST_SQLSERVER not set",
)


def _insert_attachment(cur, att_id: int, *, extraction: str = "completed", blob: str = "https://x/y.json"):
    now = datetime.utcnow()
    cur.execute(
        """
        SET IDENTITY_INSERT dbo.[Attachment] ON;
        INSERT dbo.[Attachment] (
            [Id], [CreatedDatetime], [Filename], [OriginalFilename], [ContentType],
            [FileSize], [BlobUrl], [ExtractionStatus], [ExtractedTextBlobUrl]
        ) VALUES (?, ?, N'f.pdf', N'f.pdf', N'application/pdf', 1, N'https://blob/x', ?, ?);
        SET IDENTITY_INSERT dbo.[Attachment] OFF;
        """,
        (att_id, now, extraction, blob),
    )


def test_backfill_candidates_real_schema(u701a_real_schema_conn):
    conn = u701a_real_schema_conn
    cur = conn.cursor()
    ver = "u701a-v1:abcd"

    _insert_attachment(cur, 101)
    _insert_attachment(cur, 102)
    _insert_attachment(cur, 103)
    _insert_attachment(cur, 104)
    _insert_attachment(cur, 105)  # linked, already holds a CURRENT-version hash
    _insert_attachment(cur, 106, extraction="failed")  # linked, extraction not completed
    _insert_attachment(cur, 107, blob=None)  # linked, no extracted-text blob

    cur.execute(
        """
        SET IDENTITY_INSERT dbo.[Bill] ON;
        INSERT dbo.[Bill]([Id], [CreatedDatetime], [BillDate], [DueDate], [Status], [VendorId])
        VALUES (501, SYSUTCDATETIME(), SYSUTCDATETIME(), SYSUTCDATETIME(), N'completed', 1);
        SET IDENTITY_INSERT dbo.[Bill] OFF;

        SET IDENTITY_INSERT dbo.[BillLineItem] ON;
        INSERT dbo.[BillLineItem]([Id], [CreatedDatetime], [BillId], [SubCostCodeId], [ProjectId], [Amount], [IsDraft])
        VALUES (601, SYSUTCDATETIME(), 501, 1, 1, 100.00, 0),
               (602, SYSUTCDATETIME(), 501, 1, 1, 50.00, 0),
               (603, SYSUTCDATETIME(), 501, 1, 1, 25.00, 0),
               (604, SYSUTCDATETIME(), 501, 1, 1, 10.00, 0),
               (605, SYSUTCDATETIME(), 501, 1, 1, 10.00, 0),
               (606, SYSUTCDATETIME(), 501, 1, 1, 10.00, 0);
        SET IDENTITY_INSERT dbo.[BillLineItem] OFF;

        SET IDENTITY_INSERT dbo.[BillLineItemAttachment] ON;
        INSERT dbo.[BillLineItemAttachment]([Id], [CreatedDatetime], [BillLineItemId], [AttachmentId])
        VALUES (701, SYSUTCDATETIME(), 601, 101),
               (702, SYSUTCDATETIME(), 602, 102),
               (703, SYSUTCDATETIME(), 603, 103),
               (704, SYSUTCDATETIME(), 604, 105),
               (705, SYSUTCDATETIME(), 605, 106),
               (706, SYSUTCDATETIME(), 606, 107);
        SET IDENTITY_INSERT dbo.[BillLineItemAttachment] OFF;

        INSERT dbo.[TaxpayerAttachment]([CreatedDatetime], [TaxpayerId], [AttachmentId])
        VALUES (SYSUTCDATETIME(), 1, 102);

        EXEC dbo.UpsertAttachmentTextTokenHash @AttachmentId=103, @TokenizerVersion='u701a-v1:old',
            @TokenHashes='stale', @TokenCount=1;
        EXEC dbo.UpsertAttachmentTextTokenHash @AttachmentId=105, @TokenizerVersion='u701a-v1:abcd',
            @TokenHashes='current', @TokenCount=1;
        """
    )
    _drain_cursor(cur)
    ids = {row[0] for row in bf._list_candidates(conn, tokenizer_version=ver)}
    assert ids == {101, 103}
    assert 102 not in ids
    assert 104 not in ids  # not linked to any bill line
