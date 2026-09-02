"""
U-361b — the create-only/no-adopt MONEY DOUBLE-COUNT fix.

The bug (confirmed against the live code before this fix): QBO regenerates a
line's `Line.Id` on certain edits with its content unchanged (the exact case
the pre-U-361 `_match_unmapped_by_fingerprint` was built for). Under U-361's
create-only dbo-only MISS branch, `read_by_qbo_identity(parent, "2")` misses
(nothing carries the NEW id yet), and the MISS branch minted a fresh dbo row —
stranding the OLD row (still stamped `QboId="1"`) forever, invisible to every
future direct lookup by identity. Two dbo.BillCreditLineItem rows then survive
for one QBO line: `complete_bill_credit`'s `read_by_bill_credit_id`
(entities/bill_credit/business/complete_service.py:157) sums EVERY line under
the parent, so the credit's total silently double-counts that line's money.

This test drives the connector against a lightweight STATEFUL fake of
`BillCreditLineItemService` (not a bare Mock) — a real in-memory table, so
"does exactly one row survive across a Line.Id regeneration" is asserted
against genuine created/updated state, not against call-count assertions that
could pass for the wrong reason.
"""
from decimal import Decimal
from types import SimpleNamespace

import pytest

from integrations.intuit.qbo.vendorcredit.connector.bill_credit_line_item.business.service import (
    VendorCreditLineItemConnector,
)


class _FakeBillCreditLineItemService:
    """A real in-memory `dbo.BillCreditLineItem` table, scoped to one BillCredit,
    supporting exactly the operations the connector needs. Not a Mock: this
    test's whole point is to prove genuine row survival, which a call-count
    assertion on a Mock cannot do."""

    def __init__(self):
        self._rows: dict[int, SimpleNamespace] = {}
        self._next_id = 1
        self.repo = SimpleNamespace(set_qbo_identity=self._set_qbo_identity)

    def _set_qbo_identity(self, *, id, qbo_id, realm_id):
        row = self._rows[id]
        self._rows[id] = SimpleNamespace(**{**vars(row), "qbo_id": qbo_id, "realm_id": realm_id})

    def create(self, *, bill_credit_public_id, sub_cost_code_id, project_public_id, description,
               quantity, unit_price, amount, is_billable, is_billed, billable_amount, is_draft):
        row_id = self._next_id
        self._next_id += 1
        row = SimpleNamespace(
            id=row_id, public_id=f"pub-{row_id}", row_version=f"rv-{row_id}-0",
            description=description, quantity=quantity, unit_price=unit_price, amount=amount,
            is_billable=is_billable, is_billed=is_billed, billable_amount=billable_amount,
            is_draft=is_draft, qbo_id=None, realm_id=None,
        )
        self._rows[row_id] = row
        return row

    def read_by_id(self, id):
        return self._rows.get(id)

    def read_by_qbo_identity(self, bill_credit_id, qbo_id):
        for row in self._rows.values():
            if row.qbo_id == qbo_id:
                return row
        return None

    def read_by_bill_credit_id(self, bill_credit_id):
        return list(self._rows.values())

    def update_by_public_id(self, public_id, *, row_version, sub_cost_code_id, project_public_id,
                             description, quantity, unit_price, amount, is_billable, is_billed,
                             billable_amount, is_draft):
        row_id = next(i for i, r in self._rows.items() if r.public_id == public_id)
        existing = self._rows[row_id]
        if existing.row_version != row_version:
            return None
        updated = SimpleNamespace(
            **{**vars(existing), "description": description, "quantity": quantity,
               "unit_price": unit_price, "amount": amount, "is_billable": is_billable,
               "is_billed": is_billed, "billable_amount": billable_amount, "is_draft": is_draft,
               "row_version": f"{existing.row_version}+"},
        )
        self._rows[row_id] = updated
        return updated

    def delete_by_public_id(self, public_id):
        row_id = next((i for i, r in self._rows.items() if r.public_id == public_id), None)
        if row_id is not None:
            del self._rows[row_id]


