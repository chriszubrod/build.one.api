"""Pure-logic tests for U-307c's fix to scripts/sync_qbo_item.py:

  1. `sync_local_to_qbo` (confirmed dead -- sync_qbo_item()'s own entrypoint
     always hardcoded its result to zero, no other caller existed) is deleted.
  2. `sync_qbo_to_local`'s two projection-error call sites now pass the pulled
     item's real `.qbo_id` to `record_projection_error`, not `.id` -- which is
     always `None` post-U-307c (QboItemService._upsert_item is transient) and
     would have collapsed every item projection failure into one
     indistinguishable `"None"` failure-reasons key.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from integrations.intuit.qbo.base.sync_outcome import SyncOutcome
from integrations.intuit.qbo.item.business.model import QboItem
from scripts import sync_qbo_item as module


def test_sync_local_to_qbo_deleted():
    assert not hasattr(module, "sync_local_to_qbo")


def _make_qbo_item(**overrides):
    defaults = dict(
        id=None,
        public_id=None,
        row_version=None,
        created_datetime=None,
        modified_datetime=None,
        qbo_id="ITEM-99",
        sync_token="0",
        realm_id="realm-1",
        name="13 Rough Carpentry",
        description=None,
        active=True,
        type="Service",
        parent_ref_value=None,
        parent_ref_name=None,
        level=0,
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


def test_parent_item_projection_failure_records_real_qbo_id_not_staging_pk():
    item = _make_qbo_item(qbo_id="ITEM-PARENT-1", parent_ref_value=None)
    outcome = SyncOutcome.for_service_pull(synced=[item], fetched=1)
    qbo_item_service = MagicMock()
    qbo_item_service.sync_from_qbo.return_value = outcome
    cost_code_connector = MagicMock()
    cost_code_connector.sync_from_qbo_item.side_effect = RuntimeError("transient db error")
    sub_cost_code_connector = MagicMock()

    with patch(f"{module.__name__}.pace_batch"):
        _, returned_outcome = module.sync_qbo_to_local(
            realm_id="realm-1",
            last_sync_time=None,
            qbo_item_service=qbo_item_service,
            cost_code_connector=cost_code_connector,
            sub_cost_code_connector=sub_cost_code_connector,
        )

    assert returned_outcome.projection_failed_ids == ["ITEM-PARENT-1"]
    assert "None" not in returned_outcome.failure_reasons


def test_child_item_projection_failure_records_real_qbo_id_not_staging_pk():
    item = _make_qbo_item(qbo_id="ITEM-CHILD-1", parent_ref_value="ITEM-PARENT-1")
    outcome = SyncOutcome.for_service_pull(synced=[item], fetched=1)
    qbo_item_service = MagicMock()
    qbo_item_service.sync_from_qbo.return_value = outcome
    cost_code_connector = MagicMock()
    sub_cost_code_connector = MagicMock()
    sub_cost_code_connector.sync_from_qbo_item.side_effect = RuntimeError("transient db error")

    with patch(f"{module.__name__}.pace_batch"):
        _, returned_outcome = module.sync_qbo_to_local(
            realm_id="realm-1",
            last_sync_time=None,
            qbo_item_service=qbo_item_service,
            cost_code_connector=cost_code_connector,
            sub_cost_code_connector=sub_cost_code_connector,
        )

    assert returned_outcome.projection_failed_ids == ["ITEM-CHILD-1"]
    assert "None" not in returned_outcome.failure_reasons


def test_watermark_registry_item_entry_has_no_staging_repo():
    """Companion assertion to watermark.py's own tests: item's registry row
    carries no staging_repo (dropped alongside the qbo.Item transient-ification),
    matching reimburse_charge's shape -- see test_qbo_watermark_runner.py."""
    from integrations.intuit.qbo.base.watermark import _QBO_SYNC_ENTITY_META

    assert _QBO_SYNC_ENTITY_META["item"].staging_repo is None
