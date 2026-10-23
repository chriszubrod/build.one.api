"""U-716a — expense receipt read + attach (fakes only)."""
from __future__ import annotations

import asyncio
import inspect
import logging
import uuid
from datetime import date
from decimal import Decimal
from uuid import UUID
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from entities.bill.business.read_attachment_service import AttachmentNotReadable
from entities.expense.business.receipt_match_service import (
    ExpenseReceiptConflictError,
    ExpenseReceiptNotFoundError,
    attach_receipt_to_expense,
    run_expense_read_receipt,
)
from shared.api.errors import install_error_handlers
from shared.access import EntityNotAccessibleError
from shared.rbac_constants import Modules

ATT_PUB = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
EXP_PUB = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
LI_PUB = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _attachment(**over):
    base = dict(id=9, public_id=ATT_PUB, content_type="application/pdf")
    base.update(over)
    return SimpleNamespace(**base)


def _link_state(*, is_linked=False, is_owned=True, is_taxpayer=False):
    return SimpleNamespace(is_linked=is_linked, is_owned=is_owned, is_taxpayer=is_taxpayer)


def _candidate_row(**over):
    base = dict(
        public_id=EXP_PUB,
        vendor_name="Acme",
        expense_date="2026-03-01",
        total_amount=Decimal("42.00"),
        reference_number="R-1",
        is_draft=False,
        status="completed",
        vendor_matches=True,
        days_apart=0,
    )
    base.update(over)
    return base


def test_read_passes_candidates_with_metadata():
    repo = MagicMock()
    repo.read_candidates.return_value = [_candidate_row()]
    with patch(
        "entities.expense.business.receipt_match_service.gate_attachment_for_bill_read",
        new=AsyncMock(return_value=(_attachment(), _link_state())),
    ), patch(
        "entities.expense.business.receipt_match_service.extract_with_inflight_wait",
        new=AsyncMock(
            return_value={"status": "completed", "masked_text": "total 42.00"}
        ),
    ), patch(
        "entities.expense.business.receipt_match_service.run_bill_header_extraction",
        new=AsyncMock(return_value=SimpleNamespace(stopped_reason=None)),
    ), patch(
        "entities.expense.business.receipt_match_service.header_fields_from_cascade",
        return_value=(
            {
                "vendor": {"value": {"public_id": "v", "name": "Acme"}, "extracted_name": None},
                "bill_date": {"value": "2026-03-01"},
                "total_amount": {"value": "42.00"},
                "bill_number": {"value": "R-1"},
            },
            True,
            None,
            SimpleNamespace(vendor_id=3, public_id="v", name="Acme"),
        ),
    ), patch("entities.expense.business.receipt_match_service.claude_budget") as budget, patch(
        "entities.expense.business.receipt_match_service.config.Settings"
    ) as settings_cls:
        budget.is_claude_day_stopped_async = AsyncMock(return_value=False)
        settings_cls.return_value.bill_drop_prefill_enabled = True
        settings_cls.return_value.bill_drop_prefill_timeout_seconds = 25

        out = asyncio.run(run_expense_read_receipt(
            attachment_public_id=ATT_PUB, match_repo=repo, settings=settings_cls.return_value
        ))

    assert out["status"] == "matched"
    assert len(out["candidates"]) == 1
    c = out["candidates"][0]
    assert c["vendor_matches"] is True
    assert c["days_apart"] == 0
    assert c["is_completed"] is True
    repo.read_candidates.assert_called_once()
    assert repo.read_candidates.call_args.kwargs["total_amount"] == Decimal("42.00")


def test_read_abs_total_for_refund_receipt():
    repo = MagicMock()
    repo.read_candidates.return_value = []
    with patch(
        "entities.expense.business.receipt_match_service.gate_attachment_for_bill_read",
        new=AsyncMock(return_value=(_attachment(), _link_state())),
    ), patch(
        "entities.expense.business.receipt_match_service.extract_with_inflight_wait",
        new=AsyncMock(return_value={"status": "completed", "masked_text": "x"}),
    ), patch(
        "entities.expense.business.receipt_match_service.run_bill_header_extraction",
        new=AsyncMock(return_value=SimpleNamespace(stopped_reason=None)),
    ), patch(
        "entities.expense.business.receipt_match_service.header_fields_from_cascade",
        return_value=(
            {"vendor": {}, "bill_date": {}, "total_amount": {"value": "-15.50"}, "bill_number": {}},
            True,
            None,
            None,
        ),
    ), patch("entities.expense.business.receipt_match_service.claude_budget") as budget, patch(
        "entities.expense.business.receipt_match_service.config.Settings"
    ) as settings_cls:
        budget.is_claude_day_stopped_async = AsyncMock(return_value=False)
        settings_cls.return_value.bill_drop_prefill_enabled = True
        settings_cls.return_value.bill_drop_prefill_timeout_seconds = 25
        out = asyncio.run(run_expense_read_receipt(
            attachment_public_id=ATT_PUB, match_repo=repo, settings=settings_cls.return_value
        ))

    assert out["fields"]["total_amount"] == "15.50"
    assert repo.read_candidates.call_args.kwargs["total_amount"] == Decimal("15.50")


