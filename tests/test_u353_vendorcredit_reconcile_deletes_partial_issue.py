"""U-353 regression: QboVendorCreditService._reconcile_deleted_vendor_credits must
record a partial-delete ReconciliationIssue whenever it made ANY destructive
progress before a later step fails — not just when a mapping row was deleted —
and must describe accurately what was actually destroyed.

Before U-353, Step 2 (delete the BillCredit) only ran INSIDE the same `if
bc_mapping:` block that also deleted the qbo.VendorCreditBillCredit mapping row,
so "the header got deleted" and "mapping_removed=True" were structurally coupled,
and the recorded label ("VendorCreditLineItemBillCreditLineItem") was always
accurate. U-353 repointed Step 2 onto a direct dbo-native identity lookup (no
more mapping table), decoupling the two:
  - A run with zero line mappings but a successful BillCredit delete, followed
    by a Step 3 (QboVendorCredit staging delete) failure, would silently skip
    the partial-delete issue if the flag still only tracked line mappings
    (Codex xhigh round-1 P3, confirmed real).
  - Even once fixed to record an issue, a static "VendorCreditLineItemBill
    CreditLineItem" label would misdescribe what happened whenever it was the
    BillCredit header (not a line mapping) that got destroyed (code-review
    Angle A, round 2, confirmed real).
  - A failure INSIDE bill_credit_service.delete_by_public_id itself (not just
    a later step) must also be recorded — the destructive flag must be set
    BEFORE the attempt, not only after it returns successfully (code-review
    Angle A, round 2, confirmed real).
"""
from types import SimpleNamespace
from unittest.mock import Mock, patch

from integrations.intuit.qbo.vendorcredit.business.service import QboVendorCreditService

_SERVICE = "integrations.intuit.qbo.vendorcredit.business.service"
_DELETE_RECONCILE = "integrations.intuit.qbo.base.delete_reconcile"
_LINE_MAPPING_REPO = (
    "integrations.intuit.qbo.vendorcredit.connector.bill_credit_line_item.persistence.repo"
    ".VendorCreditLineItemBillCreditLineItemMappingRepository"
)
_BILL_CREDIT_SERVICE = "entities.bill_credit.business.service.BillCreditService"


def _local_vc(*, vc_id=10, qbo_id="VC-1"):
    return SimpleNamespace(id=vc_id, qbo_id=qbo_id)


def _run_reconcile(*, bill_credit_hit, staging_delete_error=None, delete_error=None):
    repo = Mock()
    repo.read_by_realm_id.return_value = [_local_vc()]
    repo.read_lines_by_vendor_credit_id.return_value = []  # no lines -> no line mappings
    repo.delete_by_qbo_id.side_effect = staging_delete_error

    svc = QboVendorCreditService(repo=repo)

    line_mapping_repo = Mock()
    bill_credit_service = Mock()
    bill_credit_service.read_by_qbo_identity.return_value = bill_credit_hit
    if delete_error is not None:
        bill_credit_service.delete_by_public_id.side_effect = delete_error

    fake_client = Mock()
    fake_client.__enter__ = Mock(return_value=fake_client)
    fake_client.__exit__ = Mock(return_value=False)

    with patch(f"{_SERVICE}.QboVendorCreditClient", return_value=fake_client), patch(
        f"{_DELETE_RECONCILE}.strict_confirmed_deleted_ids", return_value={"VC-1"}
    ), patch(f"{_LINE_MAPPING_REPO}", return_value=line_mapping_repo), patch(
        f"{_BILL_CREDIT_SERVICE}", return_value=bill_credit_service
    ), patch(
        f"{_DELETE_RECONCILE}.record_partial_delete_issue"
    ) as mock_record_issue:
        svc._reconcile_deleted_vendor_credits("realm-1")

    return bill_credit_service, mock_record_issue


def test_billcredit_deleted_no_line_mappings_staging_failure_still_records_issue():
    """The exact gap Codex found: BillCredit delete succeeds, no line mappings
    existed, THEN the staging delete fails — must still record a partial-delete
    issue (destructive work — the BillCredit is gone — did happen), and the
    label must name the BillCredit header, not the (untouched) line mapping."""
    bill_credit = SimpleNamespace(id=99, public_id="bc-pub-99")

    bill_credit_service, mock_record_issue = _run_reconcile(
        bill_credit_hit=bill_credit,
        staging_delete_error=RuntimeError("staging delete failed"),
    )

    bill_credit_service.delete_by_public_id.assert_called_once_with("bc-pub-99")
    mock_record_issue.assert_called_once()
    kwargs = mock_record_issue.call_args.kwargs
    assert kwargs["entity_type"] == "VendorCredit"
    assert kwargs["qbo_id"] == "VC-1"
    assert kwargs["mapping_label"] == "BillCredit header"
    assert "VendorCreditLineItemBillCreditLineItem" not in kwargs["mapping_label"]


def test_delete_by_public_id_raising_still_records_issue():
    """code-review Angle A (round 2): a failure INSIDE delete_by_public_id itself
    (e.g. the U-353 deploy-gap bridge inside it silently swallowing an unrelated
    DB error, then the header delete 547s) must still be recorded — the
    destructive flag is set BEFORE the attempt, not only after success."""
    bill_credit = SimpleNamespace(id=99, public_id="bc-pub-99")

    bill_credit_service, mock_record_issue = _run_reconcile(
        bill_credit_hit=bill_credit,
        delete_error=RuntimeError("547: still referenced"),
    )

    bill_credit_service.delete_by_public_id.assert_called_once_with("bc-pub-99")
    mock_record_issue.assert_called_once()
    kwargs = mock_record_issue.call_args.kwargs
    assert kwargs["mapping_label"] == "BillCredit header"


def test_no_destructive_work_staging_failure_does_not_record_issue():
    """Sanity check on the other side: no BillCredit match, no line mappings, THEN
    the staging delete fails — nothing destructive happened, so no issue."""
    bill_credit_service, mock_record_issue = _run_reconcile(
        bill_credit_hit=None,
        staging_delete_error=RuntimeError("staging delete failed"),
    )

    bill_credit_service.delete_by_public_id.assert_not_called()
    mock_record_issue.assert_not_called()
