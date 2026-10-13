"""U-653e: BillCreate accepts line_coding_prefill_state and threads it to the placeholder BLI."""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from entities.bill.api.router import create_bill_router
from entities.bill.api.schemas import BillCreate
from entities.bill.business.service import BillService

_CURRENT_USER = {"id": 17, "username": "tester", "tenant_id": 1}

_BILL_CREATE_KW = dict(
    vendor_public_id="v-1",
    bill_date="2026-08-02",
    due_date="2026-08-02",
    bill_number="INV-1",
    attachment_public_id="a-1",
)


@pytest.mark.parametrize(
    "state",
    [None, "prefilled", "accepted", "changed"],
    ids=["none", "prefilled", "accepted", "changed"],
)
def test_bill_create_schema_accepts_valid_prefill_states(state):
    body = BillCreate(**_BILL_CREATE_KW, line_coding_prefill_state=state)
    assert body.line_coding_prefill_state == state


def test_bill_create_schema_rejects_invalid_prefill_state():
    with pytest.raises(ValidationError):
        BillCreate(**_BILL_CREATE_KW, line_coding_prefill_state="bogus")


def _create_bill_and_capture(**overrides) -> dict:
    captured = {}

    def _capture(context):
        captured["ctx"] = context
        return {"success": True, "data": {"public_id": "bill-x"}}

    with patch("entities.bill.api.router.ProcessEngine") as mock_engine_cls, patch(
        "entities.bill.api.router._run_complete_bill"
    ), patch("entities.bill.api.router.resolve_user_id", return_value=17):
        mock_engine_cls.return_value.execute_synchronous.side_effect = _capture
        body = BillCreate(**_BILL_CREATE_KW, **overrides)
        asyncio.run(
            create_bill_router(
                body=body,
                current_user=_CURRENT_USER,
            )
        )
    return captured["ctx"].payload


def test_create_bill_router_forwards_line_coding_prefill_state():
    payload = _create_bill_and_capture(line_coding_prefill_state="accepted")
    assert payload["line_coding_prefill_state"] == "accepted"


def test_create_bill_router_forwards_none_prefill_state():
    payload = _create_bill_and_capture()
    assert payload["line_coding_prefill_state"] is None


def _bill_service_for_placeholder_line():
    repo = MagicMock()
    repo.read_by_bill_number_and_vendor_id.return_value = None
    repo.create.return_value = SimpleNamespace(id=99, public_id="bill-99")
    svc = BillService(repo=repo)
    svc.attachment_service = MagicMock()
    svc.attachment_service.read_by_public_id.return_value = SimpleNamespace(
        content_type="application/pdf",
        public_id="a-1",
    )
    svc.bill_line_item_service = MagicMock()
    svc.bill_line_item_service.create.return_value = SimpleNamespace(public_id="bli-1")
    svc.bill_line_item_attachment_service = MagicMock()
    return svc


@patch("entities.bill.business.service.VendorService")
def test_bill_service_create_passes_coding_prefill_state_to_placeholder_line(
    mock_vendor_cls,
):
    mock_vendor_cls.return_value.read_by_public_id.return_value = SimpleNamespace(id=1)
    svc = _bill_service_for_placeholder_line()

    svc.create(
        bill_date="2026-08-02",
        due_date="2026-08-02",
        vendor_public_id="v-1",
        bill_number="INV-1",
        attachment_public_id="a-1",
        line_coding_prefill_state="prefilled",
    )

    kwargs = svc.bill_line_item_service.create.call_args.kwargs
    assert kwargs.get("coding_prefill_state") == "prefilled"


@patch("entities.bill.business.service.VendorService")
def test_bill_service_create_omits_coding_prefill_state_when_none(mock_vendor_cls):
    mock_vendor_cls.return_value.read_by_public_id.return_value = SimpleNamespace(id=1)
    svc = _bill_service_for_placeholder_line()

    svc.create(
        bill_date="2026-08-02",
        due_date="2026-08-02",
        vendor_public_id="v-1",
        bill_number="INV-1",
        attachment_public_id="a-1",
    )

    kwargs = svc.bill_line_item_service.create.call_args.kwargs
    assert "coding_prefill_state" not in kwargs
