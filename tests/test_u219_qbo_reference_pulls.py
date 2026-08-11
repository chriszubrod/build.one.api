"""Pure-logic tests for U-219: inactive reference pulls, create guards, scheduler authz, vendorcredit attachments."""
from __future__ import annotations

import ast
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import Mock, patch

import pytest

from integrations.intuit.qbo.account.external.client import QboAccountClient
from integrations.intuit.qbo.customer.business.model import QboCustomer
from integrations.intuit.qbo.customer.connector.customer.business.service import CustomerCustomerConnector
from integrations.intuit.qbo.customer.connector.project.business.service import CustomerProjectConnector
from integrations.intuit.qbo.customer.external.client import QboCustomerClient
from integrations.intuit.qbo.item.business.model import QboItem
from integrations.intuit.qbo.item.connector.cost_code.business.service import ItemCostCodeConnector
from integrations.intuit.qbo.item.connector.sub_cost_code.business.service import ItemSubCostCodeConnector
from integrations.intuit.qbo.item.external.client import QboItemClient
from integrations.intuit.qbo.term.business.model import QboTerm
from integrations.intuit.qbo.term.connector.payment_term.business.service import TermPaymentTermConnector
from integrations.intuit.qbo.term.external.client import QboTermClient
from integrations.intuit.qbo.vendor.business.model import QboVendor
from integrations.intuit.qbo.vendor.connector.vendor.business.service import VendorVendorConnector
from integrations.intuit.qbo.vendor.external.client import QboVendorClient
from shared.authz import (
    current_company_id,
    current_is_system_admin,
    current_user_id,
    set_authz_context,
)
from shared.scheduler import _register_qbo_pull_jobs

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

ACTIVE_PREDICATE = "Active IN (true, false)"


class _CapturingHttpClient:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def get(self, path: str, params: Optional[dict] = None, operation_name: str = "") -> dict:
        self.queries.append(params["query"])
        return {"QueryResponse": {}}


def _assert_active_in_both_branches(client_factory, query_method_name: str) -> None:
    http = _CapturingHttpClient()
    client = client_factory(http)
    query = getattr(client, query_method_name)

    query(last_updated_time="2026-01-15T12:00:00Z")
    watermark_query = http.queries[-1]
    assert ACTIVE_PREDICATE in watermark_query
    assert "Metadata.LastUpdatedTime >" in watermark_query

    query()
    full_query = http.queries[-1]
    assert ACTIVE_PREDICATE in full_query
    assert "Metadata.LastUpdatedTime >" not in full_query


@pytest.mark.parametrize(
    "client_factory,query_method",
    [
        (lambda http: QboVendorClient(realm_id="r1", http_client=http), "query_vendors"),
        (lambda http: QboCustomerClient(realm_id="r1", http_client=http), "query_customers"),
        (lambda http: QboItemClient(realm_id="r1", http_client=http), "query_items"),
        (lambda http: QboAccountClient(realm_id="r1", http_client=http), "query_accounts"),
        (lambda http: QboTermClient(realm_id="r1", http_client=http), "query_terms"),
    ],
    ids=["vendor", "customer", "item", "account", "term"],
)
def test_reference_client_queries_include_inactive_records(client_factory, query_method):
    _assert_active_in_both_branches(client_factory, query_method)


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


def _make_qbo_customer(**overrides: Any) -> QboCustomer:
    defaults = dict(
        id=1,
        public_id=None,
        row_version=None,
        created_datetime=None,
        modified_datetime=None,
        qbo_id="QBO-C-1",
        sync_token=None,
        realm_id="r1",
        display_name="Test Customer",
        title=None,
        given_name=None,
        middle_name=None,
        family_name=None,
        suffix=None,
        company_name=None,
        fully_qualified_name=None,
        level=None,
        parent_ref_value=None,
        parent_ref_name=None,
        job=False,
        active=None,
        primary_email_addr=None,
        primary_phone=None,
        mobile=None,
        fax=None,
        bill_addr_id=None,
        ship_addr_id=None,
        balance=None,
        balance_with_jobs=None,
        taxable=None,
        notes=None,
        print_on_check_name=None,
    )
    defaults.update(overrides)
    return QboCustomer(**defaults)


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
        description=None,
        active=None,
        type=None,
        parent_ref_value=None,
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


