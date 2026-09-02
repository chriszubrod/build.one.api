"""U-344 (re-dispatch after the first attempt's Gate-1 halt): store SIGNED
(negative) Price/Amount for BillCreditLineItem-sourced InvoiceLineItem rows,
at every write path, with the read-side prerequisite fixed first.

Final shape, after a Codex-fallback (Claude /code-review, xhigh) adversarial
pass surfaced two gaps the initial build missed:

  1. Read-side prerequisite: entities/budget/sql/dbo.budget_variance.sql's two
     `WHEN ili.[BillCreditLineItemId] IS NOT NULL` branches must use -ABS
     (idempotent), not a bare -ISNULL negate — the site the first U-344
     attempt's Gate-1 correctly halted on.
  2. Write path A: InvoiceLineItemService.create negates for a fresh
     BillCredit-sourced line.
  3. Write path B: LinkInvoiceLineItemSource (the sproc backing
     InvoiceReconciliationService.apply_links's relabel) now negates
     Price/Amount ATOMICALLY, in the same UPDATE that repoints the source
     FK — folded into SQL (not a follow-up Python update_by_id call) after
     review found the two-step version left a permanent-stuck-positive
     window if the second write ever failed a RowVersion check (the row
     would then read as "already_linked" on retry and never get re-negated).
  4. Self-healing write path: InvoiceLineItemService.update_by_public_id now
     also enforces the signed-negative invariant for any row whose resolved
     source_type is BillCreditLineItem — review found the QBO invoice-line
     connector's routine re-sync writes raw (positive) QBO amounts through
     this exact method on every pull, which would otherwise silently
     re-flip a correctly-negated row back to positive.
  5. QBO re-pull compatibility: the connector's `_apply_line_fields` amount-
     changed check now compares by MAGNITUDE when the existing source_type
     is BillCreditLineItem, since QBO's own reported sign for a line "is not
     guaranteed" (dbo.invoice.sql's Tier-3 comment) — without this, a
     routine re-pull sees "amount changed" purely from the sign convention
     and spuriously resets the line to Manual + un-bills its source,
     defeating path B on the very next sync.

Known, deliberately out-of-scope residual (reported, not fixed here): a line
relabeled AWAY from BillCreditLineItem (e.g. after its BillCredit is deleted,
which nullifies only the FK — see NullifyInvoiceLineItemsByBillCreditLineItemId
— leaving SourceType/Price/Amount untouched) does not get its sign restored
to positive. A full fix needs to know the target source's OWN sign convention
(ExpenseLineItem's is independently governed by Expense.IsCredit) to avoid
breaking that unrelated mechanism, which is out of U-344's scope.
"""
import re
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from entities.invoice_line_item.business.service import InvoiceLineItemService
from entities.invoice.business.reconciliation import InvoiceReconciliationService
from integrations.intuit.qbo.invoice.connector.invoice_line_item.business.service import (
    InvoiceLineItemConnector,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


# ── Part 1: read-side prerequisite (budget_variance.sql) ────────────────────

_BUDGET_VARIANCE_SQL_PATH = REPO_ROOT / "entities" / "budget" / "sql" / "dbo.budget_variance.sql"

_BILLCREDIT_BRANCH_RE = re.compile(
    r"WHEN\s+ili\.\[BillCreditLineItemId\]\s+IS\s+NOT\s+NULL\s*"
    r"\n\s*THEN\s+(-ABS\(ISNULL\(ili\.\[Price\],\s*0\)\)|-ISNULL\(ili\.\[Price\],\s*0\))",
    re.IGNORECASE,
)


def test_budget_variance_billcredit_branches_use_idempotent_abs_negation():
    sql = _BUDGET_VARIANCE_SQL_PATH.read_text()
    then_clauses = _BILLCREDIT_BRANCH_RE.findall(sql)

    assert len(then_clauses) == 2, (
        f"expected exactly 2 `ili.[BillCreditLineItemId] IS NOT NULL` branches "
        f"in dbo.budget_variance.sql, found {len(then_clauses)}"
    )
    for then_clause in then_clauses:
        assert then_clause.upper().startswith("-ABS("), (
            f"blind (non-idempotent) negation found: {then_clause!r} — a BillCredit "
            "branch must read -ABS(ISNULL(ili.[Price], 0)), not a bare -ISNULL(...), "
            "or a newly-negative stored Price flips back positive (U-344)"
        )


def test_budget_variance_billcredit_branch_mutation_proof():
    original = _BUDGET_VARIANCE_SQL_PATH.read_text()
    mutated = original.replace(
        "THEN -ABS(ISNULL(ili.[Price], 0))",
        "THEN -ISNULL(ili.[Price], 0)",
        1,
    )
    assert mutated != original, "mutation target string not found — guard text drifted"

    then_clauses = _BILLCREDIT_BRANCH_RE.findall(mutated)
    assert len(then_clauses) == 2
    assert not all(c.upper().startswith("-ABS(") for c in then_clauses), (
        "mutated SQL should fail the idempotent-negation assertion"
    )


# ── Part 2: write path A — InvoiceLineItemService.create ────────────────────

def _build_ili_service():
    repo = Mock()
    repo.create.return_value = Mock()
    service = InvoiceLineItemService(repo=repo)
    return service, repo


def _patched_invoice_service(invoice_id=99):
    invoice_service = Mock()
    invoice_service.read_by_public_id.return_value = Mock(id=invoice_id)
    return patch(
        "entities.invoice.business.service.InvoiceService",
        return_value=invoice_service,
    )


def test_create_billcredit_source_stores_negative_price_and_amount():
    service, repo = _build_ili_service()

    with _patched_invoice_service():
        service.create(
            invoice_public_id="inv-pub",
            source_type="BillCreditLineItem",
            bill_credit_line_item_id=7,
            amount=Decimal("500.00"),
            price=Decimal("500.00"),
        )

    kwargs = repo.create.call_args.kwargs
    assert kwargs["amount"] == Decimal("-500.00")
    assert kwargs["price"] == Decimal("-500.00")


def test_create_billcredit_source_idempotent_on_already_negative_input():
    service, repo = _build_ili_service()

    with _patched_invoice_service():
        service.create(
            invoice_public_id="inv-pub",
            source_type="BillCreditLineItem",
            bill_credit_line_item_id=7,
            amount=Decimal("-500.00"),
            price=Decimal("-500.00"),
        )

    kwargs = repo.create.call_args.kwargs
    assert kwargs["amount"] == Decimal("-500.00")
    assert kwargs["price"] == Decimal("-500.00")


def test_create_billcredit_source_leaves_none_price_and_amount_as_none():
    service, repo = _build_ili_service()

    with _patched_invoice_service():
        service.create(
            invoice_public_id="inv-pub",
            source_type="BillCreditLineItem",
            bill_credit_line_item_id=7,
            amount=None,
            price=None,
        )

    kwargs = repo.create.call_args.kwargs
    assert kwargs["amount"] is None
    assert kwargs["price"] is None


def test_create_bill_line_item_source_stays_positive():
    service, repo = _build_ili_service()

    with _patched_invoice_service():
        service.create(
            invoice_public_id="inv-pub",
            source_type="BillLineItem",
            bill_line_item_id=3,
            amount=Decimal("500.00"),
            price=Decimal("550.00"),
        )

    kwargs = repo.create.call_args.kwargs
    assert kwargs["amount"] == Decimal("500.00")
    assert kwargs["price"] == Decimal("550.00")


def test_create_expense_line_item_source_stays_positive():
    service, repo = _build_ili_service()

    with _patched_invoice_service():
        service.create(
            invoice_public_id="inv-pub",
            source_type="ExpenseLineItem",
            expense_line_item_id=5,
            amount=Decimal("500.00"),
            price=Decimal("550.00"),
        )

    kwargs = repo.create.call_args.kwargs
    assert kwargs["amount"] == Decimal("500.00")
    assert kwargs["price"] == Decimal("550.00")


# ── Part 2b: self-healing write — InvoiceLineItemService.update_by_public_id

def _build_ili_service_with_existing(existing):
    repo = Mock()
    repo.read_by_public_id.return_value = existing
    repo.update_by_id.side_effect = lambda item: item
    service = InvoiceLineItemService(repo=repo)
    return service, repo


def test_update_by_public_id_self_heals_billcredit_row_with_fresh_positive_price():
    existing = SimpleNamespace(
        id=55, invoice_id=None, source_type="BillCreditLineItem",
        bill_credit_line_item_id=7, bill_line_item_id=None, expense_line_item_id=None,
        sub_cost_code_id=None, description=None, quantity=None, rate=None,
        amount=Decimal("-500.00"), markup=None, price=Decimal("-500.00"), is_draft=False,
    )
    service, repo = _build_ili_service_with_existing(existing)

    result = service.update_by_public_id(
        "pub-55", row_version="rv", amount=Decimal("500.00"), price=Decimal("500.00"),
    )

    assert result.amount == Decimal("-500.00")
    assert result.price == Decimal("-500.00")
    repo.update_by_id.assert_called_once()


def test_update_by_public_id_self_heals_when_relabeling_onto_billcredit():
    existing = SimpleNamespace(
        id=55, invoice_id=None, source_type="Manual",
        bill_credit_line_item_id=None, bill_line_item_id=None, expense_line_item_id=None,
        sub_cost_code_id=None, description=None, quantity=None, rate=None,
        amount=Decimal("500.00"), markup=None, price=Decimal("500.00"), is_draft=False,
    )
    service, repo = _build_ili_service_with_existing(existing)

    result = service.update_by_public_id(
        "pub-55", row_version="rv",
        source_type="BillCreditLineItem", bill_credit_line_item_id=7,
        amount=Decimal("500.00"), price=Decimal("500.00"),
    )

    assert result.amount == Decimal("-500.00")
    assert result.price == Decimal("-500.00")


def test_update_by_public_id_idempotent_when_price_amount_not_passed():
    existing = SimpleNamespace(
        id=55, invoice_id=None, source_type="BillCreditLineItem",
        bill_credit_line_item_id=7, bill_line_item_id=None, expense_line_item_id=None,
        sub_cost_code_id=None, description="unchanged", quantity=None, rate=None,
        amount=Decimal("-500.00"), markup=None, price=Decimal("-500.00"), is_draft=False,
    )
    service, repo = _build_ili_service_with_existing(existing)

    result = service.update_by_public_id("pub-55", row_version="rv", description="still unchanged")

    assert result.amount == Decimal("-500.00")
    assert result.price == Decimal("-500.00")


def test_update_by_public_id_does_not_touch_bill_line_item_sign():
    existing = SimpleNamespace(
        id=55, invoice_id=None, source_type="BillLineItem",
        bill_credit_line_item_id=None, bill_line_item_id=3, expense_line_item_id=None,
        sub_cost_code_id=None, description=None, quantity=None, rate=None,
        amount=Decimal("500.00"), markup=None, price=Decimal("500.00"), is_draft=False,
    )
    service, repo = _build_ili_service_with_existing(existing)

    result = service.update_by_public_id(
        "pub-55", row_version="rv", amount=Decimal("500.00"), price=Decimal("500.00"),
    )

    assert result.amount == Decimal("500.00")
    assert result.price == Decimal("500.00")


def test_update_by_public_id_negation_removed_would_fail_the_self_heal_assertion():
    """Mutation-proof for the update_by_public_id self-heal: an implementation
    without it would pass through the raw positive value unchanged — this is
    the exact assertion that goes RED under that mutation."""
    existing = SimpleNamespace(
        id=55, invoice_id=None, source_type="BillCreditLineItem",
        bill_credit_line_item_id=7, bill_line_item_id=None, expense_line_item_id=None,
        sub_cost_code_id=None, description=None, quantity=None, rate=None,
        amount=Decimal("-500.00"), markup=None, price=Decimal("-500.00"), is_draft=False,
    )
    service, repo = _build_ili_service_with_existing(existing)

    result = service.update_by_public_id(
        "pub-55", row_version="rv", amount=Decimal("500.00"), price=Decimal("500.00"),
    )

    assert result.amount != Decimal("500.00")
    assert result.price != Decimal("500.00")


# ── Part 3: write path B — LinkInvoiceLineItemSource (atomic SQL negation) ──

_INVOICE_LINE_ITEM_SQL_PATH = REPO_ROOT / "entities" / "invoice_line_item" / "sql" / "dbo.invoice_line_item.sql"


def _extract_sproc(sql_text, name):
    match = re.search(
        rf"CREATE OR ALTER PROCEDURE {name}\b.*?\nEND;\nGO",
        sql_text,
        re.IGNORECASE | re.DOTALL,
    )
    assert match is not None, f"could not locate {name} in the SQL file"
    return match.group(0)


def test_link_invoice_line_item_source_negates_billcredit_atomically():
    body = _extract_sproc(_INVOICE_LINE_ITEM_SQL_PATH.read_text(), "LinkInvoiceLineItemSource")

    price_case = re.search(
        r"\[Price\]\s*=\s*CASE\s+WHEN\s+@SourceType\s*=\s*N'BillCreditLineItem'"
        r"\s+AND\s+\[Price\]\s+IS\s+NOT\s+NULL\s+THEN\s+-ABS\(\[Price\]\)\s+ELSE\s+\[Price\]\s+END",
        body, re.IGNORECASE,
    )
    amount_case = re.search(
        r"\[Amount\]\s*=\s*CASE\s+WHEN\s+@SourceType\s*=\s*N'BillCreditLineItem'"
        r"\s+AND\s+\[Amount\]\s+IS\s+NOT\s+NULL\s+THEN\s+-ABS\(\[Amount\]\)\s+ELSE\s+\[Amount\]\s+END",
        body, re.IGNORECASE,
    )
    # The regex itself requires the ELSE branch to be the bare column (not
    # another transformation), so a match already proves every OTHER source
    # type leaves Price/Amount untouched — scoped exactly to BillCreditLineItem.
    assert price_case is not None, "Price must be set via an idempotent, NULL-safe -ABS CASE scoped to BillCreditLineItem"
    assert amount_case is not None, "Amount must be set via an idempotent, NULL-safe -ABS CASE scoped to BillCreditLineItem"


def test_link_invoice_line_item_source_negation_mutation_proof():
    sql = _INVOICE_LINE_ITEM_SQL_PATH.read_text()
    mutated = sql.replace(
        "THEN -ABS([Price])\n            ELSE [Price]",
        "THEN [Price]\n            ELSE [Price]",
        1,
    )
    assert mutated != sql, "mutation target string not found — guard text drifted"
    body = _extract_sproc(mutated, "LinkInvoiceLineItemSource")
    price_case = re.search(
        r"\[Price\]\s*=\s*CASE\s+WHEN\s+@SourceType\s*=\s*N'BillCreditLineItem'"
        r"\s+AND\s+\[Price\]\s+IS\s+NOT\s+NULL\s+THEN\s+-ABS\(\[Price\]\)\s+ELSE\s+\[Price\]\s+END",
        body, re.IGNORECASE,
    )
    assert price_case is None, "mutated SQL (no negation) should fail the guard"


def _build_reconciliation_service():
    invoice_repo = Mock()
    invoice_line_item_repo = Mock()
    invoice_service = Mock()
    service = InvoiceReconciliationService(
        invoice_repo=invoice_repo,
        invoice_line_item_repo=invoice_line_item_repo,
        invoice_service=invoice_service,
    )
    return service, invoice_repo, invoice_line_item_repo


def _stub_single_line_proposal(service, *, source_type, project_id=42):
    service.propose_links = Mock(
        return_value={
            "invoice_public_id": "inv-pub",
            "invoice_id": 1,
            "project_id": project_id,
            "lines": [
                {
                    "invoice_line_item_id": 55,
                    "status": "linkable",
                    "proposed": {"source_type": source_type, "source_line_item_id": 9},
                }
            ],
            "summary": {},
        }
    )


def test_apply_links_relabel_to_billcredit_passes_the_source_fk_through():
    service, _, ili_repo = _build_reconciliation_service()
    _stub_single_line_proposal(service, source_type="BillCreditLineItem")
    ili_repo.link_invoice_line_item_source.return_value = SimpleNamespace(
        id=55, price=Decimal("-500.00"), amount=Decimal("-500.00")
    )

    service.apply_links("inv-pub")

    ili_repo.link_invoice_line_item_source.assert_called_once_with(
        invoice_line_item_id=55,
        source_type="BillCreditLineItem",
        bill_line_item_id=None,
        expense_line_item_id=None,
        bill_credit_line_item_id=9,
    )
    # The sign invariant is enforced INSIDE the sproc now (see the SQL guard
    # tests above) — apply_links makes no separate follow-up write.
    ili_repo.update_by_id.assert_not_called()


def test_apply_links_relabel_to_bill_line_item_passes_the_source_fk_through():
    service, _, ili_repo = _build_reconciliation_service()
    _stub_single_line_proposal(service, source_type="BillLineItem")
    ili_repo.link_invoice_line_item_source.return_value = SimpleNamespace(
        id=55, price=Decimal("500.00"), amount=Decimal("500.00")
    )

    service.apply_links("inv-pub")

    ili_repo.link_invoice_line_item_source.assert_called_once_with(
        invoice_line_item_id=55,
        source_type="BillLineItem",
        bill_line_item_id=9,
        expense_line_item_id=None,
        bill_credit_line_item_id=None,
    )
    ili_repo.update_by_id.assert_not_called()


# ── Part 4: QBO re-pull compatibility — magnitude-insensitive amount_changed

def _make_qbo_invoice_line(**overrides):
    defaults = dict(
        id=42, qbo_invoice_id=4, qbo_line_id="1", description="Credit",
        amount=Decimal("500"), unit_price=None, qty=None, line_num=1,
        service_date=None, linked_txn_type=None, linked_txn_id=None, item_ref_value=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _build_qbo_connector():
    invoice_line_item_service = Mock()
    invoice_line_item_service.repo = Mock()
    invoice_service = Mock()
    reconciliation_repo = Mock()
    connector = InvoiceLineItemConnector(
        invoice_line_item_service=invoice_line_item_service,
        invoice_service=invoice_service,
        reconciliation_repo=reconciliation_repo,
    )
    return connector, invoice_line_item_service, invoice_service


def test_qbo_resync_same_magnitude_billcredit_line_is_not_treated_as_changed():
    connector, ili_svc, invoice_service = _build_qbo_connector()
    qbo_line = _make_qbo_invoice_line(qbo_line_id="1", amount=Decimal("500"))
    direct_hit = SimpleNamespace(
        id=55, public_id="pub-55", row_version="rv-55", qbo_id="1",
        source_type="BillCreditLineItem", amount=Decimal("-500"),
    )
    ili_svc.read_by_qbo_identity.return_value = direct_hit
    updated = SimpleNamespace(id=55, public_id="pub-55")
    ili_svc.update_by_public_id.return_value = updated

    connector.sync_from_qbo_invoice_line(19146, "inv-pub", qbo_line, frozenset({"1"}), realm_id="realm-1")

    invoice_service._reset_source_as_unbilled.assert_not_called()
    kwargs = ili_svc.update_by_public_id.call_args.kwargs
    assert kwargs["source_type"] == "BillCreditLineItem"


def test_qbo_resync_genuinely_different_billcredit_amount_still_resets_to_manual():
    connector, ili_svc, invoice_service = _build_qbo_connector()
    qbo_line = _make_qbo_invoice_line(qbo_line_id="1", amount=Decimal("600"))
    direct_hit = SimpleNamespace(
        id=55, public_id="pub-55", row_version="rv-55", qbo_id="1",
        source_type="BillCreditLineItem", amount=Decimal("-500"),
    )
    ili_svc.read_by_qbo_identity.return_value = direct_hit
    updated = SimpleNamespace(id=55, public_id="pub-55")
    ili_svc.update_by_public_id.return_value = updated

    connector.sync_from_qbo_invoice_line(19146, "inv-pub", qbo_line, frozenset({"1"}), realm_id="realm-1")

    invoice_service._reset_source_as_unbilled.assert_called_once_with(direct_hit)
    kwargs = ili_svc.update_by_public_id.call_args.kwargs
    assert kwargs["source_type"] == "Manual"


def test_qbo_resync_manual_line_sign_flip_is_still_treated_as_changed():
    """Magnitude-insensitivity is scoped to BillCreditLineItem only — a
    Manual-sourced line's sign is meaningful (it drives Manual's own credit
    detection) and must NOT be silently ignored."""
    connector, ili_svc, invoice_service = _build_qbo_connector()
    qbo_line = _make_qbo_invoice_line(qbo_line_id="1", amount=Decimal("-500"))
    direct_hit = SimpleNamespace(
        id=55, public_id="pub-55", row_version="rv-55", qbo_id="1",
        source_type="BillLineItem", amount=Decimal("500"),
    )
    ili_svc.read_by_qbo_identity.return_value = direct_hit
    updated = SimpleNamespace(id=55, public_id="pub-55")
    ili_svc.update_by_public_id.return_value = updated

    connector.sync_from_qbo_invoice_line(19146, "inv-pub", qbo_line, frozenset({"1"}), realm_id="realm-1")

    invoice_service._reset_source_as_unbilled.assert_called_once_with(direct_hit)
    kwargs = ili_svc.update_by_public_id.call_args.kwargs
    assert kwargs["source_type"] == "Manual"