def test_read_unreadable_skips_sproc_when_no_total():
    repo = MagicMock()
    with patch(
        "entities.expense.business.receipt_match_service.gate_attachment_for_bill_read",
        new=AsyncMock(return_value=(_attachment(), _link_state())),
    ), patch(
        "entities.expense.business.receipt_match_service.extract_with_inflight_wait",
        new=AsyncMock(return_value={"status": "completed", "masked_text": "x"}),
    ), patch(
        "entities.expense.business.receipt_match_service.run_bill_header_extraction",
        new=AsyncMock(return_value=SimpleNamespace(stopped_reason=None)),
    ), patch(
        "entities.expense.business.receipt_match_service.header_fields_from_cascade",
        return_value=({"vendor": {}, "bill_date": {}, "total_amount": {}, "bill_number": {}}, False, None, None),
    ), patch("entities.expense.business.receipt_match_service.claude_budget") as budget, patch(
        "entities.expense.business.receipt_match_service.config.Settings"
    ) as settings_cls:
        budget.is_claude_day_stopped_async = AsyncMock(return_value=False)
        settings_cls.return_value.bill_drop_prefill_enabled = True
        settings_cls.return_value.bill_drop_prefill_timeout_seconds = 25
        out = asyncio.run(run_expense_read_receipt(
            attachment_public_id=ATT_PUB, match_repo=repo, settings=settings_cls.return_value
        ))

    assert out["status"] == "unreadable"
    repo.read_candidates.assert_not_called()


def test_read_disabled_and_ai_unavailable():
    with patch("entities.expense.business.receipt_match_service.config.Settings") as settings_cls:
        settings_cls.return_value.bill_drop_prefill_enabled = False
        out = asyncio.run(run_expense_read_receipt(
            attachment_public_id=ATT_PUB, settings=settings_cls.return_value
        ))
    assert out["status"] == "disabled"

    with patch("entities.expense.business.receipt_match_service.config.Settings") as settings_cls, patch(
        "entities.expense.business.receipt_match_service.claude_budget"
    ) as budget:
        settings_cls.return_value.bill_drop_prefill_enabled = True
        budget.is_claude_day_stopped_async = AsyncMock(return_value=True)
        out = asyncio.run(run_expense_read_receipt(
            attachment_public_id=ATT_PUB, settings=settings_cls.return_value
        ))
    assert out["status"] == "ai_unavailable"


def test_read_404_not_owned_attachment():
    with patch(
        "entities.expense.business.receipt_match_service.gate_attachment_for_bill_read",
        new=AsyncMock(side_effect=AttachmentNotReadable("not_found")),
    ), patch("entities.expense.business.receipt_match_service.config.Settings") as settings_cls, patch(
        "entities.expense.business.receipt_match_service.claude_budget"
    ) as budget:
        settings_cls.return_value.bill_drop_prefill_enabled = True
        budget.is_claude_day_stopped_async = AsyncMock(return_value=False)
        with pytest.raises(AttachmentNotReadable):
            asyncio.run(
                run_expense_read_receipt(
                    attachment_public_id=ATT_PUB, settings=settings_cls.return_value
                )
            )


def test_read_409_attachment_linked():
    with patch(
        "entities.expense.business.receipt_match_service.gate_attachment_for_bill_read",
        new=AsyncMock(return_value=(_attachment(), _link_state(is_linked=True))),
    ), patch("entities.expense.business.receipt_match_service.config.Settings") as settings_cls, patch(
        "entities.expense.business.receipt_match_service.claude_budget"
    ) as budget:
        settings_cls.return_value.bill_drop_prefill_enabled = True
        budget.is_claude_day_stopped_async = AsyncMock(return_value=False)
        with pytest.raises(ExpenseReceiptConflictError) as exc:
            asyncio.run(
                run_expense_read_receipt(
                    attachment_public_id=ATT_PUB, settings=settings_cls.return_value
                )
            )
    assert exc.value.detail == "attachment_linked"


