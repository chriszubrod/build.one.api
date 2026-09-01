"""U-333 characterization: consolidated recorder wrappers emit byte-identical tuples."""
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest

from integrations.intuit.qbo.attachable.connector.attachment.business.service import (
    AttachableAttachmentConnector,
)
from integrations.intuit.qbo.bill.connector.bill.business.service import BillBillConnector
from integrations.intuit.qbo.bill.connector.bill_line_item.business.service import BillLineItemConnector
from integrations.intuit.qbo.company_info.connector.business.service import CompanyInfoCompanyConnector
from integrations.intuit.qbo.customer.connector.customer.business.service import CustomerCustomerConnector
from integrations.intuit.qbo.customer.connector.project.business.service import CustomerProjectConnector
from integrations.intuit.qbo.invoice.connector.invoice.business.service import InvoiceInvoiceConnector
from integrations.intuit.qbo.invoice.connector.invoice_line_item.business.service import InvoiceLineItemConnector
from integrations.intuit.qbo.item.connector.cost_code.business.service import ItemCostCodeConnector
from integrations.intuit.qbo.item.connector.sub_cost_code.business.service import ItemSubCostCodeConnector
from integrations.intuit.qbo.physical_address.connector.business.service import PhysicalAddressAddressConnector
from integrations.intuit.qbo.purchase.connector.expense.business.service import PurchaseExpenseConnector
from integrations.intuit.qbo.purchase.connector.expense_line_item.business.service import (
    PurchaseLineExpenseLineItemConnector,
)
from integrations.intuit.qbo.vendor.connector.vendor.business.service import VendorVendorConnector
from integrations.intuit.qbo.vendorcredit.connector.bill_credit_line_item.business.service import (
    VendorCreditLineItemConnector,
)

