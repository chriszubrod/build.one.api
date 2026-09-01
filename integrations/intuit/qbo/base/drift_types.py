"""Single source of truth for qbo.ReconciliationIssue DriftType values (U-227/U-246).

Two writer subsystems both register here: the daily full reconciler
(integrations/intuit/qbo/reconciliation/business/service.py, which references these as named
constants) and the per-connector mapping-issue recorder (base/reconciliation_recorder.py — per-connector
wrappers (~17) forward through record_identity_mapping_conflict /
record_duplicate_identity_conflict, still passing string literals at the call site). A drift type
must be added here before (or on discovery of) its first use so it's declared once, not tribal
knowledge — see tests/test_qbo_reconciliation_recorder.py's AST-discovery guard.
"""
from typing import FrozenSet

# --- Daily reconciler (integrations/intuit/qbo/reconciliation/business/service.py) ---
DRIFT_QBO_MISSING_LOCALLY = "qbo_missing_locally"
DRIFT_LOCAL_MISSING_QBO = "local_missing_qbo"
DRIFT_STALE_SYNC_TOKEN = "stale_sync_token"
DRIFT_MISSING_MAPPING = "missing_mapping"
DRIFT_FIELD_MISMATCH = "field_mismatch"
DRIFT_DUPLICATE_MAPPING = "duplicate_mapping"
DRIFT_QBO_VOIDED = "qbo_voided"
DRIFT_INVOICE_DRAW_MISMATCH = "invoice_draw_mismatch"
DRIFT_BILLABLE_STATUS_DRIFT = "billable_status_drift"

# --- Per-connector mapping-issue recorder ---
DRIFT_ORPHANED_BILL_BILL_MAPPING = "orphaned_bill_bill_mapping"
DRIFT_DUPLICATE_QBO_BILL_DOCNUMBER = "duplicate_qbo_bill_docnumber"
DRIFT_DUPLICATE_QBO_INVOICE_NUMBER = "duplicate_qbo_invoice_number"
DRIFT_DUPLICATE_QBO_ITEM = "duplicate_qbo_item"
DRIFT_ORPHANED_ITEM_COST_CODE_MAPPING = "orphaned_item_cost_code_mapping"
DRIFT_ORPHAN_BILLCREDIT_HEADER = "orphan_billcredit_header"
DRIFT_ORPHAN_EXPENSE_HEADER = "orphan_expense_header"
DRIFT_ORPHANED_VC_BILLCREDIT_MAPPING = "orphaned_vc_billcredit_mapping"
DRIFT_ORPHANED_ITEM_SCC_MAPPING = "orphaned_item_scc_mapping"
DRIFT_ORPHANED_PURCH_EXPENSE_MAPPING = "orphaned_purch_expense_mapping"
DRIFT_PULL_DELETE_RECONCILE = "pull_delete_reconcile"
DRIFT_DUPLICATE_QBO_CUSTOMER = "duplicate_qbo_customer"
DRIFT_ORPHANED_CUST_PROJECT_MAPPING = "orphaned_cust_project_mapping"
DRIFT_ATTACHMENT_MAPPING_ORPHANED = "attachment_mapping_orphaned"
DRIFT_ATTACHMENT_UPLOAD_FAILED = "attachment_upload_failed"
DRIFT_DUPLICATE_QBO_VENDOR = "duplicate_qbo_vendor"
DRIFT_ORPHANED_VENDOR_VENDOR_MAPPING = "orphaned_vendor_vendor_mapping"
DRIFT_BLANK_DISPLAY_NAME_QBO_VENDOR = "blank_display_name_qbo_vendor"
DRIFT_DELETED_VENDOR_HOLDS_IDENTITY = "deleted_vendor_holds_identity"
DRIFT_WATERMARK_HOLD_BOUND_EXCEEDED = "watermark_hold_bound_exceeded"
DRIFT_PROJECT_IDENTITY_CONFLICT = "project_identity_conflict"
DRIFT_CUSTOMER_IDENTITY_CONFLICT = "customer_identity_conflict"
DRIFT_COMPANY_IDENTITY_CONFLICT = "company_identity_conflict"
DRIFT_ADDRESS_IDENTITY_CONFLICT = "address_identity_conflict"
DRIFT_VENDORCREDIT_IDENTITY_CONFLICT = "vendorcredit_identity_conflict"
DRIFT_ATTACHMENT_IDENTITY_CONFLICT = "attachment_identity_conflict"
DRIFT_PAYMENT_TERM_IDENTITY_CONFLICT = "payment_term_identity_conflict"
DRIFT_VENDOR_IDENTITY_CONFLICT = "vendor_identity_conflict"
DRIFT_COST_CODE_IDENTITY_CONFLICT = "cost_code_identity_conflict"
DRIFT_SUB_COST_CODE_IDENTITY_CONFLICT = "sub_cost_code_identity_conflict"
DRIFT_BILL_IDENTITY_CONFLICT = "bill_identity_conflict"
DRIFT_EXPENSE_IDENTITY_CONFLICT = "expense_identity_conflict"
DRIFT_INVOICE_IDENTITY_CONFLICT = "invoice_identity_conflict"
DRIFT_BILL_LINE_ITEM_IDENTITY_CONFLICT = "bill_line_item_identity_conflict"
# The other 3 line families' full "<family>_line_item_identity_conflict" name
# exceeds the live qbo.ReconciliationIssue.DriftType NVARCHAR(32) column width
# (measured prod width, see _FIELD_LIMITS above) — Bill's own name only fits
# because "bill" is short. Shortened per-family, not simply truncated.
DRIFT_INVOICE_LINE_ITEM_IDENTITY_CONFLICT = "invoice_line_identity_conflict"
DRIFT_EXPENSE_LINE_ITEM_IDENTITY_CONFLICT = "expense_line_identity_conflict"
DRIFT_BILL_CREDIT_LINE_ITEM_IDENTITY_CONFLICT = "bc_line_item_identity_conflict"

