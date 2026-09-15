"""U-457 — Expense, BillCredit and Invoice get the lifecycle block Bill has had.

Bill has emitted `status` + `review_status_kind` on every read since U-443. The
other three Review-parented documents emitted none of it and fetched no review
at all, so a client could not tell a draft expense from one sitting in someone's
review queue.

What made this cheap now rather than three repeats of Bill's history:
`attach_lifecycle` is entity-agnostic, the single-row readers already existed,
and U-455's frozen `ReviewKind` means these three inherit a kind that survives a
ReviewStatus reconfiguration from day one — Bill had to learn that the hard way.

What was NOT free: a BATCH current-review reader per entity. Only
`ReadCurrentReviewsByBillIds` existed, and without siblings each list resolves a
review per row. That N+1 is the mistake Bill's slice avoided and pinned, and it
is the actual content of this unit.

NOT IN SCOPE: `?status=` filtering. That needs a stored Status column, which is
LS-03b/c/d and not built — and post-filtering a paginated page would make
`count` lie. This is the DERIVED read-model slice only.

Live shape at ship time: Invoice has 49 review rows; Expense and BillCredit have
ZERO. For those two the block resolves to `kind: none` with `status` derived
from IsDraft alone, which is still strictly more than they emitted before.
"""

import inspect
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from shared.lifecycle import LIFECYCLE_STATUSES, REVIEW_STATUS_KINDS

REPO_ROOT = Path(__file__).resolve().parents[1]
REVIEW_SQL = REPO_ROOT / "entities/review/sql/dbo.review.sql"

# (module, helper prefix, id kwarg on Review, sproc entity, repo batch method)
ENTITIES = [
    ("entities.expense.api.router", "expense", "expense_id",
     "Expense", "read_current_by_expense_ids"),
    ("entities.bill_credit.api.router", "bill_credit", "bill_credit_id",
     "BillCredit", "read_current_by_bill_credit_ids"),
    ("entities.invoice.api.router", "invoice", "invoice_id",
     "Invoice", "read_current_by_invoice_ids"),
]


def _executable(path: Path) -> str:
    return "\n".join(l.split("--")[0] for l in path.read_text().splitlines())


def _review(**over):
    base = dict(
        status_name="Submitted", status_sort_order=10,
        status_is_final=False, status_is_declined=False, status_is_initial=True,
        review_kind="submitted",
    )
    base.update(over)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# 1 — the batch reader, which is what this unit is actually for
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module,prefix,id_kw,entity,batch", ENTITIES)
def test_each_entity_has_a_batch_current_review_reader(module, prefix, id_kw, entity, batch):
    from entities.review.persistence.repo import ReviewRepository

    assert hasattr(ReviewRepository, batch), (
        f"{entity} has no batch reader — its list would resolve one review per "
        "row, the N+1 Bill's slice avoided"
    )
    sql = _executable(REVIEW_SQL)
    assert f"CREATE OR ALTER PROCEDURE ReadCurrentReviewsBy{entity}Ids" in sql


@pytest.mark.parametrize("module,prefix,id_kw,entity,batch", ENTITIES)
def test_each_batch_sproc_returns_one_row_per_parent(module, prefix, id_kw, entity, batch):
    """`ROW_NUMBER() ... WHERE rn = 1`, partitioned by the parent — newest
    first, tie-broken on Id. Without the tie-break two rows sharing a timestamp
    make the winner engine-dependent."""
    sql = _executable(REVIEW_SQL)
    body = sql.split(f"ReadCurrentReviewsBy{entity}Ids", 1)[1].split("END;", 1)[0]
    assert f"PARTITION BY r.[{entity}Id]" in body
    assert "ORDER BY r.[CreatedDatetime] DESC, r.[Id] DESC" in body
    assert "WHERE rn = 1" in body
    assert f"WHERE r.[{entity}Id] IS NOT NULL" in body


@pytest.mark.parametrize("module,prefix,id_kw,entity,batch", ENTITIES)
def test_each_batch_sproc_carries_the_frozen_kind(module, prefix, id_kw, entity, batch):
    """These are explicit projections, so they must NAME [ReviewKind] — the
    exact omission that made U-455 inert on the Bill list. The generic scan in
    test_u455 also covers this; asserting it here too keeps the reason local."""
    sql = _executable(REVIEW_SQL)
    body = sql.split(f"ReadCurrentReviewsBy{entity}Ids", 1)[1].split("END;", 1)[0]
    assert "[ReviewKind]" in body


