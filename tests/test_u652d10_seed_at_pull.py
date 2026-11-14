"""U-652d10: the QBO purchase pull creates the CodingSuggestion row for every purchase with an uncoded line
and none yet; the expense-coding queue read no longer writes.

Covers the seed helper (per-candidate upsert, cap, failure isolation), the pull wiring (empty tick, normal
tick, after the delete reconcile, a seed error never fails the pull), the repo mapping, and the proc's filter
evaluated in SQLite (lifted verbatim from the SQL file). Every test names the mutant it kills."""
import logging
import re
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from entities.coding_suggestion.business.service import CodingSuggestionService
from integrations.intuit.qbo.purchase.business.service import QboPurchaseService

SQL_FILE = Path(__file__).resolve().parents[1] / "integrations/intuit/qbo/purchase/sql/qbo.expense_coding_queue.sql"
_LINE_REPO = "integrations.intuit.qbo.purchase.persistence.repo.QboPurchaseLineRepository"


def _candidates(*ids):
    return [
        {"qbo_purchase_id": i, "qbo_purchase_qbo_id": f"q{i}", "realm_id": "R1", "vendor_qbo_id": f"v{i}"}
        for i in ids
    ]


def _service():
    svc = CodingSuggestionService.__new__(CodingSuggestionService)
    svc.upsert_from_queue = MagicMock()
    return svc


# --------------------------------------------------------------------------- seed helper


def test_seed_upserts_each_candidate_with_its_identity():
    # kills: a dropped / swapped upsert kwarg (wrong MERGE key or vendor), or a skipped candidate
    svc = _service()
    with patch(_LINE_REPO) as repo_cls:
        repo_cls.return_value.read_unseeded_coding_purchases.return_value = _candidates(1, 2)
        result = svc.seed_unseeded_purchases(realm_id="R1")
    assert result == {"candidates": 2, "seeded": 2, "failed": 0}
    assert [c.kwargs for c in svc.upsert_from_queue.call_args_list] == [
        dict(qbo_purchase_id=1, qbo_purchase_qbo_id="q1", realm_id="R1", vendor_qbo_id="v1"),
        dict(qbo_purchase_id=2, qbo_purchase_qbo_id="q2", realm_id="R1", vendor_qbo_id="v2"),
    ]


def test_seed_passes_realm_and_default_cap():
    # kills: an unscoped realm, or a missing / changed per-tick cap
    svc = _service()
    with patch(_LINE_REPO) as repo_cls:
        repo_cls.return_value.read_unseeded_coding_purchases.return_value = []
        svc.seed_unseeded_purchases(realm_id="R9")
        repo_cls.return_value.read_unseeded_coding_purchases.assert_called_once_with(realm_id="R9", limit=200)


def test_a_failed_read_returns_zero_and_never_raises(caplog):
    # kills: letting the candidate read raise (it would fail the pull: the wrapper no longer catches), or
    # logging the error TEXT instead of its class name
    svc = _service()
    with patch(_LINE_REPO) as repo_cls, caplog.at_level(logging.WARNING):
        repo_cls.return_value.read_unseeded_coding_purchases.side_effect = RuntimeError("driver text here")
        result = svc.seed_unseeded_purchases(realm_id="R1")
    assert result == {"candidates": 0, "seeded": 0, "failed": 0, "read_failed": True}
    svc.upsert_from_queue.assert_not_called()
    assert "RuntimeError" in caplog.text and "driver text here" not in caplog.text


def test_one_failed_upsert_is_counted_and_the_rest_still_seed(caplog):
    # kills: a raise out of the loop (the pull would fail), a stop-on-first-error, or a wrong failure count;
    # and logging the error TEXT (driver text can carry values) instead of its class name
    svc = _service()
    svc.upsert_from_queue.side_effect = [RuntimeError("secret-ish driver text"), None, None]
    with patch(_LINE_REPO) as repo_cls, caplog.at_level(logging.WARNING):
        repo_cls.return_value.read_unseeded_coding_purchases.return_value = _candidates(1, 2, 3)
        result = svc.seed_unseeded_purchases(realm_id="R1")
    assert result == {"candidates": 3, "seeded": 2, "failed": 1}
    assert svc.upsert_from_queue.call_count == 3
    assert "RuntimeError" in caplog.text and "secret-ish" not in caplog.text


