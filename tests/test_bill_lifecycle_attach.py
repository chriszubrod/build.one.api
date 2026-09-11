"""U-443 (U-357 Phase 1) — Bill reads carry a derived `status` + `review_status_kind`.

Bill has no `Status` column. Today the list stitches the ReviewStatus **Name**
onto each row and the single GETs carry nothing at all, so every client has to
re-derive "is this thing in review, approved, or done?" from `is_draft` plus a
free-text admin-editable name. Three clients did it three different ways.

This unit moves that derivation server-side, unchanged in meaning:

  * `review_status_kind` — one of `none|submitted|in_review|approved|declined`,
    keyed ONLY on ReviewStatus FLAGS + position (IsDeclined / IsFinal /
    SortOrder vs the MIN active SortOrder). Never on the Name, which an admin
    can rename at will.
  * `status` — the lifecycle position: `completed` iff `is_draft is False`,
    else the review kind, else `draft`.

`review_status` keeps its existing meaning (the Name, nullable) so no client
breaks. Both new fields are ADDITIVE and DERIVED per request — there is still
no column behind them. That is also why this phase adds no `?status=` filter:
post-filtering a paginated page would make `count` lie. The column and the
filter are Phase 3.

Coverage note: cases 1-3 came over from the Sep-8 prototype and cover
`/get/bill/id/{id}` only. Everything below `--- the list ---` was added when a
mutation check showed the list wiring was unprotected: binding every row to
`review=None` (so every bill reads `draft`) and re-resolving the page's
first-sort-order once PER ROW both left the suite GREEN.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import asyncio

import pytest

from entities.bill.api.router import (
    get_bill_by_bill_number_and_vendor_router,
    get_bill_by_id_router,
    get_bill_by_public_id_router,
    get_bills_router,
)
from entities.bill.business.model import Bill
from entities.review.business.model import Review

USER = {"id": 1, "username": "tester"}


def _bill(*, id=1, is_draft=True, vendor_id=10):
    return Bill(
        id=id,
        public_id=f"bill-{id}",
        row_version="AAAA",
        created_datetime=None,
        modified_datetime=None,
        vendor_id=vendor_id,
        payment_term_id=None,
        bill_date="2026-09-01",
        due_date="2026-09-30",
        bill_number=f"B-{id}",
        total_amount=None,
        memo=None,
        is_draft=is_draft,
    )


def _review(*, bill_id, name="Submitted", sort_order=10, is_final=False,
            is_declined=False, is_initial=None):
    """`is_initial` defaults to "whatever the name implies" so the many existing
    cases below read naturally; pass it explicitly to test the flag itself."""
    if is_initial is None:
        is_initial = not is_final and not is_declined and sort_order == 10
    return Review(
        id=91,
        public_id="rev-1",
        row_version=None,
        created_datetime=None,
        modified_datetime=None,
        review_status_id=1,
        user_id=1,
        comments=None,
        bill_id=bill_id,
        expense_id=None,
        bill_credit_id=None,
        invoice_id=None,
        status_name=name,
        status_sort_order=sort_order,
        status_is_final=is_final,
        status_is_declined=is_declined,
        status_is_initial=is_initial,
        status_color=None,
        user_firstname=None,
        user_lastname=None,
    )


def test_get_by_id_draft_without_review_is_draft_kind_none():
    bill = _bill(id=7, is_draft=True)
    service = MagicMock()
    service.read_by_id.return_value = bill
    review_repo = MagicMock()
    review_repo.read_current_by_bill_id.return_value = None
    with patch("entities.bill.api.router.BillService", return_value=service), \
         patch("entities.review.persistence.repo.ReviewRepository", return_value=review_repo), \
         patch("entities.bill.api.router._resolve_vendor_public_id", return_value=None):
        payload = get_bill_by_id_router(id=7, current_user=USER)["data"]
    assert payload["status"] == "draft"
    assert payload["review_status"] is None
    assert payload["review_status_kind"] == "none"


def test_get_by_id_submitted_review_on_draft():
    bill = _bill(id=7, is_draft=True)
    service = MagicMock()
    service.read_by_id.return_value = bill
    review_repo = MagicMock()
    review_repo.read_current_by_bill_id.return_value = _review(bill_id=7)
    with patch("entities.bill.api.router.BillService", return_value=service), \
         patch("entities.review.persistence.repo.ReviewRepository", return_value=review_repo), \
         patch("entities.bill.api.router._resolve_vendor_public_id", return_value=None):
        payload = get_bill_by_id_router(id=7, current_user=USER)["data"]
    assert payload["status"] == "submitted"
    assert payload["review_status"] == "Submitted"
    assert payload["review_status_kind"] == "submitted"


def test_get_by_id_finalized_is_completed():
    bill = _bill(id=7, is_draft=False)
    service = MagicMock()
    service.read_by_id.return_value = bill
    review_repo = MagicMock()
    review_repo.read_current_by_bill_id.return_value = None
    with patch("entities.bill.api.router.BillService", return_value=service), \
         patch("entities.review.persistence.repo.ReviewRepository", return_value=review_repo), \
         patch("entities.bill.api.router._resolve_vendor_public_id", return_value=None):
        payload = get_bill_by_id_router(id=7, current_user=USER)["data"]
    assert payload["status"] == "completed"
    assert payload["is_draft"] is False
    assert payload["review_status_kind"] == "none"


# ---------------------------------------------------------------------------
# --- the list ---
# ---------------------------------------------------------------------------


def _call_get_bills(bills, review_map):
    """Drive GET /get/bills with the lifecycle collaborators mocked."""
    service = MagicMock()
    # U-447: one call returns (page, total) — the total no longer comes from a
    # second sproc, which is what let it describe a different snapshot.
    service.read_paginated.return_value = (bills, len(bills))
    repo = MagicMock()
    repo.read_first_line_item_projects.return_value = {}
    review_repo = MagicMock()
    review_repo.read_current_by_bill_ids.return_value = review_map
    vendor_repo = MagicMock()
    vendor_repo.read_public_ids_by_ids.return_value = {}
    with patch("entities.bill.api.router.BillService", return_value=service), \
         patch("entities.bill.api.router.BillRepository", return_value=repo), \
         patch("entities.bill.api.router.VendorRepository", return_value=vendor_repo), \
         patch("entities.bill.api.router.get_connection", return_value=MagicMock()), \
         patch("entities.review.persistence.repo.ReviewRepository", return_value=review_repo):
        response = asyncio.run(get_bills_router(
            page=1, page_size=50, search=None, vendor_id=None,
            is_draft=None, current_user=USER,
        ))
    return response


def test_list_binds_each_bill_to_its_OWN_review():
    """THE anti-mutation assertion for the list.

    Three drafts, three different review states. Binding every row to the same
    review — or to none — is the failure mode that a single-bill test cannot
    see, and it is exactly what the batch `review_map.get(bill.id)` lookup
    exists to prevent. A row that silently reads `draft` here is a bill that
    disappears from a reviewer's queue.
    """
    bills = [_bill(id=1), _bill(id=2), _bill(id=3)]
    review_map = {
        1: _review(bill_id=1, name="Submitted", sort_order=10),
        2: _review(bill_id=2, name="Owner Review", sort_order=20),
        3: _review(bill_id=3, name="Rejected", sort_order=30, is_declined=True),
    }
    response = _call_get_bills(bills, review_map)
    by_id = {bd["id"]: bd for bd in response["data"]}

    assert by_id[1]["status"] == "submitted"
    assert by_id[2]["status"] == "in_review"
    assert by_id[3]["status"] == "declined"
    assert by_id[1]["review_status_kind"] == "submitted"
    assert by_id[2]["review_status_kind"] == "in_review"
    assert by_id[3]["review_status_kind"] == "declined"
    # The Name is still the Name — unchanged contract for today's clients.
    assert [by_id[i]["review_status"] for i in (1, 2, 3)] == [
        "Submitted", "Owner Review", "Rejected",
    ]


def test_list_row_without_a_review_is_draft():
    """Bills with no Review row are ABSENT from the batch map, not None-valued."""
    bills = [_bill(id=1), _bill(id=2)]
    response = _call_get_bills(bills, {1: _review(bill_id=1, sort_order=10)})
    by_id = {bd["id"]: bd for bd in response["data"]}
    assert by_id[2]["status"] == "draft"
    assert by_id[2]["review_status_kind"] == "none"
    assert by_id[2]["review_status"] is None


def test_list_finalized_bill_is_completed_regardless_of_its_review():
    """`completed` is keyed on IsDraft alone. A finalized bill whose review was
    never advanced past Submitted is still `completed` — we do not fabricate an
    Approved row to make the pair look tidy."""
    response = _call_get_bills(
        [_bill(id=1, is_draft=False)],
        {1: _review(bill_id=1, name="Submitted", sort_order=10)},
    )
    bd = response["data"][0]
    assert bd["status"] == "completed"
    assert bd["review_status_kind"] == "submitted"


def test_the_list_needs_NO_boundary_lookup_at_all():
    """U-444. These three tests used to pin a per-page `ReadFirstReviewStatus`
    round-trip: fetched once per page, skipped when no row had a review, and
    NOT swallowed on failure. All of it is gone — `submitted` is now a flag on
    the Review row itself, so the page needs nothing beyond the rows it already
    fetched. The router no longer imports ReviewStatusService.
    """
    import entities.bill.api.router as bill_router

    assert not hasattr(bill_router, "ReviewStatusService"), (
        "the bill router re-acquired ReviewStatusService — the per-request "
        "boundary lookup U-444 deleted is likely back"
    )
    assert not hasattr(bill_router, "_first_review_sort_order")


def test_kind_follows_the_flag_not_the_sort_order():
    """THE U-444 regression test, at the router.

    Two active statuses share SortOrder 10; only one is the initial one. Under
    the old position rule BOTH derived `submitted`, because the rule was
    `sort_order == first_sort_order` and nothing forbade duplicate sort orders.
    """
    bills = [_bill(id=1), _bill(id=2)]
    review_map = {
        1: _review(bill_id=1, name="Submitted", sort_order=10, is_initial=True),
        2: _review(bill_id=2, name="Owner Review", sort_order=10, is_initial=False),
    }
    response = _call_get_bills(bills, review_map)
    by_id = {bd["id"]: bd for bd in response["data"]}
    assert by_id[1]["review_status_kind"] == "submitted"
    assert by_id[2]["review_status_kind"] == "in_review"


def test_reordering_does_not_relabel_a_stored_review():
    """The retroactive-relabel failure, pinned. A status sitting at SortOrder
    999 is still `submitted` if it carries the flag — position is irrelevant, so
    inserting or moving rows cannot rewrite history."""
    response = _call_get_bills(
        [_bill(id=1)],
        {1: _review(bill_id=1, name="Submitted", sort_order=999, is_initial=True)},
    )
    assert response["data"][0]["review_status_kind"] == "submitted"
    assert response["data"][0]["status"] == "submitted"


# ---------------------------------------------------------------------------
# --- the single GETs ---
# ---------------------------------------------------------------------------


def _patch_single(review=None, *, review_repo=None):
    if review_repo is None:
        review_repo = MagicMock()
        review_repo.read_current_by_bill_id.return_value = review
    return (
        patch("entities.review.persistence.repo.ReviewRepository", return_value=review_repo),
        patch("entities.bill.api.router._resolve_vendor_public_id", return_value=None),
    ), review_repo


@pytest.mark.parametrize(
    "review_kwargs,expected",
    [
        (dict(name="Submitted", sort_order=10), "submitted"),
        (dict(name="Owner Review", sort_order=20), "in_review"),
        (dict(name="Approved", sort_order=40, is_final=True), "approved"),
        (dict(name="Rejected", sort_order=99, is_declined=True), "declined"),
    ],
)
def test_get_by_id_maps_flags_to_kind_not_the_name(review_kwargs, expected):
    """The whole point of keying on flags: an admin renaming "Approved" to
    "Signed Off" must not change any client's branch. Note the declined row
    carries the HIGHEST sort order and still wins — IsDeclined is checked
    first."""
    bill = _bill(id=7, is_draft=True)
    service = MagicMock()
    service.read_by_id.return_value = bill
    patches, _ = _patch_single(_review(bill_id=7, **review_kwargs))
    with patches[0], patches[1], \
         patch("entities.bill.api.router.BillService", return_value=service):
        payload = get_bill_by_id_router(id=7, current_user=USER)["data"]
    assert payload["review_status_kind"] == expected
    assert payload["status"] == expected
    assert payload["review_status"] == review_kwargs["name"]


def test_a_failed_review_lookup_is_NOT_swallowed():
    """Codex P1, 2026-09-11 — this test used to assert the opposite.

    Swallowing returns None, which is INDISTINGUISHABLE from "this bill was
    never submitted". A bill sitting in someone's review queue would render as
    `draft` with `review_status: null` for the duration of a blip, and nobody
    would know to look. The bill row itself was read from this same database
    microseconds earlier, so the availability that bought was near zero.
    """
    bill = _bill(id=7)
    service = MagicMock()
    service.read_by_id.return_value = bill
    review_repo = MagicMock()
    review_repo.read_current_by_bill_id.side_effect = RuntimeError("db down")
    patches, _ = _patch_single(review_repo=review_repo)
    with patches[0], patches[1], \
         patch("entities.bill.api.router.BillService", return_value=service):
        with pytest.raises(RuntimeError, match="db down"):
            get_bill_by_id_router(id=7, current_user=USER)


def test_a_bill_with_no_id_never_reaches_the_review_sproc():
    """Guard against a pointless round-trip (and a NULL @BillId) on an
    unsaved/degenerate row."""
    bill = _bill(id=7)
    bill.id = None
    service = MagicMock()
    service.read_by_id.return_value = bill
    patches, review_repo = _patch_single(None)
    with patches[0], patches[1], \
         patch("entities.bill.api.router.BillService", return_value=service):
        payload = get_bill_by_id_router(id=7, current_user=USER)["data"]
    review_repo.read_current_by_bill_id.assert_not_called()
    assert payload["status"] == "draft"


def test_by_bill_number_and_vendor_carries_the_lifecycle_block():
    """The third read path — used by the QBO pull's dedupe lookup and by the
    email review-response flow. It returned a bare to_dict() before U-443."""
    bill = _bill(id=7)
    service = MagicMock()
    service.read_by_bill_number_and_vendor_public_id.return_value = bill
    patches, _ = _patch_single(_review(bill_id=7, name="Approved", sort_order=40, is_final=True))
    with patches[0], patches[1], \
         patch("entities.bill.api.router.BillService", return_value=service):
        payload = asyncio.run(get_bill_by_bill_number_and_vendor_router(
            bill_number="B-7", vendor_public_id="v-1", current_user=USER,
        ))["data"]
    assert payload["status"] == "approved"
    assert payload["review_status_kind"] == "approved"
    assert payload["review_status"] == "Approved"


def test_by_public_id_carries_the_lifecycle_block_alongside_qbo_bill_url():
    """The read BillEdit/BillView open. Its payload is assembled field-by-field
    (qbo_bill_url, vendor_public_id), so it is the easiest of the three to
    quietly rebuild from a bare to_dict() — a mutation check proved nothing
    caught that."""
    bill = _bill(id=7)
    service = MagicMock()
    service.read_by_public_id.return_value = bill
    service.get_qbo_bill_url.return_value = "https://qbo.example/bill/1"
    patches, _ = _patch_single(_review(bill_id=7, name="Owner Review", sort_order=20))
    with patches[0], patches[1], \
         patch("entities.bill.api.router.BillService", return_value=service):
        payload = asyncio.run(get_bill_by_public_id_router(
            public_id="bill-7", current_user=USER,
        ))["data"]
    assert payload["status"] == "in_review"
    assert payload["review_status_kind"] == "in_review"
    assert payload["review_status"] == "Owner Review"
    # the pre-existing enrichments are still there
    assert payload["qbo_bill_url"] == "https://qbo.example/bill/1"


def test_every_emitted_status_is_in_the_canonical_vocabulary():
    """Pins the two derived fields to the agreed six-word / five-word
    vocabularies so a future hand-edit can't invent a seventh state that
    clients have no branch for."""
    from shared.lifecycle.resolver import LIFECYCLE_STATUSES, REVIEW_STATUS_KINDS

    bills = [_bill(id=i) for i in range(1, 5)] + [_bill(id=5, is_draft=False)]
    review_map = {
        1: _review(bill_id=1, sort_order=10),
        2: _review(bill_id=2, sort_order=20),
        3: _review(bill_id=3, sort_order=40, is_final=True),
        4: _review(bill_id=4, sort_order=99, is_declined=True),
    }
    response = _call_get_bills(bills, review_map)
    emitted_status = {bd["status"] for bd in response["data"]}
    emitted_kind = {bd["review_status_kind"] for bd in response["data"]}

    assert emitted_status <= set(LIFECYCLE_STATUSES)
    assert emitted_kind <= set(REVIEW_STATUS_KINDS)
    # Not just a subset — the fixture actually reaches five of the six states,
    # so a wiring that collapsed everything onto one value would still fail.
    assert emitted_status == {
        "submitted", "in_review", "approved", "declined", "completed",
    }
