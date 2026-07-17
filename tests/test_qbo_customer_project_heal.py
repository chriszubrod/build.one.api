"""Pure-logic tests for CustomerProject heal-don't-delete mapping fixes (U-022)
and QBO customer-ref realm-scoping on line-item project resolvers (U-060)."""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from integrations.intuit.qbo.customer.connector.project.business.model import CustomerProject
from integrations.intuit.qbo.customer.connector.project.business.service import CustomerProjectConnector
from integrations.intuit.qbo.invoice.connector.invoice.business.service import InvoiceInvoiceConnector
from integrations.intuit.qbo.bill.connector.bill_line_item.business.service import BillLineItemConnector
from integrations.intuit.qbo.purchase.connector.expense_line_item.business.service import (
    PurchaseLineExpenseLineItemConnector,
)
from integrations.intuit.qbo.vendorcredit.connector.bill_credit_line_item.business.service import (
    VendorCreditLineItemConnector,
)

# The invoice connector imports CustomerProjectConnector lazily from its defining module,
# so the heal auto-heal path is patched where the class is defined.
HEAL_CONNECTOR_PATH = (
    "integrations.intuit.qbo.customer.connector.project.business.service.CustomerProjectConnector"
)


def _make_qbo_customer(
    *,
    customer_id=1,
    qbo_id="QBO-100",
    display_name="OHR2 - Chapel",
    company_name=None,
    is_job=True,
    active=True,
    notes="",
    realm_id="realm-1",
    parent_ref_value=None,
    bill_addr_id=None,
    ship_addr_id=None,
):
    return SimpleNamespace(
        id=customer_id,
        qbo_id=qbo_id,
        display_name=display_name,
        company_name=company_name,
        is_job=is_job,
        active=active,
        notes=notes,
        realm_id=realm_id,
        parent_ref_value=parent_ref_value,
        bill_addr_id=bill_addr_id,
        ship_addr_id=ship_addr_id,
    )


def _make_mapping(*, mapping_id=10, project_id=100, qbo_customer_id=1):
    return CustomerProject(
        id=mapping_id,
        public_id="map-pub-10",
        row_version=None,
        created_datetime=None,
        modified_datetime=None,
        project_id=project_id,
        qbo_customer_id=qbo_customer_id,
    )


def _make_project(
    *,
    project_id=200,
    public_id="proj-pub-200",
    name="OHR2 - Chapel",
    description="",
    status="active",
    customer_id=None,
):
    return SimpleNamespace(
        id=project_id,
        public_id=public_id,
        name=name,
        description=description,
        status=status,
        customer_id=customer_id,
    )


def _build_customer_project_connector():
    mapping_repo = Mock()
    project_service = Mock()
    project_service.repo = Mock()
    reconciliation_repo = Mock()
    connector = CustomerProjectConnector(
        mapping_repo=mapping_repo,
        project_service=project_service,
        project_address_service=Mock(),
        address_connector=Mock(),
        customer_mapping_repo=Mock(),
        reconciliation_repo=reconciliation_repo,
    )
    connector._sync_addresses = Mock()
    return connector, mapping_repo, project_service, reconciliation_repo


def _build_invoice_connector(**overrides):
    connector = InvoiceInvoiceConnector(
        mapping_repo=Mock(),
        line_mapping_repo=Mock(),
        invoice_service=Mock(),
        project_service=Mock(),
        qbo_customer_repo=Mock(),
        customer_project_repo=Mock(),
    )
    for key, value in overrides.items():
        setattr(connector, key, value)
    return connector


def _build_purchase_line_connector(**overrides):
    connector = PurchaseLineExpenseLineItemConnector(
        mapping_repo=Mock(),
        expense_line_item_service=Mock(),
        item_sub_cost_code_repo=Mock(),
        qbo_item_repo=Mock(),
        customer_project_repo=Mock(),
        qbo_customer_repo=Mock(),
    )
    for key, value in overrides.items():
        setattr(connector, key, value)
    return connector


def _build_vendor_credit_line_connector(**overrides):
    connector = VendorCreditLineItemConnector()
    for key, value in overrides.items():
        setattr(connector, key, value)
    return connector


def _build_bill_line_connector(**overrides):
    connector = BillLineItemConnector(
        qbo_customer_repo=Mock(),
        customer_project_repo=Mock(),
        project_service=Mock(),
    )
    for key, value in overrides.items():
        setattr(connector, key, value)
    return connector


