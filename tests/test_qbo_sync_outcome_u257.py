"""U-257: SyncOutcome provenance seal, project_records helper, dict-shape parity, failure_reasons."""

from __future__ import annotations

import contextlib
import logging
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from integrations.intuit.qbo.base.sync_outcome import SyncOutcome, project_records
from integrations.intuit.qbo.base import watermark as watermark_module
from integrations.intuit.qbo.base.watermark import WatermarkRun


# --------------------------------------------------------------------------- #
# (a) Provenance seal
# --------------------------------------------------------------------------- #


def test_sync_outcome_constructor_rejects_from_service_pull_kwarg():
    with pytest.raises(TypeError):
        SyncOutcome(from_service_pull=True)


def test_sync_outcome_from_service_pull_attribute_is_read_only():
    outcome = SyncOutcome()
    with pytest.raises(AttributeError):
        outcome.from_service_pull = True


def test_for_service_pull_stamps_provenance_and_forwards_kwargs():
    outcome = SyncOutcome.for_service_pull(fetched=3, synced=[])
    assert outcome.from_service_pull is True
    assert outcome.fetched == 3
    assert outcome.synced == []


def test_for_service_pull_rejects_provenance_kwargs():
    with pytest.raises(TypeError):
        SyncOutcome.for_service_pull(from_service_pull=False, fetched=1)
    with pytest.raises(TypeError):
        SyncOutcome.for_service_pull(_from_service_pull=True, fetched=1)


# --------------------------------------------------------------------------- #
# (b) project_records helper + vendorcredit normalization
# --------------------------------------------------------------------------- #


def test_project_records_success_logs_and_counts(caplog):
    outcome = SyncOutcome()
    record = SimpleNamespace(qbo_id="QB-1")

    with caplog.at_level(logging.INFO):
        project_records(
            [record],
            outcome,
            label="Test->Module",
            project_one=lambda _r: None,
            logger=logging.getLogger("test.project_records"),
        )

    assert outcome.projected_count == 1
    assert "Synced Test->Module QB-1" in caplog.text


def test_project_records_failure_records_via_record_projection_error():
    outcome = SyncOutcome()

    def _boom(_record):
        raise ValueError("vendor not mapped")

    project_records(
        [SimpleNamespace(qbo_id="QB-2")],
        outcome,
        label="Test->Module",
        project_one=_boom,
        logger=logging.getLogger("test.project_records"),
    )

    assert outcome.projected_count == 0
    assert outcome.skipped_ids == ["QB-2"]
    assert outcome.failure_reasons["skip:QB-2"] == "vendor not mapped"


def test_vendorcredit_sync_to_bill_credits_empty_input_returns_early():
    from integrations.intuit.qbo.vendorcredit.business.service import QboVendorCreditService

    service = QboVendorCreditService()
    outcome = SyncOutcome()
    service._sync_to_bill_credits([], outcome)
    assert outcome.projected_count == 0


def test_vendorcredit_sync_to_bill_credits_logs_success(caplog):
    from integrations.intuit.qbo.vendorcredit.business.service import QboVendorCreditService

    vc = SimpleNamespace(id=99, qbo_id="VC-99")
    outcome = SyncOutcome()
    service = QboVendorCreditService()

    with caplog.at_level(logging.INFO), patch.object(
        service.repo, "read_lines_by_vendor_credit_id", return_value=[]
    ), patch(
        "integrations.intuit.qbo.vendorcredit.connector.bill_credit.business.service.VendorCreditBillCreditConnector"
    ) as connector_cls:
        connector_cls.return_value.sync_from_qbo_vendor_credit.return_value = SimpleNamespace(id=1)
        service._sync_to_bill_credits([vc], outcome)

    assert outcome.projected_count == 1
    assert "Synced VendorCredit->BillCredit VC-99" in caplog.text


