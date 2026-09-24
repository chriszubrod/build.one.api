"""U-513 ph2 (vendor half) — the PRODUCTION vendor pull runs the converted
projection, and it kept the resilience the old loop provided.

Why this file exists
--------------------
U-513 ph1 made `QboVendorService.sync_from_qbo(..., sync_to_modules=True)`
hand the inline QBO payload to the connector through a closure. That path was
not what production ran: `scripts/sync_qbo_vendor.py` staged with
`sync_to_modules=False` and then ran its OWN projection loop with no payload,
so it silently kept taking the `qbo.PhysicalAddress` staging-read fallback.
`shared/scheduler.py` and `shared/api/admin.py` both import exactly that
script, so ph1 reached only the API router. Until the script was converted,
`qbo.PhysicalAddress` could not be dropped.

What the script's loop did that `project_records` does not
----------------------------------------------------------
Two things, and neither is cosmetic:

  * `with_retry(..., max_retries=MAX_RETRIES, initial_delay=INITIAL_RETRY_DELAY)`
    — transient-error retry.
  * `pace_batch(i, len(vendors), logger, "vendors")` — the inter-batch delay
    that keeps the DB connection alive. Per-row work under load TCP-drops in
    this system (`feedback_backfill_setbased_under_load.md`); dropping the
    pacing would be a resilience regression, not a tidy-up.

So ph2 moved BOTH into `QboVendorService._sync_to_vendors`'s projection
closure and deleted the script's loop. Note the asymmetry this corrects: the
service already retried and paced during STAGING — only PROJECTION was bare,
and only on the API path. `base/sync_outcome.py::project_records` is
deliberately untouched (ten call sites across eight QBO families); the
per-record index pacing needs comes from a counter inside the closure.

How the chain is pinned
-----------------------
`script → sync_from_qbo(sync_to_modules=True) → _sync_to_vendors → paced,
retried, payload-carrying projection`. The end-to-end tests drive
`sync_qbo_to_local` for the links a mock would paper over (payload threading,
the returned envelope, `sync_to_modules=True`); the per-record retry/pacing
guards drive `_sync_to_vendors` directly, because the staging loop paces with
the same `(index, total, "vendors")` shape and would otherwise be
indistinguishable from the projection ticks being asserted. One end-to-end
pacing test breaks that tie deliberately, by failing a staging row so the
projection's `total` differs from staging's.

Pure logic: repos, the QBO client and the connector are all mocked; the
harness blocks live pyodbc outright (`tests/conftest.py`).
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, call, patch

import pytest

import scripts.sync_qbo_vendor as vendor_script
from integrations.intuit.qbo.base.sync_outcome import SyncOutcome
from integrations.intuit.qbo.vendor.business.service import (
    INITIAL_RETRY_DELAY,
    MAX_RETRIES,
    QboVendorService,
)

# Model builders are reused from the ph1 file rather than re-copied: they build
# the REAL dataclasses/schemas, so a renamed field breaks one definition
# instead of silently diverging across two. (Same in-repo precedent as
# `from test_qbo_watermark_runner import _iter_sync_script_paths`.)
from test_u513_vendor_address_from_payload import REALM, _addr, _external, _staging

SERVICE_MODULE = "integrations.intuit.qbo.vendor.business.service"
CONNECTOR_MODULE = "integrations.intuit.qbo.vendor.connector.vendor.business.service"

EXPECTED_RESULT_KEYS = {"vendors_synced", "vendors_module_synced", "vendors"}

# The label failure reasons and log greps key on. It must survive the move out
# of the script — the script used "QboVendor->Vendor" while the service's
# (API-only) loop used "Vendor->Vendor", and the production spelling is the
# one that had to win.
PROJECTION_LABEL = "QboVendor->Vendor"


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------


class _FakeVendorRepo:
    """In-memory `QboVendorRepository`. `create` echoes a staging row built from
    the kwargs it was handed, so the rows the projection sees are the rows THIS
    page produced. `fail_ids` makes a chosen record fail its STAGING upsert —
    used to give the projection loop a different record count than staging."""

    def __init__(self, fail_ids=()):
        self.fail_ids = set(fail_ids)
        self._next_id = 100

    def read_by_qbo_id_and_realm_id(self, *, qbo_id, realm_id):
        return None

    def create(self, **kwargs):
        qbo_id = kwargs.get("qbo_id")
        if qbo_id in self.fail_ids:
            raise RuntimeError(f"staging upsert exploded for {qbo_id}")
        self._next_id += 1
        row = _staging(
            qbo_id=qbo_id,
            realm_id=kwargs.get("realm_id"),
            bill_addr_id=kwargs.get("bill_addr_id"),
            display_name=kwargs.get("display_name"),
        )
        row.id = self._next_id
        return row


def _service(*, fail_ids=()):
    service = QboVendorService(repo=_FakeVendorRepo(fail_ids=fail_ids))
    # The staging address WRITE deliberately stays during the transition (the
    # table is dropped in a later step); it is not what these tests are about.
    service.physical_address_service = Mock()
    service.physical_address_service.read_by_qbo_id.return_value = None
    service.physical_address_service.create.return_value = SimpleNamespace(id=555)
    return service


def _client_returning(*externals):
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.query_all_vendors.return_value = list(externals)
    return client


def _run_script(service, *externals, connector=None):
    """Drive the PRODUCTION entry point — `scripts/sync_qbo_vendor.py`'s
    `sync_qbo_to_local` — over a page of QBO vendors."""
    connector = connector if connector is not None else Mock()
    with patch(f"{SERVICE_MODULE}.QboVendorClient", return_value=_client_returning(*externals)), \
            patch(f"{CONNECTOR_MODULE}.VendorVendorConnector", return_value=connector), \
            patch(f"{SERVICE_MODULE}.pace_batch"):
        result, outcome = vendor_script.sync_qbo_to_local(
            realm_id=REALM,
            last_sync_time=None,
            qbo_vendor_service=service,
        )
    return result, outcome, connector


# --------------------------------------------------------------------------
# 1. The production script path threads the payload — the whole point
# --------------------------------------------------------------------------


def test_production_script_path_hands_the_external_record_to_the_projection():
    """THE unit. Before ph2 this arrived as `None` on every production pull —
    the scheduler and the admin dispatcher both import this script — so the
    connector fell back to reading `qbo.PhysicalAddress` back out, and ph1's
    inline-payload path was reachable only from the API router."""
    ext = _external("1246", bill_addr=_addr())

    _, _, connector = _run_script(_service(), ext)

    connector.sync_from_qbo_vendor.assert_called_once()
    assert connector.sync_from_qbo_vendor.call_args.args[1] is ext


def test_each_staging_row_gets_its_OWN_payload_through_the_script():
    """A page of vendors is the ordinary case. Taking vendor B's BillAddr while
    writing under vendor A's identity is silent, permanent address corruption
    on both rows, so the pairing is asserted on the production path too, not
    just on the service call ph1 covered."""
    ext_a = _external("1246", display_name="Alpha Supply", bill_addr=_addr(city="Brentwood"))
    ext_b = _external("329", display_name="Beta Supply", bill_addr=_addr(city="Nashville"))

    _, _, connector = _run_script(_service(), ext_a, ext_b)

    pairs = {
        c.args[0].qbo_id: c.args[1]
        for c in connector.sync_from_qbo_vendor.call_args_list
    }
    assert pairs == {"1246": ext_a, "329": ext_b}


def test_script_delegates_projection_to_the_service_instead_of_re_projecting():
    """The script must ask for `sync_to_modules=True` and then keep its hands
    off: a surviving second loop would project every row twice (two dbo writes,
    two `record_projected()` bumps for one record)."""
    service = MagicMock()
    service.sync_from_qbo.return_value = SyncOutcome.for_service_pull(
        synced=[_staging("1246")], fetched=1
    )

    vendor_script.sync_qbo_to_local(
        realm_id=REALM, last_sync_time=None, qbo_vendor_service=service,
    )

    assert service.sync_from_qbo.call_args.kwargs["sync_to_modules"] is True


def test_script_exposes_no_second_projection_of_its_own():
    """Complement to the above from the other side: exactly one connector call
    per staged row on the real path."""
    _, outcome, connector = _run_script(
        _service(), _external("1246"), _external("329"), _external("77"),
    )

    assert connector.sync_from_qbo_vendor.call_count == 3
    assert outcome.projected_count == 3


# --------------------------------------------------------------------------
# 2. with_retry still wraps the projection
# --------------------------------------------------------------------------


def test_transient_projection_error_is_actually_retried_and_then_succeeds():
    """Not a `with_retry`-was-imported assertion: the connector really fails
    twice with a transient DB error and really succeeds on the third attempt,
    and the record lands as PROJECTED with no hold. `shared.database.time` is
    mocked out so the backoff does not sleep."""
    connector = Mock()
    connector.sync_from_qbo_vendor.side_effect = [
        RuntimeError("Communication link failure"),
        RuntimeError("Communication link failure"),
        SimpleNamespace(id=55),
    ]
    outcome = SyncOutcome.for_service_pull()

    with patch(f"{CONNECTOR_MODULE}.VendorVendorConnector", return_value=connector), \
            patch(f"{SERVICE_MODULE}.pace_batch"), \
            patch("shared.database.time"):
        _service()._sync_to_vendors([_staging("1246")], outcome)

    assert connector.sync_from_qbo_vendor.call_count == 3
    assert outcome.projected_count == 1
    assert outcome.should_hold is False


def test_projection_retry_budget_is_the_documented_one():
    """Pins that the family's own MAX_RETRIES / INITIAL_RETRY_DELAY are what
    gets forwarded — a bare `with_retry(fn, row, external)` would silently fall
    back to the shared defaults (3 / 1.0s) instead."""
    connector = Mock()
    row = _staging("1246")
    ext = _external("1246", bill_addr=_addr())

    with patch(f"{CONNECTOR_MODULE}.VendorVendorConnector", return_value=connector), \
            patch(f"{SERVICE_MODULE}.pace_batch"), \
            patch(f"{SERVICE_MODULE}.with_retry") as mock_retry:
        _service()._sync_to_vendors([row], SyncOutcome.for_service_pull(), {"1246": ext})

    assert mock_retry.call_args.args == (connector.sync_from_qbo_vendor, row, ext)
    assert mock_retry.call_args.kwargs == {
        "max_retries": MAX_RETRIES,
        "initial_delay": INITIAL_RETRY_DELAY,
    }


def test_a_non_transient_projection_error_is_not_retried():
    """The other half of the retry contract: `with_retry` re-raises a
    non-transient error on the first attempt rather than burning the budget."""
    connector = Mock()
    connector.sync_from_qbo_vendor.side_effect = RuntimeError("connector exploded")
    outcome = SyncOutcome.for_service_pull()

    with patch(f"{CONNECTOR_MODULE}.VendorVendorConnector", return_value=connector), \
            patch(f"{SERVICE_MODULE}.pace_batch"), \
            patch("shared.database.time"):
        _service()._sync_to_vendors([_staging("1246")], outcome)

    assert connector.sync_from_qbo_vendor.call_count == 1


# --------------------------------------------------------------------------
# 3. pace_batch still ticks once per record — INCLUDING a record that raised
# --------------------------------------------------------------------------


def test_pace_batch_ticks_once_per_record_including_the_one_that_raised():
    """The `finally` is load-bearing. Pacing inside the `try` (or after the
    return) skips the tick for any record whose projection raised — exactly the
    run where the DB is already unhappy and the delay matters most. Indices
    must stay dense (0, 1, 2) or `pace_batch`'s `(index + 1) % BATCH_SIZE`
    arithmetic drifts off the real batch boundary."""
    connector = Mock()
    connector.sync_from_qbo_vendor.side_effect = [
        SimpleNamespace(id=1),
        RuntimeError("projection exploded"),
        SimpleNamespace(id=3),
    ]
    rows = [_staging("1"), _staging("2"), _staging("3")]

    with patch(f"{CONNECTOR_MODULE}.VendorVendorConnector", return_value=connector), \
            patch(f"{SERVICE_MODULE}.pace_batch") as mock_pace, \
            patch(f"{SERVICE_MODULE}.logger") as mock_logger, \
            patch("shared.database.time"):
        _service()._sync_to_vendors(rows, SyncOutcome.for_service_pull())

    assert mock_pace.call_count == 3
    assert mock_pace.call_args_list == [
        call(0, 3, mock_logger, "vendors"),
        call(1, 3, mock_logger, "vendors"),
        call(2, 3, mock_logger, "vendors"),
    ]


def test_pacing_on_the_production_path_counts_the_PROJECTED_rows():
    """End-to-end tie-breaker. Staging paces `(i, 3, "vendors")` over the three
    FETCHED records; with one of them failing its staging upsert the projection
    paces over the two SYNCED rows, so its ticks carry `total=2` and are
    distinguishable. Both halves must be present — this is the test that would
    catch the projection pacing being dropped on the real pull."""
    service = _service(fail_ids={"329"})
    connector = Mock()

    with patch(f"{SERVICE_MODULE}.QboVendorClient",
               return_value=_client_returning(_external("1246"), _external("329"), _external("77"))), \
            patch(f"{CONNECTOR_MODULE}.VendorVendorConnector", return_value=connector), \
            patch(f"{SERVICE_MODULE}.pace_batch") as mock_pace, \
            patch(f"{SERVICE_MODULE}.logger") as mock_logger:
        vendor_script.sync_qbo_to_local(
            realm_id=REALM, last_sync_time=None, qbo_vendor_service=service,
        )

    staging_ticks = [c for c in mock_pace.call_args_list if c.args[1] == 3]
    projection_ticks = [c for c in mock_pace.call_args_list if c.args[1] == 2]
    assert staging_ticks == [
        call(0, 3, mock_logger, "vendors"),
        call(1, 3, mock_logger, "vendors"),
        call(2, 3, mock_logger, "vendors"),
    ]
    assert projection_ticks == [
        call(0, 2, mock_logger, "vendors"),
        call(1, 2, mock_logger, "vendors"),
    ]


# --------------------------------------------------------------------------
# 4. Failure accounting: still a projection ERROR, never a silent skip
# --------------------------------------------------------------------------


def test_projection_failure_is_recorded_as_a_failure_and_holds_the_watermark():
    """A skip is excluded from `should_hold` by design, so converting a failure
    into one would advance the watermark past a record that never projected —
    lost until someone edits it in QBO again."""
    connector = Mock()
    connector.sync_from_qbo_vendor.side_effect = RuntimeError("projection exploded")
    outcome = SyncOutcome.for_service_pull()

    with patch(f"{CONNECTOR_MODULE}.VendorVendorConnector", return_value=connector), \
            patch(f"{SERVICE_MODULE}.pace_batch"), \
            patch("shared.database.time"):
        _service()._sync_to_vendors([_staging("1246")], outcome)

    assert outcome.projection_failed_ids == ["1246"]
    assert outcome.skipped_ids == []
    assert outcome.projected_count == 0
    assert outcome.should_hold is True


def test_permanent_data_error_still_classifies_as_a_skip_that_does_not_hold():
    """Complement: the failure/skip split still runs through
    `record_projection_error`'s classifier rather than being hard-coded one way
    — a plain `ValueError` (the connectors' permanent-data convention) is a
    skip and must NOT hold the watermark."""
    connector = Mock()
    connector.sync_from_qbo_vendor.side_effect = ValueError("inactive in QBO and has no local")
    outcome = SyncOutcome.for_service_pull()

    with patch(f"{CONNECTOR_MODULE}.VendorVendorConnector", return_value=connector), \
            patch(f"{SERVICE_MODULE}.pace_batch"), \
            patch("shared.database.time"):
        _service()._sync_to_vendors([_staging("1246")], outcome)

    assert outcome.skipped_ids == ["1246"]
    assert outcome.projection_failed_ids == []
    assert outcome.should_hold is False


def test_a_failure_on_the_production_path_reaches_the_returned_outcome():
    """The script hands its outcome straight to `WatermarkRun.commit`, so the
    projection failure recorded inside the service has to survive the trip back
    out through `sync_qbo_to_local`."""
    connector = Mock()
    connector.sync_from_qbo_vendor.side_effect = RuntimeError("projection exploded")

    with patch("shared.database.time"):
        _, outcome, _ = _run_script(_service(), _external("1246"), connector=connector)

    assert outcome.projection_failed_ids == ["1246"]
    assert outcome.should_hold is True


@pytest.mark.parametrize(
    "error,expected_level",
    [(RuntimeError("projection exploded"), "Failed to project"),
     (ValueError("permanent data issue"), "Skipped")],
    ids=["failure", "skip"],
)
def test_projection_label_stays_qbovendor_to_vendor(caplog, error, expected_level):
    """Failure reasons and log greps key on this exact label. The service's own
    (API-only) loop used to say `Vendor->Vendor`; converting the script had to
    carry the PRODUCTION spelling over, not the other way round."""
    connector = Mock()
    connector.sync_from_qbo_vendor.side_effect = error

    with caplog.at_level("INFO"), \
            patch(f"{CONNECTOR_MODULE}.VendorVendorConnector", return_value=connector), \
            patch(f"{SERVICE_MODULE}.pace_batch"), \
            patch("shared.database.time"):
        _service()._sync_to_vendors([_staging("1246")], SyncOutcome.for_service_pull())

    assert f"{expected_level} {PROJECTION_LABEL} 1246" in caplog.text


def test_projection_success_logs_the_same_label(caplog):
    connector = Mock()
    connector.sync_from_qbo_vendor.return_value = SimpleNamespace(id=55)

    with caplog.at_level("INFO"), \
            patch(f"{CONNECTOR_MODULE}.VendorVendorConnector", return_value=connector), \
            patch(f"{SERVICE_MODULE}.pace_batch"):
        _service()._sync_to_vendors([_staging("1246")], SyncOutcome.for_service_pull())

    assert f"Synced {PROJECTION_LABEL} 1246 to 55" in caplog.text


# --------------------------------------------------------------------------
# 5. The returned envelope keeps its exact shape
# --------------------------------------------------------------------------


def test_returned_dict_keys_are_unchanged_on_the_populated_path():
    """`shared/api/admin.py` and `shared/scheduler.py` consume this dict, and
    `sync_qbo_vendor()` itself formats `vendors_synced` /
    `vendors_module_synced` into its completion log."""
    result, _, _ = _run_script(_service(), _external("1246"), _external("329"))

    assert set(result) == EXPECTED_RESULT_KEYS
    assert result["vendors_synced"] == 2
    assert result["vendors_module_synced"] == 2
    assert [v["qbo_id"] for v in result["vendors"]] == ["1246", "329"]


def test_returned_dict_keys_are_unchanged_on_the_empty_path():
    result, outcome, connector = _run_script(_service())

    assert set(result) == EXPECTED_RESULT_KEYS
    assert result == {"vendors_synced": 0, "vendors_module_synced": 0, "vendors": []}
    connector.sync_from_qbo_vendor.assert_not_called()
    assert outcome.should_hold is False


def test_module_synced_count_comes_from_the_outcome_not_from_the_row_count():
    """`vendors_module_synced` is now derived from the service's projection
    tally. A partial page is what separates the two: reporting `len(vendors)`
    would claim three module syncs when only two happened."""
    connector = Mock()
    connector.sync_from_qbo_vendor.side_effect = [
        SimpleNamespace(id=1),
        RuntimeError("projection exploded"),
        SimpleNamespace(id=3),
    ]

    with patch("shared.database.time"):
        result, outcome, _ = _run_script(
            _service(), _external("1"), _external("2"), _external("3"),
            connector=connector,
        )

    assert result["vendors_synced"] == 3
    assert result["vendors_module_synced"] == 2
    assert outcome.projected_count == 2