def _build_vendor_connector() -> VendorVendorConnector:
    connector = VendorVendorConnector(
        mapping_repo=Mock(),
        vendor_service=Mock(),
        vendor_address_service=Mock(),
        address_connector=Mock(),
        reconciliation_repo=Mock(),
    )
    connector.mapping_repo.read_by_qbo_vendor_id.return_value = None
    connector.vendor_service.read_by_name.return_value = None
    connector._sync_addresses = Mock()
    return connector


def _build_customer_connector() -> CustomerCustomerConnector:
    connector = CustomerCustomerConnector(
        mapping_repo=Mock(),
        customer_service=Mock(),
    )
    connector.mapping_repo.read_by_qbo_customer_id.return_value = None
    return connector


def _build_project_connector() -> CustomerProjectConnector:
    connector = CustomerProjectConnector(
        mapping_repo=Mock(),
        project_service=Mock(),
        customer_mapping_repo=Mock(),
        project_address_service=Mock(),
        address_connector=Mock(),
        reconciliation_repo=Mock(),
    )
    connector.mapping_repo.read_by_qbo_customer_id.return_value = None
    connector.project_service.read_by_name.return_value = None
    connector._sync_addresses = Mock()
    return connector


def _build_cost_code_connector() -> ItemCostCodeConnector:
    connector = ItemCostCodeConnector(
        mapping_repo=Mock(),
        cost_code_service=Mock(),
    )
    connector.mapping_repo.read_by_qbo_item_id.return_value = None
    connector.cost_code_service.read_by_number.return_value = None
    return connector


def _build_sub_cost_code_connector() -> ItemSubCostCodeConnector:
    connector = ItemSubCostCodeConnector(
        mapping_repo=Mock(),
        sub_cost_code_service=Mock(),
        cost_code_mapping_repo=Mock(),
        qbo_item_repo=Mock(),
    )
    connector.mapping_repo.read_by_qbo_item_id.return_value = None
    connector.sub_cost_code_service.repo = Mock()
    connector.sub_cost_code_service.repo.read_by_cost_code_id.return_value = []
    connector.qbo_item_repo.read_by_qbo_id.return_value = SimpleNamespace(id=99)
    connector.cost_code_mapping_repo.read_by_qbo_item_id.return_value = SimpleNamespace(
        cost_code_id=10
    )
    return connector


def _build_payment_term_connector() -> TermPaymentTermConnector:
    connector = TermPaymentTermConnector(
        mapping_repo=Mock(),
        payment_term_service=Mock(),
    )
    connector.mapping_repo.read_by_qbo_term_id.return_value = None
    return connector


@pytest.mark.parametrize(
    "connector_builder,sync_method,model_builder,model_kwargs",
    [
        (
            _build_vendor_connector,
            "sync_from_qbo_vendor",
            _make_qbo_vendor,
            {},
        ),
        (
            _build_customer_connector,
            "sync_from_qbo_customer",
            _make_qbo_customer,
            {"job": False},
        ),
        (
            _build_project_connector,
            "sync_from_qbo_customer",
            _make_qbo_customer,
            {"job": True},
        ),
        (
            _build_cost_code_connector,
            "sync_from_qbo_item",
            _make_qbo_item,
            {"parent_ref_value": None},
        ),
        (
            _build_sub_cost_code_connector,
            "sync_from_qbo_item",
            _make_qbo_item,
            {"parent_ref_value": "parent-qbo-id"},
        ),
        (
            _build_payment_term_connector,
            "sync_from_qbo_term",
            _make_qbo_term,
            {},
        ),
    ],
    ids=[
        "vendor",
        "customer",
        "project",
        "cost_code",
        "sub_cost_code",
        "payment_term",
    ],
)
def test_inactive_reference_record_raises_on_create_path(
    connector_builder,
    sync_method,
    model_builder,
    model_kwargs,
):
    connector = connector_builder()
    sync = getattr(connector, sync_method)
    model = model_builder(active=False, **model_kwargs)
    with pytest.raises(ValueError, match="inactive in QBO and has no local"):
        sync(model)