KNOWN_DRIFT_TYPES: FrozenSet[str] = frozenset({
    DRIFT_QBO_MISSING_LOCALLY, DRIFT_LOCAL_MISSING_QBO, DRIFT_STALE_SYNC_TOKEN,
    DRIFT_MISSING_MAPPING, DRIFT_FIELD_MISMATCH, DRIFT_DUPLICATE_MAPPING,
    DRIFT_QBO_VOIDED, DRIFT_INVOICE_DRAW_MISMATCH, DRIFT_BILLABLE_STATUS_DRIFT,
    DRIFT_ORPHANED_BILL_BILL_MAPPING, DRIFT_DUPLICATE_QBO_BILL_DOCNUMBER,
    DRIFT_DUPLICATE_QBO_INVOICE_NUMBER, DRIFT_DUPLICATE_QBO_ITEM,
    DRIFT_ORPHANED_ITEM_COST_CODE_MAPPING,
    DRIFT_ORPHAN_BILLCREDIT_HEADER, DRIFT_ORPHAN_EXPENSE_HEADER, DRIFT_ORPHANED_VC_BILLCREDIT_MAPPING,
    DRIFT_ORPHANED_ITEM_SCC_MAPPING, DRIFT_ORPHANED_PURCH_EXPENSE_MAPPING,
    DRIFT_PULL_DELETE_RECONCILE, DRIFT_DUPLICATE_QBO_CUSTOMER,
    DRIFT_ORPHANED_CUST_PROJECT_MAPPING, DRIFT_ATTACHMENT_MAPPING_ORPHANED,
    DRIFT_ATTACHMENT_UPLOAD_FAILED, DRIFT_DUPLICATE_QBO_VENDOR,
    DRIFT_ORPHANED_VENDOR_VENDOR_MAPPING, DRIFT_BLANK_DISPLAY_NAME_QBO_VENDOR,
    DRIFT_DELETED_VENDOR_HOLDS_IDENTITY,
    DRIFT_WATERMARK_HOLD_BOUND_EXCEEDED, DRIFT_PROJECT_IDENTITY_CONFLICT,
    DRIFT_CUSTOMER_IDENTITY_CONFLICT, DRIFT_COMPANY_IDENTITY_CONFLICT,
    DRIFT_ADDRESS_IDENTITY_CONFLICT, DRIFT_VENDORCREDIT_IDENTITY_CONFLICT,
    DRIFT_ATTACHMENT_IDENTITY_CONFLICT, DRIFT_PAYMENT_TERM_IDENTITY_CONFLICT,
    DRIFT_VENDOR_IDENTITY_CONFLICT, DRIFT_COST_CODE_IDENTITY_CONFLICT,
    DRIFT_SUB_COST_CODE_IDENTITY_CONFLICT, DRIFT_BILL_IDENTITY_CONFLICT,
    DRIFT_EXPENSE_IDENTITY_CONFLICT, DRIFT_INVOICE_IDENTITY_CONFLICT,
    DRIFT_BILL_LINE_ITEM_IDENTITY_CONFLICT, DRIFT_INVOICE_LINE_ITEM_IDENTITY_CONFLICT,
    DRIFT_EXPENSE_LINE_ITEM_IDENTITY_CONFLICT, DRIFT_BILL_CREDIT_LINE_ITEM_IDENTITY_CONFLICT,
})