@pytest.mark.parametrize("module,prefix,id_kw,entity,batch", ENTITIES)
def test_an_empty_id_list_never_reaches_the_database(module, prefix, id_kw, entity, batch):
    """An empty page must not build `STRING_SPLIT('')` round trips."""
    from entities.review.persistence.repo import ReviewRepository

    repo = ReviewRepository.__new__(ReviewRepository)
    with patch("entities.review.persistence.repo.get_connection") as conn:
        assert getattr(repo, batch)([]) == {}
        conn.assert_not_called()


# ---------------------------------------------------------------------------
# 2 — the list binds each row to its OWN review, in one lookup
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module,prefix,id_kw,entity,batch", ENTITIES)
def test_the_list_resolves_reviews_ONCE_for_the_whole_page(module, prefix, id_kw, entity, batch):
    """The N+1 guard. The page must call the BATCH reader, never the
    single-row one per row."""
    import importlib

    mod = importlib.import_module(module)
    src = inspect.getsource(mod)
    listing = src[src.index(f"def _{prefix}_dict_with_lifecycle"):]
    assert batch in src, f"{module} must use the batch reader"
    single = f"read_current_by_{prefix}_id("
    # the single-row reader may appear ONLY in the single-GET helper
    helper = src[src.index(f"def _current_review_for_{prefix}"):
                 src.index(f"def _{prefix}_dict_with_lifecycle")]
    assert single in helper
    after_helper = src.replace(helper, "")
    assert single not in after_helper, (
        "the single-row reader is used outside the single-GET helper — that is "
        "the N+1"
    )
    # ...and the same N+1 wearing the HELPER's name. A mutation that swapped
    # `review_map.get(row.id)` for `_current_review_for_X(row.id)` sailed past
    # the assertion above, because the helper WRAPS the repo call: the raw
    # sproc name never appears in the list route. Assert on the list route's
    # own source instead of on "the rest of the module", since the single GETs
    # call that helper legitimately.
    #
    # NB the trailing "(" is load-bearing in `single`: without it,
    # `read_current_by_expense_id` is a substring of `read_current_by_expense_ids`
    # and this whole test passes vacuously.
    list_fn = next(
        f for name, f in vars(mod).items()
        if name.startswith("get_") and name.endswith("s_router") and callable(f)
    )
    list_src = inspect.getsource(list_fn)
    assert f"_current_review_for_{prefix}(" not in list_src, (
        "the list route calls the SINGLE-row helper per row — one round trip "
        "per row is the N+1 this unit exists to avoid"
    )
    assert batch in list_src


# (module, service attr on the router module, list route fn, extra filter kwarg)
LIST_ROUTES = [
    ("entities.expense.api.router", "ExpenseService",
     "get_expenses_router", "vendor_id"),
    ("entities.bill_credit.api.router", "BillCreditService",
     "get_bill_credits_router", "vendor_id"),
    ("entities.invoice.api.router", "InvoiceService",
     "get_invoices_router", "project_id"),
]


def _drive_list(module, service_attr, route_name, filter_kw, batch, review_map):
    """Call the REAL list route with a stubbed service + batch reader.

    Returns the route's `data` list. Everything between the sproc and the
    response is the code under test — no source strings.
    """
    import importlib

    mod = importlib.import_module(module)
    rows = [
        SimpleNamespace(id=101, is_draft=True, to_dict=lambda: {"public_id": "row-101"}),
        SimpleNamespace(id=202, is_draft=True, to_dict=lambda: {"public_id": "row-202"}),
    ]
    svc = MagicMock()
    svc.read_paginated.return_value = rows
    svc.count.return_value = 2
    with patch.object(mod, service_attr, return_value=svc), \
         patch("entities.review.persistence.repo.ReviewRepository") as Repo:
        getattr(Repo.return_value, batch).return_value = review_map
        kwargs = {"page": 1, "page_size": 50, "search": None,
                  filter_kw: None, "is_draft": None, "current_user": {}}
        return getattr(mod, route_name)(**kwargs)["data"]