@pytest.mark.parametrize(
    "connector_builder,sync_method,model_builder,model_kwargs,service_attr",
    [
        (_build_vendor_connector, "sync_from_qbo_vendor", _make_qbo_vendor, {}, "vendor_service"),
        (_build_customer_connector, "sync_from_qbo_customer", _make_qbo_customer, {"job": False}, "customer_service"),
        (_build_project_connector, "sync_from_qbo_customer", _make_qbo_customer, {"job": True}, "project_service"),
        (_build_cost_code_connector, "sync_from_qbo_item", _make_qbo_item, {"parent_ref_value": None}, "cost_code_service"),
        (
            _build_sub_cost_code_connector,
            "sync_from_qbo_item",
            _make_qbo_item,
            {"parent_ref_value": "parent-qbo-id"},
            "sub_cost_code_service",
        ),
        (_build_payment_term_connector, "sync_from_qbo_term", _make_qbo_term, {}, "payment_term_service"),
    ],
    ids=[
        "vendor",
        "customer",
        "project",
        "cost_code",
        "sub_cost_code",
        "payment_term",
    ],
)
@pytest.mark.parametrize("active", [None, True])
def test_active_none_or_true_does_not_raise_deactivation_guard(
    connector_builder,
    sync_method,
    model_builder,
    model_kwargs,
    service_attr,
    active,
):
    connector = connector_builder()
    sync = getattr(connector, sync_method)
    model = model_builder(active=active, **model_kwargs)
    created = SimpleNamespace(id=100)
    getattr(connector, service_attr).create.return_value = created
    connector.create_mapping = Mock(return_value=SimpleNamespace(id=1))

    result = sync(model)
    assert result is created


def _capture_bill_pull_job(sync_side_effect):
    scheduler = Mock()
    jobs: list[tuple[Optional[str], Any]] = []
    scheduler.add_job = lambda fn, **kwargs: jobs.append((kwargs.get("id"), fn))

    with patch("scripts.sync_qbo_bill.sync_qbo_bill", side_effect=sync_side_effect):
        _register_qbo_pull_jobs(scheduler)

    for job_id, fn in jobs:
        if job_id == "qbo_sync_bill":
            return fn
    raise AssertionError("qbo_sync_bill job not found among registered jobs")


def test_scheduler_isolated_sets_system_admin_and_restores_on_success():
    admin_seen: list[Optional[bool]] = []

    def sync_fn():
        admin_seen.append(current_is_system_admin.get())
        return {"ok": True}

    run = _capture_bill_pull_job(sync_fn)
    set_authz_context(user_id=1, company_id=1, is_system_admin=False)
    asyncio.run(run())
    assert admin_seen == [True]
    assert current_is_system_admin.get() is False


def test_scheduler_isolated_sets_system_admin_and_restores_on_failure():
    admin_seen: list[Optional[bool]] = []

    def sync_fn():
        admin_seen.append(current_is_system_admin.get())
        raise RuntimeError("boom")

    run = _capture_bill_pull_job(sync_fn)
    set_authz_context(user_id=1, company_id=1, is_system_admin=False)
    asyncio.run(run())
    assert admin_seen == [True]
    assert current_is_system_admin.get() is False


def test_sync_qbo_vendorcredit_passes_attachment_kwargs_to_sync_qbo_to_local():
    source_path = SCRIPTS_DIR / "sync_qbo_vendorcredit.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))

    call_kwargs = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "sync_qbo_to_local":
            call_kwargs = {kw.arg for kw in node.keywords if kw.arg}
            break

    assert call_kwargs is not None
    assert "sync_attachments" in call_kwargs
    assert "attachable_service" in call_kwargs


