"""
U-505 regression tests — the QBO CompanyInfo pull no longer stages a
`qbo.CompanyInfo` row; it builds a transient object the connector projects
straight into `dbo.Company`.

Both tests exist because Codex Pass 1 (gpt-5.6-terra, xhigh) found the two
defects they pin, and the pre-existing suite could not have caught either:

  P1 — a QBO response with no `Id` used to be CAUGHT. The staging row's PK was
       always truthy, so the script entered the connector, the dbo-only fast
       path short-circuited on the falsy `qbo_id` (identity_fastpath.py:680),
       the connector raised, and the script recorded a PROJECTION failure ->
       `SyncOutcome.should_hold` True -> `WatermarkRun.commit` HELD the
       watermark -> the next tick retried. After the repoint the guard reads
       `qbo_id` (correctly, `.id` is now always None), so that response is
       SKIPPED with nothing recorded, the watermark ADVANCES, and the Company
       is never created/updated -- permanently, because the delta cursor has
       moved past it. Mirrors `QboItemService._upsert_item`'s own
       `if not qbo_item.id: raise ValueError(...)` guard.

  P2 — the three `qbo.PhysicalAddress` row ids are the one thing that still
       has to survive the staging removal: they are produced by
       `_sync_physical_address` and consumed by the caller's address
       projection. Nothing asserted they arrive intact.
"""
from unittest.mock import MagicMock, patch

from integrations.intuit.qbo.company_info.business.service import QboCompanyInfoService
from integrations.intuit.qbo.company_info.external.schemas import (
    QboCompanyInfo as QboCompanyInfoExternal,
)
# The always-None contract is one invariant shared with the attachable/item
# transients -- assert it through the shipped helper, not a local subset.
from tests.test_u338_qbo_attachable_transient_factory import ALWAYS_NONE_FIELDS

REALM = "realm-504"

CLIENT_TARGET = (
    "integrations.intuit.qbo.company_info.business.service.QboCompanyInfoClient"
)


def _external(**overrides):
    """A real external-schema CompanyInfo. Built from the pydantic model rather
    than a SimpleNamespace so a renamed field breaks these tests instead of
    silently diverging from what `_build_company_info` actually reads."""
    payload = {"Id": "CI-1", "SyntaxToken": None, "CompanyName": "ACME INC",
               "LegalName": "Acme, Inc.", "Country": "US", "FiscalYearStartMonth": 1}
    payload.update(overrides)
    return QboCompanyInfoExternal(**payload)


def _assert_transient(record):
    """No local row backs this object -- every identity/audit field is None."""
    for field in ALWAYS_NONE_FIELDS:
        assert getattr(record, field) is None, f"{field} must be None on a transient QboCompanyInfo"


def _patched_client(response):
    """Mirrors tests/test_u269_qbo_staging_try_except.py::_client_cm, which
    stacks query_all_items/customers/vendors; CompanyInfo's client method is
    get_company_info, the one name that helper does not carry."""
    client = MagicMock()
    client.get_company_info.return_value = response
    ctx = MagicMock()
    ctx.__enter__.return_value = client
    ctx.__exit__.return_value = False
    return ctx


# --- P1: a response with no QBO Id must HOLD the watermark, not advance it ---

def test_missing_qbo_id_records_staging_failure_and_holds_watermark():
    """The whole point is the watermark. A response QBO returns without an `Id`
    must leave `should_hold` True so `WatermarkRun.commit` holds the delta
    cursor and the next tick retries -- exactly what the pre-repoint code did
    by way of a projection failure. Advancing here loses the record forever."""
    svc = QboCompanyInfoService()
    with patch(CLIENT_TARGET, return_value=_patched_client(_external(Id=None))):
        outcome = svc.sync_from_qbo(realm_id=REALM)

    assert outcome.should_hold is True, (
        "a CompanyInfo with no QBO Id must hold the watermark; advancing past it "
        "permanently skips the Company projection"
    )
    assert outcome.staging_failed_ids, "the failure must be recorded, not silently skipped"
    assert not outcome.synced, "a record with no QBO identity must not be reported as synced"


def test_present_qbo_id_still_syncs_and_does_not_hold():
    """The guard must not fire on the normal path -- otherwise it would hold the
    watermark forever and the test above would pass vacuously."""
    svc = QboCompanyInfoService()
    with patch(CLIENT_TARGET, return_value=_patched_client(_external())):
        outcome = svc.sync_from_qbo(realm_id=REALM)

    assert outcome.should_hold is False
    assert len(outcome.synced) == 1
    assert outcome.synced[0].qbo_id == "CI-1"
    _assert_transient(outcome.synced[0])


# --- P2: the three PhysicalAddress row ids must survive onto the transient ---

def test_three_physical_address_ids_reach_the_transient_object():
    """`_sync_physical_address` returns the qbo.PhysicalAddress row id for each
    of the three address slots. Those ids are the caller's only handle for the
    address projection, and they are position-sensitive: company / legal /
    customer-communication must not be transposed."""
    svc = QboCompanyInfoService()
    response = _external(
        CompanyAddr={"Id": "A-company"},
        LegalAddr={"Id": "A-legal"},
        CustomerCommunicationAddr={"Id": "A-cc"},
    )
    by_qbo_id = {"A-company": 11, "A-legal": 22, "A-cc": 33}

    with patch(CLIENT_TARGET, return_value=_patched_client(response)), \
         patch.object(
             QboCompanyInfoService,
             "_sync_physical_address",
             side_effect=lambda realm, addr_ref, addr_qbo_id: by_qbo_id[addr_qbo_id],
         ):
        outcome = svc.sync_from_qbo(realm_id=REALM)

    record = outcome.synced[0]
    assert record.company_addr_id == 11
    assert record.legal_addr_id == 22
    assert record.customer_communication_addr_id == 33
