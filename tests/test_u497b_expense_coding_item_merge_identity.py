"""U-497b — ExpenseCodingItem MERGE key on dbo-native purchase-line identity.

Pure-logic / source pins + SQLite behavioral fixtures (no live DB).
"""

import hashlib
import re
import sqlite3

import pytest

from tests.sproc_text import REPO_ROOT, sproc_body, strip_sql_comments

EXPENSE_CODING_SQL = (
    REPO_ROOT / "entities/expense_coding_item/sql/dbo.expense_coding_item.sql"
)
UPSERT_SPROC = "UpsertExpenseCodingItem"
DETECTION_SPROC = "ReadExternallyResolvedCodingItemCandidates"

# MERGE ... WHEN NOT MATCHED slice only: QboLineId appears 8x after U-497b (whole
# UpsertExpenseCodingItem body is 12x — OUTPUT/params also reference it).
_UPSERT_MERGE_QBO_LINE_ID_COUNT = 8


def _upsert_body() -> str:
    return strip_sql_comments(sproc_body(EXPENSE_CODING_SQL, UPSERT_SPROC))


def _merge_section(body: str) -> str:
    match = re.search(
        r"\bMERGE\b.*?\bWHEN\s+NOT\s+MATCHED\b",
        body,
        re.IGNORECASE | re.DOTALL,
    )
    assert match is not None, "UpsertExpenseCodingItem must contain MERGE ... WHEN NOT MATCHED"
    return match.group(0)


def _when_matched_update(body: str) -> str:
    match = re.search(
        r"WHEN\s+MATCHED\s+THEN\s+UPDATE\s+SET\s+(.*?)(?:\bWHEN\s+NOT\s+MATCHED\b|\bOUTPUT\b)",
        body,
        re.IGNORECASE | re.DOTALL,
    )
    assert match is not None, "UpsertExpenseCodingItem must have WHEN MATCHED THEN UPDATE SET"
    return match.group(1)


def test_u497b_s1_merge_on_keys_all_three_triple_columns():
    body = _upsert_body()
    merge = _merge_section(body)
    assert merge.count("QboLineId") == _UPSERT_MERGE_QBO_LINE_ID_COUNT
    assert merge.count("QboPurchaseQboId") >= 2
    assert merge.count("RealmId") >= 2
    assert merge.count("QboLineId") >= 2
    assert re.search(
        r"ON\s+target\.\[QboPurchaseQboId\]\s*=\s*source\.QboPurchaseQboId\s+"
        r"AND\s+target\.\[RealmId\]\s*=\s*source\.RealmId\s+"
        r"AND\s+target\.\[QboLineId\]\s*=\s*source\.QboLineId",
        merge,
        re.IGNORECASE | re.DOTALL,
    ), "MERGE ON must key on (QboPurchaseQboId, RealmId, QboLineId)"


def test_u497b_s2_when_matched_refreshes_qbo_purchase_line_id():
    body = _upsert_body()
    update_set = _when_matched_update(body)
    assert re.search(
        r"\[QboPurchaseLineId\]\s*=\s*@QboPurchaseLineId",
        update_set,
        re.IGNORECASE,
    ), (
        "WHEN MATCHED must refresh QboPurchaseLineId to the current staging PK "
        "(self-heal after pull churn)"
    )


def test_u497b_s3_merge_on_does_not_key_on_staging_pk():
    merge = _merge_section(_upsert_body())
    assert not re.search(
        r"ON\s+target\.\[QboPurchaseLineId\]\s*=\s*source\.QboPurchaseLineId",
        merge,
        re.IGNORECASE,
    ), "MERGE ON must not use volatile QboPurchaseLineId as the dedupe key"


def test_u497b_s4_arm1_dbo_native_missing_line_predicate():
    body = strip_sql_comments(sproc_body(EXPENSE_CODING_SQL, DETECTION_SPROC))
    parts = re.split(r"\bUNION\b", body, maxsplit=1, flags=re.IGNORECASE)
    arm1 = parts[0]
    assert not re.search(r"eci\.\[QboPurchaseLineId\]", arm1, re.IGNORECASE)
    assert re.search(
        r"NOT\s+EXISTS\s*\(\s*"
        r"SELECT\s+1\s+"
        r"FROM\s+\[qbo\]\.\[Purchase\]\s+p\s+"
        r"INNER\s+JOIN\s+\[qbo\]\.\[PurchaseLine\]\s+pl\s+"
        r"ON\s+pl\.\[QboPurchaseId\]\s*=\s*p\.\[Id\]\s+"
        r"WHERE\s+p\.\[QboId\]\s*=\s*eci\.\[QboPurchaseQboId\]\s+"
        r"AND\s+p\.\[RealmId\]\s*=\s*eci\.\[RealmId\]\s+"
        r"AND\s+pl\.\[QboLineId\]\s*=\s*eci\.\[QboLineId\]",
        arm1,
        re.IGNORECASE | re.DOTALL,
    )