def test_sync_qbo_vendorcredit_call_forwards_attachment_kwargs(monkeypatch):
    import sync_qbo_vendorcredit as module

    captured: dict[str, Any] = {}

    def fake_sync_qbo_to_local(**kwargs):
        captured.update(kwargs)
        outcome = Mock()
        outcome.failed_count = 0
        outcome.projected_count = 0
        outcome.skipped_ids = []
        outcome.projection_failed_ids = []
        outcome.staging_failed_ids = []
        return ({"vendor_credits_synced": 0}, outcome)

    monkeypatch.setattr(module, "sync_qbo_to_local", fake_sync_qbo_to_local)
    monkeypatch.setattr(module, "SyncService", Mock)
    monkeypatch.setattr(module, "QboVendorCreditService", Mock)
    monkeypatch.setattr(module, "VendorCreditBillCreditConnector", Mock)
    monkeypatch.setattr(module, "QboAuthService", lambda: Mock(read_all=Mock(return_value=[Mock(realm_id="r1")])))
    monkeypatch.setattr(
        module,
        "WatermarkRun",
        lambda *args, **kwargs: Mock(
            open=Mock(
                return_value=Mock(
                    query_start="2026-01-01T00:00:00+00:00",
                    last_sync_time=None,
                    commit=Mock(return_value=None),
                )
            )
        ),
    )
    attachable = object()
    monkeypatch.setattr(
        module,
        "QboAttachableService",
        Mock(return_value=attachable),
    )

    module.sync_qbo_vendorcredit(sync_attachments=True)

    assert captured.get("sync_attachments") is True
    assert captured.get("attachable_service") is attachable


def test_sync_qbo_to_local_defaults_attachments_off():
    """The inner default is the safe one (False), matching the bill sibling.

    The call site now passes `sync_attachments` explicitly, so this default only
    governs a future caller that omits it — pin it so a silent revert to True
    cannot re-enable attachment pulls for an omitting caller.
    """
    import inspect

    import sync_qbo_vendorcredit as module

    sig = inspect.signature(module.sync_qbo_to_local)
    assert sig.parameters["sync_attachments"].default is False
    assert sig.parameters["attachable_service"].default is None


def test_scheduler_isolated_restores_prior_context_in_callers_context(monkeypatch):
    """Assert the `finally` restore for real.

    The other two scheduler tests drive the job through `asyncio.to_thread`, which
    COPIES the context — so their post-run assertions hold whether or not the
    restore runs, and a deleted `finally` survives them. Here we capture the inner
    sync callable and invoke it directly in this test's own context, where a
    missing restore genuinely leaks system-admin to the caller.
    """
    admin_seen: list[Optional[bool]] = []

    def sync_fn():
        admin_seen.append(current_is_system_admin.get())
        return {"ok": True}

    run = _capture_bill_pull_job(sync_fn)

    captured: dict[str, Any] = {}

    async def _capture_only(fn, *args, **kwargs):
        captured["fn"] = fn
        return None

    monkeypatch.setattr(asyncio, "to_thread", _capture_only)
    asyncio.run(run())
    inner = captured.get("fn")
    assert inner is not None, "did not capture the scheduler's inner sync callable"

    set_authz_context(user_id=7, company_id=3, is_system_admin=False)
    inner()

    assert admin_seen == [True]
    assert current_is_system_admin.get() is False
    assert current_user_id.get() == 7
    assert current_company_id.get() == 3