# --------------------------------------------------------------------------- pull wiring


def _pull_service(fetched):
    svc = QboPurchaseService.__new__(QboPurchaseService)
    svc._upsert_purchase = MagicMock(side_effect=lambda p, realm: SimpleNamespace(id=1, qbo_id=p.id))
    svc._sync_to_expenses = MagicMock()
    svc._reconcile_deleted_purchases = MagicMock()
    svc._seed_coding_suggestions = MagicMock()
    client = MagicMock()
    client.__enter__.return_value.query_all_purchases.return_value = fetched
    return svc, client


@pytest.mark.parametrize("fetched", [[], [SimpleNamespace(id="q1")]], ids=["empty_tick", "normal_tick"])
def test_every_pull_seeds_its_realm(fetched):
    # kills: seeding only on a normal tick (a failed seed would wait for the next QBO change) or only on an
    # empty one; seeding the wrong realm
    svc, client = _pull_service(fetched)
    with patch("integrations.intuit.qbo.purchase.business.service.QboPurchaseClient", return_value=client), \
         patch("integrations.intuit.qbo.purchase.business.service.pace_batch"):
        svc.sync_from_qbo(realm_id="R7")
    svc._seed_coding_suggestions.assert_called_once_with("R7")


def test_seed_runs_after_the_projection_and_the_delete_reconcile():
    # kills: seeding before the full-sync reconcile (a purchase QBO deleted gets a fresh row), or, on the
    # sync_to_modules=True path, before the projection (the row would wait for the stamp instead of resolving
    # its ExpenseId at write time). The scheduled path projects after sync_from_qbo returns and stamps then.
    svc, client = _pull_service([SimpleNamespace(id="q1")])
    order = []
    svc._sync_to_expenses.side_effect = lambda purchases, outcome: order.append("project")
    svc._reconcile_deleted_purchases.side_effect = lambda realm: order.append("reconcile")
    svc._seed_coding_suggestions.side_effect = lambda realm: order.append("seed")
    with patch("integrations.intuit.qbo.purchase.business.service.QboPurchaseClient", return_value=client), \
         patch("integrations.intuit.qbo.purchase.business.service.pace_batch"):
        svc.sync_from_qbo(realm_id="R1", sync_to_modules=True, reconcile_deletes=True)
    assert order == ["project", "reconcile", "seed"]


def test_the_pull_wrapper_seeds_its_realm():
    # kills: the wrapper calling the seed for another realm, or not at all
    with patch("entities.coding_suggestion.business.service.CodingSuggestionService") as svc_cls:
        QboPurchaseService._seed_coding_suggestions("R1")
        svc_cls.return_value.seed_unseeded_purchases.assert_called_once_with(realm_id="R1")


# --------------------------------------------------------------------------- repo mapping


def test_repo_maps_the_proc_columns():
    # kills: a wrong proc name, dropped params, or a column mapped to the wrong key
    from integrations.intuit.qbo.purchase.persistence.repo import QboPurchaseLineRepository

    row = SimpleNamespace(QboPurchaseId=7, QboPurchaseQboId="q7", RealmId="R1", VendorQboId="v7")
    cursor = MagicMock()
    cursor.fetchall.return_value = [row]
    conn = MagicMock()
    conn.__enter__.return_value.cursor.return_value = cursor
    with patch("integrations.intuit.qbo.purchase.persistence.repo.get_connection", return_value=conn), \
         patch("integrations.intuit.qbo.purchase.persistence.repo.call_procedure") as call:
        out = QboPurchaseLineRepository.__new__(QboPurchaseLineRepository).read_unseeded_coding_purchases(
            realm_id="R1", limit=9
        )
    assert call.call_args.kwargs["name"] == "ReadUnseededCodingPurchases"
    assert call.call_args.kwargs["params"] == {"RealmId": "R1", "Limit": 9}
    assert out == [{"qbo_purchase_id": 7, "qbo_purchase_qbo_id": "q7", "realm_id": "R1", "vendor_qbo_id": "v7"}]