def test_u497b_upsert_params_require_identity_triple():
    params = strip_sql_comments(sproc_body(EXPENSE_CODING_SQL, UPSERT_SPROC))
    param_list = re.search(r"\((.*?)\)\s*AS\b", params, re.DOTALL).group(1)
    compact = re.sub(r"\s+", "", param_list)
    assert "@QboLineIdNVARCHAR(50)," in compact
    assert "@QboPurchaseQboIdNVARCHAR(50)," in compact
    assert "@RealmIdNVARCHAR(50)," in compact
    assert "= NULL" not in param_list.split("@VendorId")[0]


def test_u497b_python_upsert_requires_identity_triple():
    import inspect

    from entities.expense_coding_item.business.service import ExpenseCodingItemService
    from entities.expense_coding_item.persistence.repo import ExpenseCodingItemRepository

    for sig in (
        inspect.signature(ExpenseCodingItemService.upsert_from_queue),
        inspect.signature(ExpenseCodingItemRepository.upsert_from_queue),
    ):
        for name in ("qbo_line_id", "qbo_purchase_qbo_id", "realm_id"):
            p = sig.parameters[name]
            assert p.default is inspect.Parameter.empty, (
                f"{name} must be required (no default NULL triple)"
            )


def test_u497b_unique_index_on_purchase_line_identity_in_base_file():
    text = EXPENSE_CODING_SQL.read_text()
    assert "UQ_ExpenseCodingItem_PurchaseLineIdentity" in text
    assert (
        "ON dbo.[ExpenseCodingItem] ([QboPurchaseQboId], [RealmId], [QboLineId])"
        in text
    )


# ---------------------------------------------------------------------------
# SQLite behavioral — mirrors MERGE key semantics from live sproc pins
# ---------------------------------------------------------------------------


def _sqlite_fixture_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("ATTACH DATABASE ':memory:' AS dbo")
    conn.execute("ATTACH DATABASE ':memory:' AS qbo")
    conn.executescript(
        """
        CREATE TABLE dbo.ExpenseCodingItem(
            Id INTEGER PRIMARY KEY,
            PublicId TEXT NOT NULL,
            QboPurchaseId INTEGER NOT NULL,
            QboPurchaseLineId INTEGER NOT NULL,
            QboLineId TEXT NOT NULL,
            QboPurchaseQboId TEXT NOT NULL,
            RealmId TEXT NOT NULL,
            Status TEXT NOT NULL DEFAULT 'pending',
            SuggestedDescription TEXT,
            ModifiedDatetime TEXT
        );
        CREATE TABLE qbo.Purchase(
            Id INTEGER PRIMARY KEY,
            QboId TEXT,
            RealmId TEXT
        );
        CREATE TABLE qbo.PurchaseLine(
            Id INTEGER PRIMARY KEY,
            QboPurchaseId INTEGER,
            QboLineId TEXT,
            AccountRefName TEXT,
            ItemRefValue TEXT
        );
        """
    )
    return conn


def _sqlite_upsert_like_sproc(
    conn: sqlite3.Connection,
    *,
    qbo_purchase_id: int,
    qbo_purchase_line_id: int,
    qbo_line_id: str,
    qbo_purchase_qbo_id: str,
    realm_id: str,
) -> None:
    """Upsert keyed like UpsertExpenseCodingItem MERGE ON (triple identity)."""
    row = conn.execute(
        """
        SELECT Id FROM dbo.ExpenseCodingItem
        WHERE QboPurchaseQboId = ? AND RealmId = ? AND QboLineId = ?
        """,
        (qbo_purchase_qbo_id, realm_id, qbo_line_id),
    ).fetchone()
    if row:
        conn.execute(
            """
            UPDATE dbo.ExpenseCodingItem
            SET QboPurchaseId = ?,
                QboPurchaseLineId = ?,
                ModifiedDatetime = 'now'
            WHERE Id = ?
            """,
            (qbo_purchase_id, qbo_purchase_line_id, row["Id"]),
        )
    else:
        conn.execute(
            """
            INSERT INTO dbo.ExpenseCodingItem (
                PublicId, QboPurchaseId, QboPurchaseLineId, QboLineId,
                QboPurchaseQboId, RealmId, Status, ModifiedDatetime
            ) VALUES (?, ?, ?, ?, ?, ?, 'pending', 'now')
            """,
            (
                "new-pub",
                qbo_purchase_id,
                qbo_purchase_line_id,
                qbo_line_id,
                qbo_purchase_qbo_id,
                realm_id,
            ),
        )
    conn.commit()