# --- PART 1: CustomerProjectConnector.sync_from_qbo_customer ---


def test_heal_repoints_mapping_when_project_missing_but_name_match_unbound():
    """(a) Missing project + name match (unbound) repoints mapping in place."""
    connector, mapping_repo, project_service, _ = _build_customer_project_connector()
    qbo_customer = _make_qbo_customer()
    mapping = _make_mapping(project_id=999)
    replacement = _make_project(project_id=200)

    mapping_repo.read_by_qbo_customer_id.return_value = mapping
    project_service.read_by_id.return_value = None
    project_service.read_by_name.return_value = replacement
    mapping_repo.read_by_project_id.return_value = None
    project_service.repo.update_by_id.side_effect = lambda p: p

    result = connector.sync_from_qbo_customer(qbo_customer)

    assert result is replacement
    assert mapping.project_id == 200
    mapping_repo.update_by_id.assert_called_once_with(mapping)
    mapping_repo.delete_by_id.assert_not_called()
    project_service.create.assert_not_called()


def test_heal_raises_and_records_issue_when_project_missing_and_name_miss():
    """(b) Missing project + no name match raises ValueError and records orphaned mapping."""
    connector, mapping_repo, project_service, reconciliation_repo = _build_customer_project_connector()
    qbo_customer = _make_qbo_customer()
    mapping = _make_mapping(project_id=999)

    mapping_repo.read_by_qbo_customer_id.return_value = mapping
    project_service.read_by_id.return_value = None
    project_service.read_by_name.return_value = None

    with pytest.raises(ValueError, match="preserving mapping, skipping"):
        connector.sync_from_qbo_customer(qbo_customer)

    reconciliation_repo.create.assert_called_once()
    call_kwargs = reconciliation_repo.create.call_args.kwargs
    assert call_kwargs["drift_type"] == "orphaned_customer_project_mapping"
    mapping_repo.delete_by_id.assert_not_called()
    project_service.create.assert_not_called()


def test_happy_path_mapping_exists_project_found_updates_normally():
    """(c) Existing mapping + project found follows normal update path."""
    connector, mapping_repo, project_service, reconciliation_repo = _build_customer_project_connector()
    qbo_customer = _make_qbo_customer()
    mapping = _make_mapping(project_id=200)
    project = _make_project(project_id=200)

    mapping_repo.read_by_qbo_customer_id.return_value = mapping
    project_service.read_by_id.return_value = project
    project_service.repo.update_by_id.side_effect = lambda p: p

    result = connector.sync_from_qbo_customer(qbo_customer)

    assert result is project
    reconciliation_repo.create.assert_not_called()
    mapping_repo.delete_by_id.assert_not_called()
    mapping_repo.update_by_id.assert_not_called()
    project_service.create.assert_not_called()


def test_heal_duplicate_qbo_customer_when_replacement_bound_to_other():
    """(d) Name match finds Project bound to a different QboCustomer — record duplicate, no mutate."""
    connector, mapping_repo, project_service, reconciliation_repo = _build_customer_project_connector()
    qbo_customer = _make_qbo_customer(customer_id=1)
    mapping = _make_mapping(project_id=999, qbo_customer_id=1)
    replacement = _make_project(project_id=200)
    other_mapping = _make_mapping(mapping_id=20, project_id=200, qbo_customer_id=99)

    mapping_repo.read_by_qbo_customer_id.return_value = mapping
    project_service.read_by_id.return_value = None
    project_service.read_by_name.return_value = replacement
    mapping_repo.read_by_project_id.return_value = other_mapping

    result = connector.sync_from_qbo_customer(qbo_customer)

    assert result is replacement
    reconciliation_repo.create.assert_called_once()
    call_kwargs = reconciliation_repo.create.call_args.kwargs
    assert call_kwargs["drift_type"] == "duplicate_qbo_customer"
    mapping_repo.update_by_id.assert_not_called()
    mapping_repo.delete_by_id.assert_not_called()
    project_service.create.assert_not_called()


# --- PART 2: InvoiceInvoiceConnector._get_project_public_id ---


