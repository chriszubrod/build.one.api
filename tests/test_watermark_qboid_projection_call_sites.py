"""Mutation-proof tests: every sync_qbo_*.py projection loop records the real qbo_id.

See docs/design/watermark-qboid-projection.md — each test builds a staging object
with a distinct internal .id (staging PK) and .qbo_id, forces projection failure,
and asserts projection_failed_ids carries .qbo_id only.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from integrations.intuit.qbo.base.sync_outcome import SyncOutcome
from integrations.intuit.qbo.bill.business.model import QboBill
from integrations.intuit.qbo.company_info.business.model import QboCompanyInfo
from integrations.intuit.qbo.customer.business.model import QboCustomer
from integrations.intuit.qbo.invoice.business.model import QboInvoice
from integrations.intuit.qbo.purchase.business.model import QboPurchase
from integrations.intuit.qbo.term.business.model import QboTerm
from integrations.intuit.qbo.vendor.business.model import QboVendor
from integrations.intuit.qbo.vendorcredit.business.model import QboVendorCredit
from scripts import sync_qbo_bill as bill_module
from scripts import sync_qbo_company_info as company_info_module
from scripts import sync_qbo_customer as customer_module
from scripts import sync_qbo_invoice as invoice_module
from scripts import sync_qbo_purchase as purchase_module
from scripts import sync_qbo_term as term_module
from scripts import sync_qbo_vendor as vendor_module
from scripts import sync_qbo_vendorcredit as vendorcredit_module

REALM_ID = "realm-test"
STAGING_PK = 501


def _retry_calls_fn(fn, *args, **kwargs):
    return fn(*args)


def _make_qbo_bill(*, staging_pk=STAGING_PK, qbo_id="QB-BILL-501"):
    return QboBill(
        id=staging_pk,
        public_id="11111111-1111-1111-1111-111111111111",
        row_version=None,
        created_datetime=None,
        modified_datetime=None,
        qbo_id=qbo_id,
        sync_token="0",
        realm_id=REALM_ID,
        vendor_ref_value="1",
        vendor_ref_name="Vendor",
        txn_date="2026-08-01",
        due_date=None,
        doc_number="INV-1",
        private_note=None,
        total_amt=Decimal("0"),
        balance=None,
        ap_account_ref_value=None,
        ap_account_ref_name=None,
        sales_term_ref_value=None,
        sales_term_ref_name=None,
        currency_ref_value=None,
        currency_ref_name=None,
        exchange_rate=None,
        department_ref_value=None,
        department_ref_name=None,
        global_tax_calculation=None,
    )


def _make_qbo_purchase(*, staging_pk=STAGING_PK, qbo_id="QB-PURCHASE-501"):
    return QboPurchase(
        id=staging_pk,
        public_id="22222222-2222-2222-2222-222222222222",
        row_version=None,
        created_datetime=None,
        modified_datetime=None,
        qbo_id=qbo_id,
        sync_token="0",
        realm_id=REALM_ID,
        payment_type="CreditCard",
        account_ref_value=None,
        account_ref_name=None,
        entity_ref_value="1",
        entity_ref_name="Vendor",
        credit=False,
        txn_date="2026-08-01",
        doc_number=None,
        private_note=None,
        total_amt=Decimal("0"),
        currency_ref_value=None,
        currency_ref_name=None,
        exchange_rate=None,
        department_ref_value=None,
        department_ref_name=None,
        global_tax_calculation=None,
    )


def _make_qbo_invoice(*, staging_pk=STAGING_PK, qbo_id="QB-INVOICE-501"):
    return QboInvoice(
        id=staging_pk,
        public_id="33333333-3333-3333-3333-333333333333",
        row_version=None,
        created_datetime=None,
        modified_datetime=None,
        qbo_id=qbo_id,
        sync_token="0",
        realm_id=REALM_ID,
        customer_ref_value="1",
        customer_ref_name="Customer",
        txn_date="2026-08-01",
        due_date=None,
        ship_date=None,
        doc_number="INV-501",
        private_note=None,
        customer_memo=None,
        bill_email=None,
        total_amt=Decimal("100"),
        balance=None,
        deposit=None,
        sales_term_ref_value=None,
        sales_term_ref_name=None,
        currency_ref_value=None,
        currency_ref_name=None,
        exchange_rate=None,
        department_ref_value=None,
        department_ref_name=None,
        class_ref_value=None,
        class_ref_name=None,
        ship_method_ref_value=None,
        ship_method_ref_name=None,
        tracking_num=None,
        print_status=None,
        email_status=None,
        allow_online_ach_payment=None,
        allow_online_credit_card_payment=None,
        apply_tax_after_discount=None,
        global_tax_calculation=None,
    )


def _make_qbo_vendor_credit(*, staging_pk=STAGING_PK, qbo_id="QB-VENDORCREDIT-501"):
    return QboVendorCredit(
        id=staging_pk,
        public_id="44444444-4444-4444-4444-444444444444",
        row_version=None,
        created_datetime=None,
        modified_datetime=None,
        realm_id=REALM_ID,
        qbo_id=qbo_id,
        sync_token="0",
        vendor_ref_value="1",
        vendor_ref_name="Vendor",
        txn_date="2026-08-01",
        doc_number="VC-501",
        total_amt=Decimal("0"),
        private_note=None,
        ap_account_ref_value=None,
        ap_account_ref_name=None,
        currency_ref_value=None,
        currency_ref_name=None,
    )


def _make_qbo_vendor(*, staging_pk=STAGING_PK, qbo_id="QB-VENDOR-501"):
    return QboVendor(
        id=staging_pk,
        public_id="55555555-5555-5555-5555-555555555555",
        row_version=None,
        created_datetime=None,
        modified_datetime=None,
        qbo_id=qbo_id,
        sync_token="0",
        realm_id=REALM_ID,
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
        active=True,
        primary_email_addr=None,
        primary_phone=None,
        mobile=None,
        fax=None,
        bill_addr_id=None,
        balance=None,
        acct_num=None,
        web_addr=None,
    )


def _make_qbo_customer(*, staging_pk=STAGING_PK, qbo_id="QB-CUSTOMER-501", job=False):
    return QboCustomer(
        id=staging_pk,
        public_id="66666666-6666-6666-6666-666666666666",
        row_version=None,
        created_datetime=None,
        modified_datetime=None,
        qbo_id=qbo_id,
        sync_token="0",
        realm_id=REALM_ID,
        display_name="Parent Co" if not job else "Job 501",
        title=None,
        given_name=None,
        middle_name=None,
        family_name=None,
        suffix=None,
        company_name=None,
        fully_qualified_name=None,
        level=0 if not job else 1,
        parent_ref_value=None if not job else "1",
        parent_ref_name=None,
        job=job,
        active=True,
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


def _make_qbo_term(*, staging_pk=STAGING_PK, qbo_id="QB-TERM-501"):
    return QboTerm(
        id=staging_pk,
        public_id="77777777-7777-7777-7777-777777777777",
        row_version=None,
        created_datetime=None,
        modified_datetime=None,
        qbo_id=qbo_id,
        sync_token="0",
        realm_id=REALM_ID,
        name="Net 30",
        discount_percent=None,
        discount_days=None,
        active=True,
        type="STANDARD",
        day_of_month_due=None,
        discount_day_of_month=None,
        due_next_month_days=None,
        due_days=30,
    )


def _make_qbo_company_info(*, staging_pk=STAGING_PK, qbo_id="QB-COMPANY-501"):
    return QboCompanyInfo(
        id=staging_pk,
        public_id="88888888-8888-8888-8888-888888888888",
        row_version=None,
        created_datetime=None,
        modified_datetime=None,
        qbo_id=qbo_id,
        sync_token="0",
        realm_id=REALM_ID,
        company_name="Build One",
        legal_name=None,
        company_addr_id=None,
        legal_addr_id=None,
        customer_communication_addr_id=None,
        tax_payer_id=None,
        fiscal_year_start_month=None,
        country=None,
        email=None,
        web_addr=None,
        currency_ref=None,
    )


def test_bill_projection_failure_records_real_qbo_id_not_staging_pk():
    bill = _make_qbo_bill()
    outcome = SyncOutcome.for_service_pull(synced=[bill], fetched=1)
    qbo_bill_service = MagicMock()
    qbo_bill_service.sync_from_qbo.return_value = outcome
    bill_connector = MagicMock()
    bill_connector.sync_from_qbo_bill.side_effect = RuntimeError("transient db error")

    with patch(f"{bill_module.__name__}.BillService"), patch(
        f"{bill_module.__name__}.BillLineItemService"
    ), patch(f"{bill_module.__name__}.read_lines_riding_out_race", return_value=[]), patch(
        f"{bill_module.__name__}.with_retry", side_effect=_retry_calls_fn
    ), patch(f"{bill_module.__name__}.pace_batch"):
        _, returned_outcome = bill_module.sync_qbo_to_local(
            realm_id=REALM_ID,
            last_sync_time=None,
            qbo_bill_service=qbo_bill_service,
            bill_connector=bill_connector,
        )

    assert returned_outcome.projection_failed_ids == ["QB-BILL-501"]


def test_purchase_projection_failure_records_real_qbo_id_not_staging_pk():
    purchase = _make_qbo_purchase()
    outcome = SyncOutcome.for_service_pull(synced=[purchase], fetched=1)
    qbo_purchase_service = MagicMock()
    qbo_purchase_service.sync_from_qbo.return_value = outcome
    purchase_connector = MagicMock()
    purchase_connector.sync_from_qbo_purchase.side_effect = RuntimeError("transient db error")

    with patch(f"{purchase_module.__name__}.QboAttachableService"), patch(
        "entities.expense.business.service.ExpenseService"
    ), patch("entities.expense_line_item.business.service.ExpenseLineItemService"), patch(
        f"{purchase_module.__name__}.read_lines_riding_out_race", return_value=[]
    ), patch(f"{purchase_module.__name__}.with_retry", side_effect=_retry_calls_fn), patch(
        f"{purchase_module.__name__}.pace_batch"
    ):
        _, returned_outcome = purchase_module.sync_qbo_to_local(
            realm_id=REALM_ID,
            last_sync_time=None,
            qbo_purchase_service=qbo_purchase_service,
            purchase_connector=purchase_connector,
        )

    assert returned_outcome.projection_failed_ids == ["QB-PURCHASE-501"]


def test_invoice_projection_failure_records_real_qbo_id_not_staging_pk():
    invoice = _make_qbo_invoice()
    outcome = SyncOutcome.for_service_pull(synced=[invoice], fetched=1)
    qbo_invoice_service = MagicMock()
    qbo_invoice_service.sync_from_qbo.return_value = outcome
    qbo_invoice_service.line_repo.read_all.return_value = []
    invoice_connector = MagicMock()
    invoice_connector.sync_from_qbo_invoice.side_effect = RuntimeError("transient db error")

    with patch(f"{invoice_module.__name__}.with_retry", side_effect=_retry_calls_fn), patch(
        f"{invoice_module.__name__}.pace_batch"
    ):
        _, returned_outcome = invoice_module.sync_qbo_to_local(
            realm_id=REALM_ID,
            last_sync_time=None,
            qbo_invoice_service=qbo_invoice_service,
            invoice_connector=invoice_connector,
        )

    assert returned_outcome.projection_failed_ids == ["QB-INVOICE-501"]


def test_vendorcredit_no_row_projection_failure_records_real_qbo_id_not_staging_pk():
    vendor_credit = _make_qbo_vendor_credit()
    outcome = SyncOutcome.for_service_pull(synced=[vendor_credit], fetched=1)
    qbo_vendor_credit_service = MagicMock()
    qbo_vendor_credit_service.sync_from_qbo.return_value = outcome
    vendor_credit_connector = MagicMock()
    vendor_credit_connector.sync_from_qbo_vendor_credit.return_value = None

    with patch(f"{vendorcredit_module.__name__}.BillCreditCompleteService"), patch(
        f"{vendorcredit_module.__name__}.BillCreditLineItemService"
    ), patch(f"{vendorcredit_module.__name__}.read_lines_riding_out_race", return_value=[SimpleNamespace()]), patch(
        f"{vendorcredit_module.__name__}.with_retry", side_effect=_retry_calls_fn
    ), patch(f"{vendorcredit_module.__name__}.pace_batch"):
        _, returned_outcome = vendorcredit_module.sync_qbo_to_local(
            realm_id=REALM_ID,
            last_sync_time=None,
            qbo_vendor_credit_service=qbo_vendor_credit_service,
            vendor_credit_connector=vendor_credit_connector,
        )

    assert returned_outcome.projection_failed_ids == ["QB-VENDORCREDIT-501"]


def test_vendorcredit_exception_projection_failure_records_real_qbo_id_not_staging_pk():
    vendor_credit = _make_qbo_vendor_credit(qbo_id="QB-VENDORCREDIT-502")
    outcome = SyncOutcome.for_service_pull(synced=[vendor_credit], fetched=1)
    qbo_vendor_credit_service = MagicMock()
    qbo_vendor_credit_service.sync_from_qbo.return_value = outcome
    vendor_credit_connector = MagicMock()
    vendor_credit_connector.sync_from_qbo_vendor_credit.side_effect = RuntimeError("transient db error")

    with patch(f"{vendorcredit_module.__name__}.BillCreditCompleteService"), patch(
        f"{vendorcredit_module.__name__}.BillCreditLineItemService"
    ), patch(f"{vendorcredit_module.__name__}.read_lines_riding_out_race", return_value=[SimpleNamespace()]), patch(
        f"{vendorcredit_module.__name__}.with_retry", side_effect=_retry_calls_fn
    ), patch(f"{vendorcredit_module.__name__}.pace_batch"):
        _, returned_outcome = vendorcredit_module.sync_qbo_to_local(
            realm_id=REALM_ID,
            last_sync_time=None,
            qbo_vendor_credit_service=qbo_vendor_credit_service,
            vendor_credit_connector=vendor_credit_connector,
        )

    assert returned_outcome.projection_failed_ids == ["QB-VENDORCREDIT-502"]


def test_vendor_projection_failure_records_real_qbo_id_not_staging_pk():
    vendor = _make_qbo_vendor()
    outcome = SyncOutcome.for_service_pull(synced=[vendor], fetched=1)
    qbo_vendor_service = MagicMock()
    qbo_vendor_service.sync_from_qbo.return_value = outcome
    vendor_connector = MagicMock()
    vendor_connector.sync_from_qbo_vendor.side_effect = RuntimeError("transient db error")

    with patch(f"{vendor_module.__name__}.with_retry", side_effect=_retry_calls_fn), patch(
        f"{vendor_module.__name__}.pace_batch"
    ):
        _, returned_outcome = vendor_module.sync_qbo_to_local(
            realm_id=REALM_ID,
            last_sync_time=None,
            qbo_vendor_service=qbo_vendor_service,
            vendor_connector=vendor_connector,
        )

    assert returned_outcome.projection_failed_ids == ["QB-VENDOR-501"]


def test_customer_parent_projection_failure_records_real_qbo_id_not_staging_pk():
    customer = _make_qbo_customer(job=False)
    outcome = SyncOutcome.for_service_pull(synced=[customer], fetched=1)
    qbo_customer_service = MagicMock()
    qbo_customer_service.sync_from_qbo.return_value = outcome
    customer_connector = MagicMock()
    customer_connector.sync_from_qbo_customer.side_effect = RuntimeError("transient db error")
    project_connector = MagicMock()

    with patch(f"{customer_module.__name__}.with_retry", side_effect=_retry_calls_fn), patch(
        f"{customer_module.__name__}.pace_batch"
    ):
        _, returned_outcome = customer_module.sync_qbo_to_local(
            realm_id=REALM_ID,
            last_sync_time=None,
            qbo_customer_service=qbo_customer_service,
            customer_connector=customer_connector,
            project_connector=project_connector,
        )

    assert returned_outcome.projection_failed_ids == ["QB-CUSTOMER-501"]


def test_customer_project_projection_failure_records_real_qbo_id_not_staging_pk():
    customer = _make_qbo_customer(job=True, qbo_id="QB-PROJECT-501")
    outcome = SyncOutcome.for_service_pull(synced=[customer], fetched=1)
    qbo_customer_service = MagicMock()
    qbo_customer_service.sync_from_qbo.return_value = outcome
    customer_connector = MagicMock()
    project_connector = MagicMock()
    project_connector.sync_from_qbo_customer.side_effect = RuntimeError("transient db error")

    with patch(f"{customer_module.__name__}.with_retry", side_effect=_retry_calls_fn), patch(
        f"{customer_module.__name__}.pace_batch"
    ):
        _, returned_outcome = customer_module.sync_qbo_to_local(
            realm_id=REALM_ID,
            last_sync_time=None,
            qbo_customer_service=qbo_customer_service,
            customer_connector=customer_connector,
            project_connector=project_connector,
        )

    assert returned_outcome.projection_failed_ids == ["QB-PROJECT-501"]


def test_term_incremental_projection_failure_records_real_qbo_id_not_staging_pk():
    term = _make_qbo_term()
    outcome = SyncOutcome.for_service_pull(synced=[term], fetched=1)
    qbo_term_service = MagicMock()
    qbo_term_service.sync_from_qbo.return_value = outcome
    term_connector = MagicMock()
    term_connector.sync_from_qbo_term.side_effect = RuntimeError("transient db error")

    with patch(f"{term_module.__name__}.with_retry", side_effect=_retry_calls_fn), patch(
        f"{term_module.__name__}.pace_batch"
    ):
        _, returned_outcome = term_module.sync_qbo_to_local(
            realm_id=REALM_ID,
            last_sync_time=None,
            qbo_term_service=qbo_term_service,
            term_connector=term_connector,
        )

    assert returned_outcome.projection_failed_ids == ["QB-TERM-501"]


def test_term_existing_rows_projection_failure_records_real_qbo_id_not_staging_pk():
    term = _make_qbo_term(qbo_id="QB-TERM-502")
    outcome = SyncOutcome.for_service_pull()
    qbo_term_repo = MagicMock()
    qbo_term_repo.read_all.return_value = [term]
    term_mapping_repo = MagicMock()
    term_mapping_repo.read_by_qbo_term_id.return_value = None
    term_connector = MagicMock()
    term_connector.sync_from_qbo_term.side_effect = RuntimeError("transient db error")

    with patch(f"{term_module.__name__}.with_retry", side_effect=_retry_calls_fn), patch(
        f"{term_module.__name__}.pace_batch"
    ):
        term_module.sync_existing_terms_to_payment_terms(
            qbo_term_repo=qbo_term_repo,
            term_connector=term_connector,
            term_mapping_repo=term_mapping_repo,
            outcome=outcome,
        )

    assert outcome.projection_failed_ids == ["QB-TERM-502"]


def test_company_info_projection_failure_records_real_qbo_id_not_staging_pk():
    company_info = _make_qbo_company_info()
    outcome = SyncOutcome.for_service_pull(synced=[company_info], fetched=1)

    mock_run = MagicMock()
    mock_run.open.return_value = mock_run
    mock_run.last_sync_time = None
    mock_run.query_start = datetime.now(timezone.utc)
    mock_run.commit.return_value = SimpleNamespace(
        to_dict=lambda: {"last_sync_datetime": "2026-01-01T00:00:00Z"},
        last_sync_datetime="2026-01-01T00:00:00Z",
    )

    mock_company_info_service = MagicMock()
    mock_company_info_service.sync_from_qbo.return_value = outcome
    mock_company_connector = MagicMock()
    mock_company_connector.sync_from_qbo_to_company.side_effect = RuntimeError("transient db error")
    mock_auth = MagicMock()
    mock_auth.resolve_realm_id.return_value = REALM_ID

    with patch(f"{company_info_module.__name__}.WatermarkRun", return_value=mock_run), patch(
        f"{company_info_module.__name__}.SyncService"
    ), patch(
        f"{company_info_module.__name__}.QboCompanyInfoService", return_value=mock_company_info_service
    ), patch(
        f"{company_info_module.__name__}.CompanyInfoCompanyConnector", return_value=mock_company_connector
    ), patch(f"{company_info_module.__name__}.PhysicalAddressAddressConnector"), patch(
        f"{company_info_module.__name__}.QboAuthService", return_value=mock_auth
    ), patch(f"{company_info_module.__name__}.assert_cli_system_admin"):
        company_info_module.sync_qbo_company_info()

    assert outcome.projection_failed_ids == ["QB-COMPANY-501"]
