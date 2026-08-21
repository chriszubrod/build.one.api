"""Pure-logic regression tests for U-275: dbo-native QboActive mirror.

Every sync_from_qbo_* branch (update / heal / create) on the Vendor, PaymentTerm,
and SubCostCode connectors must thread the QBO record's `active` flag through to
`repo.set_qbo_identity(..., active=...)` so the pull path actually populates the
new mirror column, not just tolerates the new kwarg. A regression here silently
freezes QboActive at NULL forever (falls back to a stale-forever mirror) while
still passing every pre-existing test, since none of them asserted on `active`
before this unit.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from integrations.intuit.qbo.item.business.model import QboItem
from integrations.intuit.qbo.item.connector.sub_cost_code.business.model import ItemSubCostCode
from integrations.intuit.qbo.item.connector.sub_cost_code.business.service import ItemSubCostCodeConnector
from integrations.intuit.qbo.term.business.model import QboTerm
from integrations.intuit.qbo.term.connector.payment_term.business.model import TermPaymentTerm
from integrations.intuit.qbo.term.connector.payment_term.business.service import TermPaymentTermConnector
from integrations.intuit.qbo.vendor.business.model import QboVendor
from integrations.intuit.qbo.vendor.connector.vendor.business.model import VendorVendor
from integrations.intuit.qbo.vendor.connector.vendor.business.service import VendorVendorConnector


def _make_qbo_vendor(**overrides: Any) -> QboVendor:
    defaults = dict(
        id=1,
        public_id=None,
        row_version=None,
        created_datetime=None,
        modified_datetime=None,
        qbo_id="QBO-V-1",
        sync_token=None,
        realm_id="r1",
        display_name="Acme Supply",
        title=None,
        given_name=None,
        middle_name=None,
        family_name=None,
        suffix=None,
        company_name=None,
        print_on_check_name=None,
        tax_identifier=None,
        vendor_1099=None,
        active=None,
        primary_email_addr=None,
        primary_phone=None,
        mobile=None,
        fax=None,
        bill_addr_id=None,
        balance=None,
        acct_num=None,
        web_addr=None,
    )
    defaults.update(overrides)
    return QboVendor(**defaults)


def _make_qbo_term(**overrides: Any) -> QboTerm:
    defaults = dict(
        id=1,
        public_id=None,
        row_version=None,
        created_datetime=None,
        modified_datetime=None,
        qbo_id="QBO-T-1",
        sync_token=None,
        realm_id="r1",
        name="Net 30",
        discount_percent=None,
        discount_days=None,
        active=None,
        type=None,
        day_of_month_due=None,
        discount_day_of_month=None,
        due_next_month_days=None,
        due_days=30,
    )
    defaults.update(overrides)
    return QboTerm(**defaults)


def _make_qbo_item(**overrides: Any) -> QboItem:
    defaults = dict(
        id=1,
        public_id=None,
        row_version=None,
        created_datetime=None,
        modified_datetime=None,
        qbo_id="QBO-I-1",
        sync_token=None,
        realm_id="r1",
        name="01 Permits",
        description="desc",
        active=None,
        type=None,
        parent_ref_value="parent-qbo-id",
        parent_ref_name=None,
        level=None,
        fully_qualified_name=None,
        sku=None,
        unit_price=None,
        purchase_cost=None,
        taxable=None,
        income_account_ref_value=None,
        income_account_ref_name=None,
        expense_account_ref_value=None,
        expense_account_ref_name=None,
    )
    defaults.update(overrides)
    return QboItem(**defaults)


# ------------------------------------------------------------------------- #
# Vendor
# ------------------------------------------------------------------------- #

def _build_vendor_connector():
    connector = VendorVendorConnector(
        mapping_repo=Mock(),
        vendor_service=Mock(),
        vendor_address_service=Mock(),
        address_connector=Mock(),
        reconciliation_repo=Mock(),
    )
    connector._sync_addresses = Mock()
    return connector


def test_vendor_update_path_threads_active():
    connector = _build_vendor_connector()
    qbo_vendor = _make_qbo_vendor(active=False)
    mapping = VendorVendor(
        id=10, public_id="m1", row_version=None, created_datetime=None,
        modified_datetime=None, vendor_id=100, qbo_vendor_id=1,
    )
    vendor = Mock(id=100, name="Acme Supply")
    connector.mapping_repo.read_by_qbo_vendor_id.return_value = mapping
    connector.vendor_service.read_by_id.return_value = vendor
    connector.vendor_service.repo.update_by_id.side_effect = lambda v: v

    connector.sync_from_qbo_vendor(qbo_vendor)

    connector.vendor_service.repo.set_qbo_identity.assert_called_once_with(
        id=100, qbo_id="QBO-V-1", realm_id="r1", active=False,
    )


def test_vendor_heal_path_threads_active():
    connector = _build_vendor_connector()
    qbo_vendor = _make_qbo_vendor(active=True)
    mapping = VendorVendor(
        id=10, public_id="m1", row_version=None, created_datetime=None,
        modified_datetime=None, vendor_id=999, qbo_vendor_id=1,
    )
    replacement = Mock(id=200, name="Acme Supply", public_id="v-pub-200")
    connector.mapping_repo.read_by_qbo_vendor_id.return_value = mapping
    connector.vendor_service.read_by_id.return_value = None
    connector.vendor_service.read_by_name.return_value = replacement
    connector.mapping_repo.read_by_vendor_id.return_value = None
    connector.vendor_service.repo.update_by_id.side_effect = lambda v: v

    connector.sync_from_qbo_vendor(qbo_vendor)

    connector.vendor_service.repo.set_qbo_identity.assert_called_once_with(
        id=200, qbo_id="QBO-V-1", realm_id="r1", active=True,
    )


def test_vendor_create_path_threads_active():
    connector = _build_vendor_connector()
    qbo_vendor = _make_qbo_vendor(active=True, display_name="Brand New Vendor")
    created = Mock(id=300, name="Brand New Vendor")
    connector.mapping_repo.read_by_qbo_vendor_id.return_value = None
    connector.vendor_service.read_by_name.return_value = None
    connector.vendor_service.create.return_value = created
    connector.mapping_repo.read_by_vendor_id.return_value = None
    connector.mapping_repo.create.return_value = VendorVendor(
        id=1, public_id="m2", row_version=None, created_datetime=None,
        modified_datetime=None, vendor_id=300, qbo_vendor_id=1,
    )

    connector.sync_from_qbo_vendor(qbo_vendor)

    connector.vendor_service.repo.set_qbo_identity.assert_called_once_with(
        id=300, qbo_id="QBO-V-1", realm_id="r1", active=True,
    )


# ------------------------------------------------------------------------- #
# PaymentTerm (no heal branch)
# ------------------------------------------------------------------------- #

def _build_payment_term_connector():
    connector = TermPaymentTermConnector(
        mapping_repo=Mock(), payment_term_service=Mock(), reconciliation_repo=Mock()
    )
    # U-282: default the direct dbo-identity fast path to a miss so these tests keep
    # exercising the mapping-table path they're testing (mirrors U-276's identical fix
    # for customer/project).
    connector.payment_term_service.read_by_qbo_identity.return_value = None
    return connector


def test_payment_term_update_path_threads_active():
    connector = _build_payment_term_connector()
    qbo_term = _make_qbo_term(active=False)
    mapping = TermPaymentTerm(
        id=1, public_id="m1", row_version=None, created_datetime=None,
        modified_datetime=None, payment_term_id=13, qbo_term_id=1,
    )
    payment_term = Mock(id=13, name="Net 30")
    connector.mapping_repo.read_by_qbo_term_id.return_value = mapping
    connector.payment_term_service.read_by_id.return_value = payment_term
    connector.payment_term_service.repo.update_by_id.side_effect = lambda pt: pt

    connector.sync_from_qbo_term(qbo_term)

    connector.payment_term_service.repo.set_qbo_identity.assert_called_once_with(
        id=13, qbo_id="QBO-T-1", realm_id="r1", active=False,
    )


def test_payment_term_create_path_threads_active():
    connector = _build_payment_term_connector()
    qbo_term = _make_qbo_term(active=True)
    created = Mock(id=14, name="Net 30")
    connector.mapping_repo.read_by_qbo_term_id.return_value = None
    connector.payment_term_service.create.return_value = created
    connector.mapping_repo.read_by_payment_term_id.return_value = None
    connector.mapping_repo.create.return_value = TermPaymentTerm(
        id=2, public_id="m2", row_version=None, created_datetime=None,
        modified_datetime=None, payment_term_id=14, qbo_term_id=1,
    )

    connector.sync_from_qbo_term(qbo_term)

    connector.payment_term_service.repo.set_qbo_identity.assert_called_once_with(
        id=14, qbo_id="QBO-T-1", realm_id="r1", active=True,
    )


# ------------------------------------------------------------------------- #
# SubCostCode
# ------------------------------------------------------------------------- #

def _build_sub_cost_code_connector():
    connector = ItemSubCostCodeConnector(
        mapping_repo=Mock(),
        sub_cost_code_service=Mock(),
        cost_code_mapping_repo=Mock(),
        qbo_item_repo=Mock(),
        reconciliation_repo=Mock(),
    )
    connector.qbo_item_repo.read_by_qbo_id.return_value = Mock(id=99)
    connector.cost_code_mapping_repo.read_by_qbo_item_id.return_value = Mock(cost_code_id=10)
    return connector


def test_sub_cost_code_update_path_threads_active():
    connector = _build_sub_cost_code_connector()
    qbo_item = _make_qbo_item(active=False)
    mapping = ItemSubCostCode(
        id=1, public_id="m1", row_version=None, created_datetime=None,
        modified_datetime=None, sub_cost_code_id=100, qbo_item_id=1,
    )
    sub_cost_code = Mock(id=100, name="Permits", number="01", description="desc", cost_code_id=10)
    connector.mapping_repo.read_by_qbo_item_id.return_value = mapping
    connector.sub_cost_code_service.read_by_id.return_value = sub_cost_code
    connector.sub_cost_code_service.repo.update_by_id.side_effect = lambda e: e

    connector.sync_from_qbo_item(qbo_item)

    connector.sub_cost_code_service.repo.set_qbo_identity.assert_called_once_with(
        id=100, qbo_id="QBO-I-1", realm_id="r1", active=False,
    )


def test_sub_cost_code_heal_path_threads_active():
    connector = _build_sub_cost_code_connector()
    qbo_item = _make_qbo_item(active=True)
    mapping = ItemSubCostCode(
        id=1, public_id="m1", row_version=None, created_datetime=None,
        modified_datetime=None, sub_cost_code_id=999, qbo_item_id=1,
    )
    replacement = Mock(id=200, name="Permits", number="01", description="desc", cost_code_id=10)
    connector.mapping_repo.read_by_qbo_item_id.return_value = mapping
    connector.sub_cost_code_service.read_by_id.return_value = None
    connector.sub_cost_code_service.repo.read_by_cost_code_id.return_value = [replacement]
    connector.mapping_repo.read_by_sub_cost_code_id.return_value = None
    connector.sub_cost_code_service.repo.update_by_id.side_effect = lambda e: e

    connector.sync_from_qbo_item(qbo_item)

    connector.sub_cost_code_service.repo.set_qbo_identity.assert_called_once_with(
        id=200, qbo_id="QBO-I-1", realm_id="r1", active=True,
    )


def test_sub_cost_code_create_path_threads_active():
    connector = _build_sub_cost_code_connector()
    qbo_item = _make_qbo_item(active=True)
    created = Mock(id=300, name="Permits")
    connector.mapping_repo.read_by_qbo_item_id.return_value = None
    connector.sub_cost_code_service.repo.read_by_cost_code_id.return_value = []
    connector.sub_cost_code_service.create.return_value = created
    connector.mapping_repo.read_by_sub_cost_code_id.return_value = None
    connector.mapping_repo.create.return_value = ItemSubCostCode(
        id=2, public_id="m2", row_version=None, created_datetime=None,
        modified_datetime=None, sub_cost_code_id=300, qbo_item_id=1,
    )

    connector.sync_from_qbo_item(qbo_item)

    connector.sub_cost_code_service.repo.set_qbo_identity.assert_called_once_with(
        id=300, qbo_id="QBO-I-1", realm_id="r1", active=True,
    )


# ------------------------------------------------------------------------- #
# Steal-path SQL guard (static text check — no live DB in this harness, per
# the existing convention in test_qbo_identity_headers.py: "FIX 1/2 sproc
# no-op + steal guards are SQL-only ... not regression-tested [via a live
# call] here — the guards live in the sprocs themselves.")
# ------------------------------------------------------------------------- #

REPO_ROOT = Path(__file__).resolve().parents[1]


def _sproc_body(sql_text: str, name: str) -> str:
    match = re.search(
        rf"CREATE\s+OR\s+ALTER\s+PROCEDURE\s+{name}\b(.*?)\nGO",
        sql_text,
        re.DOTALL | re.IGNORECASE,
    )
    assert match, f"{name} not found"
    return match.group(1)


def _first_update_set_clause(body: str, table: str) -> str:
    """The steal block's UPDATE is the FIRST `UPDATE dbo.[table] ... SET ... WHERE`
    inside the sproc body — it runs before the main preserve-on-NULL UPDATE."""
    match = re.search(
        rf"UPDATE\s+dbo\.\[{table}\]\s*\n?\s*SET(.*?)WHERE",
        body,
        re.DOTALL | re.IGNORECASE,
    )
    assert match, f"UPDATE dbo.[{table}] SET...WHERE not found"
    return match.group(1)


@pytest.mark.parametrize(
    "sql_path,sproc,table",
    [
        ("entities/vendor/sql/dbo.vendor.sql", "SetVendorQboIdentity", "Vendor"),
        ("entities/payment_term/sql/dbo.payment_term.sql", "SetPaymentTermQboIdentity", "PaymentTerm"),
        ("entities/sub_cost_code/sql/dbo.subcostcode.sql", "SetSubCostCodeQboIdentity", "SubCostCode"),
    ],
)
def test_steal_block_clears_qbo_active(sql_path, sproc, table):
    """Identity-steal UPDATE must null QboActive too, or the losing row's mirror
    goes permanently stale — nothing will ever repopulate it once QboId is gone
    (Codex-caught P2, U-275 review round 1)."""
    text = (REPO_ROOT / sql_path).read_text(encoding="utf-8")
    body = _sproc_body(text, sproc)
    steal_set = _first_update_set_clause(body, table)
    assert "[QboId] = NULL" in steal_set
    assert "[QboActive] = NULL" in steal_set, (
        f"{sproc}: steal-path UPDATE clears QboId/RealmId but leaves QboActive stale."
    )


@pytest.mark.parametrize(
    "sql_path,sproc",
    [
        ("entities/vendor/sql/dbo.vendor.sql", "SetVendorQboIdentity"),
        ("entities/payment_term/sql/dbo.payment_term.sql", "SetPaymentTermQboIdentity"),
        ("entities/sub_cost_code/sql/dbo.subcostcode.sql", "SetSubCostCodeQboIdentity"),
    ],
)
def test_main_update_preserves_qbo_active_on_null(sql_path, sproc):
    """The main upsert UPDATE must PRESERVE QboActive when @Active IS NULL. A caller
    that omits @Active — the old container during the SQL-first deploy window, or any
    partial identity update — must not wipe the live mirror to NULL. This CASE guard is
    exactly what makes SQL-first safe; without a test, a /simplify pass could collapse it
    to an unconditional `[QboActive] = @Active` undetected (U-275 Gate-2 finding — pytest
    mocks the DB so it cannot execute the T-SQL; this pins the guard textually)."""
    text = (REPO_ROOT / sql_path).read_text(encoding="utf-8")
    body = _sproc_body(text, sproc)
    normalized = re.sub(r"\s+", " ", body)
    assert (
        "[QboActive] = CASE WHEN @Active IS NOT NULL THEN @Active ELSE [QboActive] END"
        in normalized
    ), f"{sproc}: main UPDATE must NULL-preserve QboActive (CASE WHEN @Active IS NOT NULL ...)."
