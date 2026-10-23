"""Pure-logic tests for U-718 bite 2 — QBO staging delete reconcile (CDC + full)."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from integrations.intuit.qbo.base.errors import (
    QboBudgetExceededError,
    QboNotFoundError,
    QboUnexpectedError,
)
from integrations.intuit.qbo.base.delete_reconcile import strict_confirmed_deleted_ids


def _confirm_deleted_ids():
    from integrations.intuit.qbo.base.delete_reconcile import confirm_deleted_ids

    return confirm_deleted_ids

REALM = "realm-u718"


# --------------------------------------------------------------------------- #
# CDC parse (fetch_cdc_deleted_ids)
# --------------------------------------------------------------------------- #


def _import_cdc():
    from integrations.intuit.qbo.base.cdc import fetch_cdc_deleted_ids

    return fetch_cdc_deleted_ids


def _utc_now():
    return datetime.now(timezone.utc)


def test_cdc_parses_deleted_vs_active_entries():
    fetch_cdc_deleted_ids = _import_cdc()
    http = MagicMock()
    http.get.return_value = {
        "CDCResponse": [
            {
                "QueryResponse": [
                    {
                        "Bill": [
                            {"Id": "1", "status": "Deleted"},
                            {"Id": "2", "status": "Updated"},
                        ],
                        "Purchase": [{"Id": "9", "status": "Deleted"}],
                    }
                ]
            }
        ]
    }
    since = _utc_now() - timedelta(hours=1)
    out = fetch_cdc_deleted_ids(
        http,
        entities=["Bill", "Purchase"],
        changed_since=since,
    )
    assert out["Bill"] == {"1"}
    assert out["Purchase"] == {"9"}


def test_cdc_missing_cdc_response_raises():
    fetch_cdc_deleted_ids = _import_cdc()
    http = MagicMock()
    http.get.return_value = {"QueryResponse": []}
    with pytest.raises(QboUnexpectedError):
        fetch_cdc_deleted_ids(http, entities=["Bill"], changed_since=_utc_now() - timedelta(hours=1))


def test_cdc_fault_raises():
    fetch_cdc_deleted_ids = _import_cdc()
    http = MagicMock()
    http.get.return_value = {"Fault": {"Error": [{"code": "1"}]}}
    with pytest.raises(QboUnexpectedError):
        fetch_cdc_deleted_ids(http, entities=["Bill"], changed_since=_utc_now() - timedelta(hours=1))


def test_cdc_naive_changed_since_raises():
    fetch_cdc_deleted_ids = _import_cdc()
    naive = datetime(2026, 10, 1, 12, 0, 0)
    with pytest.raises(ValueError, match="timezone-aware"):
        fetch_cdc_deleted_ids(MagicMock(), entities=["Bill"], changed_since=naive)


def test_cdc_changed_since_older_than_30_days_raises():
    fetch_cdc_deleted_ids = _import_cdc()
    old = _utc_now() - timedelta(days=31)
    with pytest.raises(ValueError, match="30"):
        fetch_cdc_deleted_ids(MagicMock(), entities=["Bill"], changed_since=old)


def test_cdc_fault_inside_cdc_response_raises():
    fetch_cdc_deleted_ids = _import_cdc()
    http = MagicMock()
    http.get.return_value = {
        "CDCResponse": [{"Fault": {"Error": [{"code": "1"}]}}],
    }
    with pytest.raises(QboUnexpectedError):
        fetch_cdc_deleted_ids(http, entities=["Bill"], changed_since=_utc_now() - timedelta(hours=1))


def test_cdc_fault_inside_query_response_raises():
    fetch_cdc_deleted_ids = _import_cdc()
    http = MagicMock()
    http.get.return_value = {
        "CDCResponse": [
            {"QueryResponse": [{"Fault": {"Error": [{"code": "1"}]}}]}
        ],
    }
    with pytest.raises(QboUnexpectedError):
        fetch_cdc_deleted_ids(http, entities=["Bill"], changed_since=_utc_now() - timedelta(hours=1))


def test_cdc_cdc_response_null_element_raises():
    fetch_cdc_deleted_ids = _import_cdc()
    http = MagicMock()
    http.get.return_value = {"CDCResponse": [None]}
    with pytest.raises(QboUnexpectedError):
        fetch_cdc_deleted_ids(http, entities=["Bill"], changed_since=_utc_now() - timedelta(hours=1))


def test_cdc_query_response_null_raises():
    fetch_cdc_deleted_ids = _import_cdc()
    http = MagicMock()
    http.get.return_value = {
        "CDCResponse": [{"QueryResponse": None}],
    }
    with pytest.raises(QboUnexpectedError):
        fetch_cdc_deleted_ids(http, entities=["Bill"], changed_since=_utc_now() - timedelta(hours=1))


def test_cdc_entity_payload_null_raises():
    fetch_cdc_deleted_ids = _import_cdc()
    http = MagicMock()
    http.get.return_value = {
        "CDCResponse": [{"QueryResponse": [{"Bill": None}]}],
    }
    with pytest.raises(QboUnexpectedError):
        fetch_cdc_deleted_ids(http, entities=["Bill"], changed_since=_utc_now() - timedelta(hours=1))


def test_cdc_absent_entity_key_means_zero_rows():
    fetch_cdc_deleted_ids = _import_cdc()
    http = MagicMock()
    http.get.return_value = {
        "CDCResponse": [{"QueryResponse": [{}]}],
    }
    since = _utc_now() - timedelta(hours=1)
    out = fetch_cdc_deleted_ids(http, entities=["Bill"], changed_since=since)
    assert out["Bill"] == set()


def test_cdc_query_carries_explicit_utc_offset():
    fetch_cdc_deleted_ids = _import_cdc()
    http = MagicMock()
    http.get.return_value = {
        "CDCResponse": [{"QueryResponse": [{"Bill": []}]}]
    }
    # Relative to now: a fixed date ages past the 30-day CDC window and breaks the test.
    since = (_utc_now() - timedelta(hours=2)).replace(microsecond=0)
    fetch_cdc_deleted_ids(http, entities=["Bill"], changed_since=since)
    params = http.get.call_args.kwargs.get("params") or http.get.call_args[1].get("params")
    assert "+00:00" in params["changedSince"]


# --------------------------------------------------------------------------- #
# confirm_deleted_ids / strict_confirmed_deleted_ids refactor
# --------------------------------------------------------------------------- #


def test_confirm_404_confirms_set():
    confirm_deleted_ids = _confirm_deleted_ids()
    gets = []

    def confirm(qbo_id):
        gets.append(qbo_id)
        if qbo_id == "gone":
            raise QboNotFoundError("deleted")
        return {"Id": qbo_id}

    confirmed = confirm_deleted_ids(
        entity_type="Bill",
        realm_id=REALM,
        candidates=["gone", "alive"],
        confirm_get=confirm,
    )
    assert confirmed == {"gone"}
    assert gets == ["gone", "alive"]


def test_confirm_systemic_aborts_third_never_called():
    confirm_deleted_ids = _confirm_deleted_ids()
    calls = []

    def confirm(qbo_id):
        calls.append(qbo_id)
        if qbo_id == "a":
            raise QboNotFoundError("gone")
        if qbo_id == "b":
            raise QboBudgetExceededError("blocked")
        return object()

    result = confirm_deleted_ids(
        entity_type="Bill",
        realm_id=REALM,
        candidates=["a", "b", "c"],
        confirm_get=confirm,
    )
    assert result is None
    assert calls == ["a", "b"]


def test_strict_max_candidates_zero_raises_before_live_fetch():
    fetch = MagicMock()
    with pytest.raises(ValueError, match="max_candidates"):
        strict_confirmed_deleted_ids(
            entity_type="Bill",
            realm_id=REALM,
            fetch_live_ids=fetch,
            confirm_get=MagicMock(),
            local_qbo_ids=["1", "2"],
            max_candidates=0,
        )
    fetch.assert_not_called()


def test_strict_max_candidates_minus_one_raises_before_live_fetch():
    fetch = MagicMock()
    with pytest.raises(ValueError, match="max_candidates"):
        strict_confirmed_deleted_ids(
            entity_type="Bill",
            realm_id=REALM,
            fetch_live_ids=fetch,
            confirm_get=MagicMock(),
            local_qbo_ids=["1"],
            max_candidates=-1,
        )
    fetch.assert_not_called()


def test_strict_ceiling_exceeded_record_issue_false_skips_issue_write(monkeypatch):
    monkeypatch.setenv("QBO_PULL_DELETE_MAX_CANDIDATES", "1")
    with patch(
        "integrations.intuit.qbo.base.delete_reconcile.record_delete_reconcile_issue",
    ) as record_issue:
        result = strict_confirmed_deleted_ids(
            entity_type="Bill",
            realm_id=REALM,
            fetch_live_ids=lambda: [],
            confirm_get=MagicMock(),
            local_qbo_ids=["10", "11"],
            max_candidates=1,
            record_issue=False,
        )
    assert result is None
    record_issue.assert_not_called()


def test_confirm_systemic_log_includes_remaining():
    confirm_deleted_ids = _confirm_deleted_ids()
    from integrations.intuit.qbo.base.errors import QboBudgetExceededError

    with patch("integrations.intuit.qbo.base.delete_reconcile.logger") as log_mock:
        confirm_deleted_ids(
            entity_type="Bill",
            realm_id=REALM,
            candidates=["a", "b", "c"],
            confirm_get=lambda qid: (_ for _ in ()).throw(
                QboNotFoundError("gone") if qid == "a" else QboBudgetExceededError("x")
            ),
        )
    warning_calls = [
        c for c in log_mock.warning.call_args_list if c.kwargs.get("extra", {}).get("remaining") == 2
    ]
    assert len(warning_calls) == 1


def test_strict_max_candidates_override_lifts_env_ceiling(monkeypatch):
    monkeypatch.setenv("QBO_PULL_DELETE_MAX_CANDIDATES", "2")
    confirm = MagicMock(side_effect=lambda qid: (_ for _ in ()).throw(QboNotFoundError("x")))

    result = strict_confirmed_deleted_ids(
        entity_type="Bill",
        realm_id=REALM,
        fetch_live_ids=lambda: [],
        confirm_get=confirm,
        local_qbo_ids=["10", "11", "12"],
        max_candidates=10,
    )
    assert result == {"10", "11", "12"}
    assert confirm.call_count == 3


# --------------------------------------------------------------------------- #
# Staging delete service (injected fakes)
# --------------------------------------------------------------------------- #


def _make_service(**kwargs):
    from integrations.intuit.qbo.staging_delete.business.service import (
        QboStagingDeleteReconcileService,
    )

    defaults = dict(
        bill_repo=MagicMock(),
        purchase_repo=MagicMock(),
        vendor_credit_repo=MagicMock(),
        invoice_repo=MagicMock(),
        reimburse_charge_repo=MagicMock(),
        bill_client=MagicMock(),
        purchase_client=MagicMock(),
        vendor_credit_client=MagicMock(),
        invoice_client=MagicMock(),
        bill_service_factory=lambda: MagicMock(),
        expense_service_factory=lambda: MagicMock(),
        bill_credit_service_factory=lambda: MagicMock(),
        invoice_service_factory=lambda: MagicMock(),
        reconciliation_repo_factory=lambda: MagicMock(),
        fetch_cdc_deleted_ids=lambda *a, **k: {e: set() for e in k.get("entities", [])},
    )
    defaults.update(kwargs)
    return QboStagingDeleteReconcileService(**defaults)


def test_cdc_confirm_then_staging_delete():
    from integrations.intuit.qbo.staging_delete.business.service import (
        QboStagingDeleteReconcileService,
    )

    bill_repo = MagicMock()
    bill_repo.read_by_qbo_id_and_realm_id.return_value = MagicMock(qbo_id="42")
    bill_client = MagicMock()
    bill_client._http_client = MagicMock()
    svc = QboStagingDeleteReconcileService(
        bill_repo=bill_repo,
        purchase_repo=MagicMock(),
        vendor_credit_repo=MagicMock(),
        invoice_repo=MagicMock(),
        reimburse_charge_repo=MagicMock(),
        bill_client=bill_client,
        purchase_client=MagicMock(),
        vendor_credit_client=MagicMock(),
        invoice_client=MagicMock(),
        bill_service_factory=lambda: MagicMock(read_by_qbo_identity=MagicMock(return_value=None)),
    )
    empty_cdc = {
        "Bill": {"42"},
        "Purchase": set(),
        "VendorCredit": set(),
        "Invoice": set(),
        "ReimburseCharge": set(),
    }
    with patch(
        "integrations.intuit.qbo.staging_delete.business.service.fetch_cdc_deleted_ids",
        return_value=empty_cdc,
    ), patch(
        "integrations.intuit.qbo.staging_delete.business.service.confirm_deleted_ids",
        return_value={"42"},
    ):
        out = svc.run_cdc(REALM, dry_run=False)
    assert out["entities"]["Bill"]["staging_deleted"] == 1
    bill_repo.delete_by_qbo_id_and_realm_id.assert_called_once_with("42", REALM)


def test_cdc_deleted_not_in_staging_skips_get():
    with patch(
        "integrations.intuit.qbo.staging_delete.business.service.fetch_cdc_deleted_ids",
        return_value={"Bill": {"99"}, "Purchase": set(), "VendorCredit": set(), "Invoice": set(), "ReimburseCharge": set()},
    ), patch(
        "integrations.intuit.qbo.staging_delete.business.service.confirm_deleted_ids",
    ) as confirm_mock:
        from integrations.intuit.qbo.staging_delete.business.service import (
            QboStagingDeleteReconcileService,
        )

        bill_repo = MagicMock()
        bill_repo.read_by_qbo_id_and_realm_id.return_value = None
        bill_client = MagicMock()
        bill_client._http_client = MagicMock()
        svc = QboStagingDeleteReconcileService(
            bill_repo=bill_repo,
            purchase_repo=MagicMock(),
            vendor_credit_repo=MagicMock(),
            invoice_repo=MagicMock(),
            reimburse_charge_repo=MagicMock(),
            bill_client=bill_client,
            purchase_client=MagicMock(),
            vendor_credit_client=MagicMock(),
            invoice_client=MagicMock(),
        )
        svc.run_cdc(REALM)
    confirm_mock.assert_not_called()
    bill_repo.delete_by_qbo_id_and_realm_id.assert_not_called()


def test_invoice_bound_creates_issue_then_deletes_staging():
    from integrations.intuit.qbo.staging_delete.business.service import (
        QboStagingDeleteReconcileService,
        _EntityRuntime,
    )

    recon = MagicMock(read_unresolved_issue_keys_by_drift_type=MagicMock(return_value=[]))
    dbo_invoice = MagicMock(
        public_id="inv-pub",
        invoice_number="INV-1",
        total_amount=Decimal("100.00"),
        is_draft=True,
    )
    invoice_svc = MagicMock(read_by_qbo_identity=MagicMock(return_value=dbo_invoice))
    invoice_repo = MagicMock()
    invoice_repo.delete_by_qbo_id = MagicMock()
    runtime = _EntityRuntime(
        name="Invoice",
        staging_repo=invoice_repo,
        confirm_get=MagicMock(),
        fetch_live_ids=MagicMock(),
        delete_staging=lambda qid, rid: invoice_repo.delete_by_qbo_id_and_realm_id(qid, rid),
    )

    svc = QboStagingDeleteReconcileService(
        invoice_repo=invoice_repo,
        invoice_service_factory=lambda: invoice_svc,
        reconciliation_repo_factory=lambda: recon,
    )
    svc._act_on_confirmed("Invoice", REALM, "55", dry_run=False, runtime=runtime)
    recon.create.assert_called_once()
    assert recon.create.call_args.kwargs["drift_type"] == "qbo_deleted"
    invoice_repo.delete_by_qbo_id_and_realm_id.assert_called_once_with("55", REALM)


def test_invoice_issue_already_open_skips_create_still_deletes():
    from integrations.intuit.qbo.staging_delete.business.service import (
        QboStagingDeleteReconcileService,
        _EntityRuntime,
    )

    recon = MagicMock(
        read_unresolved_issue_keys_by_drift_type=MagicMock(
            return_value=[(REALM, "Invoice", "55")]
        )
    )
    invoice_svc = MagicMock(read_by_qbo_identity=MagicMock(return_value=MagicMock(public_id="p")))
    invoice_repo = MagicMock()
    runtime = _EntityRuntime(
        name="Invoice",
        staging_repo=invoice_repo,
        confirm_get=MagicMock(),
        fetch_live_ids=MagicMock(),
        delete_staging=lambda qid, rid: invoice_repo.delete_by_qbo_id_and_realm_id(qid, rid),
    )

    svc = QboStagingDeleteReconcileService(
        invoice_repo=invoice_repo,
        reconciliation_repo_factory=lambda: recon,
        invoice_service_factory=lambda: invoice_svc,
    )
    svc._act_on_confirmed("Invoice", REALM, "55", dry_run=False, runtime=runtime)
    recon.create.assert_not_called()
    invoice_repo.delete_by_qbo_id_and_realm_id.assert_called_once_with("55", REALM)


def test_invoice_issue_write_fails_no_staging_delete():
    from integrations.intuit.qbo.staging_delete.business.service import (
        QboStagingDeleteReconcileService,
        _EntityRuntime,
    )

    recon = MagicMock(
        read_unresolved_issue_keys_by_drift_type=MagicMock(return_value=[]),
        create=MagicMock(side_effect=RuntimeError("db down")),
    )
    invoice_svc = MagicMock(read_by_qbo_identity=MagicMock(return_value=MagicMock(
        public_id="p", invoice_number="n", total_amount=Decimal("1"), is_draft=False
    )))
    invoice_repo = MagicMock()
    runtime = _EntityRuntime(
        name="Invoice",
        staging_repo=invoice_repo,
        confirm_get=MagicMock(),
        fetch_live_ids=MagicMock(),
        delete_staging=lambda qid, rid: invoice_repo.delete_by_qbo_id_and_realm_id(qid, rid),
    )

    svc = QboStagingDeleteReconcileService(
        invoice_repo=invoice_repo,
        reconciliation_repo_factory=lambda: recon,
        invoice_service_factory=lambda: invoice_svc,
    )
    counts = svc._empty_entity_counts()
    svc._act_on_confirmed(
        "Invoice", REALM, "55", dry_run=False, counts=counts, runtime=runtime
    )
    assert counts["flag_errors"] == 1
    assert counts["staging_deleted"] == 0
    invoice_repo.delete_by_qbo_id_and_realm_id.assert_not_called()


def test_bill_bound_dbo_still_bound_no_issue_staging_deleted():
    from integrations.intuit.qbo.staging_delete.business.service import (
        QboStagingDeleteReconcileService,
        _EntityRuntime,
    )

    bill_svc = MagicMock(read_by_qbo_identity=MagicMock(return_value=MagicMock()))
    bill_repo = MagicMock()
    recon = MagicMock()
    runtime = _EntityRuntime(
        name="Bill",
        staging_repo=bill_repo,
        confirm_get=MagicMock(),
        fetch_live_ids=MagicMock(),
        delete_staging=lambda qid, rid: bill_repo.delete_by_qbo_id_and_realm_id(qid, rid),
        dbo_still_bound=lambda qid, rid: bill_svc.read_by_qbo_identity(qid, rid) is not None,
    )

    svc = QboStagingDeleteReconcileService(
        bill_repo=bill_repo,
        bill_service_factory=lambda: bill_svc,
        reconciliation_repo_factory=lambda: recon,
    )
    counts = svc._empty_entity_counts()
    svc._act_on_confirmed(
        "Bill", REALM, "7", dry_run=False, counts=counts, runtime=runtime
    )
    assert counts["dbo_still_bound"] == 1
    recon.create.assert_not_called()
    bill_repo.delete_by_qbo_id_and_realm_id.assert_called_once_with("7", REALM)


def test_dry_run_no_writes():
    from integrations.intuit.qbo.staging_delete.business.service import (
        QboStagingDeleteReconcileService,
        _EntityRuntime,
    )

    bill_repo = MagicMock()
    runtime = _EntityRuntime(
        name="Bill",
        staging_repo=bill_repo,
        confirm_get=MagicMock(),
        fetch_live_ids=MagicMock(),
        delete_staging=lambda qid, rid: bill_repo.delete_by_qbo_id_and_realm_id(qid, rid),
    )
    svc = QboStagingDeleteReconcileService(bill_repo=bill_repo)
    counts = svc._empty_entity_counts()
    svc._act_on_confirmed(
        "Bill", REALM, "1", dry_run=True, counts=counts, runtime=runtime
    )
    assert counts["staging_deleted"] == 1
    bill_repo.delete_by_qbo_id_and_realm_id.assert_not_called()


def test_full_mode_ceiling_aborts_one_entity_others_continue(monkeypatch):
    monkeypatch.setenv("QBO_PULL_DELETE_MAX_CANDIDATES", "1")
    from integrations.intuit.qbo.staging_delete.business.service import (
        QboStagingDeleteReconcileService,
    )

    bill_repo = MagicMock()
    bill_repo.read_by_realm_id.return_value = [MagicMock(qbo_id="1"), MagicMock(qbo_id="2")]
    purchase_repo = MagicMock()
    purchase_repo.read_by_realm_id.return_value = [MagicMock(qbo_id="p1")]

    with patch(
        "integrations.intuit.qbo.staging_delete.business.service.strict_confirmed_deleted_ids",
        side_effect=[None, {"p1"}],
    ):
        svc = QboStagingDeleteReconcileService(
            bill_repo=bill_repo,
            purchase_repo=purchase_repo,
            vendor_credit_repo=MagicMock(read_by_realm_id=MagicMock(return_value=[])),
            invoice_repo=MagicMock(read_by_realm_id=MagicMock(return_value=[])),
            reimburse_charge_repo=MagicMock(read_by_realm_id=MagicMock(return_value=[])),
            bill_client=MagicMock(),
            purchase_client=MagicMock(),
            vendor_credit_client=MagicMock(),
            invoice_client=MagicMock(),
            expense_service_factory=lambda: MagicMock(read_by_qbo_identity=MagicMock(return_value=None)),
        )
        out = svc.run_full(REALM, entities=["Bill", "Purchase"])
    assert out["entities"]["Bill"]["aborted"] is not None
    assert out["entities"]["Bill"]["staging_deleted"] == 0
    assert out["entities"]["Purchase"]["staging_deleted"] == 1
    purchase_repo.delete_by_qbo_id_and_realm_id.assert_called_once_with("p1", REALM)


def test_full_mode_max_candidates_override(monkeypatch):
    monkeypatch.setenv("QBO_PULL_DELETE_MAX_CANDIDATES", "1")
    from integrations.intuit.qbo.staging_delete.business.service import (
        QboStagingDeleteReconcileService,
    )

    bill_repo = MagicMock()
    bill_repo.read_by_realm_id.return_value = [MagicMock(qbo_id="1"), MagicMock(qbo_id="2")]

    with patch(
        "integrations.intuit.qbo.staging_delete.business.service.strict_confirmed_deleted_ids",
        return_value={"1", "2"},
    ) as strict_mock:
        svc = QboStagingDeleteReconcileService(
            bill_repo=bill_repo,
            vendor_credit_repo=MagicMock(read_by_realm_id=MagicMock(return_value=[])),
            purchase_repo=MagicMock(read_by_realm_id=MagicMock(return_value=[])),
            invoice_repo=MagicMock(read_by_realm_id=MagicMock(return_value=[])),
            reimburse_charge_repo=MagicMock(read_by_realm_id=MagicMock(return_value=[])),
            bill_client=MagicMock(),
            purchase_client=MagicMock(),
            vendor_credit_client=MagicMock(),
            invoice_client=MagicMock(),
            bill_service_factory=lambda: MagicMock(read_by_qbo_identity=MagicMock(return_value=None)),
        )
        svc.run_full(REALM, entities=["Bill"], max_candidates=50)
    assert strict_mock.call_args.kwargs.get("max_candidates") == 50


def test_cdc_alive_skips_delete():
    from integrations.intuit.qbo.staging_delete.business.service import (
        QboStagingDeleteReconcileService,
    )

    bill_repo = MagicMock()
    bill_repo.read_by_qbo_id_and_realm_id.return_value = MagicMock(qbo_id="42")

    with patch(
        "integrations.intuit.qbo.staging_delete.business.service.fetch_cdc_deleted_ids",
        return_value={"Bill": {"42"}, "Purchase": set(), "VendorCredit": set(), "Invoice": set(), "ReimburseCharge": set()},
    ), patch(
        "integrations.intuit.qbo.staging_delete.business.service.confirm_deleted_ids",
        return_value=set(),
    ):
        bill_client = MagicMock()
        bill_client._http_client = MagicMock()
        svc = QboStagingDeleteReconcileService(
            bill_repo=bill_repo,
            purchase_repo=MagicMock(),
            vendor_credit_repo=MagicMock(),
            invoice_repo=MagicMock(),
            reimburse_charge_repo=MagicMock(),
            bill_client=bill_client,
            purchase_client=MagicMock(),
            vendor_credit_client=MagicMock(),
            invoice_client=MagicMock(),
        )
        out = svc.run_cdc(REALM)
    assert out["entities"]["Bill"]["staging_deleted"] == 0
    bill_repo.delete_by_qbo_id_and_realm_id.assert_not_called()


def test_cdc_systemic_confirm_aborts_entity():
    from integrations.intuit.qbo.staging_delete.business.service import (
        QboStagingDeleteReconcileService,
    )

    bill_repo = MagicMock()
    bill_repo.read_by_qbo_id_and_realm_id.return_value = MagicMock(qbo_id="42")

    with patch(
        "integrations.intuit.qbo.staging_delete.business.service.fetch_cdc_deleted_ids",
        return_value={"Bill": {"42"}, "Purchase": set(), "VendorCredit": set(), "Invoice": set(), "ReimburseCharge": set()},
    ), patch(
        "integrations.intuit.qbo.staging_delete.business.service.confirm_deleted_ids",
        return_value=None,
    ):
        bill_client = MagicMock()
        bill_client._http_client = MagicMock()
        svc = QboStagingDeleteReconcileService(
            bill_repo=bill_repo,
            purchase_repo=MagicMock(),
            vendor_credit_repo=MagicMock(),
            invoice_repo=MagicMock(),
            reimburse_charge_repo=MagicMock(),
            bill_client=bill_client,
            purchase_client=MagicMock(),
            vendor_credit_client=MagicMock(),
            invoice_client=MagicMock(),
        )
        out = svc.run_cdc(REALM)
    assert out["entities"]["Bill"]["aborted"] == "systemic_confirm_abort"
    bill_repo.delete_by_qbo_id_and_realm_id.assert_not_called()


def test_cdc_lookback_clamped_below_30_day_cdc_window(monkeypatch):
    monkeypatch.setenv("QBO_STAGING_DELETE_CDC_LOOKBACK_HOURS", "720")
    from integrations.intuit.qbo.staging_delete.business.service import (
        _clamp_lookback_hours,
    )

    assert _clamp_lookback_hours(None) == 696


def test_full_mode_real_ceiling_no_confirm_no_delete(monkeypatch):
    monkeypatch.setenv("QBO_PULL_DELETE_MAX_CANDIDATES", "1")
    from integrations.intuit.qbo.staging_delete.business.service import (
        QboStagingDeleteReconcileService,
    )

    bill_repo = MagicMock()
    bill_repo.read_by_realm_id.return_value = [
        MagicMock(qbo_id="1"),
        MagicMock(qbo_id="2"),
    ]
    bill_client = MagicMock()
    bill_client.query_all_bill_ids.return_value = []
    confirm = MagicMock()
    bill_client.get_bill = confirm

    svc = QboStagingDeleteReconcileService(
        bill_repo=bill_repo,
        purchase_repo=MagicMock(read_by_realm_id=MagicMock(return_value=[])),
        vendor_credit_repo=MagicMock(read_by_realm_id=MagicMock(return_value=[])),
        invoice_repo=MagicMock(read_by_realm_id=MagicMock(return_value=[])),
        reimburse_charge_repo=MagicMock(read_by_realm_id=MagicMock(return_value=[])),
        bill_client=bill_client,
        purchase_client=MagicMock(),
        vendor_credit_client=MagicMock(),
        invoice_client=MagicMock(),
    )
    out = svc.run_full(REALM, entities=["Bill"])
    assert out["entities"]["Bill"]["aborted"] is not None
    assert out["entities"]["Bill"]["staging_deleted"] == 0
    confirm.assert_not_called()
    bill_repo.delete_by_qbo_id_and_realm_id.assert_not_called()


def _full_mode_ceiling_bill_setup():
    from integrations.intuit.qbo.staging_delete.business.service import (
        QboStagingDeleteReconcileService,
    )

    bill_repo = MagicMock()
    bill_repo.read_by_realm_id.return_value = [
        MagicMock(qbo_id="1"),
        MagicMock(qbo_id="2"),
    ]
    bill_client = MagicMock()
    bill_client.query_all_bill_ids.return_value = []
    svc = QboStagingDeleteReconcileService(
        bill_repo=bill_repo,
        purchase_repo=MagicMock(read_by_realm_id=MagicMock(return_value=[])),
        vendor_credit_repo=MagicMock(read_by_realm_id=MagicMock(return_value=[])),
        invoice_repo=MagicMock(read_by_realm_id=MagicMock(return_value=[])),
        reimburse_charge_repo=MagicMock(read_by_realm_id=MagicMock(return_value=[])),
        bill_client=bill_client,
        purchase_client=MagicMock(),
        vendor_credit_client=MagicMock(),
        invoice_client=MagicMock(),
    )
    return svc


def test_full_mode_dry_run_ceiling_does_not_write_issue(monkeypatch):
    monkeypatch.setenv("QBO_PULL_DELETE_MAX_CANDIDATES", "1")
    with patch(
        "integrations.intuit.qbo.reconciliation.persistence.repo.ReconciliationIssueRepository.create",
        new_callable=MagicMock,
    ) as create_mock:
        svc = _full_mode_ceiling_bill_setup()
        out = svc.run_full(REALM, entities=["Bill"], dry_run=True)
    assert out["entities"]["Bill"]["aborted"] is not None
    assert out["entities"]["Bill"]["staging_deleted"] == 0
    create_mock.assert_not_called()


def test_full_mode_live_ceiling_writes_issue_once(monkeypatch):
    monkeypatch.setenv("QBO_PULL_DELETE_MAX_CANDIDATES", "1")
    with patch(
        "integrations.intuit.qbo.reconciliation.persistence.repo.ReconciliationIssueRepository.create",
        new_callable=MagicMock,
    ) as create_mock:
        svc = _full_mode_ceiling_bill_setup()
        out = svc.run_full(REALM, entities=["Bill"], dry_run=False)
    assert out["entities"]["Bill"]["aborted"] is not None
    assert out["entities"]["Bill"]["staging_deleted"] == 0
    create_mock.assert_called_once()


def test_cdc_real_ceiling_no_confirm_no_delete(monkeypatch):
    monkeypatch.setenv("QBO_PULL_DELETE_MAX_CANDIDATES", "1")
    from integrations.intuit.qbo.staging_delete.business.service import (
        QboStagingDeleteReconcileService,
    )

    bill_repo = MagicMock()
    bill_repo.read_by_qbo_id_and_realm_id.return_value = MagicMock(qbo_id="1")
    bill_client = MagicMock()
    bill_client._http_client = MagicMock()
    confirm = MagicMock()
    bill_client.get_bill = confirm

    empty_rest = {
        "Purchase": set(),
        "VendorCredit": set(),
        "Invoice": set(),
        "ReimburseCharge": set(),
    }
    with patch(
        "integrations.intuit.qbo.staging_delete.business.service.fetch_cdc_deleted_ids",
        return_value={"Bill": {"1", "2"}, **empty_rest},
    ):
        svc = QboStagingDeleteReconcileService(
            bill_repo=bill_repo,
            purchase_repo=MagicMock(),
            vendor_credit_repo=MagicMock(),
            invoice_repo=MagicMock(),
            reimburse_charge_repo=MagicMock(),
            bill_client=bill_client,
            purchase_client=MagicMock(),
            vendor_credit_client=MagicMock(),
            invoice_client=MagicMock(),
        )
        out = svc.run_cdc(REALM)
    assert out["entities"]["Bill"]["aborted"] == "candidate_ceiling_exceeded"
    confirm.assert_not_called()
    bill_repo.delete_by_qbo_id_and_realm_id.assert_not_called()


def test_full_mode_live_ids_fetched_once():
    from integrations.intuit.qbo.staging_delete.business.service import (
        QboStagingDeleteReconcileService,
    )

    calls = {"n": 0}

    def fetch_live():
        calls["n"] += 1
        return ["99"]

    bill_repo = MagicMock()
    bill_repo.read_by_realm_id.return_value = [MagicMock(qbo_id="99")]
    bill_client = MagicMock()
    bill_client.query_all_bill_ids.side_effect = fetch_live
    bill_client.get_bill.side_effect = lambda qid: (_ for _ in ()).throw(
        QboNotFoundError("gone")
    )

    svc = QboStagingDeleteReconcileService(
        bill_repo=bill_repo,
        purchase_repo=MagicMock(read_by_realm_id=MagicMock(return_value=[])),
        vendor_credit_repo=MagicMock(read_by_realm_id=MagicMock(return_value=[])),
        invoice_repo=MagicMock(read_by_realm_id=MagicMock(return_value=[])),
        reimburse_charge_repo=MagicMock(read_by_realm_id=MagicMock(return_value=[])),
        bill_client=bill_client,
        purchase_client=MagicMock(),
        vendor_credit_client=MagicMock(),
        invoice_client=MagicMock(),
        bill_service_factory=lambda: MagicMock(read_by_qbo_identity=MagicMock(return_value=None)),
    )
    svc.run_full(REALM, entities=["Bill"])
    assert calls["n"] == 1


def test_full_mode_live_fetch_failure_aborts_entity():
    from integrations.intuit.qbo.staging_delete.business.service import (
        QboStagingDeleteReconcileService,
    )

    bill_repo = MagicMock()
    bill_repo.read_by_realm_id.return_value = [MagicMock(qbo_id="1")]
    bill_client = MagicMock()
    bill_client.query_all_bill_ids.side_effect = RuntimeError("pager dropped")

    svc = QboStagingDeleteReconcileService(
        bill_repo=bill_repo,
        purchase_repo=MagicMock(read_by_realm_id=MagicMock(return_value=[])),
        vendor_credit_repo=MagicMock(read_by_realm_id=MagicMock(return_value=[])),
        invoice_repo=MagicMock(read_by_realm_id=MagicMock(return_value=[])),
        reimburse_charge_repo=MagicMock(read_by_realm_id=MagicMock(return_value=[])),
        bill_client=bill_client,
        purchase_client=MagicMock(),
        vendor_credit_client=MagicMock(),
        invoice_client=MagicMock(),
    )
    out = svc.run_full(REALM, entities=["Bill"])
    assert out["entities"]["Bill"]["aborted"] is not None
    bill_repo.delete_by_qbo_id_and_realm_id.assert_not_called()


def test_binding_read_error_skips_delete_increments_counter():
    from integrations.intuit.qbo.staging_delete.business.service import (
        QboStagingDeleteReconcileService,
        _EntityRuntime,
    )

    bill_repo = MagicMock()
    bill_svc = MagicMock(read_by_qbo_identity=MagicMock(side_effect=RuntimeError("db")))
    runtime = _EntityRuntime(
        name="Bill",
        staging_repo=bill_repo,
        confirm_get=MagicMock(),
        fetch_live_ids=MagicMock(),
        delete_staging=lambda qid, rid: bill_repo.delete_by_qbo_id_and_realm_id(qid, rid),
        dbo_still_bound=lambda qid, rid: bill_svc.read_by_qbo_identity(qid, rid) is not None,
    )
    svc = QboStagingDeleteReconcileService(bill_repo=bill_repo)
    counts = svc._empty_entity_counts()
    svc._act_on_confirmed(
        "Bill", REALM, "9", dry_run=False, counts=counts, runtime=runtime
    )
    assert counts.get("binding_read_errors", 0) == 1
    assert counts["staging_deleted"] == 0
    bill_repo.delete_by_qbo_id_and_realm_id.assert_not_called()


def test_entity_processing_error_aborts_entity_others_continue():
    from integrations.intuit.qbo.staging_delete.business.service import (
        QboStagingDeleteReconcileService,
    )

    bill_repo = MagicMock()
    bill_repo.read_by_realm_id.side_effect = RuntimeError("boom")
    purchase_repo = MagicMock()
    purchase_repo.read_by_realm_id.return_value = []

    svc = QboStagingDeleteReconcileService(
        bill_repo=bill_repo,
        purchase_repo=purchase_repo,
        vendor_credit_repo=MagicMock(read_by_realm_id=MagicMock(return_value=[])),
        invoice_repo=MagicMock(read_by_realm_id=MagicMock(return_value=[])),
        reimburse_charge_repo=MagicMock(read_by_realm_id=MagicMock(return_value=[])),
        bill_client=MagicMock(),
        purchase_client=MagicMock(),
        vendor_credit_client=MagicMock(),
        invoice_client=MagicMock(),
    )
    out = svc.run_full(REALM, entities=["Bill", "Purchase"])
    assert out["entities"]["Bill"]["aborted"] is not None


def test_full_invoice_filters_confirmed_with_normalized_persisted_ids():
    from integrations.intuit.qbo.staging_delete.business.service import (
        QboStagingDeleteReconcileService,
    )

    invoice_repo = MagicMock()
    invoice_repo.read_by_realm_id.return_value = [MagicMock(qbo_id=" 77 ")]
    invoice_client = MagicMock()
    invoice_client.query_all_invoice_ids.return_value = []
    invoice_client.get_invoice.side_effect = lambda qid: (_ for _ in ()).throw(
        QboNotFoundError("gone")
    )

    svc = QboStagingDeleteReconcileService(
        bill_repo=MagicMock(read_by_realm_id=MagicMock(return_value=[])),
        purchase_repo=MagicMock(read_by_realm_id=MagicMock(return_value=[])),
        vendor_credit_repo=MagicMock(read_by_realm_id=MagicMock(return_value=[])),
        invoice_repo=invoice_repo,
        reimburse_charge_repo=MagicMock(read_by_realm_id=MagicMock(return_value=[])),
        bill_client=MagicMock(),
        purchase_client=MagicMock(),
        vendor_credit_client=MagicMock(),
        invoice_client=invoice_client,
        invoice_service_factory=lambda: MagicMock(read_by_qbo_identity=MagicMock(return_value=None)),
    )
    out = svc.run_full(REALM, entities=["Invoice"])
    assert out["entities"]["Invoice"]["staging_deleted"] == 1
    invoice_repo.delete_by_qbo_id_and_realm_id.assert_called_once_with("77", REALM)


def test_invoice_issue_dedup_normalizes_persisted_qbo_id():
    from integrations.intuit.qbo.staging_delete.business.service import (
        QboStagingDeleteReconcileService,
        _EntityRuntime,
    )

    recon = MagicMock(
        read_unresolved_issue_keys_by_drift_type=MagicMock(
            return_value=[(REALM, "Invoice", " 55 ")]
        )
    )
    invoice_repo = MagicMock()
    runtime = _EntityRuntime(
        name="Invoice",
        staging_repo=invoice_repo,
        confirm_get=MagicMock(),
        fetch_live_ids=MagicMock(),
        delete_staging=lambda qid, rid: invoice_repo.delete_by_qbo_id_and_realm_id(qid, rid),
    )
    svc = QboStagingDeleteReconcileService(
        invoice_repo=invoice_repo,
        reconciliation_repo_factory=lambda: recon,
        invoice_service_factory=lambda: MagicMock(read_by_qbo_identity=MagicMock(return_value=None)),
    )
    counts = svc._empty_entity_counts()
    svc._act_on_confirmed(
        "Invoice", REALM, "55", dry_run=False, counts=counts, runtime=runtime
    )
    recon.create.assert_not_called()
    invoice_repo.delete_by_qbo_id_and_realm_id.assert_called_once()
