"""U-715: ReadCodingSuggestionExistsForPurchase against real SQL Server."""
from __future__ import annotations

import os
import uuid

import pytest

from tests_sqlserver.test_u652_coding_suggestion_sql import _insert_expense_purchase, conn

pytestmark = pytest.mark.skipif(
    not os.environ.get("BUILDONE_TEST_SQLSERVER"),
    reason="BUILDONE_TEST_SQLSERVER not set (needs a throwaway SQL Server)",
)


def test_read_exists_for_purchase_returns_row_or_empty(conn):
    cur = conn.cursor()
    purchase_id, _, expense_id = _insert_expense_purchase(
        cur, qbo_purchase_qbo_id="P-715", realm_id="realm-715"
    )
    pub = uuid.uuid4()
    cur.execute(
        """
        INSERT INTO dbo.CodingSuggestion (
            PublicId, TargetKind, ExpenseId, QboPurchaseId, QboPurchaseQboId, RealmId,
            Status, SuggestedProjectId
        ) VALUES (?, N'expense', ?, ?, N'P-715', N'realm-715', N'pending', 999);
        """,
        (pub, expense_id, purchase_id),
    )
    conn.commit()

    cur.execute(
        """
        CREATE OR ALTER FUNCTION dbo.UserCanAccessProject(
            @ActorUserId BIGINT,
            @ActorIsSystemAdmin BIT,
            @ProjectId BIGINT
        )
        RETURNS BIT
        AS
        BEGIN
            IF @ProjectId = 999 RETURN 0;
            RETURN 1;
        END;
        """
    )
    conn.commit()

    cur.execute(
        """
        EXEC dbo.ReadCodingSuggestionExistsForPurchase @RealmId=N'realm-715', @QboPurchaseQboId=N'P-715';
        """
    )
    row = cur.fetchone()
    assert row is not None
    assert str(row.PublicId).lower() == str(pub).lower()

    cur.execute(
        """
        EXEC dbo.ReadCodingSuggestionExistsForPurchase @RealmId=N'realm-715', @QboPurchaseQboId=N'missing';
        """
    )
    assert cur.fetchone() is None

    cur.execute(
        """
        EXEC dbo.ReadCodingSuggestionStateByExpenseIds @ExpenseIds=?, @ActorUserId=42, @ActorIsSystemAdmin=0;
        """,
        (str(expense_id),),
    )
    assert cur.fetchall() == []
    conn.commit()