@pytest.mark.parametrize(
    "connector_builder,sync_method,model_builder,model_kwargs,service_attr,lookup_attr",
    [
        (_build_vendor_connector, "sync_from_qbo_vendor", _make_qbo_vendor, {},
         "vendor_service", "read_by_name"),
        (_build_project_connector, "sync_from_qbo_customer", _make_qbo_customer, {"job": True},
         "project_service", "read_by_name"),
        (_build_cost_code_connector, "sync_from_qbo_item", _make_qbo_item, {"parent_ref_value": None},
         "cost_code_service", "read_by_number"),
        (_build_sub_cost_code_connector, "sync_from_qbo_item", _make_qbo_item,
         {"parent_ref_value": "parent-qbo-id"}, "sub_cost_code_service", "read_by_cost_code_id"),
    ],
    ids=["vendor", "project", "cost_code", "sub_cost_code"],
)
def test_inactive_unmapped_record_is_not_adopted_onto_a_live_local_row(
    connector_builder,
    sync_method,
    model_builder,
    model_kwargs,
    service_attr,
    lookup_attr,
):
    """Regression for the Codex P1: the guard must cover ADOPT, not just CREATE.

    These four connectors resolve an unmapped QBO record against an existing local
    row before creating one. The item connectors match on the parsed NUMBER, which
    survives QBO's " (deleted)" rename — so an inactive item like "01 Permits (deleted)"
    would match the live local CostCode "01", overwrite its name/description with the
    dead record's, and bind the mapping to it. Guarding only the create branch left
    that path wide open, and the original tests missed it because their builders pin
    the adopt lookup to None.
    """
    connector = connector_builder()
    live_row = SimpleNamespace(id=42, name="Live Local Row", description="keep me", number="01")
    service = getattr(connector, service_attr)
    getattr(service, lookup_attr).return_value = (
        [live_row] if lookup_attr == "read_by_cost_code_id" else live_row
    )
    connector.create_mapping = Mock()
    # Pin the reverse-mapping lookups to None so the connector would take the
    # genuine ADOPT-AND-BIND path without the guard. A bare Mock returns a truthy
    # object here, which would divert vendor/project into their "already bound to a
    # different QBO record" branch and prove something weaker than we mean to.
    for reverse in ("read_by_vendor_id", "read_by_project_id"):
        if hasattr(connector.mapping_repo, reverse):
            getattr(connector.mapping_repo, reverse).return_value = None

    model = model_builder(active=False, **model_kwargs)
    with pytest.raises(ValueError, match="inactive in QBO and has no local"):
        getattr(connector, sync_method)(model)

    # the live local row must be untouched and unbound
    assert live_row.name == "Live Local Row"
    assert live_row.description == "keep me"
    service.repo.update_by_id.assert_not_called()
    service.create.assert_not_called()
    connector.create_mapping.assert_not_called()


def _vendorcredit_sync_mocks(monkeypatch):
    """Shared monkeypatch setup for sync_qbo_vendorcredit attachment-forwarding tests."""
    import sync_qbo_vendorcredit as module

    captured: dict[str, Any] = {}

    def fake_sync_qbo_to_local(**kwargs):
        captured.update(kwargs)
        outcome = Mock()
        outcome.failed_count = 0
        outcome.projected_count = 0
        outcome.skipped_ids = []
        outcome.projection_failed_ids = []
        outcome.staging_failed_ids = []
        return ({"vendor_credits_synced": 0}, outcome)

    monkeypatch.setattr(module, "sync_qbo_to_local", fake_sync_qbo_to_local)
    monkeypatch.setattr(module, "SyncService", Mock)
    monkeypatch.setattr(module, "QboVendorCreditService", Mock)
    monkeypatch.setattr(module, "VendorCreditBillCreditConnector", Mock)
    monkeypatch.setattr(module, "QboAuthService", lambda: Mock(read_all=Mock(return_value=[Mock(realm_id="r1")])))
    monkeypatch.setattr(
        module,
        "WatermarkRun",
        lambda *args, **kwargs: Mock(
            open=Mock(
                return_value=Mock(
                    query_start="2026-01-01T00:00:00+00:00",
                    last_sync_time=None,
                    commit=Mock(return_value=None),
                )
            )
        ),
    )
    attachable = object()
    monkeypatch.setattr(
        module,
        "QboAttachableService",
        Mock(return_value=attachable),
    )
    return module, captured, attachable