def test_read_no_receipt_text_in_logs(caplog):
    caplog.set_level(logging.INFO)
    repo = MagicMock()
    repo.read_candidates.return_value = []
    secret = "SUPER_SECRET_MEMO_LINE"
    with patch(
        "entities.expense.business.receipt_match_service.gate_attachment_for_bill_read",
        new=AsyncMock(return_value=(_attachment(), _link_state())),
    ), patch(
        "entities.expense.business.receipt_match_service.extract_with_inflight_wait",
        new=AsyncMock(return_value={"status": "completed", "masked_text": secret}),
    ), patch(
        "entities.expense.business.receipt_match_service.run_bill_header_extraction",
        new=AsyncMock(return_value=SimpleNamespace(stopped_reason=None)),
    ), patch(
        "entities.expense.business.receipt_match_service.header_fields_from_cascade",
        return_value=(
            {
                "vendor": {},
                "bill_date": {"value": "2026-01-01"},
                "total_amount": {"value": "1.00"},
                "bill_number": {"value": "R-1"},
            },
            True,
            None,
            None,
        ),
    ), patch("entities.expense.business.receipt_match_service.claude_budget") as budget, patch(
        "entities.expense.business.receipt_match_service.config.Settings"
    ) as settings_cls:
        budget.is_claude_day_stopped_async = AsyncMock(return_value=False)
        settings_cls.return_value.bill_drop_prefill_enabled = True
        settings_cls.return_value.bill_drop_prefill_timeout_seconds = 25
        out = asyncio.run(run_expense_read_receipt(
            attachment_public_id=ATT_PUB, match_repo=repo, settings=settings_cls.return_value
        ))

    assert secret not in str(out)
    assert secret not in caplog.text


def test_attach_ok_shape():
    repo = MagicMock()
    repo.attach_first_receipt.return_value = {
        "result_code": "ok",
        "expense_public_id": EXP_PUB,
        "expense_line_item_public_id": LI_PUB,
        "attachment_public_id": ATT_PUB,
    }
    expense = SimpleNamespace(id=5, status="draft", is_draft=True)
    with patch(
        "entities.expense.business.receipt_match_service.gate_attachment_for_bill_read",
        new=AsyncMock(return_value=(_attachment(), _link_state())),
    ), patch(
        "entities.expense.business.receipt_match_service.ExpenseService"
    ) as exp_svc_cls:
        exp_svc_cls.return_value.read_by_public_id.return_value = expense
        out = asyncio.run(
            attach_receipt_to_expense(
                expense_public_id=EXP_PUB,
                attachment_public_id=ATT_PUB,
                match_repo=repo,
                expense_service=exp_svc_cls.return_value,
            )
        )
    assert out == {
        "expense_public_id": EXP_PUB,
        "expense_line_item_public_id": LI_PUB,
        "attachment_public_id": ATT_PUB,
    }


@pytest.mark.parametrize(
    "code,detail",
    [
        ("attachment_linked", "attachment_linked"),
        ("expense_has_receipt", "expense_has_receipt"),
        ("expense_has_no_lines", "expense_has_no_lines"),
    ],
)
def test_attach_conflict_codes(code, detail):
    repo = MagicMock()
    repo.attach_first_receipt.return_value = {"result_code": code}
    with patch(
        "entities.expense.business.receipt_match_service.gate_attachment_for_bill_read",
        new=AsyncMock(return_value=(_attachment(), _link_state())),
    ), patch(
        "entities.expense.business.receipt_match_service.ExpenseService"
    ) as exp_svc_cls:
        exp_svc_cls.return_value.read_by_public_id.return_value = SimpleNamespace(
            id=1, status="draft", is_draft=True
        )
        with pytest.raises(ExpenseReceiptConflictError) as exc:
            asyncio.run(
                attach_receipt_to_expense(
                    expense_public_id=EXP_PUB,
                    attachment_public_id=ATT_PUB,
                    match_repo=repo,
                )
            )
    assert exc.value.detail == detail


def test_attach_not_found_expense():
    repo = MagicMock()
    repo.attach_first_receipt.return_value = {"result_code": "not_found"}
    with patch(
        "entities.expense.business.receipt_match_service.gate_attachment_for_bill_read",
        new=AsyncMock(return_value=(_attachment(), _link_state())),
    ), patch(
        "entities.expense.business.receipt_match_service.ExpenseService"
    ) as exp_svc_cls:
        exp_svc_cls.return_value.read_by_public_id.return_value = None
        with pytest.raises(ExpenseReceiptNotFoundError):
            asyncio.run(
                attach_receipt_to_expense(
                    expense_public_id=EXP_PUB,
                    attachment_public_id=ATT_PUB,
                    match_repo=repo,
                )
            )