@pytest.mark.parametrize(
    "module,service_attr,route_name,filter_kw,batch",
    [(m, s, r, f, b) for (m, s, r, f), (_, _p, _i, _e, b)
     in zip(LIST_ROUTES, ENTITIES)],
)
def test_the_list_binds_each_row_to_its_OWN_review(
    module, service_attr, route_name, filter_kw, batch
):
    """A page-wide map keyed by parent id: row 1's review must not leak onto
    row 2. Bill's slice had a test for exactly this because the shape invites
    it.

    This drives the REAL route. The version Codex rejected asserted the string
    `"review_map.get("` appeared in the module source, which stays green if the
    code looks a row up under the WRONG key, or hands every row the same
    review — the two failures it claimed to cover.
    """
    data = _drive_list(module, service_attr, route_name, filter_kw, batch,
                       {101: _review()})

    assert data[0]["public_id"] == "row-101"
    assert data[0]["review_status_kind"] == "submitted", (
        "the row WITH a review lost it — the map is keyed wrong"
    )
    assert data[1]["public_id"] == "row-202"
    assert data[1]["review_status_kind"] == "none", (
        "row 101's review leaked onto row 202"
    )
    assert data[1]["status"] == "draft"


@pytest.mark.parametrize(
    "module,service_attr,route_name,filter_kw,batch",
    [(m, s, r, f, b) for (m, s, r, f), (_, _p, _i, _e, b)
     in zip(LIST_ROUTES, ENTITIES)],
)
def test_the_list_asks_the_batch_reader_for_exactly_the_page_ids(
    module, service_attr, route_name, filter_kw, batch
):
    """One call, carrying both ids — not one call per row, not the whole table."""
    import importlib

    mod = importlib.import_module(module)
    rows = [SimpleNamespace(id=101, is_draft=True, to_dict=lambda: {}),
            SimpleNamespace(id=202, is_draft=True, to_dict=lambda: {})]
    svc = MagicMock()
    svc.read_paginated.return_value = rows
    svc.count.return_value = 2
    with patch.object(mod, service_attr, return_value=svc), \
         patch("entities.review.persistence.repo.ReviewRepository") as Repo:
        reader = getattr(Repo.return_value, batch)
        reader.return_value = {}
        kwargs = {"page": 1, "page_size": 50, "search": None,
                  filter_kw: None, "is_draft": None, "current_user": {}}
        getattr(mod, route_name)(**kwargs)

    reader.assert_called_once()
    assert list(reader.call_args.args[0]) == [101, 202]


# ---------------------------------------------------------------------------
# 3 — the payload
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module,prefix,id_kw,entity,batch", ENTITIES)
def test_a_draft_without_a_review_is_draft_kind_none(module, prefix, id_kw, entity, batch):
    import importlib

    mod = importlib.import_module(module)
    fn = getattr(mod, f"_{prefix}_dict_with_lifecycle")
    doc = SimpleNamespace(is_draft=True, to_dict=lambda: {"public_id": "p"})
    out = fn(doc, review=None)
    assert out["status"] == "draft"
    assert out["review_status_kind"] == "none"
    assert out["review_status"] is None


@pytest.mark.parametrize("module,prefix,id_kw,entity,batch", ENTITIES)
def test_a_finalized_document_is_completed_regardless_of_its_review(
    module, prefix, id_kw, entity, batch
):
    """`completed` outranks any review state. A finished document carrying an
    open review is `completed` with a stale kind — we never fabricate."""
    import importlib

    mod = importlib.import_module(module)
    fn = getattr(mod, f"_{prefix}_dict_with_lifecycle")
    doc = SimpleNamespace(is_draft=False, to_dict=lambda: {"public_id": "p"})
    out = fn(doc, review=_review())
    assert out["status"] == "completed"
    assert out["review_status_kind"] == "submitted"


@pytest.mark.parametrize("module,prefix,id_kw,entity,batch", ENTITIES)
def test_the_kind_comes_from_the_frozen_value_not_the_live_flags(
    module, prefix, id_kw, entity, batch
):
    """These three inherit U-455 from day one: a row stamped `submitted` stays
    `submitted` even after the status it points at loses IsInitial."""
    import importlib

    mod = importlib.import_module(module)
    fn = getattr(mod, f"_{prefix}_dict_with_lifecycle")
    doc = SimpleNamespace(is_draft=True, to_dict=lambda: {"public_id": "p"})
    stale = _review(review_kind="submitted", status_is_initial=False)
    assert fn(doc, review=stale)["review_status_kind"] == "submitted"


