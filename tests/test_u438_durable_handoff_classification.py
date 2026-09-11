"""U-438 — the three defects Codex found in U-437's own fix.

U-437 taught the completion pipeline to distinguish "queued" from "silently
dropped". Its independent review found three ways it still got that wrong, and
all three had shipped without coverage — reverting each left the suite green.
That is why this file exists.

  P0  Exception-type sniffing was the wrong instrument. U-437 flagged a failed
      Excel/SharePoint leg only when the exception was `MsOutboxEnqueueError`,
      but `MsOutboxRepository.create` raises `map_database_error(...)` — a
      DatabaseError. So the LIKELIEST failure (a DB outage mid-enqueue) left
      `enqueue_failed` False and the job was marked successful with nothing
      queued. Any exception now means the leg did not queue.

  P0  Pre-enqueue early returns bypassed the flag entirely. A missing
      DriveItem/Drive/Vendor returned before any enqueue was attempted, so a
      REQUIRED leg silently never queued. Those now classify as failures —
      while a genuine no-op (no workbook mapped, no module folder linked) must
      NOT, or every project without a tracker would fail forever.

  P1  U-437 introduced a regression: the typed wrappers resolved the tenant and
      raised BEFORE the gate was consulted, so `ALLOW_MS_WRITES=false` PLUS a
      missing MS auth raised instead of quietly refusing — failing the job in a
      deliberately gated environment, inverting the invariant the unit exists to
      protect.

The invariant, stated once:

    row returned -> queued          -> job may succeed
    None         -> POLICY REFUSAL  -> job may succeed (gated env is not broken)
    raises       -> GENUINE FAILURE -> job MUST fail (nothing queued, no retry)
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from entities.bill.business.service import BillService

_MOD = "entities.bill.business.service"


def _bill():
    return SimpleNamespace(
        id=55, public_id="pub-55", vendor_id=7, bill_date="2026-09-01",
        bill_number="INV-1", is_draft=False,
    )


def _svc():
    svc = BillService(repo=MagicMock())
    svc.vendor_service = MagicMock()
    svc.vendor_service.read_by_id.return_value = SimpleNamespace(id=7, name="Acme", abbreviation="ACME")
    return svc


# ---------------------------------------------------------------------------
# P0 — any exception means the leg never queued (not just the typed one)
# ---------------------------------------------------------------------------


def test_a_NON_typed_exception_still_flags_the_excel_leg_as_failed():
    """The regression Codex caught: repo.create raises a DatabaseError, not
    MsOutboxEnqueueError, so type-sniffing missed the likeliest failure."""
    svc = _svc()
    svc._project_excel_connector = MagicMock()
    svc._project_excel_connector.get_excel_for_project.side_effect = RuntimeError(
        "database is down"  # deliberately NOT MsOutboxEnqueueError
    )

    result = svc.sync_to_excel_workbook(bill=_bill(), line_items=[], project_id=12)

    assert result["enqueue_failed"] is True, (
        "any exception means this project's rows never queued — narrowing to a "
        "single exception type is how the DB-outage case slipped through"
    )


# ---------------------------------------------------------------------------
# P0 — required-leg early returns are failures; genuine no-ops are not
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mapping, expect_failed, why",
    [
        (None, False, "no workbook mapped for this project is a legitimate NO-OP"),
        ({"id": 1}, True, "a mapping exists but has no worksheet_name — malformed, required leg"),
    ],
)
def test_early_returns_are_classified_not_ignored(mapping, expect_failed, why):
    svc = _svc()
    svc._project_excel_connector = MagicMock()
    svc._project_excel_connector.get_excel_for_project.return_value = mapping

    result = svc.sync_to_excel_workbook(bill=_bill(), line_items=[], project_id=12)

    assert result.get("enqueue_failed") is expect_failed, why


def test_a_missing_drive_is_a_required_leg_failure():
    """DriveItem/Drive resolve failures mean the rows were never queued and
    nothing will retry them — distinct from 'this project has no tracker'."""
    svc = _svc()
    svc._project_excel_connector = MagicMock()
    svc._project_excel_connector.get_excel_for_project.return_value = {
        "id": 1, "worksheet_name": "DETAILS",
    }
    with patch(f"{_MOD}.MsDriveItemRepository") as MockRepo:
        MockRepo.return_value.read_all.return_value = []  # driveitem not found
        result = svc.sync_to_excel_workbook(bill=_bill(), line_items=[], project_id=12)

    assert result["enqueue_failed"] is True


# ---------------------------------------------------------------------------
# P1 — the gate must be consulted BEFORE anything that can raise
# ---------------------------------------------------------------------------


def test_a_closed_gate_with_no_tenant_REFUSES_rather_than_raising():
    """The regression U-437 introduced, and the one that would break prod.

    `ALLOW_MS_WRITES=false` plus a missing MS auth must return None (a policy
    refusal). U-437 resolved the tenant first and raised, which fails the
    completion job — so a deliberately gated environment would have had every
    completion fail forever.
    """
    from integrations.ms.outbox.business.service import MsOutboxService

    svc = MsOutboxService()
    with patch("integrations.ms.outbox.business.service._writes_allowed", return_value=False), \
         patch("integrations.ms.outbox.business.service._resolve_tenant_id", return_value=None):
        assert svc.enqueue_excel_insert(
            entity_type="Bill", entity_public_id="p", drive_id="d", item_id="i",
            worksheet_name="w", row_index=1, values=[[]],
        ) is None
        assert svc.enqueue_sharepoint_upload(
            entity_type="Bill", entity_public_id="p", drive_id="d", parent_item_id="i",
            filename="f.pdf", content_type="application/pdf", blob_path="b", attachment_id=1,
        ) is None


def test_an_OPEN_gate_with_no_tenant_still_raises():
    """The other half — with writes enabled, a missing tenant is a real failure."""
    from integrations.ms.outbox.business.service import (
        MsOutboxEnqueueError,
        MsOutboxService,
    )

    svc = MsOutboxService()
    with patch("integrations.ms.outbox.business.service._writes_allowed", return_value=True), \
         patch("integrations.ms.outbox.business.service._resolve_tenant_id", return_value=None):
        with pytest.raises(MsOutboxEnqueueError):
            svc.enqueue_excel_insert(
                entity_type="Bill", entity_public_id="p", drive_id="d", item_id="i",
                worksheet_name="w", row_index=1, values=[[]],
            )
