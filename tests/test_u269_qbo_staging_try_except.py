"""U-269: QBO item/customer STAGING loops must isolate one bad record instead of
aborting the whole batch. Mirrors the try/except + record_staging_failure shape
already covered (informally) by the sibling bill/term/vendor/account/purchase/
invoice staging loops via base/sync_outcome.py.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from integrations.intuit.qbo.customer.business.service import QboCustomerService
from integrations.intuit.qbo.item.business.service import QboItemService


def _client_cm(records):
    """A MagicMock usable as `with SomeClient(...) as client:` where
    `client.query_all_*(...)` returns `records`."""
    client = MagicMock()
    client.query_all_items.return_value = records
    client.query_all_customers.return_value = records
    client.query_all_vendors.return_value = records
    cm = MagicMock()
    cm.__enter__.return_value = client
    cm.__exit__.return_value = False
    return cm


def test_item_staging_loop_isolates_one_bad_record_and_continues():
    service = QboItemService()
    qbo_items = [
        SimpleNamespace(id="i-1", parent_ref=None),
        SimpleNamespace(id="i-2", parent_ref=None),
        SimpleNamespace(id="i-3", parent_ref=None),
    ]
    good_1 = SimpleNamespace(id=101)
    good_3 = SimpleNamespace(id=103)
    service._upsert_item = MagicMock(
        side_effect=[good_1, ValueError("simulated malformed record"), good_3]
    )

    with patch(
        "integrations.intuit.qbo.item.business.service.QboItemClient",
        return_value=_client_cm(qbo_items),
    ):
        outcome = service.sync_from_qbo(realm_id="realm-1", sync_to_modules=False)

    # All three records must be attempted — a mid-batch raise must not abort the loop.
    assert service._upsert_item.call_count == 3
    assert outcome.fetched == 3
    assert outcome.synced == [good_1, good_3]
    assert outcome.staging_failed_ids == ["i-2"]


def test_customer_staging_loop_isolates_one_bad_record_and_continues():
    service = QboCustomerService(repo=MagicMock())
    qbo_customers = [
        SimpleNamespace(id="c-1", job=False),
        SimpleNamespace(id="c-2", job=False),
        SimpleNamespace(id="c-3", job=False),
    ]
    good_1 = SimpleNamespace(id=201)
    good_3 = SimpleNamespace(id=203)
    service._upsert_customer = MagicMock(
        side_effect=[good_1, ValueError("simulated malformed record"), good_3]
    )

    with patch(
        "integrations.intuit.qbo.customer.business.service.QboCustomerClient",
        return_value=_client_cm(qbo_customers),
    ):
        outcome = service.sync_from_qbo(realm_id="realm-1", sync_to_modules=False)

    # All three records must be attempted — a mid-batch raise must not abort the loop.
    assert service._upsert_customer.call_count == 3
    assert outcome.fetched == 3
    assert outcome.synced == [good_1, good_3]
    assert outcome.staging_failed_ids == ["c-2"]