def test_get_project_public_id_auto_heals_missing_mapping():
    """(a) Missing CustomerProject mapping auto-heals via name match and returns public_id."""
    qbo_customer = _make_qbo_customer()
    healed_project = _make_project(public_id="healed-pub-id")

    mapping_repo = Mock()
    mapping_repo.read_by_project_id.return_value = None
    mapping_repo.read_by_qbo_customer_id.return_value = None
    mapping_repo.create.return_value = _make_mapping(project_id=healed_project.id)

    project_service = Mock()
    project_service.read_by_name.return_value = healed_project

    heal_connector = CustomerProjectConnector(
        mapping_repo=mapping_repo,
        project_service=project_service,
        project_address_service=Mock(),
        address_connector=Mock(),
        customer_mapping_repo=Mock(),
        reconciliation_repo=Mock(),
    )
    heal_connector._sync_addresses = Mock()

    qbo_customer_repo = Mock()
    qbo_customer_repo.read_by_qbo_id.return_value = qbo_customer

    customer_project_repo = Mock()
    customer_project_repo.read_by_qbo_customer_id.return_value = None

    invoice_connector = _build_invoice_connector(
        qbo_customer_repo=qbo_customer_repo,
        customer_project_repo=customer_project_repo,
    )

    with patch(HEAL_CONNECTOR_PATH, return_value=heal_connector):
        result = invoice_connector._get_project_public_id("QBO-100")

    assert result == "healed-pub-id"
    mapping_repo.create.assert_called_once_with(
        project_id=healed_project.id,
        qbo_customer_id=qbo_customer.id,
    )


def test_get_project_public_id_returns_none_when_heal_cannot_resolve():
    """(b-i) _get_project_public_id returns None when heal cannot resolve a local Project."""
    qbo_customer = _make_qbo_customer()

    mapping_repo = Mock()
    project_service = Mock()
    project_service.read_by_name.return_value = None

    heal_connector = CustomerProjectConnector(
        mapping_repo=mapping_repo,
        project_service=project_service,
        project_address_service=Mock(),
        address_connector=Mock(),
        customer_mapping_repo=Mock(),
        reconciliation_repo=Mock(),
    )

    qbo_customer_repo = Mock()
    qbo_customer_repo.read_by_qbo_id.return_value = qbo_customer

    customer_project_repo = Mock()
    customer_project_repo.read_by_qbo_customer_id.return_value = None

    invoice_connector = _build_invoice_connector(
        qbo_customer_repo=qbo_customer_repo,
        customer_project_repo=customer_project_repo,
    )

    with patch(HEAL_CONNECTOR_PATH, return_value=heal_connector):
        result = invoice_connector._get_project_public_id("QBO-100")

    assert result is None
    mapping_repo.create.assert_not_called()


def test_sync_from_qbo_invoice_raises_when_project_public_id_unresolvable():
    """(b-ii) sync_from_qbo_invoice fails loud when project binding cannot be resolved."""
    invoice_connector = _build_invoice_connector()
    invoice_connector._get_project_public_id = Mock(return_value=None)

    qbo_invoice = SimpleNamespace(
        id=50,
        qbo_id="INV-50",
        customer_ref_value="QBO-100",
        realm_id="realm-1",
        doc_number="1001",
        txn_date="2026-07-01",
        due_date="2026-07-31",
        private_note=None,
        total_amt=Decimal("1000.00"),
    )

    with pytest.raises(ValueError, match="No project mapping found for QBO customer ref"):
        invoice_connector.sync_from_qbo_invoice(qbo_invoice, [])


# --- PART 3: Code-review follow-up guards ---


def test_heal_missing_mapping_rejects_non_job_customer():
    """Non-job (top-level) QboCustomer must not be name-bound to a Project."""
    connector, mapping_repo, project_service, _ = _build_customer_project_connector()
    qbo_customer = _make_qbo_customer(is_job=False)
    matching_project = _make_project()

    project_service.read_by_name.return_value = matching_project

    result = connector.heal_missing_mapping(qbo_customer)

    assert result is None
    mapping_repo.create.assert_not_called()
    project_service.read_by_name.assert_not_called()


def test_get_project_public_id_uses_realm_scoped_lookup_when_realm_given():
    """Realm-scoped customer lookup when realm_id is provided."""
    qbo_customer = _make_qbo_customer()
    healed_project = _make_project(public_id="healed-pub-id")

    qbo_customer_repo = Mock()
    qbo_customer_repo.read_by_qbo_id_and_realm_id.return_value = qbo_customer
    qbo_customer_repo.read_by_qbo_id.return_value = qbo_customer

    customer_project_repo = Mock()
    customer_project_repo.read_by_qbo_customer_id.return_value = None

    invoice_connector = _build_invoice_connector(
        qbo_customer_repo=qbo_customer_repo,
        customer_project_repo=customer_project_repo,
    )

    with patch(HEAL_CONNECTOR_PATH) as mock_connector_cls:
        mock_connector_cls.return_value.heal_missing_mapping.return_value = healed_project
        result = invoice_connector._get_project_public_id("QBO-100", "realm-1")

    assert result == "healed-pub-id"
    qbo_customer_repo.read_by_qbo_id_and_realm_id.assert_called_once_with("QBO-100", "realm-1")
    qbo_customer_repo.read_by_qbo_id.assert_not_called()