# Captured pre-refactor from scratchpad harness (U-333 Task 4).
_EXPECTED = {
    "bill": {
        "drift_type": "bill_identity_conflict",
        "entity_type": "Bill",
        "entity_public_id": None,
        "qbo_id": "BILL-99",
        "realm_id": "realm-1",
        "details": (
            "BillBill identity conflict. dbo.Bill 55 carries native QBO identity for QboBill 4 "
            "(QboId=BILL-99, RealmId=realm-1). qbo-side: the mapping table still binds that same "
            "QboBill to a DIFFERENT Bill 9 (mapping 2). local-side: Bill 55's own mapping row "
            "(mapping 3) still binds it to a DIFFERENT QboBill 5. Not auto-repointed — "
            "investigate which side is correct."
        ),
    },
    "invoice": {
        "drift_type": "invoice_identity_conflict",
        "entity_type": "Invoice",
        "entity_public_id": None,
        "qbo_id": "INV-99",
        "realm_id": "realm-1",
        "details": (
            "InvoiceInvoice identity conflict. dbo.Invoice 55 carries native QBO identity for "
            "QboInvoice 4 (QboId=INV-99, RealmId=realm-1). qbo-side: the mapping table still binds "
            "that same QboInvoice to a DIFFERENT Invoice 9 (mapping 2). local-side: Invoice 55's "
            "own mapping row (mapping 3) still binds it to a DIFFERENT QboInvoice 5. Not "
            "auto-repointed — investigate which side is correct."
        ),
    },
    "expense": {
        "drift_type": "expense_identity_conflict",
        "entity_type": "Expense",
        "entity_public_id": None,
        "qbo_id": "PUR-99",
        "realm_id": "realm-1",
        "details": (
            "PurchaseExpense identity conflict. dbo.Expense 55 carries native QBO identity for "
            "QboPurchase 4 (QboId=PUR-99, RealmId=realm-1). qbo-side: the mapping table still binds "
            "that same QboPurchase to a DIFFERENT Expense 9 (mapping 2). local-side: Expense 55's "
            "own mapping row (mapping 3) still binds it to a DIFFERENT QboPurchase 5. Not "
            "auto-repointed — investigate which side is correct."
        ),
    },
    # "physical_address" (this legacy-conflict shape) was superseded in U-351 by
    # "address_diff_qbo" below — PhysicalAddressAddressConnector no longer has
    # _record_identity_mapping_conflict_issue (qbo.PhysicalAddressAddress is
    # retired); its dbo-only duplicate-identity guard is a different method,
    # covered by the "address_diff_qbo" entry alongside customer/project/company.
    # "payment_term_diff_qbo" entry removed in U-352 (/simplify altitude finding):
    # TermPaymentTermConnector's create path never adopts a pre-existing row by
    # name, so it has no side-channel collision a duplicate-identity conflict could
    # arise from — `_record_duplicate_qbo_payment_term_issue` (and the
    # candidate-scoped lock that was its only caller) was removed as dead code
    # rather than kept for sibling-shape symmetry. See
    # integrations/intuit/qbo/term/connector/payment_term/business/service.py's
    # `_stamp_payment_term_identity` docstring for the full reasoning.
    # U-353: "bill_credit" removed — VendorCreditBillCreditConnector no longer has
    # _record_identity_mapping_conflict_issue (qbo.VendorCreditBillCredit is retired;
    # run_identity_fastpath_dbo_only has no mapping-table-vs-dbo conflict state left
    # to detect — dbo.BillCredit's own unique index is the sole guard now).
    "bill_line_item": {
        "drift_type": "bill_line_item_identity_conflict",
        "entity_type": "BillLineItem",
        "entity_public_id": None,
        "qbo_id": "LINE-99",
        "realm_id": "realm-1",
        "details": (
            "BillLineItemBillLine identity conflict. dbo.BillLineItem 55 carries native QBO identity "
            "for QboBillLine 4 (QboLineId=LINE-99). qbo-side: the mapping table still binds that "
            "same QboBillLine to a DIFFERENT BillLineItem 9 (mapping 2). local-side: BillLineItem "
            "55's own mapping row (mapping 3) still binds it to a DIFFERENT QboBillLine 5. Not "
            "auto-repointed — investigate which side is correct."
        ),
    },
    "expense_line_item": {
        "drift_type": "expense_line_identity_conflict",
        "entity_type": "ExpenseLineItem",
        "entity_public_id": None,
        "qbo_id": "EL-99",
        "realm_id": "realm-1",
        "details": (
            "PurchaseLineExpenseLineItem identity conflict. dbo.ExpenseLineItem 55 carries native "
            "QBO identity for QboPurchaseLine 4 (QboLineId=EL-99). qbo-side: the mapping table "
            "still binds that same QboPurchaseLine to a DIFFERENT ExpenseLineItem 9 (mapping 2). "
            "local-side: ExpenseLineItem 55's own mapping row (mapping 3) still binds it to a "
            "DIFFERENT QboPurchaseLine 5. Not auto-repointed — investigate which side is correct."
        ),
    },
    "invoice_line_item": {
        "drift_type": "invoice_line_identity_conflict",
        "entity_type": "InvoiceLineItem",
        "entity_public_id": None,
        "qbo_id": "IL-99",
        "realm_id": "realm-1",
        "details": (
            "InvoiceLineItemInvoiceLine identity conflict. dbo.InvoiceLineItem 55 carries native "
            "QBO identity for QboInvoiceLine 4 (QboLineId=IL-99). qbo-side: the mapping table "
            "still binds that same QboInvoiceLine to a DIFFERENT InvoiceLineItem 9 (mapping 2). "
            "local-side: InvoiceLineItem 55's own mapping row (mapping 3) still binds it to a "
            "DIFFERENT QboInvoiceLine 5. Not auto-repointed — investigate which side is correct."
        ),
    },
    "bill_credit_line_item": {
        "drift_type": "bc_line_item_identity_conflict",
        "entity_type": "BillCreditLineItem",
        "entity_public_id": None,
        "qbo_id": "BCL-99",
        "realm_id": "realm-1",
        "details": (
            "VendorCreditLineItemBillCreditLineItem identity conflict. dbo.BillCreditLineItem 55 "
            "carries native QBO identity for QboVendorCreditLine 4 (QboLineId=BCL-99). qbo-side: "
            "the mapping table still binds that same QboVendorCreditLine to a DIFFERENT "
            "BillCreditLineItem 9 (mapping 2). local-side: BillCreditLineItem 55's own mapping row "
            "(mapping 3) still binds it to a DIFFERENT QboVendorCreditLine 5. Not auto-repointed — "
            "investigate which side is correct."
        ),
    },
    "customer_same_qbo": {
        "drift_type": "customer_identity_conflict",
        "entity_type": "Customer",
        "entity_public_id": "11111111-1111-1111-1111-111111111111",
        "qbo_id": "C-99",
        "realm_id": "realm-in",
        "details": (
            "Duplicate QBO customer detected. QboCustomer 4 (Name='Acme') name-matches local "
            "Customer 7 which already carries the SAME QboId C-99 but a DIFFERENT RealmId "
            "('realm-old' vs incoming 'realm-in'). Resolve by merging or renaming one of the QBO "
            "customers."
        ),
    },
    "customer_diff_qbo": {
        "drift_type": "customer_identity_conflict",
        "entity_type": "Customer",
        "entity_public_id": "11111111-1111-1111-1111-111111111111",
        "qbo_id": "C-NEW",
        "realm_id": "realm-in",
        "details": (
            "Duplicate QBO customer detected. QboCustomer 4 (Name='Acme') name-matches local "
            "Customer 7 which already carries a DIFFERENT QboId C-OLD (realm 'realm-old'). Resolve "
            "by merging or renaming one of the QBO customers."
        ),
    },
    "project_diff_qbo": {
        "drift_type": "project_identity_conflict",
        "entity_type": "Project",
        "entity_public_id": "22222222-2222-2222-2222-222222222222",
        "qbo_id": "C-NEW",
        "realm_id": "realm-in",
        "details": (
            "Duplicate QBO sub-customer detected. QboCustomer 4 (DisplayName='Job1') name-matches "
            "local Project 7 which already carries a DIFFERENT QboId C-OLD (realm 'realm-old'). "
            "Resolve by merging or renaming one of the QBO sub-customers."
        ),
    },
    "vendor": {
        "drift_type": "duplicate_qbo_vendor",
        "entity_type": "Vendor",
        "entity_public_id": "33333333-3333-3333-3333-333333333333",
        "qbo_id": "V-NEW",
        "realm_id": "realm-1",
        "details": (
            "Duplicate QBO vendor detected. QboVendor 4 (QboId=V-NEW, DisplayName='VendorX') "
            "name-matches local Vendor 7 which already carries a DIFFERENT identity (QboId=V-OLD). "
            "Resolve by merging or renaming one of the QBO vendors."
        ),
    },
    "company_diff_qbo": {
        "drift_type": "company_identity_conflict",
        "entity_type": "Company",
        "entity_public_id": "77777777-7777-7777-7777-777777777777",
        "qbo_id": "CI-NEW",
        "realm_id": "realm-in",
        "details": (
            "Duplicate QBO company detected. QboCompanyInfo 4 (Name='Acme') name-matches local "
            "Company 7 which already carries a DIFFERENT QboId CI-OLD (realm 'realm-old'). Resolve "
            "by merging or renaming one of the QBO companies."
        ),
    },
    "address_diff_qbo": {
        "drift_type": "address_identity_conflict",
        "entity_type": "Address",
        "entity_public_id": "88888888-8888-8888-8888-888888888888",
        "qbo_id": "ADDR-NEW",
        "realm_id": "realm-in",
        "details": (
            "Duplicate QBO address detected. QboPhysicalAddress 4 name-matches local "
            "Address 7 which already carries a DIFFERENT QboId ADDR-OLD (realm 'realm-old'). Resolve "
            "by merging or renaming one of the QBO addresses."
        ),
    },
    "cost_code": {
        "drift_type": "duplicate_qbo_item",
        "entity_type": "CostCode",
        "entity_public_id": "44444444-4444-4444-4444-444444444444",
        "qbo_id": "ITEM-NEW",
        "realm_id": "realm-1",
        "details": (
            "Duplicate QBO item detected. QboItem qbo_id=ITEM-NEW (Name='Code1') number-matches "
            "local CostCode 7 which already carries a DIFFERENT QboId ITEM-OLD. Resolve by merging "
            "or renaming one of the QBO items."
        ),
    },
    "sub_cost_code": {
        "drift_type": "duplicate_qbo_item",
        "entity_type": "SubCostCode",
        "entity_public_id": "55555555-5555-5555-5555-555555555555",
        "qbo_id": "ITEM-NEW",
        "realm_id": "realm-1",
        "details": (
            "Duplicate QBO item detected. QboItem qbo_id=ITEM-NEW (Name='Sub1') number-matches "
            "local SubCostCode 7 which already carries a DIFFERENT QboId ITEM-OLD. Resolve by "
            "merging or renaming one of the QBO items."
        ),
    },
    "attachment": {
        "drift_type": "attachment_identity_conflict",
        "entity_type": "Attachment",
        "entity_public_id": "66666666-6666-6666-6666-666666666666",
        "qbo_id": "ATT-NEW",
        "realm_id": "realm-in",
        "details": (
            "Attachment stamp-time identity conflict. Candidate Attachment 99 already carries "
            "QboId=ATT-OLD (realm 'realm-old') and cannot be re-stamped with qbo_id=ATT-NEW "
            "realm_id=realm-in. Resolve by merging or restoring the correct mapping."
        ),
    },
}