# --------------------------------------------------------------------------- the proc


def _proc_body() -> str:
    text = SQL_FILE.read_text()
    m = re.search(r"CREATE OR ALTER PROCEDURE ReadUnseededCodingPurchases\b(.*?)\nEND;\nGO\n", text, re.S)
    assert m, "ReadUnseededCodingPurchases missing or not GO-terminated"
    return m.group(1)


def test_proc_caps_and_orders():
    # kills: an uncapped scan, a NULL / non-positive limit reaching TOP, or a non-deterministic order
    body = _proc_body()
    assert "IF @Limit IS NULL OR @Limit < 1 SET @Limit = 200;" in body
    assert "SELECT TOP (@Limit)" in body
    assert body.rstrip().endswith("ORDER BY p.[Id];")


def _sqlite_filter() -> str:
    body = _proc_body()
    where = body[body.index("FROM [qbo].[Purchase] p"):body.index("ORDER BY")]
    where = where.replace("[qbo].", "").replace("[dbo].", "").replace("[", "").replace("]", "")
    return where.replace("@RealmId", ":realm")


def _seedable(*, realm=None, lines=(1,), suggestion=None, purchase_realm="R1", purchase_qbo="q1"):
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE Purchase (Id INT, QboId TEXT, RealmId TEXT, EntityRefValue TEXT)")
    con.execute("CREATE TABLE PurchaseLine (QboPurchaseId INT, IsUncodedPlaceholder INT)")
    con.execute("CREATE TABLE CodingSuggestion (RealmId TEXT, QboPurchaseQboId TEXT)")
    con.execute("INSERT INTO Purchase VALUES (1, ?, ?, 'v1')", (purchase_qbo, purchase_realm))
    for flag in lines:
        con.execute("INSERT INTO PurchaseLine VALUES (1, ?)", (flag,))
    if suggestion is not None:
        con.execute("INSERT INTO CodingSuggestion VALUES (?, ?)", suggestion)
    return con.execute(f"SELECT p.Id {_sqlite_filter()}", {"realm": realm}).fetchone() is not None


@pytest.mark.parametrize(
    "case_id,kwargs,expected,kills",
    [
        ("uncoded line, no suggestion -> seed", dict(), True, "an always-false filter"),
        ("only coded lines -> skip", dict(lines=(0,)), False, "dropping the IsUncodedPlaceholder = 1 test"),
        ("no lines -> skip", dict(lines=()), False, "replacing EXISTS with NOT EXISTS"),
        ("one coded + one uncoded -> seed", dict(lines=(0, 1)), True, "requiring every line uncoded"),
        ("already has a suggestion -> skip", dict(suggestion=("R1", "q1")), False, "dropping the NOT EXISTS"),
        ("suggestion for another realm -> seed", dict(suggestion=("R2", "q1")), True,
         "a NOT EXISTS that ignores RealmId"),
        ("suggestion for another purchase -> seed", dict(suggestion=("R1", "q2")), True,
         "a NOT EXISTS that ignores QboPurchaseQboId"),
        ("realm filter matches -> seed", dict(realm="R1"), True, "an inverted realm filter"),
        ("realm filter excludes -> skip", dict(realm="R2"), False, "dropping the realm filter"),
        ("no QBO identity -> skip", dict(purchase_qbo=None), False,
         "dropping the QboId IS NOT NULL guard (a permanent failure at the head of the scan)"),
    ],
    ids=lambda v: v if isinstance(v, str) else None,
)
def test_proc_filter(case_id, kwargs, expected, kills):
    assert _seedable(**kwargs) is expected, f"{case_id} (kills: {kills})"