@pytest.mark.parametrize(
    "env_value,expected_sync_attachments",
    [
        (None, True),
        ("false", False),
        ("FALSE", False),
        (" false ", False),
        ("true", True),
    ],
    ids=["unset", "false", "FALSE", "whitespace_false", "true"],
)
def test_vendorcredit_attachment_kill_switch_env(
    monkeypatch, env_value, expected_sync_attachments
):
    """QBO_VENDORCREDIT_SYNC_ATTACHMENTS=false forces attachments off; unset/true leave default on."""
    if env_value is None:
        monkeypatch.delenv("QBO_VENDORCREDIT_SYNC_ATTACHMENTS", raising=False)
    else:
        monkeypatch.setenv("QBO_VENDORCREDIT_SYNC_ATTACHMENTS", env_value)

    module, captured, attachable = _vendorcredit_sync_mocks(monkeypatch)
    module.sync_qbo_vendorcredit(sync_attachments=True)

    assert captured.get("sync_attachments") is expected_sync_attachments
    if expected_sync_attachments:
        assert captured.get("attachable_service") is attachable
    else:
        assert captured.get("attachable_service") is None


def test_vendorcredit_attachment_kill_switch_is_one_way(monkeypatch):
    """Kill switch can only disable — caller sync_attachments=False stays off when unset."""
    monkeypatch.delenv("QBO_VENDORCREDIT_SYNC_ATTACHMENTS", raising=False)

    module, captured, _attachable = _vendorcredit_sync_mocks(monkeypatch)
    module.sync_qbo_vendorcredit(sync_attachments=False)

    assert captured.get("sync_attachments") is False
    assert captured.get("attachable_service") is None


@pytest.mark.parametrize(
    "connector_builder,sync_method,model_builder,model_kwargs,setup_mapped",
    [
        (
            _build_customer_connector,
            "sync_from_qbo_customer",
            _make_qbo_customer,
            {"job": False, "display_name": "Curated Customer (deleted)"},
            lambda c: _setup_customer_mapped(c, "Curated Customer"),
        ),
        (
            _build_project_connector,
            "sync_from_qbo_customer",
            _make_qbo_customer,
            {"job": True, "display_name": "My Project (deleted)"},
            lambda c: _setup_project_mapped(c, "My Project"),
        ),
        (
            _build_cost_code_connector,
            "sync_from_qbo_item",
            _make_qbo_item,
            {"parent_ref_value": None, "name": "01 Permits (deleted)"},
            lambda c: _setup_cost_code_mapped(c, "Permits"),
        ),
        (
            _build_sub_cost_code_connector,
            "sync_from_qbo_item",
            _make_qbo_item,
            {"parent_ref_value": "parent-qbo-id", "name": "01.1 Sub Item (deleted)"},
            lambda c: _setup_sub_cost_code_mapped(c, "Sub Item"),
        ),
        (
            _build_payment_term_connector,
            "sync_from_qbo_term",
            _make_qbo_term,
            {"name": "Net 30 (deleted)"},
            lambda c: _setup_payment_term_mapped(c, "Net 30"),
        ),
    ],
    ids=["customer", "project", "cost_code", "sub_cost_code", "payment_term"],
)
def test_mapped_path_preserves_non_blank_local_name_when_qbo_renamed_deleted(
    connector_builder,
    sync_method,
    model_builder,
    model_kwargs,
    setup_mapped,
):
    """Mapped-path UPDATE must not clobber a curated local name with QBO's (deleted) suffix."""
    connector = connector_builder()
    local_name = setup_mapped(connector)
    model = model_builder(active=False, **model_kwargs)
    result = getattr(connector, sync_method)(model)
    assert result.name == local_name