def _assert_recorded(repo, expected):
    repo.create.assert_called_once()
    kwargs = repo.create.call_args.kwargs
    for key in ("drift_type", "entity_type", "entity_public_id", "qbo_id", "realm_id", "details"):
        assert kwargs[key] == expected[key], f"{key}: {kwargs[key]!r} != {expected[key]!r}"
    assert kwargs["severity"] == "critical"
    assert kwargs["action"] == "manual_review"


@pytest.mark.parametrize("family", list(_EXPECTED))
def test_recorder_consolidation_emits_identical_tuple(family):
    expected = _EXPECTED[family]
    if family == "bill":
        c = object.__new__(BillBillConnector)
        c.reconciliation_repo = Mock()
        c._record_identity_mapping_conflict_issue(
            qbo_bill=SimpleNamespace(id=4, qbo_id="BILL-99", realm_id="realm-1"),
            dbo_bill_id=55,
            local_side_mapping=SimpleNamespace(id=3, bill_id=55, qbo_bill_id=5),
            qbo_side_mapping=SimpleNamespace(id=2, bill_id=9, qbo_bill_id=4),
        )
    elif family == "invoice":
        c = object.__new__(InvoiceInvoiceConnector)
        c.reconciliation_repo = Mock()
        c._record_identity_mapping_conflict_issue(
            qbo_invoice=SimpleNamespace(id=4, qbo_id="INV-99", realm_id="realm-1"),
            dbo_invoice_id=55,
            local_side_mapping=SimpleNamespace(id=3, invoice_id=55, qbo_invoice_id=5),
            qbo_side_mapping=SimpleNamespace(id=2, invoice_id=9, qbo_invoice_id=4),
        )
    elif family == "expense":
        c = object.__new__(PurchaseExpenseConnector)
        c.reconciliation_repo = Mock()
        c._record_identity_mapping_conflict_issue(
            qbo_purchase=SimpleNamespace(id=4, qbo_id="PUR-99", realm_id="realm-1"),
            dbo_expense_id=55,
            local_side_mapping=SimpleNamespace(id=3, expense_id=55, qbo_purchase_id=5),
            qbo_side_mapping=SimpleNamespace(id=2, expense_id=9, qbo_purchase_id=4),
        )
    elif family == "bill_line_item":
        c = object.__new__(BillLineItemConnector)
        c.reconciliation_repo = Mock()
        c._record_line_identity_mapping_conflict_issue(
            qbo_bill_line=SimpleNamespace(id=4, qbo_line_id="LINE-99"),
            dbo_line_id=55,
            local_side_mapping=SimpleNamespace(id=3, bill_line_item_id=55, qbo_bill_line_id=5),
            qbo_side_mapping=SimpleNamespace(id=2, bill_line_item_id=9, qbo_bill_line_id=4),
            realm_id="realm-1",
        )
    elif family == "expense_line_item":
        c = object.__new__(PurchaseLineExpenseLineItemConnector)
        c.reconciliation_repo = Mock()
        c._record_line_identity_mapping_conflict_issue(
            qbo_line=SimpleNamespace(id=4, qbo_line_id="EL-99"),
            dbo_line_id=55,
            local_side_mapping=SimpleNamespace(id=3, expense_line_item_id=55, qbo_purchase_line_id=5),
            qbo_side_mapping=SimpleNamespace(id=2, expense_line_item_id=9, qbo_purchase_line_id=4),
            realm_id="realm-1",
        )
    elif family == "invoice_line_item":
        c = object.__new__(InvoiceLineItemConnector)
        c.reconciliation_repo = Mock()
        c._record_line_identity_mapping_conflict_issue(
            qbo_invoice_line=SimpleNamespace(id=4, qbo_line_id="IL-99"),
            dbo_line_id=55,
            local_side_mapping=SimpleNamespace(id=3, invoice_line_item_id=55, qbo_invoice_line_id=5),
            qbo_side_mapping=SimpleNamespace(id=2, invoice_line_item_id=9, qbo_invoice_line_id=4),
            realm_id="realm-1",
        )
    elif family == "bill_credit_line_item":
        c = object.__new__(VendorCreditLineItemConnector)
        c.reconciliation_repo = Mock()
        c._record_line_identity_mapping_conflict_issue(
            qbo_line=SimpleNamespace(id=4, qbo_line_id="BCL-99"),
            dbo_line_id=55,
            local_side_mapping=SimpleNamespace(
                id=3, bill_credit_line_item_id=55, qbo_vendor_credit_line_id=5
            ),
            qbo_side_mapping=SimpleNamespace(
                id=2, bill_credit_line_item_id=9, qbo_vendor_credit_line_id=4
            ),
            realm_id="realm-1",
        )
    elif family == "customer_same_qbo":
        c = object.__new__(CustomerCustomerConnector)
        c.reconciliation_repo = Mock()
        c._record_duplicate_qbo_customer_issue(
            qbo_customer=SimpleNamespace(id=4, qbo_id="C-99", realm_id="realm-in", display_name="Acme"),
            local_customer=SimpleNamespace(
                id=7, public_id=UUID("11111111-1111-1111-1111-111111111111"), realm_id="realm-old"
            ),
            existing_qbo_id="C-99",
        )
    elif family == "customer_diff_qbo":
        c = object.__new__(CustomerCustomerConnector)
        c.reconciliation_repo = Mock()
        c._record_duplicate_qbo_customer_issue(
            qbo_customer=SimpleNamespace(id=4, qbo_id="C-NEW", realm_id="realm-in", display_name="Acme"),
            local_customer=SimpleNamespace(
                id=7, public_id=UUID("11111111-1111-1111-1111-111111111111"), realm_id="realm-old"
            ),
            existing_qbo_id="C-OLD",
        )
    elif family == "project_diff_qbo":
        c = object.__new__(CustomerProjectConnector)
        c.reconciliation_repo = Mock()
        c._record_project_identity_conflict_issue(
            qbo_customer=SimpleNamespace(id=4, qbo_id="C-NEW", realm_id="realm-in", display_name="Job1"),
            local_project=SimpleNamespace(
                id=7, public_id=UUID("22222222-2222-2222-2222-222222222222"), realm_id="realm-old"
            ),
            existing_qbo_id="C-OLD",
        )
    elif family == "vendor":
        c = object.__new__(VendorVendorConnector)
        c.reconciliation_repo = Mock()
        c._record_duplicate_qbo_vendor_issue(
            qbo_vendor=SimpleNamespace(id=4, qbo_id="V-NEW", realm_id="realm-1", display_name="VendorX"),
            local_vendor=SimpleNamespace(id=7, public_id=UUID("33333333-3333-3333-3333-333333333333")),
            existing_qbo_id="V-OLD",
        )
    elif family == "company_diff_qbo":
        c = object.__new__(CompanyInfoCompanyConnector)
        c.reconciliation_repo = Mock()
        c._record_duplicate_qbo_company_issue(
            qbo_company_info=SimpleNamespace(id=4, qbo_id="CI-NEW", realm_id="realm-in", legal_name="Acme"),
            local_company=SimpleNamespace(
                id=7, public_id=UUID("77777777-7777-7777-7777-777777777777"), realm_id="realm-old"
            ),
            existing_qbo_id="CI-OLD",
            realm_id="realm-in",
        )
    elif family == "address_diff_qbo":
        c = object.__new__(PhysicalAddressAddressConnector)
        c.reconciliation_repo = Mock()
        c._record_duplicate_qbo_address_issue(
            qbo_physical_address=SimpleNamespace(id=4, qbo_id="ADDR-NEW", realm_id="realm-in"),
            local_address=SimpleNamespace(
                id=7, public_id=UUID("88888888-8888-8888-8888-888888888888"), realm_id="realm-old"
            ),
            existing_qbo_id="ADDR-OLD",
        )
    elif family == "cost_code":
        c = object.__new__(ItemCostCodeConnector)
        c.reconciliation_repo = Mock()
        c._record_duplicate_qbo_item_issue(
            qbo_item=SimpleNamespace(qbo_id="ITEM-NEW", realm_id="realm-1", name="Code1"),
            local_cost_code=SimpleNamespace(id=7, public_id=UUID("44444444-4444-4444-4444-444444444444")),
            existing_qbo_id="ITEM-OLD",
        )
    elif family == "sub_cost_code":
        c = object.__new__(ItemSubCostCodeConnector)
        c.reconciliation_repo = Mock()
        c._record_duplicate_qbo_item_issue(
            qbo_item=SimpleNamespace(qbo_id="ITEM-NEW", realm_id="realm-1", name="Sub1"),
            local_sub_cost_code=SimpleNamespace(
                id=7, public_id=UUID("55555555-5555-5555-5555-555555555555")
            ),
            existing_qbo_id="ITEM-OLD",
        )
    elif family == "attachment":
        c = object.__new__(AttachableAttachmentConnector)
        c.reconciliation_repo = Mock()
        c._record_duplicate_qbo_attachment_issue(
            attachment_id=99,
            qbo_id="ATT-NEW",
            realm_id="realm-in",
            local_attachment=SimpleNamespace(
                public_id=UUID("66666666-6666-6666-6666-666666666666"), realm_id="realm-old"
            ),
            existing_qbo_id="ATT-OLD",
        )
    else:
        pytest.fail(f"unhandled family {family!r}")

    _assert_recorded(c.reconciliation_repo, expected)
