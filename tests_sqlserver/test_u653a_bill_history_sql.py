"""U-653a: bill vendor-history sprocs against real SQL Server."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from uuid import UUID

import pyodbc
import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("BUILDONE_TEST_SQLSERVER"),
    reason="BUILDONE_TEST_SQLSERVER not set (needs a throwaway SQL Server)",
)

_LOCAL_SERVERS = {"127.0.0.1", "localhost"}


def _server_host(conn_str: str) -> str:
    for part in conn_str.split(";"):
        key, _, value = part.partition("=")
        if key.strip().upper() == "SERVER":
            host = re.sub(r"^tcp:", "", value.strip().strip("{}"), flags=re.IGNORECASE)
            return host.split(",")[0].strip().lower()
    return ""


if os.environ.get("BUILDONE_TEST_SQLSERVER") and _server_host(
    os.environ["BUILDONE_TEST_SQLSERVER"]
) not in _LOCAL_SERVERS:
    raise RuntimeError(
        "tests_sqlserver refuses to run: SERVER in BUILDONE_TEST_SQLSERVER must be 127.0.0.1 or localhost"
    )

REPO_ROOT = Path(__file__).resolve().parents[1]
BILL_CODING_SQL = REPO_ROOT / "entities/coding_suggestion/sql/bill_coding_suggestion.sql"
CODING_SUGGESTION_SQL = REPO_ROOT / "entities/coding_suggestion/sql/dbo.coding_suggestion.sql"
GOLDEN_PATH = Path(__file__).resolve().parent / "u653a_bill_history_golden.json"


def _sqlserver_connect(conn_str: str, *, autocommit: bool = False):
    return pyodbc.connect(conn_str, autocommit=autocommit)


def _batches(sql: str) -> list[str]:
    out, cur = [], []
    for line in sql.splitlines():
        if line.strip() == "GO":
            out.append("\n".join(cur))
            cur = []
        else:
            cur.append(line)
    out.append("\n".join(cur))
    return [b for b in out if any(l.strip() and not l.strip().startswith("--") for l in b.splitlines())]


def _exec_batches(cur, path: Path) -> None:
    for batch in _batches(path.read_text(encoding="utf-8")):
        cur.execute(batch)


def _drop_all(cur) -> None:
    cur.execute(
        "IF OBJECT_ID('dbo.BillDominantVoteForVendor', 'IF') IS NOT NULL "
        "DROP FUNCTION dbo.BillDominantVoteForVendor"
    )
    for proc in (
        "dbo.ReadCodingSuggestionByBillId",
        "dbo.UpsertCodingSuggestionForBill",
        "dbo.ReadVendorDominantProjectForBills",
        "dbo.ReadVendorDominantSubCostCodeForBills",
    ):
        cur.execute(f"IF OBJECT_ID('{proc}', 'P') IS NOT NULL DROP PROCEDURE {proc}")
    for tbl in (
        "dbo.CodingSuggestion",
        "dbo.BillLineItem",
        "dbo.Bill",
        "dbo.SubCostCode",
    ):
        cur.execute(f"IF OBJECT_ID('{tbl}', 'U') IS NOT NULL DROP TABLE {tbl}")


def _install_minimal_schema(cur) -> None:
    """Minimal dbo.Bill / BillLineItem / SubCostCode / CodingSuggestion for U-653a history tests."""
    cur.execute(
        """
        CREATE TABLE dbo.SubCostCode (
            Id BIGINT NOT NULL PRIMARY KEY,
            PublicId UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
            Number NVARCHAR(20) NOT NULL,
            Name NVARCHAR(255) NOT NULL
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE dbo.Bill (
            Id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
            VendorId BIGINT NOT NULL,
            Status NVARCHAR(20) NOT NULL DEFAULT 'draft',
            CreatedDatetime DATETIME2(3) NOT NULL,
            IsDraft AS (CASE WHEN Status = 'completed' THEN CAST(0 AS BIT) ELSE CAST(1 AS BIT) END) PERSISTED
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE dbo.BillLineItem (
            Id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
            BillId BIGINT NOT NULL,
            SubCostCodeId BIGINT NULL,
            ProjectId BIGINT NULL,
            Amount DECIMAL(18,2) NULL
        );
        """
    )
    _exec_batches(cur, CODING_SUGGESTION_SQL)
    _exec_batches(cur, BILL_CODING_SQL)


def _insert_scc(cur, scc_id: int, number: str, *, public_id: str | None = None) -> None:
    if public_id is None:
        cur.execute(
            "INSERT INTO dbo.SubCostCode (Id, Number, Name) VALUES (?, ?, ?)",
            scc_id,
            number,
            f"Name-{number}",
        )
    else:
        cur.execute(
            "INSERT INTO dbo.SubCostCode (Id, PublicId, Number, Name) VALUES (?, ?, ?, ?)",
            scc_id,
            public_id,
            number,
            f"Name-{number}",
        )


def _insert_bill(cur, vendor_id: int, created: str, *, completed: bool = True) -> int:
    status = "completed" if completed else "draft"
    cur.execute(
        """
        INSERT INTO dbo.Bill (VendorId, Status, CreatedDatetime)
        OUTPUT INSERTED.Id
        VALUES (?, ?, ?)
        """,
        vendor_id,
        status,
        created,
    )
    return int(cur.fetchone()[0])


def _insert_line(cur, bill_id: int, scc_id: int, project_id: int, amount: float, line_id: int | None = None) -> None:
    if line_id is None:
        cur.execute(
            """
            INSERT INTO dbo.BillLineItem (BillId, SubCostCodeId, ProjectId, Amount)
            VALUES (?, ?, ?, ?)
            """,
            bill_id,
            scc_id,
            project_id,
            amount,
        )
    else:
        cur.execute("SET IDENTITY_INSERT dbo.BillLineItem ON")
        cur.execute(
            """
            INSERT INTO dbo.BillLineItem (Id, BillId, SubCostCodeId, ProjectId, Amount)
            VALUES (?, ?, ?, ?, ?)
            """,
            line_id,
            bill_id,
            scc_id,
            project_id,
            amount,
        )
        cur.execute("SET IDENTITY_INSERT dbo.BillLineItem OFF")


@pytest.fixture()
def conn():
    conn_str = os.environ["BUILDONE_TEST_SQLSERVER"]
    c = _sqlserver_connect(conn_str, autocommit=True)
    cur = c.cursor()
    _drop_all(cur)
    _install_minimal_schema(cur)
    yield c
    _drop_all(cur)
    c.close()


def _row_to_dict(row) -> dict | None:
    if row is None:
        return None
    return {col[0]: getattr(row, col[0]) for col in row.cursor_description}


def _exec_hist(cur, vendor_id: int, **kwargs):
    parts = ["@VendorId = ?"]
    params: list = [vendor_id]
    if "project_id" in kwargs:
        parts.append("@ProjectId = ?")
        params.append(kwargs["project_id"])
    if "as_of" in kwargs:
        parts.append("@AsOfDatetime = ?")
        params.append(kwargs["as_of"])
    if "exclude" in kwargs:
        parts.append("@ExcludeBillId = ?")
        params.append(kwargs["exclude"])
    sql = f"EXEC dbo.ReadVendorDominantSubCostCodeForBills {', '.join(parts)}"
    cur.execute(sql, params)
    row = cur.fetchone()
    while cur.nextset():
        pass
    return row


def _exec_project_hist(cur, vendor_id: int, **kwargs):
    parts = ["@VendorId = ?"]
    params: list = [vendor_id]
    if "as_of" in kwargs:
        parts.append("@AsOfDatetime = ?")
        params.append(kwargs["as_of"])
    if "exclude" in kwargs:
        parts.append("@ExcludeBillId = ?")
        params.append(kwargs["exclude"])
    sql = f"EXEC dbo.ReadVendorDominantProjectForBills {', '.join(parts)}"
    cur.execute(sql, params)
    row = cur.fetchone()
    while cur.nextset():
        pass
    return row


def _install_equivalence_fixture(cur) -> dict:
    """Rich multi-vendor bill history corpus for proc equivalence (40+ param combos)."""
    meta: dict = {"bill_ids": {}, "scc_public": {}}
    for scc_id, num, pid in (
        (1, "A", "11111111-1111-1111-1111-000000000001"),
        (2, "B", "11111111-1111-1111-1111-000000000002"),
        (3, "C", "11111111-1111-1111-1111-000000000003"),
        (4, "D", "11111111-1111-1111-1111-000000000004"),
    ):
        _insert_scc(cur, scc_id, num, public_id=pid)
        meta["scc_public"][scc_id] = pid

    v100 = []
    for i in range(3):
        bid = _insert_bill(cur, 100, f"2026-01-0{i + 1}T10:00:00")
        _insert_line(cur, bid, 2, 10, 300.0)
        _insert_line(cur, bid, 1, 10, 100.0)
        v100.append(bid)
    meta["bill_ids"][100] = v100

    v200 = []
    for day in ("01", "02", "03", "04"):
        bid = _insert_bill(cur, 200, f"2026-01-{day}T10:00:00")
        _insert_line(cur, bid, 1, 10, 10.0)
        v200.append(bid)
    meta["bill_ids"][200] = v200

    v300 = []
    for _ in range(2):
        bid = _insert_bill(cur, 300, "2026-01-01T10:00:00", completed=True)
        _insert_line(cur, bid, 1, 10, 1.0)
        v300.append(bid)
    draft = _insert_bill(cur, 300, "2026-01-02T10:00:00", completed=False)
    _insert_line(cur, draft, 1, 10, 999.0)
    v300.append(draft)
    meta["bill_ids"][300] = v300

    v400 = []
    b1 = _insert_bill(cur, 400, "2026-01-01T10:00:00")
    _insert_line(cur, b1, 1, 10, 100.0, line_id=501)
    v400.append(b1)
    b2 = _insert_bill(cur, 400, "2026-01-02T10:00:00")
    _insert_line(cur, b2, 1, 20, 100.0, line_id=502)
    v400.append(b2)
    b3 = _insert_bill(cur, 400, "2026-01-03T10:00:00")
    _insert_line(cur, b3, 2, 10, 100.0)
    v400.append(b3)
    b4 = _insert_bill(cur, 400, "2026-01-04T10:00:00")
    _insert_line(cur, b4, 1, 10, 50.0)
    v400.append(b4)
    meta["bill_ids"][400] = v400

    v801 = []
    for i in range(3):
        bid = _insert_bill(cur, 801, f"2026-02-0{i + 1}T10:00:00")
        _insert_line(cur, bid, 1, 10, 5.0)
        v801.append(bid)
    null_only = _insert_bill(cur, 801, "2026-02-04T10:00:00")
    cur.execute(
        "INSERT INTO dbo.BillLineItem (BillId, SubCostCodeId, ProjectId, Amount) VALUES (?, NULL, 10, 50)",
        null_only,
    )
    v801.append(null_only)
    meta["bill_ids"][801] = v801

    bid = _insert_bill(cur, 802, "2026-03-01T10:00:00")
    _insert_line(cur, bid, 1, 10, 10.0)
    bid2 = _insert_bill(cur, 802, "2026-03-02T10:00:00")
    cur.execute(
        "INSERT INTO dbo.BillLineItem (BillId, SubCostCodeId, ProjectId, Amount) VALUES (?, NULL, 10, 100)",
        bid2,
    )
    v802 = [bid, bid2]
    for i in range(2):
        b = _insert_bill(cur, 802, f"2026-03-0{i + 3}T10:00:00")
        _insert_line(cur, b, 1, 10, 1.0)
        v802.append(b)
    meta["bill_ids"][802] = v802

    v803 = []
    for i in range(3):
        b = _insert_bill(cur, 803, f"2026-04-0{i + 1}T10:00:00")
        _insert_line(cur, b, 1, 30, 100.0, line_id=600 + i * 2)
        _insert_line(cur, b, 2, 30, 100.0, line_id=601 + i * 2)
        v803.append(b)
    meta["bill_ids"][803] = v803

    v804 = []
    for proj, scc, n in ((10, 1, 3), (10, 1, 1), (20, 2, 2), (20, 2, 2), (20, 2, 2)):
        b = _insert_bill(cur, 804, f"2026-05-{n:02d}T10:00:00")
        _insert_line(cur, b, scc, proj, 50.0)
        v804.append(b)
    meta["bill_ids"][804] = v804

    v805 = []
    for day in ("01", "02", "03", "04", "05"):
        b = _insert_bill(cur, 805, f"2026-06-{day}T10:00:00")
        _insert_line(cur, b, 3, 40, float(day))
        v805.append(b)
    meta["bill_ids"][805] = v805

    v806 = []
    for i in range(2):
        b = _insert_bill(cur, 806, f"2026-07-0{i + 1}T10:00:00")
        _insert_line(cur, b, 4, 50, 1.0)
        v806.append(b)
    meta["bill_ids"][806] = v806

    tie = _insert_bill(cur, 807, "2026-08-01T10:00:00")
    _insert_line(cur, tie, 1, 60, -50.0)
    _insert_line(cur, tie, 1, 60, 50.0)
    meta["bill_ids"][807] = [tie]

    return meta


def _equivalence_param_grid(meta: dict) -> list[tuple[str, dict]]:
    """Return (case_key, kwargs) for SCC and project procs — 40+ combinations."""
    bids = meta["bill_ids"]
    grid: list[tuple[str, dict]] = []

    def add_scc(key: str, vendor: int, **kw):
        grid.append((f"scc:{key}", {"proc": "scc", "vendor": vendor, **kw}))

    def add_proj(key: str, vendor: int, **kw):
        grid.append((f"proj:{key}", {"proc": "proj", "vendor": vendor, **kw}))

    for vendor in (100, 200, 300, 400, 801, 802, 803, 804, 805, 806, 807):
        add_scc(f"v{vendor}-plain", vendor)
        add_proj(f"v{vendor}-plain", vendor)

    add_scc("v200-asof-mid", 200, as_of="2026-01-03T12:00:00")
    add_scc("v200-asof-full-excl", 200, as_of="2026-01-05T00:00:00", exclude=bids[200][0])
    add_proj("v200-asof-mid", 200, as_of="2026-01-03T12:00:00")
    add_proj("v200-excl-first", 200, exclude=bids[200][0])

    add_scc("v400-proj10", 400, project_id=10, as_of="2026-02-01T00:00:00")
    add_scc("v400-proj20", 400, project_id=20)
    add_scc("v400-proj99", 400, project_id=99)

    add_scc("v805-asof-excl", 805, as_of="2026-06-04T12:00:00", exclude=bids[805][4])
    add_proj("v805-asof-excl", 805, as_of="2026-06-04T12:00:00", exclude=bids[805][4])

    add_scc("v803-tie", 803)
    add_proj("v804-projects", 804)
    add_scc("v806-insufficient", 806)
    add_proj("v806-insufficient", 806)

    for as_of in (None, "2026-01-01T00:00:00", "2026-12-31T23:59:59"):
        label = "none" if as_of is None else as_of[:10]
        add_scc(f"v100-asof-{label}", 100, **({"as_of": as_of} if as_of else {}))
        add_proj(f"v100-asof-{label}", 100, **({"as_of": as_of} if as_of else {}))

    for ex in (None, bids[805][0], bids[805][2]):
        label = "none" if ex is None else f"id{ex}"
        kw = {} if ex is None else {"exclude": ex}
        add_scc(f"v805-excl-{label}", 805, **kw)
        add_proj(f"v805-excl-{label}", 805, **kw)

    assert len(grid) >= 40, f"expected >=40 combos, got {len(grid)}"
    return grid


def _run_equivalence_case(cur, case: dict):
    vendor = case["vendor"]
    if case["proc"] == "scc":
        row = _exec_hist(
            cur,
            vendor,
            **{k: v for k, v in case.items() if k in ("project_id", "as_of", "exclude")},
        )
    else:
        row = _exec_project_hist(
            cur,
            vendor,
            **{k: v for k, v in case.items() if k in ("as_of", "exclude")},
        )
    return _serialize_row(row)


def _serialize_row(row) -> dict | None:
    d = _row_to_dict(row)
    if d is None:
        return None
    out = {}
    for k, v in d.items():
        if hasattr(v, "hex"):
            out[k] = str(v)
        elif isinstance(v, UUID):
            out[k] = str(v)
        else:
            out[k] = v
    return out


def capture_equivalence_golden(conn_str: str | None = None) -> int:
    conn_str = conn_str or os.environ["BUILDONE_TEST_SQLSERVER"]
    c = _sqlserver_connect(conn_str, autocommit=True)
    cur = c.cursor()
    _drop_all(cur)
    _install_minimal_schema(cur)
    meta = _install_equivalence_fixture(cur)
    golden = {"meta": meta, "cases": {}}
    for key, case in _equivalence_param_grid(meta):
        golden["cases"][key] = _run_equivalence_case(cur, case)
    GOLDEN_PATH.write_text(json.dumps(golden, indent=2, sort_keys=True), encoding="utf-8")
    _drop_all(cur)
    c.close()
    return len(golden["cases"])


def test_dominant_scc_vote_and_minimum_support(conn):
    cur = conn.cursor()
    _insert_scc(cur, 1, "A")
    _insert_scc(cur, 2, "B")
    vendor = 100
    for i in range(3):
        bid = _insert_bill(cur, vendor, f"2026-01-0{i+1}T10:00:00")
        _insert_line(cur, bid, 2, 10, 300.0)
        _insert_line(cur, bid, 1, 10, 100.0)
    row = _exec_hist(cur, vendor, as_of="2026-02-01T00:00:00")
    assert row is not None
    assert int(row.TopSubCostCodeId) == 2
    assert int(row.TopCount) == 3
    assert int(row.TotalCount) == 3

    _drop_all(cur)
    _install_minimal_schema(cur)
    _insert_scc(cur, 1, "A")
    for i in range(2):
        bid = _insert_bill(cur, vendor, f"2026-01-0{i+1}T10:00:00")
        _insert_line(cur, bid, 1, 10, 50.0)
    assert _exec_hist(cur, vendor) is None


def test_as_of_excludes_future_bills_and_exclude_bill_id(conn):
    cur = conn.cursor()
    _insert_scc(cur, 1, "A")
    vendor = 200
    ids = []
    for day in ("01", "02", "03", "04"):
        ids.append(_insert_bill(cur, vendor, f"2026-01-{day}T10:00:00"))
        _insert_line(cur, ids[-1], 1, 10, 10.0)
    row = _exec_hist(cur, vendor, as_of="2026-01-03T12:00:00")
    assert row is not None
    assert int(row.TotalCount) == 3

    row2 = _exec_hist(cur, vendor, as_of="2026-01-05T00:00:00", exclude=ids[0])
    assert int(row2.TotalCount) == 3


def test_draft_bills_excluded(conn):
    cur = conn.cursor()
    _insert_scc(cur, 1, "A")
    vendor = 300
    for _ in range(2):
        bid = _insert_bill(cur, vendor, "2026-01-01T10:00:00", completed=True)
        _insert_line(cur, bid, 1, 10, 1.0)
    draft = _insert_bill(cur, vendor, "2026-01-02T10:00:00", completed=False)
    _insert_line(cur, draft, 1, 10, 999.0)
    assert _exec_hist(cur, vendor) is None


def test_project_filter_and_scc_tie_break(conn):
    cur = conn.cursor()
    _insert_scc(cur, 1, "A")
    _insert_scc(cur, 2, "B")
    vendor = 400
    b1 = _insert_bill(cur, vendor, "2026-01-01T10:00:00")
    _insert_line(cur, b1, 1, 10, 100.0, line_id=5)
    b2 = _insert_bill(cur, vendor, "2026-01-02T10:00:00")
    _insert_line(cur, b2, 1, 20, 100.0, line_id=3)
    b3 = _insert_bill(cur, vendor, "2026-01-03T10:00:00")
    _insert_line(cur, b3, 2, 10, 100.0)
    b4 = _insert_bill(cur, vendor, "2026-01-04T10:00:00")
    _insert_line(cur, b4, 1, 10, 50.0)
    row = _exec_hist(cur, vendor, project_id=10, as_of="2026-02-01T00:00:00")
    assert row is not None
    assert int(row.TopSubCostCodeId) == 1
    assert int(row.TopCount) == 2
    assert int(row.TotalCount) == 3


def test_upsert_twice_updates_in_place_confirmed_untouched(conn):
    cur = conn.cursor()
    bill_id = _insert_bill(cur, 500, "2026-01-01T10:00:00")
    cur.execute(
        """
        EXEC dbo.UpsertCodingSuggestionForBill
            @BillId = ?,
            @SuggestedSubCostCodeId = 1,
            @SuggestionSource = 'vendor_history'
        """,
        bill_id,
    )
    cur.execute(
        """
        UPDATE dbo.CodingSuggestion
        SET ConfirmedProjectId = 99,
            ConfirmedSubCostCodeId = 88,
            WasOverridden = 1
        WHERE BillId = ?
        """,
        bill_id,
    )
    cur.execute(
        """
        EXEC dbo.UpsertCodingSuggestionForBill
            @BillId = ?,
            @SuggestedSubCostCodeId = 2,
            @SuggestionSource = 'vendor_project_history'
        """,
        bill_id,
    )
    cur.execute("SELECT COUNT(*) FROM dbo.CodingSuggestion WHERE BillId = ?", bill_id)
    assert int(cur.fetchone()[0]) == 1
    cur.execute(
        """
        SELECT SuggestedSubCostCodeId, ConfirmedProjectId, ConfirmedSubCostCodeId, WasOverridden
        FROM dbo.CodingSuggestion WHERE BillId = ?
        """,
        bill_id,
    )
    row = cur.fetchone()
    assert int(row.SuggestedSubCostCodeId) == 2
    assert int(row.ConfirmedProjectId) == 99
    assert int(row.ConfirmedSubCostCodeId) == 88
    assert bool(row.WasOverridden) is True


def test_upsert_replace_clears_nulls_preserve_when_not_replace(conn):
    cur = conn.cursor()
    bill_id = _insert_bill(cur, 700, "2026-01-01T10:00:00")
    cur.execute(
        """
        EXEC dbo.UpsertCodingSuggestionForBill
            @BillId = ?,
            @SuggestedSubCostCodeId = 1,
            @SuggestionSource = 'vendor_history',
            @SuggestionReason = 'prior',
            @SuggestionConfidence = 0.75
        """,
        bill_id,
    )
    cur.execute(
        """
        EXEC dbo.UpsertCodingSuggestionForBill
            @BillId = ?,
            @ReplaceSuggestion = 1,
            @SuggestedSubCostCodeId = NULL,
            @SuggestionSource = NULL,
            @SuggestionReason = NULL,
            @SuggestionConfidence = NULL,
            @PrefillState = NULL
        """,
        bill_id,
    )
    cur.execute(
        """
        SELECT SuggestedSubCostCodeId, SuggestionSource, SuggestionReason,
               SuggestionConfidence, PrefillState
        FROM dbo.CodingSuggestion WHERE BillId = ?
        """,
        bill_id,
    )
    row = cur.fetchone()
    assert row.SuggestedSubCostCodeId is None
    assert row.SuggestionSource is None
    assert row.SuggestionReason is None
    assert row.SuggestionConfidence is None
    assert row.PrefillState is None

    cur.execute(
        """
        EXEC dbo.UpsertCodingSuggestionForBill
            @BillId = ?,
            @SuggestedSubCostCodeId = 2,
            @SuggestionSource = 'memo',
            @SuggestionReason = 'hint',
            @SuggestionConfidence = 0.5,
            @PrefillState = 'prefilled'
        """,
        bill_id,
    )
    cur.execute(
        """
        EXEC dbo.UpsertCodingSuggestionForBill
            @BillId = ?,
            @SuggestedSubCostCodeId = NULL,
            @SuggestionSource = NULL,
            @SuggestionReason = NULL,
            @SuggestionConfidence = NULL,
            @PrefillState = NULL
        """,
        bill_id,
    )
    cur.execute(
        """
        SELECT SuggestedSubCostCodeId, SuggestionSource, SuggestionReason,
               SuggestionConfidence, PrefillState
        FROM dbo.CodingSuggestion WHERE BillId = ?
        """,
        bill_id,
    )
    row2 = cur.fetchone()
    assert int(row2.SuggestedSubCostCodeId) == 2
    assert row2.SuggestionSource == "memo"
    assert row2.SuggestionReason == "hint"
    assert float(row2.SuggestionConfidence) == pytest.approx(0.5)
    assert row2.PrefillState == "prefilled"


def test_upsert_and_read_round_trip_features_json_and_model_version(conn):
    cur = conn.cursor()
    bill_id = _insert_bill(cur, 600, "2026-01-01T10:00:00")
    features = '{"vendor_id":600,"grain":"vendor"}'
    model_version = "hist-v1"
    cur.execute(
        """
        EXEC dbo.UpsertCodingSuggestionForBill
            @BillId = ?,
            @FeaturesJson = ?,
            @ModelVersion = ?,
            @SuggestionSource = 'vendor_history'
        """,
        bill_id,
        features,
        model_version,
    )
    row = cur.fetchone()
    assert row is not None
    assert row.FeaturesJson == features
    assert row.ModelVersion == model_version

    cur.execute("EXEC dbo.ReadCodingSuggestionByBillId @BillId = ?", bill_id)
    row2 = cur.fetchone()
    assert row2 is not None
    assert row2.FeaturesJson == features
    assert row2.ModelVersion == model_version


def test_history_procs_match_frozen_equivalence_golden(conn):
    assert GOLDEN_PATH.is_file(), "run capture_equivalence_golden() before refactoring SQL"
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    cur = conn.cursor()
    _drop_all(cur)
    _install_minimal_schema(cur)
    meta = _install_equivalence_fixture(cur)
    assert set(meta["bill_ids"].keys()) == {int(k) for k in golden["meta"]["bill_ids"]}
    for key, case in _equivalence_param_grid(meta):
        expected = golden["cases"][key]
        actual = _run_equivalence_case(cur, case)
        if case["proc"] == "scc":
            public_id = actual.get("SubCostCodePublicId") if actual else None
            actual_cmp = (
                {k: v for k, v in actual.items() if k != "SubCostCodePublicId"}
                if actual
                else None
            )
            assert actual_cmp == expected, f"mismatch for {key}"
            if expected is not None:
                top_id = int(expected["TopSubCostCodeId"])
                want = golden["meta"]["scc_public"][str(top_id)]
                assert public_id is not None and str(public_id).lower() == want.lower()
        else:
            assert actual == expected, f"mismatch for {key}"


if __name__ == "__main__":
    n = capture_equivalence_golden()
    print(f"Wrote {n} cases to {GOLDEN_PATH}")
