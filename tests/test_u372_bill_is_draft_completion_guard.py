"""U-372: `Bill.is_draft` must not be flippable via the generic update path.

`BillService.update_by_public_id` backs `PUT /api/v1/update/bill/{public_id}`
— reachable from web (`BillEdit.tsx`, which only ever echoes the current
value back), iOS (`BillDetailView.swift`'s `Toggle("Draft", ...)`, a genuine
user-facing flip), and the `update_bill` agent tool. None of those may
change a bill's completion state directly: doing so commits `IsDraft=False`
locally without running `complete_bill`'s SharePoint/Excel/QBO push.

Only two callers may legitimately change it, both calling
`update_by_public_id` as a direct Python method (never through the router,
so the new `_via_completion_pipeline` kwarg is structurally unreachable from
any HTTP path):
  - `BillService.complete_bill()` — the completion pipeline itself.
  - `BillBillConnector._apply_bill_fields` (QBO-pull reconciliation) — see
    `tests/test_u283_bill_qbo_identity_repoint.py` for its own coverage.

These tests exercise `update_by_public_id`'s guard directly, then confirm
`complete_bill()` passes the escape hatch.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from entities.bill.business.service import BillService


def _service_with_existing(is_draft: bool) -> tuple[BillService, MagicMock]:
    """A BillService whose read_by_public_id returns a mock bill with no
    vendor/bill_number (short-circuits the unrelated duplicate-check branch),
    and whose repo is a bare MagicMock (no live-DB touch)."""
    service = BillService(repo=MagicMock())
    existing = MagicMock()
    existing.is_draft = is_draft
    existing.vendor_id = None
    existing.bill_number = None
    service.read_by_public_id = MagicMock(return_value=existing)
    service.repo.update_by_id.side_effect = lambda bill: bill
    return service, existing


@pytest.mark.parametrize(
    "initial,target",
    [(True, False), (False, True)],
    ids=["true_to_false", "false_to_true_undraft_is_blocked_too"],
)
def test_without_escape_hatch_raises(initial, target):
    """Both directions are blocked — un-drafting a completed bill via the
    generic path has no legitimate caller either, and leaving it open would
    let a client silently invalidate the already-synced-externally invariant
    with no audit trail."""
    service, _ = _service_with_existing(is_draft=initial)
    with pytest.raises(ValueError, match="completion state"):
        service.update_by_public_id(public_id="bill-1", row_version="rv", is_draft=target)
    service.repo.update_by_id.assert_not_called()


def test_same_value_echo_does_not_raise():
    """Matches BillEdit.tsx's actual behavior — it always PUTs the bill's
    current is_draft value back on every autosave, never a genuine flip.
    That echo must keep succeeding with zero web changes needed."""
    service, existing = _service_with_existing(is_draft=True)
    result = service.update_by_public_id(public_id="bill-1", row_version="rv", is_draft=True)
    assert result is not None
    assert existing.is_draft is True
    service.repo.update_by_id.assert_called_once()


def test_omitted_is_draft_never_raises():
    service, existing = _service_with_existing(is_draft=True)
    result = service.update_by_public_id(public_id="bill-1", row_version="rv")
    assert result is not None
    assert existing.is_draft is True


@pytest.mark.parametrize("initial,target", [(True, False), (False, True)])
def test_via_completion_pipeline_allows_flip(initial, target):
    service, existing = _service_with_existing(is_draft=initial)
    result = service.update_by_public_id(
        public_id="bill-1", row_version="rv", is_draft=target, _via_completion_pipeline=True,
    )
    assert result is not None
    assert existing.is_draft is target


def _make_bill() -> SimpleNamespace:
    return SimpleNamespace(
        id=55,
        public_id="pub-55",
        row_version="rv-0",
        vendor_id=101,
        payment_term_id=None,
        bill_number="B-1",
        bill_date="2026-09-01",
        due_date="2026-09-30",
        total_amount=None,
        memo=None,
        is_draft=True,
    )


def test_complete_bill_passes_completion_pipeline_escape_hatch():
    """complete_bill() is the one legitimate True->False caller — prove its
    internal call to update_by_public_id actually carries the escape hatch,
    not just that the guard exists in isolation."""
    service = BillService(repo=MagicMock())
    service.read_by_public_id = MagicMock(return_value=_make_bill())
    service.vendor_service = MagicMock()
    service.vendor_service.read_by_id.return_value = SimpleNamespace(public_id="vendor-pub-1")
    service.bill_line_item_service = MagicMock()
    service.bill_line_item_service.read_by_bill_id.return_value = []
    # Bypass the lazy qbo_auth_service property so _enqueue_qbo_sync's
    # `read_all()` call can't reach a real (DB-backed) QboAuthService.
    service._qbo_auth_service = MagicMock(read_all=MagicMock(return_value=[]))

    with patch.object(BillService, "update_by_public_id") as mock_update:
        mock_update.return_value = SimpleNamespace(id=55, public_id="pub-55", is_draft=False)
        result = service.complete_bill(public_id="pub-55")

    mock_update.assert_called_once()
    kwargs = mock_update.call_args.kwargs
    assert kwargs["is_draft"] is False
    assert kwargs["_via_completion_pipeline"] is True
    assert result["bill_finalized"] is True