def test_get_project_public_id_realm_miss_returns_none_without_heal():
    """Realm miss returns None without attempting heal or mapping lookup."""
    qbo_customer_repo = Mock()
    qbo_customer_repo.read_by_qbo_id_and_realm_id.return_value = None

    customer_project_repo = Mock()

    invoice_connector = _build_invoice_connector(
        qbo_customer_repo=qbo_customer_repo,
        customer_project_repo=customer_project_repo,
    )

    with patch(HEAL_CONNECTOR_PATH) as mock_connector_cls:
        result = invoice_connector._get_project_public_id("QBO-100", "realm-1")

    assert result is None
    customer_project_repo.read_by_qbo_customer_id.assert_not_called()
    mock_connector_cls.assert_not_called()


# --- PART 4: PurchaseLineExpenseLineItemConnector._get_project_public_id ---


def test_purchase_get_project_public_id_uses_realm_scoped_lookup_when_realm_given():
    """Realm-scoped customer lookup when realm_id is provided."""
    qbo_customer = _make_qbo_customer()
    project = _make_project(public_id="proj-pub-200")
    mapping = _make_mapping(project_id=project.id)

    qbo_customer_repo = Mock()
    qbo_customer_repo.read_by_qbo_id_and_realm_id.return_value = qbo_customer
    qbo_customer_repo.read_by_qbo_id.return_value = qbo_customer

    customer_project_repo = Mock()
    customer_project_repo.read_by_qbo_customer_id.return_value = mapping

    connector = _build_purchase_line_connector(
        qbo_customer_repo=qbo_customer_repo,
        customer_project_repo=customer_project_repo,
    )

    with patch(
        "integrations.intuit.qbo.purchase.connector.expense_line_item.business.service.ProjectService"
    ) as mock_project_svc:
        mock_project_svc.return_value.read_by_id.return_value = project
        result = connector._get_project_public_id("QBO-100", "realm-1")

    assert result == "proj-pub-200"
    qbo_customer_repo.read_by_qbo_id_and_realm_id.assert_called_once_with("QBO-100", "realm-1")
    qbo_customer_repo.read_by_qbo_id.assert_not_called()


def test_purchase_get_project_public_id_falls_back_to_unscoped_lookup_without_realm():
    """No realm_id falls back to read_by_qbo_id for back-compat."""
    qbo_customer = _make_qbo_customer()
    project = _make_project(public_id="proj-pub-200")
    mapping = _make_mapping(project_id=project.id)

    qbo_customer_repo = Mock()
    qbo_customer_repo.read_by_qbo_id.return_value = qbo_customer

    customer_project_repo = Mock()
    customer_project_repo.read_by_qbo_customer_id.return_value = mapping

    connector = _build_purchase_line_connector(
        qbo_customer_repo=qbo_customer_repo,
        customer_project_repo=customer_project_repo,
    )

    with patch(
        "integrations.intuit.qbo.purchase.connector.expense_line_item.business.service.ProjectService"
    ) as mock_project_svc:
        mock_project_svc.return_value.read_by_id.return_value = project
        result = connector._get_project_public_id("QBO-100")

    assert result == "proj-pub-200"
    qbo_customer_repo.read_by_qbo_id.assert_called_once_with("QBO-100")
    qbo_customer_repo.read_by_qbo_id_and_realm_id.assert_not_called()


# --- PART 5: VendorCreditLineItemConnector._get_project_public_id ---

QBO_CUSTOMER_REPO_PATH = "integrations.intuit.qbo.customer.persistence.repo.QboCustomerRepository"
CUSTOMER_PROJECT_REPO_PATH = (
    "integrations.intuit.qbo.customer.connector.project.persistence.repo.CustomerProjectRepository"
)