# --------------------------------------------------------------------------- #
# (c) Early-return dict key parity (bill / vendorcredit / purchase)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "module_name",
    [
        "scripts.sync_qbo_account",
        "scripts.sync_qbo_bill",
        "scripts.sync_qbo_customer",
        "scripts.sync_qbo_invoice",
        "scripts.sync_qbo_item",
        "scripts.sync_qbo_purchase",
        "scripts.sync_qbo_term",
        "scripts.sync_qbo_vendor",
        "scripts.sync_qbo_vendorcredit",
    ],
)
def test_sync_qbo_to_local_early_and_late_return_dict_keys_match(module_name):
    import ast
    import importlib

    path = importlib.import_module(module_name).__file__
    tree = ast.parse(open(path, encoding="utf-8").read())
    return_dict_key_sets: list[set[str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "sync_qbo_to_local":
            for child in ast.walk(node):
                if isinstance(child, ast.Return) and isinstance(child.value, ast.Tuple):
                    dict_node = child.value.elts[0]
                    if isinstance(dict_node, ast.Dict):
                        keys = {k.value for k in dict_node.keys if isinstance(k, ast.Constant)}
                        return_dict_key_sets.append(keys)
    assert len(return_dict_key_sets) >= 2
    late_keys = return_dict_key_sets[-1]
    for keys in return_dict_key_sets:
        assert keys == late_keys, f"{module_name}: {keys ^ late_keys} differ between returns"


# --------------------------------------------------------------------------- #
# (d) failure_reasons diagnostics
# --------------------------------------------------------------------------- #


def test_failure_reasons_populated_for_staging_projection_and_skip():
    outcome = SyncOutcome()
    outcome.record_staging_failure("s1", RuntimeError("db timeout"))
    outcome.record_projection_failure("p1", ValueError("connector blew up"))
    outcome.record_staging_skip("k1", reason="malformed QBO row")

    assert outcome.failure_reasons["staging:s1"] == "db timeout"
    assert outcome.failure_reasons["projection:p1"] == "connector blew up"
    assert outcome.failure_reasons["staging_skip:k1"] == "malformed QBO row"
    assert outcome.summary()["failure_reasons"] == outcome.failure_reasons


def test_failure_reasons_namespaced_when_staging_and_projection_share_same_id():
    outcome = SyncOutcome()
    outcome.record_staging_failure("123", "staging upsert failed")
    outcome.record_projection_failure("123", "projection connector failed")

    assert outcome.failure_reasons["staging:123"] == "staging upsert failed"
    assert outcome.failure_reasons["projection:123"] == "projection connector failed"

    reason = outcome.hold_reason()
    assert reason is not None
    assert "123 (staging upsert failed)" in reason
    assert "123 (projection connector failed)" in reason


def test_hold_reason_includes_failure_reason_text():
    outcome = SyncOutcome()
    outcome.record_staging_failure("99", "upsert returned no row")
    outcome.record_projection_failure("42", "sync returned falsy")

    reason = outcome.hold_reason()
    assert reason is not None
    assert "99 (upsert returned no row)" in reason
    assert "42 (sync returned falsy)" in reason


def test_record_bound_forced_advance_details_include_failure_reasons():
    outcome = SyncOutcome.for_service_pull()
    outcome.record_staging_failure("QB-staging", "VendorCredit upsert returned no row")
    outcome.record_projection_failure("7", "sync_from_qbo_vendor_credit returned no BillCredit row")

    recorded = []

    def _fake_record_mapping_issue(_repo, **kwargs):
        recorded.append(kwargs)

    mock_auth_service = Mock(return_value=Mock(read_all=Mock(return_value=[SimpleNamespace(realm_id="realm-1")])))
    with contextlib.ExitStack() as stack:
        stack.enter_context(
            patch("integrations.intuit.qbo.auth.business.service.QboAuthService", mock_auth_service)
        )
        stack.enter_context(
            patch("integrations.intuit.qbo.reconciliation.persistence.repo.ReconciliationIssueRepository", Mock())
        )
        stack.enter_context(
            patch(
                "integrations.intuit.qbo.base.reconciliation_recorder.record_mapping_issue",
                side_effect=_fake_record_mapping_issue,
            )
        )
        run = WatermarkRun(
            sync_service=Mock(),
            provider="qbo",
            env="prod",
            entity="vendorcredit",
        )
        run.sync_record = SimpleNamespace(
            id=1,
            entity="vendorcredit",
            last_sync_datetime=None,
            hold_started_datetime=None,
            modified_datetime=None,
            created_datetime=None,
        )
        run._record_bound_forced_advance(outcome, held=__import__("datetime").timedelta(hours=3))

    assert len(recorded) == 2
    staging_details = next(r["details"] for r in recorded if r["qbo_id"] == "QB-staging")
    projection_details = next(r["details"] for r in recorded if r["qbo_id"] == "7")
    assert "VendorCredit upsert returned no row" in staging_details
    assert "sync_from_qbo_vendor_credit returned no BillCredit row" in projection_details