@pytest.mark.parametrize("module,prefix,id_kw,entity,batch", ENTITIES)
def test_every_emitted_value_is_in_the_canonical_vocabulary(
    module, prefix, id_kw, entity, batch
):
    import importlib

    mod = importlib.import_module(module)
    fn = getattr(mod, f"_{prefix}_dict_with_lifecycle")
    for is_draft in (True, False):
        for review in (None, _review(), _review(review_kind="approved", status_is_final=True),
                       _review(review_kind="declined", status_is_declined=True)):
            doc = SimpleNamespace(is_draft=is_draft, to_dict=lambda: {})
            out = fn(doc, review=review)
            assert out["status"] in LIFECYCLE_STATUSES
            assert out["review_status_kind"] in REVIEW_STATUS_KINDS


# ---------------------------------------------------------------------------
# 4 — the failure mode Bill's slice got wrong first
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module,prefix,id_kw,entity,batch", ENTITIES)
def test_a_failed_review_lookup_is_NOT_swallowed(module, prefix, id_kw, entity, batch):
    """Returning None on a DB error is indistinguishable from "never
    submitted", so a document in someone's queue renders as `draft` /
    `review_status: null` during a blip — a wrong answer about a money document
    dressed as a right one. Codex made Bill raise; these must too."""
    import importlib

    mod = importlib.import_module(module)
    helper = getattr(mod, f"_current_review_for_{prefix}")
    with patch("entities.review.persistence.repo.ReviewRepository") as Repo:
        Repo.return_value = MagicMock(
            **{f"read_current_by_{prefix}_id.side_effect": RuntimeError("db down")}
        )
        with pytest.raises(RuntimeError):
            helper(123)


@pytest.mark.parametrize("module,prefix,id_kw,entity,batch", ENTITIES)
def test_a_document_with_no_id_never_reaches_the_review_sproc(
    module, prefix, id_kw, entity, batch
):
    import importlib

    mod = importlib.import_module(module)
    helper = getattr(mod, f"_current_review_for_{prefix}")
    with patch("entities.review.persistence.repo.ReviewRepository") as Repo:
        assert helper(None) is None
        Repo.assert_not_called()


# ---------------------------------------------------------------------------
# 5 — scope
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module,prefix,id_kw,entity,batch", ENTITIES)
def test_no_status_filter_is_added_in_this_phase(module, prefix, id_kw, entity, batch):
    """`?status=` needs the stored Status column (LS-03b/c/d, not built), and
    post-filtering a paginated page would make `count` lie. Adding it here
    would be a filter with nothing behind it."""
    import importlib

    mod = importlib.import_module(module)
    src = inspect.getsource(mod)
    assert not re.search(r"^\s+status: Optional\[str\] = Query\(", src, re.M), (
        f"{entity} gained a ?status= filter — that is Phase 3, and there is no "
        "Status column to filter on"
    )


@pytest.mark.parametrize("module,prefix,id_kw,entity,batch", ENTITIES)
def test_the_single_GET_actually_wires_the_block(module, prefix, id_kw, entity, batch):
    """The gap a mutation found: every test above exercised the HELPERS, and
    none asserted the routes call them. All three single GETs could be reverted
    to a bare `to_dict()` — the block silently gone from every detail page —
    and the suite stayed green.

    Asserted on the route function's own source, so it is about wiring rather
    than about the helper being importable.
    """
    import importlib

    mod = importlib.import_module(module)
    fn = next(
        f for name, f in vars(mod).items()
        if name.startswith("get_") and name.endswith("_by_public_id_router")
    )
    src = inspect.getsource(fn)
    executable = "\n".join(l.split("#")[0] for l in src.splitlines())
    assert f"_{prefix}_dict_with_lifecycle(" in executable, (
        f"{entity}'s single GET returns a bare to_dict() — the lifecycle block "
        "never reaches the detail page"
    )
    assert f"_current_review_for_{prefix}(" in executable, (
        "and it must resolve the review, or the block is always kind=none"
    )


@pytest.mark.parametrize("module,prefix,id_kw,entity,batch", ENTITIES)
def test_the_list_route_actually_wires_the_block(module, prefix, id_kw, entity, batch):
    """Same gap, list side."""
    import importlib

    mod = importlib.import_module(module)
    fn = next(
        f for name, f in vars(mod).items()
        if name.startswith("get_") and name.endswith("s_router") and callable(f)
    )
    src = inspect.getsource(fn)
    executable = "\n".join(l.split("#")[0] for l in src.splitlines())
    assert f"_{prefix}_dict_with_lifecycle(" in executable
    assert batch in executable


