"""U-513 ph2: the PRODUCTION customer sync path threads the QBO payload.

WHY THIS FILE EXISTS
--------------------
`scripts/sync_qbo_customer.py` is what actually runs — `shared/scheduler.py` and
`shared/api/admin.py:375` import it. Until ph2 it called
`sync_from_qbo(sync_to_modules=False)` and ran its OWN projection loops, so the
external payloads (which only ever exist inside `sync_from_qbo`'s staging loop)
never reached projection and the connector fell back to reading
`qbo.PhysicalAddress` — the table U-513 is sunsetting. The converted
`sync_to_modules=True` path was reached only by the API router.

The loops were not deletable on their own: they carried `with_retry` and
`pace_batch`, which `project_records` does not provide. Both moved into the
service's projection closures.

⚠️ Written by the EM, not by the builder. The ph2 customer agent STALLED
immediately before writing this file — its production changes landed with the
suite green and ZERO tests pinning them, because the pre-existing tests were
written against the pre-conversion shape.
"""
from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

from integrations.intuit.qbo.base.sync_outcome import SyncOutcome
from integrations.intuit.qbo.customer.business import service as customer_service_module
from integrations.intuit.qbo.customer.business.service import QboCustomerService
from scripts import sync_qbo_customer as customer_script


def _qbo_customer(qbo_id="QB-C-1", job=False):
    return MagicMock(qbo_id=qbo_id, id=1, is_job=job, is_parent_customer=not job,
                     realm_id="realm-1", bill_addr_id=None, ship_addr_id=None,
                     parent_ref_value=None)


# ── the point of ph2 ─────────────────────────────────────────────────────────

def test_the_production_script_no_longer_runs_its_own_projection_loop():
    """The script must delegate projection to the service.

    While it projected for itself it could not be handed the payload, so the
    connector took the staging-read fallback — which is exactly what blocks
    dropping qbo.PhysicalAddress.
    """
    src = inspect.getsource(customer_script)
    assert "sync_to_modules=False" not in src, (
        "the script still asks the service NOT to project, so it must project "
        "itself -- without the payload, from staging"
    )
    for gone in ("with_retry(", "pace_batch("):
        assert gone not in src, (
            f"{gone} remains in the script: a projection loop is still here. "
            f"Retry and pacing moved into the service's closures."
        )


def test_the_payload_reaches_the_parent_projection():
    """THE ph2 guarantee: the connector receives the external record, not None."""
    parent = _qbo_customer("QB-C-1")
    external = MagicMock(id="QB-C-1")
    outcome = SyncOutcome.for_service_pull(synced=[parent], fetched=1)
    connector = MagicMock()

    with patch(f"{customer_service_module.__name__}.with_retry",
               side_effect=lambda fn, *a, **k: fn(*a)), \
         patch(f"{customer_service_module.__name__}.pace_batch"), \
         patch(f"{customer_service_module.__name__}.CustomerCustomerConnector",
               return_value=connector):
        QboCustomerService(repo=MagicMock())._sync_to_customers(
            [parent], outcome, {"QB-C-1": external}
        )

    assert connector.sync_from_qbo_customer.called
    passed = connector.sync_from_qbo_customer.call_args[0]
    assert external in passed, (
        f"the parent projection got {passed!r}; the external payload was not "
        f"threaded, so the connector will read the staging row instead"
    )


def test_a_missing_payload_entry_does_not_crash_the_projection():
    """A partial map must degrade to the staging fallback, not raise —
    the transitional fallback is still load-bearing until it is deleted."""
    parent = _qbo_customer("QB-C-1")
    outcome = SyncOutcome.for_service_pull(synced=[parent], fetched=1)
    connector = MagicMock()

    with patch(f"{customer_service_module.__name__}.with_retry",
               side_effect=lambda fn, *a, **k: fn(*a)), \
         patch(f"{customer_service_module.__name__}.pace_batch"), \
         patch(f"{customer_service_module.__name__}.CustomerCustomerConnector",
               return_value=connector):
        QboCustomerService(repo=MagicMock())._sync_to_customers([parent], outcome, {})

    assert connector.sync_from_qbo_customer.called
    assert not outcome.projection_failed_ids


