import asyncio
from unittest.mock import MagicMock, patch

from entities.bill.api.router import create_bill_router, update_bill_by_public_id_router
from entities.bill.api.schemas import BillCreate, BillUpdate


def test_nondraft_create_does_not_schedule_completion():
    mock_complete = MagicMock()
    with patch("entities.bill.api.router.ProcessEngine") as mock_engine_cls, patch(
        "entities.bill.api.router._run_complete_bill", mock_complete
    ), patch("entities.bill.api.router.resolve_user_id", return_value=17):
        mock_engine_cls.return_value.execute_synchronous.return_value = {
            "success": True,
            "data": {"public_id": "bill-x", "is_draft": False},
        }
        body = BillCreate(
            vendor_public_id="v-1",
            bill_date="2026-07-19",
            due_date="2026-07-19",
            bill_number="INV-1",
            attachment_public_id="a-1",
            is_draft=False,
        )
        resp = asyncio.run(
            create_bill_router(
                body=body,
                current_user={"id": 17, "username": "tester", "tenant_id": 1},
            )
        )

    mock_complete.assert_not_called()
    assert not hasattr(resp, "status_code")
    assert resp["data"]["public_id"] == "bill-x"


def test_draft_to_complete_update_does_not_schedule_completion():
    mock_complete = MagicMock()
    with patch("entities.bill.api.router.ProcessEngine") as mock_engine_cls, patch(
        "entities.bill.api.router._run_complete_bill", mock_complete
    ):
        mock_engine_cls.return_value.execute_synchronous.return_value = {
            "success": True,
            "data": {"public_id": "bill-x", "is_draft": False},
        }
        body = BillUpdate(
            row_version="rv",
            vendor_public_id="v-1",
            bill_date="2026-07-19",
            due_date="2026-07-19",
            bill_number="INV-1",
            is_draft=False,
        )
        resp = asyncio.run(
            update_bill_by_public_id_router(
                public_id="bill-x",
                body=body,
                current_user={"id": 17, "username": "tester", "tenant_id": 1},
            )
        )

    mock_complete.assert_not_called()
    assert not hasattr(resp, "status_code")
    assert resp["data"]["public_id"] == "bill-x"