def test_vendorcredit_get_project_public_id_uses_realm_scoped_lookup_when_realm_given():
    """Realm-scoped customer lookup when realm_id is provided."""
    qbo_customer = _make_qbo_customer()
    project = _make_project(public_id="proj-pub-200")
    mapping = SimpleNamespace(project_id=project.id)

    qbo_customer_repo = Mock()
    qbo_customer_repo.read_by_qbo_id_and_realm_id.return_value = qbo_customer
    qbo_customer_repo.read_by_qbo_id.return_value = qbo_customer

    customer_project_repo = Mock()
    customer_project_repo.read_by_qbo_customer_id.return_value = mapping

    connector = _build_vendor_credit_line_connector(
        project_service=Mock(read_by_id=Mock(return_value=project)),
    )

    with patch(QBO_CUSTOMER_REPO_PATH, return_value=qbo_customer_repo), patch(
        CUSTOMER_PROJECT_REPO_PATH, return_value=customer_project_repo
    ):
        result = connector._get_project_public_id("QBO-100", "realm-1")

    assert result == "proj-pub-200"
    qbo_customer_repo.read_by_qbo_id_and_realm_id.assert_called_once_with("QBO-100", "realm-1")
    qbo_customer_repo.read_by_qbo_id.assert_not_called()


def test_vendorcredit_get_project_public_id_falls_back_to_unscoped_lookup_without_realm():
    """No realm_id falls back to read_by_qbo_id for back-compat."""
    qbo_customer = _make_qbo_customer()
    project = _make_project(public_id="proj-pub-200")
    mapping = SimpleNamespace(project_id=project.id)

    qbo_customer_repo = Mock()
    qbo_customer_repo.read_by_qbo_id.return_value = qbo_customer

    customer_project_repo = Mock()
    customer_project_repo.read_by_qbo_customer_id.return_value = mapping

    connector = _build_vendor_credit_line_connector(
        project_service=Mock(read_by_id=Mock(return_value=project)),
    )

    with patch(QBO_CUSTOMER_REPO_PATH, return_value=qbo_customer_repo), patch(
        CUSTOMER_PROJECT_REPO_PATH, return_value=customer_project_repo
    ):
        result = connector._get_project_public_id("QBO-100")

    assert result == "proj-pub-200"
    qbo_customer_repo.read_by_qbo_id.assert_called_once_with("QBO-100")
    qbo_customer_repo.read_by_qbo_id_and_realm_id.assert_not_called()


# --- PART 6: BillLineItemConnector._get_project_public_id ---


def test_bill_get_project_public_id_uses_realm_scoped_lookup_when_realm_given():
    """Realm-scoped customer lookup when realm_id is provided."""
    qbo_customer = _make_qbo_customer()
    project = _make_project(public_id="proj-pub-200")
    mapping = _make_mapping(project_id=project.id)

    qbo_customer_repo = Mock()
    qbo_customer_repo.read_by_qbo_id_and_realm_id.return_value = qbo_customer
    qbo_customer_repo.read_by_qbo_id.return_value = qbo_customer

    customer_project_repo = Mock()
    customer_project_repo.read_by_qbo_customer_id.return_value = mapping

    project_service = Mock()
    project_service.read_by_id.return_value = project

    connector = _build_bill_line_connector(
        qbo_customer_repo=qbo_customer_repo,
        customer_project_repo=customer_project_repo,
        project_service=project_service,
    )

    result = connector._get_project_public_id("QBO-100", "realm-1")

    assert result == "proj-pub-200"
    qbo_customer_repo.read_by_qbo_id_and_realm_id.assert_called_once_with("QBO-100", "realm-1")
    qbo_customer_repo.read_by_qbo_id.assert_not_called()


def test_bill_get_project_public_id_falls_back_to_unscoped_lookup_without_realm():
    """No realm_id falls back to read_by_qbo_id for back-compat."""
    qbo_customer = _make_qbo_customer()
    project = _make_project(public_id="proj-pub-200")
    mapping = _make_mapping(project_id=project.id)

    qbo_customer_repo = Mock()
    qbo_customer_repo.read_by_qbo_id.return_value = qbo_customer

    customer_project_repo = Mock()
    customer_project_repo.read_by_qbo_customer_id.return_value = mapping

    project_service = Mock()
    project_service.read_by_id.return_value = project

    connector = _build_bill_line_connector(
        qbo_customer_repo=qbo_customer_repo,
        customer_project_repo=customer_project_repo,
        project_service=project_service,
    )

    result = connector._get_project_public_id("QBO-100")

    assert result == "proj-pub-200"
    qbo_customer_repo.read_by_qbo_id.assert_called_once_with("QBO-100")
    qbo_customer_repo.read_by_qbo_id_and_realm_id.assert_not_called()