# ---------------------------------------------------------------------------
# 6 — the alternate lookup routes (Codex P1 x2)
# ---------------------------------------------------------------------------
#
# Both are LIVE agent tools, not dead code:
#   entities/expense/intelligence/tools.py     -> read_expense_by_reference_and_vendor
#   entities/bill_credit/intelligence/tools.py -> read_bill_credit_by_number_and_vendor
#
# U-446d taught the agent fleet the lifecycle vocabulary. Without these, an
# agent that looked a document up by its human-facing number got NO `status`
# while the same document by public_id got one — the exact inconsistency this
# unit exists to remove. Bill's by-bill-number route has carried the block
# since U-443 (test_bill_lifecycle_attach.py); these two were the omission.
#
# Invoice has no by-number route to fix.


def test_expense_by_reference_number_carries_the_lifecycle_block():
    import entities.expense.api.router as mod

    expense = SimpleNamespace(id=77, is_draft=False,
                              to_dict=lambda: {"public_id": "exp-77"})
    svc = MagicMock()
    svc.read_by_reference_number_and_vendor_public_id.return_value = expense
    with patch.object(mod, "ExpenseService", return_value=svc), \
         patch("entities.review.persistence.repo.ReviewRepository") as Repo:
        Repo.return_value.read_current_by_expense_id.return_value = _review()
        out = mod.get_expense_by_reference_number_and_vendor_router(
            reference_number="R-1", vendor_public_id="v-1", current_user={}
        )["data"]

    assert out["public_id"] == "exp-77"
    assert out["status"] == "completed", "IsDraft=0 wins over any review"
    assert out["review_status_kind"] == "submitted"


def test_bill_credit_by_credit_number_carries_the_lifecycle_block():
    import entities.bill_credit.api.router as mod

    bc = SimpleNamespace(id=88, is_draft=True, to_dict=lambda: {"public_id": "bc-88"})
    svc = MagicMock()
    svc.read_by_credit_number_and_vendor_public_id.return_value = bc
    with patch.object(mod, "BillCreditService", return_value=svc), \
         patch("entities.review.persistence.repo.ReviewRepository") as Repo:
        Repo.return_value.read_current_by_bill_credit_id.return_value = _review()
        out = mod.get_bill_credit_by_credit_number_and_vendor_router(
            credit_number="CR-1", vendor_public_id="v-1", current_user={}
        )["data"]

    assert out["public_id"] == "bc-88"
    assert out["status"] == "submitted"
    assert out["review_status_kind"] == "submitted"


def test_bill_credit_by_credit_number_still_returns_null_when_missing():
    """Its not-found contract is `return None`, NOT a 404 like Expense's. The
    lifecycle attach must not quietly convert one into the other — an agent
    tool branches on it."""
    import entities.bill_credit.api.router as mod

    svc = MagicMock()
    svc.read_by_credit_number_and_vendor_public_id.return_value = None
    with patch.object(mod, "BillCreditService", return_value=svc):
        assert mod.get_bill_credit_by_credit_number_and_vendor_router(
            credit_number="nope", vendor_public_id="v-1", current_user={}
        ) is None


@pytest.mark.parametrize(
    "module,route_name,prefix",
    [
        ("entities.expense.api.router",
         "get_expense_by_reference_number_and_vendor_router", "expense"),
        ("entities.bill_credit.api.router",
         "get_bill_credit_by_credit_number_and_vendor_router", "bill_credit"),
    ],
)
def test_the_alternate_lookup_route_actually_wires_the_block(module, route_name, prefix):
    """Wiring, asserted on the route's own source — the same shape that caught
    the single-GET gap. The behavioural tests above would also fail, but this
    one names WHY in the failure."""
    import importlib

    mod = importlib.import_module(module)
    src = inspect.getsource(getattr(mod, route_name))
    executable = "\n".join(l.split("#")[0] for l in src.splitlines())
    assert f"_{prefix}_dict_with_lifecycle(" in executable, (
        f"{route_name} returns a bare to_dict() — agents looking a document up "
        "by its number get no lifecycle while public_id lookups do"
    )
    assert f"_current_review_for_{prefix}(" in executable