def _sqlite_upsert_staging_pk_key(
    conn: sqlite3.Connection,
    *,
    qbo_purchase_id: int,
    qbo_purchase_line_id: int,
    qbo_line_id: str,
    qbo_purchase_qbo_id: str,
    realm_id: str,
) -> None:
    """Old MERGE key (staging PK only) — used to prove mutation 5 goes RED."""
    row = conn.execute(
        "SELECT Id FROM dbo.ExpenseCodingItem WHERE QboPurchaseLineId = ?",
        (qbo_purchase_line_id,),
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE dbo.ExpenseCodingItem SET QboPurchaseId = ? WHERE Id = ?",
            (qbo_purchase_id, row["Id"]),
        )
    else:
        conn.execute(
            """
            INSERT INTO dbo.ExpenseCodingItem (
                PublicId, QboPurchaseId, QboPurchaseLineId, QboLineId,
                QboPurchaseQboId, RealmId, Status, ModifiedDatetime
            ) VALUES (?, ?, ?, ?, ?, ?, 'pending', 'now')
            """,
            (
                "new-pub",
                qbo_purchase_id,
                qbo_purchase_line_id,
                qbo_line_id,
                qbo_purchase_qbo_id,
                realm_id,
            ),
        )
    conn.commit()


def _arm1_candidates_sqlite() -> str:
    return """
        SELECT eci.PublicId
        FROM dbo.ExpenseCodingItem eci
        WHERE eci.Status IN ('pending', 'suggested', 'flagged', 'changed_in_qbo')
          AND NOT EXISTS (
              SELECT 1
              FROM qbo.Purchase p
              INNER JOIN qbo.PurchaseLine pl ON pl.QboPurchaseId = p.Id
              WHERE p.QboId = eci.QboPurchaseQboId
                AND p.RealmId = eci.RealmId
                AND pl.QboLineId = eci.QboLineId
          )
          AND EXISTS (
              SELECT 1 FROM qbo.Purchase p
              WHERE p.QboId = eci.QboPurchaseQboId AND p.RealmId = eci.RealmId
          )
          AND NOT EXISTS (
              SELECT 1 FROM qbo.Purchase p
              INNER JOIN qbo.PurchaseLine pl ON pl.QboPurchaseId = p.Id
              WHERE p.QboId = eci.QboPurchaseQboId AND p.RealmId = eci.RealmId
                AND pl.AccountRefName LIKE '%NEED TO CATEGORIZE%'
          )
    """


def test_u497b_s5_reseed_re_adopts_existing_row_under_new_staging_pk():
    conn = _sqlite_fixture_conn()
    conn.executescript(
        """
        INSERT INTO dbo.ExpenseCodingItem VALUES (
            1, 'keep-pub', 10, 100, '1', 'PUR-1', 'realm-1',
            'suggested', 'history-field', 't0'
        );
        INSERT INTO qbo.Purchase VALUES (10, 'PUR-1', 'realm-1');
        INSERT INTO qbo.PurchaseLine VALUES (200, 10, '1', 'Cost : NEED TO CATEGORIZE', NULL);
        """
    )
    _sqlite_upsert_like_sproc(
        conn,
        qbo_purchase_id=10,
        qbo_purchase_line_id=200,
        qbo_line_id="1",
        qbo_purchase_qbo_id="PUR-1",
        realm_id="realm-1",
    )
    rows = conn.execute("SELECT * FROM dbo.ExpenseCodingItem").fetchall()
    assert len(rows) == 1
    assert rows[0]["PublicId"] == "keep-pub"
    assert rows[0]["QboPurchaseLineId"] == 200
    assert rows[0]["SuggestedDescription"] == "history-field"
    assert rows[0]["Status"] == "suggested"


def test_u497b_s5_mutation_staging_pk_key_inserts_second_row():
    conn = _sqlite_fixture_conn()
    conn.executescript(
        """
        INSERT INTO dbo.ExpenseCodingItem VALUES (
            1, 'keep-pub', 10, 100, '1', 'PUR-1', 'realm-1',
            'suggested', 'history-field', 't0'
        );
        INSERT INTO qbo.Purchase VALUES (10, 'PUR-1', 'realm-1');
        INSERT INTO qbo.PurchaseLine VALUES (200, 10, '1', 'Cost : NEED TO CATEGORIZE', NULL);
        """
    )
    _sqlite_upsert_staging_pk_key(
        conn,
        qbo_purchase_id=10,
        qbo_purchase_line_id=200,
        qbo_line_id="1",
        qbo_purchase_qbo_id="PUR-1",
        realm_id="realm-1",
    )
    assert len(conn.execute("SELECT * FROM dbo.ExpenseCodingItem").fetchall()) == 2