# ── resilience the script's loops used to provide ────────────────────────────

def test_retry_and_pacing_survived_the_move_into_the_service():
    """Deleting the script's loops without these would be a silent resilience
    regression: per-row work under load causes TCP drops here."""
    rows = [_qbo_customer(f"QB-C-{i}") for i in range(3)]
    outcome = SyncOutcome.for_service_pull(synced=rows, fetched=3)
    connector = MagicMock()

    with patch(f"{customer_service_module.__name__}.with_retry",
               side_effect=lambda fn, *a, **k: fn(*a)) as retry, \
         patch(f"{customer_service_module.__name__}.pace_batch") as pace, \
         patch(f"{customer_service_module.__name__}.CustomerCustomerConnector",
               return_value=connector):
        QboCustomerService(repo=MagicMock())._sync_to_customers(rows, outcome, {})

    assert retry.call_count == 3, "projection is no longer retried"
    assert pace.call_count == 3, "pacing is no longer applied per record"


def test_pacing_still_runs_for_a_record_whose_projection_RAISED():
    """`finally`, not the happy path. A page of failures must still pace, or a
    failing pull hammers the connection with no delay at all."""
    rows = [_qbo_customer("QB-C-1"), _qbo_customer("QB-C-2")]
    outcome = SyncOutcome.for_service_pull(synced=rows, fetched=2)
    connector = MagicMock()
    connector.sync_from_qbo_customer.side_effect = RuntimeError("boom")

    with patch(f"{customer_service_module.__name__}.with_retry",
               side_effect=lambda fn, *a, **k: fn(*a)), \
         patch(f"{customer_service_module.__name__}.pace_batch") as pace, \
         patch(f"{customer_service_module.__name__}.CustomerCustomerConnector",
               return_value=connector):
        QboCustomerService(repo=MagicMock())._sync_to_customers(rows, outcome, {})

    assert pace.call_count == 2, "a raising record skipped its pace_batch"


def test_a_projection_failure_is_a_FAILURE_not_a_skip():
    """Skips are excluded from `should_hold`. Converting a projection failure
    into a skip would silently advance the watermark past a broken row."""
    parent = _qbo_customer("QB-C-1")
    outcome = SyncOutcome.for_service_pull(synced=[parent], fetched=1)
    connector = MagicMock()
    connector.sync_from_qbo_customer.side_effect = RuntimeError("boom")

    with patch(f"{customer_service_module.__name__}.with_retry",
               side_effect=lambda fn, *a, **k: fn(*a)), \
         patch(f"{customer_service_module.__name__}.pace_batch"), \
         patch(f"{customer_service_module.__name__}.CustomerCustomerConnector",
               return_value=connector):
        QboCustomerService(repo=MagicMock())._sync_to_customers([parent], outcome, {})

    assert outcome.projection_failed_ids == ["QB-C-1"]
    assert not outcome.skipped_ids, "a projection failure was recorded as a SKIP"
    assert outcome.should_hold, "the watermark will advance past a failed projection"


# ── the ordering that used to hold by accident ───────────────────────────────

def test_parents_project_before_jobs():
    """LOAD-BEARING. A job's billing fallback reads the parent's dbo.Address,
    which the parent's OWN projection creates (the U-513 circularity fix). The
    script's parent/job split used to enforce this ordering incidentally; after
    ph2 the service owns it, so it needs pinning rather than luck."""
    src = inspect.getsource(customer_service_module.QboCustomerService.sync_from_qbo)
    # Match the CALL SITES, not any mention. `_sync_to_customers` also appears in
    # a comment ~1700 chars earlier; searching for the bare name found that
    # instead, so this test passed even with the two calls swapped. Caught by
    # mutation, not by reading.
    i_parent = src.find("self._sync_to_customers(")
    i_job = src.find("self._sync_to_projects(")
    assert i_parent != -1 and i_job != -1, "one of the projection CALLS is gone"
    assert i_parent < i_job, (
        "jobs project BEFORE parents: a job's billing fallback would read a "
        "dbo.Address its parent has not written yet, and silently resolve to "
        "nothing -- packets fall back to name-only"
    )
