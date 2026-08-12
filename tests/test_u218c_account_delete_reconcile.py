"""U-218c — Retire account delete-reconcile."""
import inspect
from unittest.mock import MagicMock, patch

import pytest

from integrations.intuit.qbo.account.business.model import QboAccount
from integrations.intuit.qbo.account.business.service import QboAccountService
from integrations.intuit.qbo.account.external.schemas import QboAccount as QboAccountSchema
from integrations.intuit.qbo.base.sync_outcome import SyncOutcome

REALM_ID = "realm-test"


def test_account_sync_from_qbo_rejects_reconcile_deletes_param():
    params = inspect.signature(QboAccountService.sync_from_qbo).parameters
    assert "reconcile_deletes" not in params


def test_account_absent_from_qbo_response_not_deactivated():
    svc = QboAccountService(repo=MagicMock())

    local_still_active = MagicMock(
        qbo_id="99",
        name="Old GL",
        active=True,
        row_version_bytes=b"v",
        sync_token="1",
    )
    svc.repo.read_by_realm_id.return_value = [local_still_active]

    fetched = QboAccountSchema.model_validate(
        {
            "Id": "1",
            "SyncToken": "0",
            "Name": "Cash",
            "Active": True,
            "AccountType": "Bank",
            "Classification": "Asset",
        }
    )

    with patch.object(svc, "_upsert_account", return_value=MagicMock()) as upsert:
        with patch(
            "integrations.intuit.qbo.account.business.service.QboAccountClient"
        ) as client_cls:
            client_cls.return_value.__enter__.return_value.query_all_accounts.return_value = [
                fetched
            ]
            outcome = svc.sync_from_qbo(realm_id=REALM_ID, last_updated_time=None)

    assert isinstance(outcome, SyncOutcome)
    upsert.assert_called_once()
    svc.repo.update_by_qbo_id.assert_not_called()


def test_account_absent_from_qbo_response_leaves_local_row_untouched_via_real_upsert():
    """Full sync path: only returned QBO rows are upserted; absent locals are not touched."""
    repo = MagicMock()
    absent_local = QboAccount(
        id=99,
        public_id=None,
        row_version="djE=",
        created_datetime=None,
        modified_datetime=None,
        qbo_id="99",
        sync_token="1",
        realm_id=REALM_ID,
        name="Old GL",
        acct_num=None,
        description=None,
        active=True,
        classification=None,
        account_type=None,
        account_sub_type=None,
        fully_qualified_name=None,
        sub_account=None,
        parent_ref_value=None,
        parent_ref_name=None,
        current_balance=None,
        current_balance_with_sub_accounts=None,
        currency_ref_value=None,
        currency_ref_name=None,
    )
    repo.read_by_realm_id.return_value = [absent_local]
    repo.read_by_qbo_id_and_realm_id.return_value = None

    created = QboAccount(
        id=1,
        public_id=None,
        row_version="djI=",
        created_datetime=None,
        modified_datetime=None,
        qbo_id="1",
        sync_token="0",
        realm_id=REALM_ID,
        name="Cash",
        acct_num=None,
        description=None,
        active=True,
        classification="Asset",
        account_type="Bank",
        account_sub_type=None,
        fully_qualified_name=None,
        sub_account=None,
        parent_ref_value=None,
        parent_ref_name=None,
        current_balance=None,
        current_balance_with_sub_accounts=None,
        currency_ref_value=None,
        currency_ref_name=None,
    )
    repo.create.return_value = created

    svc = QboAccountService(repo=repo)
    fetched = QboAccountSchema.model_validate(
        {
            "Id": "1",
            "SyncToken": "0",
            "Name": "Cash",
            "Active": True,
            "AccountType": "Bank",
            "Classification": "Asset",
        }
    )

    with patch(
        "integrations.intuit.qbo.account.business.service.QboAccountClient"
    ) as client_cls, patch(
        "integrations.intuit.qbo.account.business.service.with_retry",
        side_effect=lambda fn, *args, **kwargs: fn(*args),
    ):
        client_cls.return_value.__enter__.return_value.query_all_accounts.return_value = [
            fetched
        ]
        outcome = svc.sync_from_qbo(realm_id=REALM_ID, last_updated_time=None)

    assert isinstance(outcome, SyncOutcome)
    assert len(outcome.synced) == 1
    repo.create.assert_called_once()
    repo.update_by_qbo_id.assert_not_called()
    # Absent account "99" never read for deactivation — only upsert lookup for returned id.
    deactivation_calls = [
        c for c in repo.update_by_qbo_id.call_args_list
        if c.kwargs.get("qbo_id") == "99" or (c.args and c.args[0] == "99")
    ]
    assert deactivation_calls == []
    absent_reads = [
        c for c in repo.read_by_qbo_id_and_realm_id.call_args_list
        if c.kwargs.get("qbo_id") == "99"
    ]
    assert absent_reads == []