def _make_qbo_line(**overrides):
    defaults = dict(
        id=42, qbo_vendor_credit_id=4, qbo_line_id="1", description="Materials",
        amount=Decimal("500"), qty=Decimal("1"), unit_price=Decimal("500"),
        billable_status=None, customer_ref_value=None, item_ref_value=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _build_connector():
    connector = VendorCreditLineItemConnector()
    connector.bill_credit_line_item_service = _FakeBillCreditLineItemService()
    connector.reconciliation_repo = None  # not exercised unless a failure path fires
    connector._get_project_public_id = lambda *a, **k: None
    connector._get_sub_cost_code_id = lambda *a, **k: None
    return connector


def test_line_id_regeneration_readopts_in_place_no_duplicate_no_double_count(grant_qbo_app_lock):
    """The exact scenario the bug produced: mint under Line.Id '1', QBO
    regenerates it to '2' with identical content on the next pull. Before this
    fix: TWO dbo rows would survive (a stray, permanently-orphaned duplicate).
    After: exactly ONE row survives, re-stamped '1' -> '2', SAME dbo.Id."""
    connector = _build_connector()

    # Pull 1: QBO line "1" is new -- create.
    line_v1 = _make_qbo_line(qbo_line_id="1")
    created = connector.sync_from_qbo_line(19146, "bc-pub", line_v1, frozenset({"1"}), realm_id="realm-1")
    assert created.qbo_id == "1"
    original_dbo_id = created.id

    # Pull 2: QBO regenerated the line's id to "2" -- SAME content. "1" is no
    # longer in this pull's live set.
    line_v2 = _make_qbo_line(qbo_line_id="2")
    result = connector.sync_from_qbo_line(19146, "bc-pub", line_v2, frozenset({"2"}), realm_id="realm-1")

    all_lines = connector.bill_credit_line_item_service.read_by_bill_credit_id(19146)
    assert len(all_lines) == 1, (
        f"expected exactly ONE dbo.BillCreditLineItem to survive a Line.Id "
        f"regeneration, found {len(all_lines)} - this is the money double-count bug"
    )
    assert result.id == original_dbo_id, "the readopted row must keep its ORIGINAL dbo.Id"
    assert result.qbo_id == "2"
    assert all_lines[0].amount == Decimal("500")  # no duplicated/split money


def test_line_id_regeneration_with_a_genuinely_different_line_present_only_readopts_the_matching_one(grant_qbo_app_lock):
    """Two lines exist; only line "1"'s id regenerates. Line "9" (different
    content, still live) must never be touched or mistaken for the orphan."""
    connector = _build_connector()

    line_a_v1 = _make_qbo_line(qbo_line_id="1", description="Materials", amount=Decimal("500"))
    line_b = _make_qbo_line(qbo_line_id="9", description="Labor", amount=Decimal("250"))
    created_a = connector.sync_from_qbo_line(19146, "bc-pub", line_a_v1, frozenset({"1", "9"}), realm_id="realm-1")
    created_b = connector.sync_from_qbo_line(19146, "bc-pub", line_b, frozenset({"1", "9"}), realm_id="realm-1")

    line_a_v2 = _make_qbo_line(qbo_line_id="2", description="Materials", amount=Decimal("500"))
    connector.sync_from_qbo_line(19146, "bc-pub", line_a_v2, frozenset({"2", "9"}), realm_id="realm-1")
    # Re-sync line B too (a real pull re-syncs every current line every tick).
    connector.sync_from_qbo_line(19146, "bc-pub", line_b, frozenset({"2", "9"}), realm_id="realm-1")

    all_lines = connector.bill_credit_line_item_service.read_by_bill_credit_id(19146)
    assert len(all_lines) == 2
    assert created_a.id in {li.id for li in all_lines}
    assert created_b.id in {li.id for li in all_lines}
    by_id = {li.id: li for li in all_lines}
    assert by_id[created_a.id].qbo_id == "2"  # readopted
    assert by_id[created_b.id].qbo_id == "9"  # untouched


def test_content_change_alongside_id_regeneration_does_not_readopt(grant_qbo_app_lock):
    """A genuinely NEW line (different content) must never be mistaken for a
    readopt target just because an old orphan happens to sit unbound -- it
    should create fresh, not corrupt an unrelated row's data."""
    connector = _build_connector()

    line_v1 = _make_qbo_line(qbo_line_id="1", description="Materials", amount=Decimal("500"))
    created = connector.sync_from_qbo_line(19146, "bc-pub", line_v1, frozenset({"1"}), realm_id="realm-1")

    # "1" goes away, but the NEW line "2" has DIFFERENT content -- not a
    # regeneration of the same line, a genuinely new one.
    different_line = _make_qbo_line(qbo_line_id="2", description="Labor", amount=Decimal("999"))
    connector.sync_from_qbo_line(19146, "bc-pub", different_line, frozenset({"2"}), realm_id="realm-1")

    all_lines = connector.bill_credit_line_item_service.read_by_bill_credit_id(19146)
    assert len(all_lines) == 2  # both survive -- this is NOT a double-count, they're different lines
    by_id = {li.id: li for li in all_lines}
    assert by_id[created.id].qbo_id == "1"  # the original orphan is untouched (no fingerprint match)
    assert by_id[created.id].description == "Materials"