def test_u497b_s6_arm1_returns_item_when_line_genuinely_gone():
    conn = _sqlite_fixture_conn()
    conn.executescript(
        """
        INSERT INTO dbo.ExpenseCodingItem VALUES (
            1, 'orphan-pub', 10, 100, '9', 'PUR-1', 'realm-1',
            'pending', NULL, 't0'
        );
        INSERT INTO qbo.Purchase VALUES (10, 'PUR-1', 'realm-1');
        INSERT INTO qbo.PurchaseLine VALUES (200, 10, '1', 'Materials : coded', 'item-1');
        """
    )
    rows = conn.execute(_arm1_candidates_sqlite()).fetchall()
    assert len(rows) == 1
    assert rows[0]["PublicId"] == "orphan-pub"


def _arm1_staging_pk_predicate_sqlite() -> str:
    """Arm 1 before U-497b (staging PK missing) — mutation 6 must not match."""
    return """
        SELECT eci.PublicId
        FROM dbo.ExpenseCodingItem eci
        WHERE eci.Status IN ('pending', 'suggested', 'flagged', 'changed_in_qbo')
          AND NOT EXISTS (
              SELECT 1 FROM qbo.PurchaseLine pl
              WHERE pl.Id = eci.QboPurchaseLineId
          )
          AND EXISTS (
              SELECT 1 FROM qbo.Purchase p
              WHERE p.QboId = eci.QboPurchaseQboId AND p.RealmId = eci.RealmId
          )
          AND NOT EXISTS (
              SELECT 1 FROM qbo.Purchase p
              INNER JOIN qbo.PurchaseLine pl ON pl.QboPurchaseId = p.Id
              WHERE p.QboId = eci.QboPurchaseQboId AND p.RealmId = eci.RealmId
                AND pl.AccountRefName LIKE '%NEED TO CATEGORIZE%'
          )
    """


def test_u497b_s6_mutation_staging_pk_arm1_false_positive_after_re_adopt():
    """Stale PK arm fires while dbo-native arm correctly skips (line returned under new Id)."""
    conn = _sqlite_fixture_conn()
    conn.executescript(
        """
        INSERT INTO dbo.ExpenseCodingItem VALUES (
            1, 're-adopted-pub', 10, 100, '9', 'PUR-1', 'realm-1',
            'pending', NULL, 't0'
        );
        INSERT INTO qbo.Purchase VALUES (10, 'PUR-1', 'realm-1');
        INSERT INTO qbo.PurchaseLine VALUES (500, 10, '9', 'Materials : coded', 'item-9');
        """
    )
    assert len(conn.execute(_arm1_candidates_sqlite()).fetchall()) == 0
    assert len(conn.execute(_arm1_staging_pk_predicate_sqlite()).fetchall()) == 1


# ---------------------------------------------------------------------------
# Mutation proofs (in-memory sproc text)
# ---------------------------------------------------------------------------


def _file_md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def _mutate_drop_qbo_line_id_from_merge_on(text: str) -> str:
    return text.replace(
        "AND target.[RealmId] = source.RealmId\n"
        "       AND target.[QboLineId] = source.QboLineId",
        "AND target.[RealmId] = source.RealmId",
        1,
    )


def _mutate_remove_qbo_purchase_line_id_refresh(text: str) -> str:
    return text.replace(
        "            [QboPurchaseLineId] = @QboPurchaseLineId,\n",
        "",
        1,
    )


def _mutate_restore_staging_pk_merge_on(text: str) -> str:
    old = (
        "    MERGE dbo.[ExpenseCodingItem] WITH (HOLDLOCK) AS target\n"
        "    USING (\n"
        "        SELECT\n"
        "            @QboPurchaseQboId AS QboPurchaseQboId,\n"
        "            @RealmId AS RealmId,\n"
        "            @QboLineId AS QboLineId\n"
        "    ) AS source\n"
        "    ON target.[QboPurchaseQboId] = source.QboPurchaseQboId\n"
        "       AND target.[RealmId] = source.RealmId\n"
        "       AND target.[QboLineId] = source.QboLineId\n"
    )
    new = (
        "    MERGE dbo.[ExpenseCodingItem] WITH (HOLDLOCK) AS target\n"
        "    USING (SELECT @QboPurchaseLineId AS QboPurchaseLineId) AS source\n"
        "    ON target.[QboPurchaseLineId] = source.QboPurchaseLineId\n"
    )
    return text.replace(old, new, 1)