def test_attach_terminal_parent_allowed_and_logs(caplog):
    caplog.set_level(logging.INFO)
    repo = MagicMock()
    repo.attach_first_receipt.return_value = {
        "result_code": "ok",
        "expense_public_id": EXP_PUB,
        "expense_line_item_public_id": LI_PUB,
        "attachment_public_id": ATT_PUB,
    }
    expense = SimpleNamespace(id=99, status="completed", is_draft=False)
    with patch(
        "entities.expense.business.receipt_match_service.gate_attachment_for_bill_read",
        new=AsyncMock(return_value=(_attachment(), _link_state())),
    ), patch(
        "entities.expense.business.receipt_match_service.ExpenseService"
    ) as exp_svc_cls, patch(
        "entities.expense.business.receipt_match_service.current_user_id"
    ) as uid:
        uid.get.return_value = 17
        exp_svc_cls.return_value.read_by_public_id.return_value = expense
        asyncio.run(
            attach_receipt_to_expense(
                expense_public_id=EXP_PUB,
                attachment_public_id=ATT_PUB,
                match_repo=repo,
                expense_service=exp_svc_cls.return_value,
            )
        )
    assert "expense.attach_receipt.terminal_add" in caplog.text


def _route_client(*, allow: bool, route_name: str) -> TestClient:
    from entities.expense.api.router import (
        expense_attach_receipt_router,
        expense_read_receipt_router,
        router as expense_router,
    )

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(expense_router)
    if route_name == "read":
        fn = expense_read_receipt_router
    else:
        fn = expense_attach_receipt_router
    dep = inspect.signature(fn).parameters["_"].default.dependency
    if allow:
        app.dependency_overrides[dep] = lambda: {"id": 17, "tenant_id": 1}
    else:

        def _deny():
            raise HTTPException(status_code=403, detail="Forbidden")

        app.dependency_overrides[dep] = _deny
    return TestClient(app, raise_server_exceptions=False)


def test_route_permission_enforced():
    client = _route_client(allow=False, route_name="read")
    r = client.post(
        "/api/v1/expense/read-receipt",
        json={"attachment_public_id": ATT_PUB},
    )
    assert r.status_code == 403

    client_attach = _route_client(allow=False, route_name="attach")
    r2 = client_attach.post(
        f"/api/v1/expense/{EXP_PUB}/attach-receipt",
        json={"attachment_public_id": ATT_PUB},
    )
    assert r2.status_code == 403


def test_route_permission_is_expenses_can_update():
    from entities.expense.api.router import (
        expense_attach_receipt_router,
        expense_read_receipt_router,
    )

    def _perm(endpoint):
        sig = inspect.signature(endpoint)
        for name in ("current_user", "_"):
            if name not in sig.parameters:
                continue
            dep = sig.parameters[name].default.dependency
            nonlocals = inspect.getclosurevars(dep).nonlocals
            return nonlocals.get("module_name"), nonlocals.get("permission")
        return None, None

    assert _perm(expense_read_receipt_router) == (Modules.EXPENSES, "can_update")
    assert _perm(expense_attach_receipt_router) == (Modules.EXPENSES, "can_update")


def test_route_http_exception_not_swallowed_to_500():
    client = _route_client(allow=True, route_name="attach")
    with patch(
        "entities.expense.api.router.attach_receipt_to_expense",
        new=AsyncMock(side_effect=HTTPException(status_code=418, detail="teapot")),
    ):
        r = client.post(
            f"/api/v1/expense/{EXP_PUB}/attach-receipt",
            json={"attachment_public_id": ATT_PUB},
        )
    assert r.status_code == 418


def test_route_entity_not_accessible_not_swallowed():
    client = _route_client(allow=True, route_name="attach")

    with patch(
        "entities.expense.api.router.attach_receipt_to_expense",
        new=AsyncMock(side_effect=EntityNotAccessibleError("expense", 1)),
    ):
        r = client.post(
            f"/api/v1/expense/{EXP_PUB}/attach-receipt",
            json={"attachment_public_id": ATT_PUB},
        )
    assert r.status_code == 404


def test_route_read_404_attachment_not_found():
    client = _route_client(allow=True, route_name="read")
    with patch(
        "entities.expense.api.router.run_expense_read_receipt_with_timeout",
        new=AsyncMock(side_effect=AttachmentNotReadable("not_found")),
    ):
        r = client.post(
            "/api/v1/expense/read-receipt",
            json={"attachment_public_id": ATT_PUB},
        )
    assert r.status_code == 404
    assert r.json()["detail"] == "Attachment not found"


