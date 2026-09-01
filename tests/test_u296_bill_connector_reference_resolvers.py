"""U-296 (Wave-1 §3, from the U-294 readiness audit) / U-352: BillBillConnector's
push-side reference resolvers.

(a) `_get_ap_account_ref`'s qbo.Account fallback: proved-safe, not removed
    (see test_u281_account_ap_account_repoint.py for the repointed fallback
    query itself — those tests were updated alongside this unit). Live
    equivalence confirmed 2026-08-22: the realm's 252-row qbo.Account
    mirror carries exactly one AccountType='Accounts Payable' row (QboId
    "7"), matching dbo.Company's already-cached value exactly.

(b) `_get_qbo_sales_term_ref` — reads dbo.PaymentTerm.QboId/.Name (U-282)
    EXCLUSIVELY as of U-352: the legacy qbo.TermPaymentTerm -> qbo.Term
    two-hop fallback was retired alongside `TermPaymentTermConnector`'s own
    dbo-only migration (the U-349 program's 3rd family). A dbo-native miss
    (no QboId stamped, a realm mismatch, or a transient read failure) now
    returns None (no SalesTermRef on the push) rather than falling back to
    a second store that no longer exists — a deliberate push-path behavior
    change, pinned by the tests below.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest


REALM_ID = "realm-1"
OTHER_REALM_ID = "realm-2"


def _make_connector(**overrides):
    from integrations.intuit.qbo.bill.connector.bill.business.service import BillBillConnector

    kwargs = dict(
        bill_service=MagicMock(),
        vendor_service=MagicMock(),
        reconciliation_repo=MagicMock(),
        qbo_account_repo=MagicMock(),
        company_service=MagicMock(),
        payment_term_service=MagicMock(),
    )
    kwargs.update(overrides)
    return BillBillConnector(**kwargs)


def _make_payment_term(**overrides):
    defaults = dict(qbo_id="8", realm_id=REALM_ID, name="Net 10")
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# --- _get_qbo_sales_term_ref ---


def test_sales_term_ref_short_circuits_on_falsy_payment_term_id():
    connector = _make_connector()

    ref = connector._get_qbo_sales_term_ref(None, REALM_ID)

    assert ref is None
    connector.payment_term_service.read_by_id.assert_not_called()


def test_sales_term_ref_dbo_hit_returns_value_and_name():
    connector = _make_connector()
    connector.payment_term_service.read_by_id.return_value = _make_payment_term(
        qbo_id="8", realm_id=REALM_ID, name="Net 10"
    )

    ref = connector._get_qbo_sales_term_ref(2, REALM_ID)

    assert ref.value == "8"
    assert ref.name == "Net 10"


def test_sales_term_ref_returns_none_when_dbo_has_no_qbo_id():
    """U-352: a dbo-native miss (never synced, or synced before identity
    stamping existed) now returns None -- there is no qbo.TermPaymentTerm
    fallback left to hop through."""
    connector = _make_connector()
    connector.payment_term_service.read_by_id.return_value = _make_payment_term(
        qbo_id=None, realm_id=REALM_ID
    )

    ref = connector._get_qbo_sales_term_ref(2, REALM_ID)

    assert ref is None


def test_sales_term_ref_returns_none_when_dbo_realm_id_is_none():
    """A PaymentTerm whose QboId was stamped (e.g. by a backfill) before its
    RealmId column existed/was populated is a real, schema-permitted state
    (dbo.PaymentTerm.RealmId is nullable, SetPaymentTermQboIdentity accepts
    @RealmId=NULL) -- it must not be trusted for any realm, and (U-352) there
    is no fallback left to recover it through."""
    connector = _make_connector()
    connector.payment_term_service.read_by_id.return_value = _make_payment_term(
        qbo_id="8", realm_id=None
    )

    ref = connector._get_qbo_sales_term_ref(2, REALM_ID)

    assert ref is None


def test_sales_term_ref_returns_none_when_dbo_read_raises():
    """A transient dbo.PaymentTerm error must not crash a live Bill push --
    U-352 removed the legacy two-hop this used to fall through to, so the
    read failure now degrades to no SalesTermRef instead."""
    connector = _make_connector()
    connector.payment_term_service.read_by_id.side_effect = Exception("db boom")

    ref = connector._get_qbo_sales_term_ref(2, REALM_ID)  # must not raise

    assert ref is None


def test_sales_term_ref_returns_none_on_realm_mismatch():
    """A dbo.PaymentTerm row stamped for a different realm must not be
    trusted for this push -- multi-realm safety, matching this file's other
    dbo-first resolvers. U-352: no fallback left to recover it through."""
    connector = _make_connector()
    connector.payment_term_service.read_by_id.return_value = _make_payment_term(
        qbo_id="8", realm_id=OTHER_REALM_ID
    )

    ref = connector._get_qbo_sales_term_ref(2, REALM_ID)

    assert ref is None


def test_sales_term_ref_none_when_dbo_missing():
    connector = _make_connector()
    connector.payment_term_service.read_by_id.return_value = None

    ref = connector._get_qbo_sales_term_ref(2, REALM_ID)

    assert ref is None


# --- QboAccountRepository.read_by_realm_id_and_account_type (U-296 sproc shape) ---


def test_qbo_account_repo_read_by_realm_id_and_account_type_calls_sproc():
    from integrations.intuit.qbo.account.persistence.repo import QboAccountRepository

    repo = QboAccountRepository()
    cursor = MagicMock()
    cursor.fetchall.return_value = []

    with patch(
        "integrations.intuit.qbo.account.persistence.repo.get_connection"
    ) as mock_conn_ctx, patch(
        "integrations.intuit.qbo.account.persistence.repo.call_procedure"
    ) as mock_call:
        mock_conn_ctx.return_value.__enter__.return_value.cursor.return_value = cursor
        result = repo.read_by_realm_id_and_account_type(REALM_ID, "Accounts Payable")

    assert result == []
    mock_call.assert_called_once()
    assert mock_call.call_args.kwargs["name"] == "ReadQboAccountsByRealmIdAndAccountType"
    assert mock_call.call_args.kwargs["params"] == {
        "RealmId": REALM_ID,
        "AccountType": "Accounts Payable",
    }