def _mutate_restore_arm1_staging_pk(text: str) -> str:
    new_arm = (
        "      AND NOT EXISTS (\n"
        "          SELECT 1\n"
        "          FROM [qbo].[PurchaseLine] pl\n"
        "          WHERE pl.[Id] = eci.[QboPurchaseLineId]\n"
        "      )"
    )
    old_arm = (
        "      AND NOT EXISTS (\n"
        "          SELECT 1\n"
        "          FROM [qbo].[Purchase] p\n"
        "          INNER JOIN [qbo].[PurchaseLine] pl ON pl.[QboPurchaseId] = p.[Id]\n"
        "          WHERE p.[QboId] = eci.[QboPurchaseQboId]\n"
        "            AND p.[RealmId] = eci.[RealmId]\n"
        "            AND pl.[QboLineId] = eci.[QboLineId]\n"
        "      )"
    )
    return text.replace(old_arm, new_arm, 1)


def _upsert_body_from_full_sql(text: str) -> str:
    return strip_sql_comments(
        re.search(
            rf"CREATE\s+OR\s+ALTER\s+PROCEDURE\s+(?:dbo\.)?{UPSERT_SPROC}\b(.*?)^GO\s*$",
            text,
            re.DOTALL | re.MULTILINE | re.IGNORECASE,
        ).group(1)
    )


def test_u497b_mutation_s1_drop_qbo_line_id_from_on_goes_red():
    original = EXPENSE_CODING_SQL.read_text()
    mutated = _mutate_drop_qbo_line_id_from_merge_on(original)
    assert mutated != original
    assert _file_md5(mutated) != _file_md5(original)
    body = _upsert_body_from_full_sql(mutated)
    merge = _merge_section(body)
    with pytest.raises(AssertionError):
        assert re.search(
            r"AND\s+target\.\[QboLineId\]\s*=\s*source\.QboLineId",
            merge,
            re.IGNORECASE,
        )


def test_u497b_mutation_s2_remove_line_id_refresh_goes_red():
    original = EXPENSE_CODING_SQL.read_text()
    mutated = _mutate_remove_qbo_purchase_line_id_refresh(original)
    assert mutated != original
    body = _upsert_body_from_full_sql(mutated)
    update_set = _when_matched_update(body)
    with pytest.raises(AssertionError):
        assert re.search(
            r"\[QboPurchaseLineId\]\s*=\s*@QboPurchaseLineId",
            update_set,
            re.IGNORECASE,
        )


def test_u497b_mutation_s3_restore_staging_pk_on_goes_red():
    original = EXPENSE_CODING_SQL.read_text()
    mutated = _mutate_restore_staging_pk_merge_on(original)
    assert mutated != original
    assert _file_md5(mutated) != _file_md5(original)
    body = _upsert_body_from_full_sql(mutated)
    merge = _merge_section(body)
    assert re.search(
        r"ON\s+target\.\[QboPurchaseLineId\]\s*=\s*source\.QboPurchaseLineId",
        merge,
        re.IGNORECASE,
    )
    with pytest.raises(AssertionError):
        assert not re.search(
            r"ON\s+target\.\[QboPurchaseLineId\]\s*=\s*source\.QboPurchaseLineId",
            merge,
            re.IGNORECASE,
        )


def test_u497b_mutation_s4_restore_arm1_pk_predicate_goes_red():
    original = EXPENSE_CODING_SQL.read_text()
    mutated = _mutate_restore_arm1_staging_pk(original)
    assert mutated != original
    body = strip_sql_comments(mutated)
    parts = re.split(r"\bUNION\b", body, maxsplit=1, flags=re.I)
    arm1 = parts[0]
    assert re.search(r"pl\.\[Id\]\s*=\s*eci\.\[QboPurchaseLineId\]", arm1, re.I)
    with pytest.raises(AssertionError):
        assert not re.search(r"eci\.\[QboPurchaseLineId\]", arm1, re.I)


def test_u497b_mutation_s1_drop_qbo_line_id_md5_confirmed():
    original = EXPENSE_CODING_SQL.read_text()
    mutated = _mutate_drop_qbo_line_id_from_merge_on(original)
    assert _file_md5(mutated) != _file_md5(original)


def test_u497b_mutation_s2_remove_refresh_md5_confirmed():
    original = EXPENSE_CODING_SQL.read_text()
    mutated = _mutate_remove_qbo_purchase_line_id_refresh(original)
    assert _file_md5(mutated) != _file_md5(original)