def test_route_read_409_attachment_linked_shape():
    client = _route_client(allow=True, route_name="read")
    with patch(
        "entities.expense.api.router.run_expense_read_receipt_with_timeout",
        new=AsyncMock(side_effect=ExpenseReceiptConflictError("attachment_linked")),
    ):
        r = client.post(
            "/api/v1/expense/read-receipt",
            json={"attachment_public_id": ATT_PUB},
        )
    assert r.status_code == 409
    assert r.json()["detail"] == "attachment_linked"


def test_route_attach_404_attachment_not_found():
    client = _route_client(allow=True, route_name="attach")
    with patch(
        "entities.expense.api.router.attach_receipt_to_expense",
        new=AsyncMock(side_effect=ExpenseReceiptNotFoundError("Attachment")),
    ):
        r = client.post(
            f"/api/v1/expense/{EXP_PUB}/attach-receipt",
            json={"attachment_public_id": ATT_PUB},
        )
    assert r.status_code == 404
    assert r.json()["detail"] == "Attachment not found"


def test_route_attach_409_conflict_shape():
    client = _route_client(allow=True, route_name="attach")
    with patch(
        "entities.expense.api.router.attach_receipt_to_expense",
        new=AsyncMock(side_effect=ExpenseReceiptConflictError("expense_has_receipt")),
    ):
        r = client.post(
            f"/api/v1/expense/{EXP_PUB}/attach-receipt",
            json={"attachment_public_id": ATT_PUB},
        )
    assert r.status_code == 409
    assert r.json()["detail"] == "expense_has_receipt"