@pytest.mark.parametrize(
    "connector_builder,sync_method,model_builder,model_kwargs,setup_mapped,incoming_name",
    [
        (
            _build_customer_connector,
            "sync_from_qbo_customer",
            _make_qbo_customer,
            {"job": False},
            lambda c: _setup_customer_mapped(c, ""),
            "Fresh Customer (deleted)",
        ),
        (
            _build_project_connector,
            "sync_from_qbo_customer",
            _make_qbo_customer,
            {"job": True},
            lambda c: _setup_project_mapped(c, ""),
            "Fresh Project (deleted)",
        ),
        (
            _build_cost_code_connector,
            "sync_from_qbo_item",
            _make_qbo_item,
            {"parent_ref_value": None},
            lambda c: _setup_cost_code_mapped(c, ""),
            "02 Fresh Item (deleted)",
        ),
        (
            _build_sub_cost_code_connector,
            "sync_from_qbo_item",
            _make_qbo_item,
            {"parent_ref_value": "parent-qbo-id"},
            lambda c: _setup_sub_cost_code_mapped(c, ""),
            "02.1 Fresh Sub (deleted)",
        ),
        (
            _build_payment_term_connector,
            "sync_from_qbo_term",
            _make_qbo_term,
            {},
            lambda c: _setup_payment_term_mapped(c, ""),
            "Net 45 (deleted)",
        ),
    ],
    ids=["customer", "project", "cost_code", "sub_cost_code", "payment_term"],
)
def test_mapped_path_fills_blank_local_name_from_qbo_deleted_name(
    connector_builder,
    sync_method,
    model_builder,
    model_kwargs,
    setup_mapped,
    incoming_name,
):
    """Blank stored name on the mapped path takes the incoming QBO (deleted) name."""
    connector = connector_builder()
    setup_mapped(connector)
    model = model_builder(**{**model_kwargs, **_qbo_name_field(model_builder, incoming_name)})
    result = getattr(connector, sync_method)(model)
    if model_builder is _make_qbo_item and " " in incoming_name:
        expected = incoming_name.split(" ", 1)[1]
    else:
        expected = incoming_name
    assert result.name == expected


def _qbo_name_field(model_builder, incoming_name: str) -> dict[str, str]:
    if model_builder is _make_qbo_term:
        return {"name": incoming_name}
    if model_builder is _make_qbo_item:
        return {"name": incoming_name}
    return {"display_name": incoming_name}


def _setup_customer_mapped(connector: CustomerCustomerConnector, local_name: str) -> str:
    mapping = SimpleNamespace(id=1, customer_id=10)
    customer = SimpleNamespace(id=10, name=local_name, email="", phone="")
    connector.mapping_repo.read_by_qbo_customer_id.return_value = mapping
    connector.customer_service.read_by_id.return_value = customer
    connector.customer_service.repo.update_by_id.side_effect = lambda c: c
    return local_name


def _setup_project_mapped(connector: CustomerProjectConnector, local_name: str) -> str:
    mapping = SimpleNamespace(id=1, project_id=10)
    project = SimpleNamespace(
        id=10, name=local_name, description="", status="active", customer_id=None
    )
    connector.mapping_repo.read_by_qbo_customer_id.return_value = mapping
    connector.project_service.read_by_id.return_value = project
    connector.project_service.repo.update_by_id.side_effect = lambda p: p
    return local_name


def _setup_cost_code_mapped(connector: ItemCostCodeConnector, local_name: str) -> str:
    mapping = SimpleNamespace(id=1, cost_code_id=10)
    cost_code = SimpleNamespace(id=10, number="01", name=local_name, description="")
    connector.mapping_repo.read_by_qbo_item_id.return_value = mapping
    connector.cost_code_service.read_by_id.return_value = cost_code
    connector.cost_code_service.repo.update_by_id.side_effect = lambda c: c
    return local_name


def _setup_sub_cost_code_mapped(connector: ItemSubCostCodeConnector, local_name: str) -> str:
    mapping = SimpleNamespace(id=1, sub_cost_code_id=10)
    sub_cost_code = SimpleNamespace(
        id=10, number="01.1", name=local_name, description="", cost_code_id=10
    )
    connector.mapping_repo.read_by_qbo_item_id.return_value = mapping
    connector.sub_cost_code_service.read_by_id.return_value = sub_cost_code
    connector.sub_cost_code_service.repo.update_by_id.side_effect = lambda s: s
    return local_name


def _setup_payment_term_mapped(connector: TermPaymentTermConnector, local_name: str) -> str:
    mapping = SimpleNamespace(id=1, payment_term_id=10)
    payment_term = SimpleNamespace(
        id=10,
        name=local_name,
        description=None,
        discount_percent=None,
        discount_days=None,
        due_days=30,
    )
    connector.mapping_repo.read_by_qbo_term_id.return_value = mapping
    connector.payment_term_service.read_by_id.return_value = payment_term
    connector.payment_term_service.repo.update_by_id.side_effect = lambda p: p
    return local_name
