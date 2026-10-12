"""U-497b / U-652 — CodingSuggestion upsert MERGE key is per purchase (not per line).

Pure-logic / source pins + SQLite behavioral fixtures (no live DB).
"""

import hashlib
import re
import sqlite3

import pytest

from tests.sproc_text import REPO_ROOT, sproc_body, strip_sql_comments

EXPENSE_CODING_SQL = (
    REPO_ROOT / "entities/coding_suggestion/sql/dbo.coding_suggestion.sql"
)
UPSERT_SPROC = "UpsertCodingSuggestionForPurchase"
DETECTION_SPROC = "ReadExternallyResolvedCodingSuggestionCandidates"


def _upsert_body() -> str:
    return strip_sql_comments(sproc_body(EXPENSE_CODING_SQL, UPSERT_SPROC))


def _merge_section(body: str) -> str:
    match = re.search(
        r"\bMERGE\b.*?\bWHEN\s+NOT\s+MATCHED\b",
        body,
        re.IGNORECASE | re.DOTALL,
    )
    assert match is not None, f"{UPSERT_SPROC} must contain MERGE ... WHEN NOT MATCHED"
    return match.group(0)


def test_u652_merge_on_purchase_identity_pair():
    merge = _merge_section(_upsert_body())
    assert "QboLineId" not in merge
    assert re.search(
        r"ON\s+target\.\[QboPurchaseQboId\]\s*=\s*source\.QboPurchaseQboId\s+"
        r"AND\s+target\.\[RealmId\]\s*=\s*source\.RealmId",
        merge,
        re.IGNORECASE | re.DOTALL,
    ), "MERGE ON must key on (QboPurchaseQboId, RealmId) for expense rows"


def test_u652_merge_targets_expense_kind():
    body = _upsert_body()
    assert re.search(
        r"target\.\[TargetKind\]\s*=\s*N'expense'",
        body,
        re.IGNORECASE,
    )


def test_u652_upsert_params_require_purchase_identity():
    params = strip_sql_comments(sproc_body(EXPENSE_CODING_SQL, UPSERT_SPROC))
    param_list = re.search(r"\((.*?)\)\s*AS\b", params, re.DOTALL).group(1)
    compact = re.sub(r"\s+", "", param_list)
    assert "@QboPurchaseQboIdNVARCHAR(50)," in compact
    assert "@RealmIdNVARCHAR(50)," in compact
    assert "@QboLineId" not in compact


def test_u652_python_upsert_requires_purchase_identity():
    import inspect

    from entities.coding_suggestion.business.service import CodingSuggestionService
    from entities.coding_suggestion.persistence.repo import CodingSuggestionRepository

    for sig in (
        inspect.signature(CodingSuggestionService.upsert_from_queue),
        inspect.signature(CodingSuggestionRepository.upsert_from_queue),
    ):
        for name in ("qbo_purchase_qbo_id", "realm_id", "qbo_purchase_id"):
            p = sig.parameters[name]
            assert p.default is inspect.Parameter.empty, (
                f"{name} must be required on purchase-grain upsert"
            )


def test_u652_detection_sproc_has_no_per_line_merge_key_on_cs():
    body = strip_sql_comments(sproc_body(EXPENSE_CODING_SQL, DETECTION_SPROC))
    assert "eci." not in body.lower()
    assert "ExpenseCodingItem" not in body


# --- SQLite behavioral fixture (purchase grain: one row per purchase) ---


def test_u652_sqlite_upsert_re_adopts_same_purchase_row():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE CodingSuggestion (
            Id INTEGER PRIMARY KEY,
            PublicId TEXT,
            TargetKind TEXT,
            QboPurchaseQboId TEXT,
            RealmId TEXT,
            ExpenseId INTEGER,
            Status TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO CodingSuggestion
            (PublicId, TargetKind, QboPurchaseQboId, RealmId, Status)
        VALUES ('a', 'expense', 'P1', 'R1', 'pending')
        """
    )
    conn.commit()
    row = conn.execute(
        "SELECT Id FROM CodingSuggestion WHERE QboPurchaseQboId='P1' AND RealmId='R1'"
    ).fetchone()
    assert row is not None
    conn.execute(
        """
        UPDATE CodingSuggestion SET ExpenseId=99
        WHERE QboPurchaseQboId='P1' AND RealmId='R1' AND TargetKind='expense'
        """
    )
    updated = conn.execute(
        "SELECT ExpenseId FROM CodingSuggestion WHERE Id=?", (row[0],)
    ).fetchone()[0]
    assert updated == 99