def test_route_attach_malformed_expense_public_id_404():
    client = _route_client(allow=True, route_name="attach")
    r = client.post(
        "/api/v1/expense/not-a-uuid/attach-receipt",
        json={"attachment_public_id": ATT_PUB},
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "Expense not found"


def test_attach_malformed_expense_id_before_attachment_gate():
    repo = MagicMock()
    with patch(
        "entities.expense.business.receipt_match_service.gate_attachment_for_bill_read",
        new=AsyncMock(),
    ) as gate:
        with pytest.raises(ExpenseReceiptNotFoundError):
            asyncio.run(
                attach_receipt_to_expense(
                    expense_public_id="not-a-uuid",
                    attachment_public_id=ATT_PUB,
                    match_repo=repo,
                )
            )
    gate.assert_not_called()
    repo.attach_first_receipt.assert_not_called()


def test_attach_not_owned_attachment_404():
    with patch(
        "entities.expense.business.receipt_match_service.gate_attachment_for_bill_read",
        new=AsyncMock(side_effect=AttachmentNotReadable("not_found")),
    ):
        with pytest.raises(ExpenseReceiptNotFoundError) as exc:
            asyncio.run(
                attach_receipt_to_expense(
                    expense_public_id=EXP_PUB,
                    attachment_public_id=ATT_PUB,
                )
            )
    assert exc.value.entity == "Attachment"


def test_attach_is_linked_refused_before_sproc():
    repo = MagicMock()
    with patch(
        "entities.expense.business.receipt_match_service.gate_attachment_for_bill_read",
        new=AsyncMock(return_value=(_attachment(), _link_state(is_linked=True))),
    ):
        with pytest.raises(ExpenseReceiptConflictError):
            asyncio.run(
                attach_receipt_to_expense(
                    expense_public_id=EXP_PUB,
                    attachment_public_id=ATT_PUB,
                    match_repo=repo,
                )
            )
    repo.attach_first_receipt.assert_not_called()


def test_read_timeout_status():
    with patch(
        "entities.expense.business.receipt_match_service.gate_attachment_for_bill_read",
        new=AsyncMock(return_value=(_attachment(), _link_state())),
    ), patch(
        "entities.expense.business.receipt_match_service.extract_with_inflight_wait",
        new=AsyncMock(return_value={"status": "timeout"}),
    ), patch("entities.expense.business.receipt_match_service.claude_budget") as budget, patch(
        "entities.expense.business.receipt_match_service.config.Settings"
    ) as settings_cls:
        budget.is_claude_day_stopped_async = AsyncMock(return_value=False)
        settings_cls.return_value.bill_drop_prefill_enabled = True
        settings_cls.return_value.bill_drop_prefill_timeout_seconds = 25
        out = asyncio.run(
            run_expense_read_receipt(
                attachment_public_id=ATT_PUB, settings=settings_cls.return_value
            )
        )
    assert out["status"] == "timeout"


def test_read_no_match_status():
    repo = MagicMock()
    repo.read_candidates.return_value = []
    with patch(
        "entities.expense.business.receipt_match_service.gate_attachment_for_bill_read",
        new=AsyncMock(return_value=(_attachment(), _link_state())),
    ), patch(
        "entities.expense.business.receipt_match_service.extract_with_inflight_wait",
        new=AsyncMock(return_value={"status": "completed", "masked_text": "x"}),
    ), patch(
        "entities.expense.business.receipt_match_service.run_bill_header_extraction",
        new=AsyncMock(return_value=SimpleNamespace(stopped_reason=None)),
    ), patch(
        "entities.expense.business.receipt_match_service.header_fields_from_cascade",
        return_value=(
            {"vendor": {}, "bill_date": {}, "total_amount": {"value": "9.99"}, "bill_number": {}},
            True,
            None,
            None,
        ),
    ), patch("entities.expense.business.receipt_match_service.claude_budget") as budget, patch(
        "entities.expense.business.receipt_match_service.config.Settings"
    ) as settings_cls:
        budget.is_claude_day_stopped_async = AsyncMock(return_value=False)
        settings_cls.return_value.bill_drop_prefill_enabled = True
        settings_cls.return_value.bill_drop_prefill_timeout_seconds = 25
        out = asyncio.run(
            run_expense_read_receipt(
                attachment_public_id=ATT_PUB, match_repo=repo, settings=settings_cls.return_value
            )
        )
    assert out["status"] == "no_match"


def test_read_passes_is_taxpayer_to_extraction():
    repo = MagicMock()
    repo.read_candidates.return_value = []
    extract = AsyncMock(return_value={"status": "completed", "masked_text": "total 1.00"})
    with patch(
        "entities.expense.business.receipt_match_service.gate_attachment_for_bill_read",
        new=AsyncMock(return_value=(_attachment(), _link_state(is_taxpayer=True))),
    ), patch(
        "entities.expense.business.receipt_match_service.extract_with_inflight_wait",
        new=extract,
    ), patch(
        "entities.expense.business.receipt_match_service.run_bill_header_extraction",
        new=AsyncMock(return_value=SimpleNamespace(stopped_reason=None)),
    ), patch(
        "entities.expense.business.receipt_match_service.header_fields_from_cascade",
        return_value=(
            {"vendor": {}, "bill_date": {}, "total_amount": {"value": "1.00"}, "bill_number": {}},
            True,
            None,
            None,
        ),
    ), patch("entities.expense.business.receipt_match_service.claude_budget") as budget, patch(
        "entities.expense.business.receipt_match_service.config.Settings"
    ) as settings_cls:
        budget.is_claude_day_stopped_async = AsyncMock(return_value=False)
        settings_cls.return_value.bill_drop_prefill_enabled = True
        settings_cls.return_value.bill_drop_prefill_timeout_seconds = 25
        asyncio.run(
            run_expense_read_receipt(
                attachment_public_id=ATT_PUB,
                match_repo=repo,
                settings=settings_cls.return_value,
            )
        )
    assert extract.call_args.kwargs["is_taxpayer"] is True


def test_read_allows_none_content_type():
    repo = MagicMock()
    repo.read_candidates.return_value = []
    with patch(
        "entities.expense.business.receipt_match_service.gate_attachment_for_bill_read",
        new=AsyncMock(return_value=(_attachment(content_type=None), _link_state())),
    ), patch(
        "entities.expense.business.receipt_match_service.extract_with_inflight_wait",
        new=AsyncMock(return_value={"status": "completed", "masked_text": "total 3.00"}),
    ), patch(
        "entities.expense.business.receipt_match_service.run_bill_header_extraction",
        new=AsyncMock(return_value=SimpleNamespace(stopped_reason=None)),
    ), patch(
        "entities.expense.business.receipt_match_service.header_fields_from_cascade",
        return_value=(
            {"vendor": {}, "bill_date": {}, "total_amount": {"value": "3.00"}, "bill_number": {}},
            True,
            None,
            None,
        ),
    ), patch("entities.expense.business.receipt_match_service.claude_budget") as budget, patch(
        "entities.expense.business.receipt_match_service.config.Settings"
    ) as settings_cls:
        budget.is_claude_day_stopped_async = AsyncMock(return_value=False)
        settings_cls.return_value.bill_drop_prefill_enabled = True
        settings_cls.return_value.bill_drop_prefill_timeout_seconds = 25
        out = asyncio.run(
            run_expense_read_receipt(
                attachment_public_id=ATT_PUB, match_repo=repo, settings=settings_cls.return_value
            )
        )
    assert out["status"] == "no_match"
    repo.read_candidates.assert_called_once()


def test_read_expense_date_isoformat_not_compact_str_slice():
    repo = MagicMock()
    repo.read_candidates.return_value = []
    with patch(
        "entities.expense.business.receipt_match_service.gate_attachment_for_bill_read",
        new=AsyncMock(return_value=(_attachment(), _link_state())),
    ), patch(
        "entities.expense.business.receipt_match_service.extract_with_inflight_wait",
        new=AsyncMock(return_value={"status": "completed", "masked_text": "x"}),
    ), patch(
        "entities.expense.business.receipt_match_service.run_bill_header_extraction",
        new=AsyncMock(return_value=SimpleNamespace(stopped_reason=None)),
    ), patch(
        "entities.expense.business.receipt_match_service.header_fields_from_cascade",
        return_value=(
            {
                "vendor": {},
                "bill_date": {"value": "20260101"},
                "total_amount": {"value": "4.00"},
                "bill_number": {},
            },
            True,
            None,
            None,
        ),
    ), patch("entities.expense.business.receipt_match_service.claude_budget") as budget, patch(
        "entities.expense.business.receipt_match_service.config.Settings"
    ) as settings_cls:
        budget.is_claude_day_stopped_async = AsyncMock(return_value=False)
        settings_cls.return_value.bill_drop_prefill_enabled = True
        settings_cls.return_value.bill_drop_prefill_timeout_seconds = 25
        out = asyncio.run(
            run_expense_read_receipt(
                attachment_public_id=ATT_PUB, match_repo=repo, settings=settings_cls.return_value
            )
        )
    assert out["fields"]["expense_date"] == "2026-01-01"

    repo.read_candidates.return_value = []
    with patch(
        "entities.expense.business.receipt_match_service.gate_attachment_for_bill_read",
        new=AsyncMock(return_value=(_attachment(), _link_state())),
    ), patch(
        "entities.expense.business.receipt_match_service.extract_with_inflight_wait",
        new=AsyncMock(return_value={"status": "completed", "masked_text": "x"}),
    ), patch(
        "entities.expense.business.receipt_match_service.run_bill_header_extraction",
        new=AsyncMock(return_value=SimpleNamespace(stopped_reason=None)),
    ), patch(
        "entities.expense.business.receipt_match_service.header_fields_from_cascade",
        return_value=(
            {
                "vendor": {},
                "bill_date": {"value": date(2026, 1, 1)},
                "total_amount": {"value": "4.00"},
                "bill_number": {},
            },
            True,
            None,
            None,
        ),
    ), patch("entities.expense.business.receipt_match_service.claude_budget") as budget, patch(
        "entities.expense.business.receipt_match_service.config.Settings"
    ) as settings_cls:
        budget.is_claude_day_stopped_async = AsyncMock(return_value=False)
        settings_cls.return_value.bill_drop_prefill_enabled = True
        settings_cls.return_value.bill_drop_prefill_timeout_seconds = 25
        out2 = asyncio.run(
            run_expense_read_receipt(
                attachment_public_id=ATT_PUB, match_repo=repo, settings=settings_cls.return_value
            )
        )
    assert out2["fields"]["expense_date"] == "2026-01-01"
    assert repo.read_candidates.call_args.kwargs["expense_date"].isoformat() == "2026-01-01"


def test_read_vendor_id_resolved_by_public_id_when_match_lacks_id():
    repo = MagicMock()
    repo.read_candidates.return_value = []
    vendor = SimpleNamespace(id=42, name="Resolved")
    with patch(
        "entities.expense.business.receipt_match_service.gate_attachment_for_bill_read",
        new=AsyncMock(return_value=(_attachment(), _link_state())),
    ), patch(
        "entities.expense.business.receipt_match_service.extract_with_inflight_wait",
        new=AsyncMock(return_value={"status": "completed", "masked_text": "x"}),
    ), patch(
        "entities.expense.business.receipt_match_service.run_bill_header_extraction",
        new=AsyncMock(return_value=SimpleNamespace(stopped_reason=None)),
    ), patch(
        "entities.expense.business.receipt_match_service.header_fields_from_cascade",
        return_value=(
            {
                "vendor": {"value": {"public_id": "cccccccc-cccc-cccc-cccc-cccccccccccc", "name": "V"}},
                "bill_date": {},
                "total_amount": {"value": "5.00"},
                "bill_number": {},
            },
            True,
            None,
            SimpleNamespace(vendor_id=None, public_id="cccccccc-cccc-cccc-cccc-cccccccccccc", name="V"),
        ),
    ), patch(
        "entities.expense.business.receipt_match_service.VendorService"
    ) as vendor_cls, patch("entities.expense.business.receipt_match_service.claude_budget") as budget, patch(
        "entities.expense.business.receipt_match_service.config.Settings"
    ) as settings_cls:
        vendor_cls.return_value.read_by_public_id.return_value = vendor
        budget.is_claude_day_stopped_async = AsyncMock(return_value=False)
        settings_cls.return_value.bill_drop_prefill_enabled = True
        settings_cls.return_value.bill_drop_prefill_timeout_seconds = 25
        asyncio.run(
            run_expense_read_receipt(
                attachment_public_id=ATT_PUB, match_repo=repo, settings=settings_cls.return_value
            )
        )
    assert repo.read_candidates.call_args.kwargs["vendor_id"] == 42


def test_attach_non_terminal_does_not_log_terminal_add(caplog):
    caplog.set_level(logging.INFO)
    repo = MagicMock()
    repo.attach_first_receipt.return_value = {
        "result_code": "ok",
        "expense_public_id": EXP_PUB,
        "expense_line_item_public_id": LI_PUB,
        "attachment_public_id": ATT_PUB,
    }
    with patch(
        "entities.expense.business.receipt_match_service.gate_attachment_for_bill_read",
        new=AsyncMock(return_value=(_attachment(), _link_state())),
    ), patch(
        "entities.expense.business.receipt_match_service.ExpenseService"
    ) as exp_svc_cls:
        exp_svc_cls.return_value.read_by_public_id.return_value = SimpleNamespace(
            id=1, status="draft", is_draft=True
        )
        asyncio.run(
            attach_receipt_to_expense(
                expense_public_id=EXP_PUB,
                attachment_public_id=ATT_PUB,
                match_repo=repo,
            )
        )
    assert "expense.attach_receipt.terminal_add" not in caplog.text


def test_repo_passes_actor_contextvars_to_sprocs():
    from entities.expense.persistence.receipt_match_repo import ExpenseReceiptMatchRepository

    captured = {}

    def fake_call_procedure(*, cursor, name, params):
        captured["name"] = name
        captured["params"] = params

    conn = MagicMock()
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = None
    conn.cursor.return_value = cursor

    with patch(
        "entities.expense.persistence.receipt_match_repo.get_connection",
        return_value=conn,
    ), patch(
        "entities.expense.persistence.receipt_match_repo.call_procedure",
        side_effect=fake_call_procedure,
    ), patch(
        "entities.expense.persistence.receipt_match_repo.current_user_id"
    ) as uid, patch(
        "entities.expense.persistence.receipt_match_repo.current_is_system_admin"
    ) as admin:
        uid.get.return_value = 716
        admin.get.return_value = True
        ExpenseReceiptMatchRepository().read_candidates(
            total_amount=Decimal("1.00"),
            expense_date=date(2026, 1, 1),
            vendor_id=1,
        )
    assert captured["name"] == "ReadExpenseReceiptCandidates"
    assert captured["params"]["ActorUserId"] == 716
    assert captured["params"]["ActorIsSystemAdmin"] == 1

    captured.clear()
    with patch(
        "entities.expense.persistence.receipt_match_repo.get_connection",
        return_value=conn,
    ), patch(
        "entities.expense.persistence.receipt_match_repo.call_procedure",
        side_effect=fake_call_procedure,
    ), patch(
        "entities.expense.persistence.receipt_match_repo.current_user_id"
    ) as uid, patch(
        "entities.expense.persistence.receipt_match_repo.current_is_system_admin"
    ) as admin:
        uid.get.return_value = 717
        admin.get.return_value = False
        ExpenseReceiptMatchRepository().attach_first_receipt(
            expense_public_id=UUID(EXP_PUB),
            attachment_id=9,
            created_by_user_id=717,
        )
    assert captured["name"] == "AttachFirstReceiptToExpense"
    assert captured["params"]["ActorUserId"] == 717
    assert captured["params"]["ActorIsSystemAdmin"] == 0


def test_attach_no_dash_expense_id_is_canonicalised_for_lookup():
    """U-716a r2: a dash-less UUID parses in Python but not as UNIQUEIDENTIFIER in SQL."""
    repo = MagicMock()
    repo.attach_first_receipt.return_value = {
        "result_code": "ok",
        "expense_line_item_public_id": LI_PUB,
        "attachment_public_id": ATT_PUB,
    }
    expense_svc = MagicMock()
    expense_svc.read_by_public_id.return_value = SimpleNamespace(id=5, status="draft", is_draft=True)
    no_dash = EXP_PUB.replace("-", "")
    with patch(
        "entities.expense.business.receipt_match_service.gate_attachment_for_bill_read",
        new=AsyncMock(return_value=(_attachment(), _link_state())),
    ):
        out = asyncio.run(
            attach_receipt_to_expense(
                expense_public_id=no_dash,
                attachment_public_id=ATT_PUB,
                match_repo=repo,
                expense_service=expense_svc,
            )
        )
    expense_svc.read_by_public_id.assert_called_once_with(str(uuid.UUID(EXP_PUB)))
    assert out["expense_public_id"] == str(uuid.UUID(EXP_PUB))
