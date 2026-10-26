"""U-676p: draft-from-attachment service + route (fakes)."""
from __future__ import annotations

import asyncio
import inspect
from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from entities.attachment.persistence.repo import AttachmentLinkState
from entities.bill.api.draft_from_attachment_router import (
    draft_bill_from_attachment_router,
    router as draft_router,
)
from entities.bill.api.schemas import BillCreate, BillUpdate
from entities.bill.business import draft_from_attachment_service as dfs
from entities.bill.business.draft_from_attachment_service import draft_bill_from_attachment
from entities.bill.business.model import Bill
from entities.bill.business import read_attachment_service as ras
from entities.bill.business.read_attachment_service import AttachmentNotReadable
from entities.bill.business.service import BillService, DuplicateBillError
from shared.access import EntityNotAccessibleError

ATT_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
ATT2_UUID = "11111111-bbbb-cccc-dddd-eeeeeeeeeeee"
EXISTING_BILL = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
INACCESSIBLE_DUP = "CCCCCCCC-CCCC-CCCC-CCCC-CCCCCCCCCCCC"
VENDOR_UUID = "cccccccc-cccc-cccc-cccc-cccccccccccc"
DUE_ON_RECEIPT_TERM = "ffffffff-ffff-ffff-ffff-ffffffffffff"


def _attachment(**kw):
    base = dict(
        id=99,
        public_id=ATT_UUID,
        file_hash="deadbeef",
        content_type="application/pdf",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _read_ok(**field_overrides):
    fields = {
        "vendor": {
            "value": {"public_id": VENDOR_UUID, "name": "Acme"},
            "extracted_name": "Acme",
            "confidence": "0.9000",
        },
        "bill_number": {"value": "INV-9", "confidence": "0.9000"},
        "bill_date": {"value": "2026-05-01", "confidence": "0.9000"},
        "total_amount": {"value": "250.00", "confidence": "0.9000"},
        "description": {"value": "Materials", "confidence": "0.9000"},
        "long_description": {"value": None, "confidence": None},
        "reference": {"value": None, "confidence": None},
    }
    fields.update(field_overrides)
    return {
        "status": "ok",
        "message": None,
        "fields": fields,
        "coding": {
            "project_public_id": "dddddddd-dddd-dddd-dddd-dddddddddddd",
            "sub_cost_code_public_id": "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
            "sub_cost_code_number": "05.100",
        },
        "model": "claude-haiku-5-5",
    }


@pytest.fixture
def client_can_create() -> TestClient:
    app = FastAPI()
    from shared.api.errors import install_error_handlers

    install_error_handlers(app)
    app.include_router(draft_router)
    dep = inspect.signature(draft_bill_from_attachment_router).parameters[
        "current_user"
    ].default.dependency
    app.dependency_overrides[dep] = lambda: {
        "id": 17,
        "sub": "auth-sub",
        "tenant_id": 1,
        "username": "u",
    }
    with patch(
        "entities.bill.api.draft_from_attachment_router.resolve_user_id",
        return_value=17,
    ):
        client = TestClient(app, raise_server_exceptions=False)
        yield client


@pytest.fixture
def client_no_create() -> TestClient:
    app = FastAPI()
    from shared.api.errors import install_error_handlers

    install_error_handlers(app)
    app.include_router(draft_router)
    dep = inspect.signature(draft_bill_from_attachment_router).parameters[
        "current_user"
    ].default.dependency

    def _deny():
        raise HTTPException(status_code=403, detail="Forbidden")

    app.dependency_overrides[dep] = _deny
    return TestClient(app, raise_server_exceptions=False)


def _service_mocks(
    *,
    hash_bill: str | None = None,
    create_bill=None,
):
    att_svc = MagicMock()
    att_svc.read_by_public_id.return_value = _attachment()
    blia_svc = MagicMock()
    blia_svc.read_draft_bill_public_id_by_attachment_hash.return_value = hash_bill
    bill_svc = MagicMock()
    if create_bill is None:
        create_bill = SimpleNamespace(public_id=EXISTING_BILL)
    bill_svc.create.return_value = create_bill
    read_fn = AsyncMock(return_value=_read_ok())
    return att_svc, blia_svc, bill_svc, read_fn


@contextmanager
def _patch_gate(*, unlinked: bool = True, owned: bool = True):
    """Patch draft path ownership + link-state (after real attachment read)."""
    state = AttachmentLinkState(is_linked=not unlinked, is_taxpayer=False, is_owned=owned)
    # The gate (read_attachment_service) and the in-lock re-check (draft service) both
    # read link state; patch both names.
    with patch.object(ras, "read_attachment_link_state", return_value=state), patch.object(
        dfs, "read_attachment_link_state", return_value=state
    ):
        yield


def _run(awaitable):
    return asyncio.run(awaitable)


def test_same_pdf_existing_skips_read_and_create(_no_live_payment_term_lookup):
    pt_cls = _no_live_payment_term_lookup
    att_svc, blia_svc, bill_svc, read_fn = _service_mocks(hash_bill=EXISTING_BILL)
    with _patch_gate(), patch.object(dfs, "_resolve_sub_cost_code_id", return_value=501):
        out = _run(
            draft_bill_from_attachment(
                attachment_public_id=ATT_UUID,
                attachment_service=att_svc,
                blia_service=blia_svc,
                bill_service=bill_svc,
                read_fn=read_fn,
            )
        )
    assert out["existing"] is True
    assert out["existing_reason"] == "same_pdf"
    assert out["bill_public_id"] == EXISTING_BILL.lower()
    read_fn.assert_not_called()
    bill_svc.create.assert_not_called()
    pt_cls.return_value.read_by_name.assert_not_called()
    pt_cls.return_value.read_all.assert_not_called()


def test_list_drop_default_payment_term_found(_no_live_payment_term_lookup):
    pt_cls = _no_live_payment_term_lookup
    _configure_due_on_receipt(pt_cls, name="Due on receipt")
    att_svc, blia_svc, bill_svc, read_fn = _service_mocks()
    with _patch_gate(), patch.object(dfs, "_resolve_sub_cost_code_id", return_value=501):
        _run(
            draft_bill_from_attachment(
                attachment_public_id=ATT_UUID,
                attachment_service=att_svc,
                blia_service=blia_svc,
                bill_service=bill_svc,
                read_fn=read_fn,
            )
        )
    assert bill_svc.create.call_args.kwargs["payment_term_public_id"] == DUE_ON_RECEIPT_TERM


def test_list_drop_default_payment_term_case_insensitive(_no_live_payment_term_lookup):
    pt_cls = _no_live_payment_term_lookup
    _configure_due_on_receipt(pt_cls, name="DUE ON RECEIPT")
    att_svc, blia_svc, bill_svc, read_fn = _service_mocks()
    with _patch_gate(), patch.object(dfs, "_resolve_sub_cost_code_id", return_value=None):
        _run(
            draft_bill_from_attachment(
                attachment_public_id=ATT_UUID,
                attachment_service=att_svc,
                blia_service=blia_svc,
                bill_service=bill_svc,
                read_fn=read_fn,
            )
        )
    assert bill_svc.create.call_args.kwargs["payment_term_public_id"] == DUE_ON_RECEIPT_TERM


def test_list_drop_default_payment_term_not_found_still_creates(_no_live_payment_term_lookup):
    pt_cls = _no_live_payment_term_lookup
    pt_cls.return_value.read_by_name.return_value = None
    pt_cls.return_value.read_all.return_value = [
        SimpleNamespace(name="Net 30", public_id="99999999-9999-9999-9999-999999999999"),
    ]
    att_svc, blia_svc, bill_svc, read_fn = _service_mocks()
    with _patch_gate(), patch.object(dfs, "_resolve_sub_cost_code_id", return_value=None):
        out = _run(
            draft_bill_from_attachment(
                attachment_public_id=ATT_UUID,
                attachment_service=att_svc,
                blia_service=blia_svc,
                bill_service=bill_svc,
                read_fn=read_fn,
            )
        )
    assert out["existing"] is False
    assert "payment_term_public_id" not in bill_svc.create.call_args.kwargs


def test_list_drop_default_payment_term_lookup_raises_still_creates(_no_live_payment_term_lookup):
    pt_cls = _no_live_payment_term_lookup
    pt_cls.return_value.read_all.side_effect = RuntimeError("db down")
    att_svc, blia_svc, bill_svc, read_fn = _service_mocks()
    with _patch_gate(), patch.object(dfs, "_resolve_sub_cost_code_id", return_value=None):
        out = _run(
            draft_bill_from_attachment(
                attachment_public_id=ATT_UUID,
                attachment_service=att_svc,
                blia_service=blia_svc,
                bill_service=bill_svc,
                read_fn=read_fn,
            )
        )
    assert out["existing"] is False
    assert "payment_term_public_id" not in bill_svc.create.call_args.kwargs


def test_list_drop_duplicate_retry_keeps_default_payment_term(_no_live_payment_term_lookup):
    pt_cls = _no_live_payment_term_lookup
    _configure_due_on_receipt(pt_cls)
    att_svc, blia_svc, bill_svc, read_fn = _service_mocks(
        create_bill=SimpleNamespace(public_id=EXISTING_BILL)
    )
    bill_svc.create.side_effect = [
        DuplicateBillError("dup", existing_bill_public_id=INACCESSIBLE_DUP),
        SimpleNamespace(public_id=EXISTING_BILL),
    ]

    def _deny(_pid):
        raise EntityNotAccessibleError("bill", 99)

    bill_svc.read_by_public_id.side_effect = _deny
    with _patch_gate(), patch.object(dfs, "_resolve_sub_cost_code_id", return_value=None):
        _run(
            draft_bill_from_attachment(
                attachment_public_id=ATT_UUID,
                attachment_service=att_svc,
                blia_service=blia_svc,
                bill_service=bill_svc,
                read_fn=read_fn,
            )
        )
    assert bill_svc.create.call_count == 2
    for call in bill_svc.create.call_args_list:
        assert call.kwargs["payment_term_public_id"] == DUE_ON_RECEIPT_TERM


def test_same_pdf_inaccessible_creates():
    att_svc, blia_svc, bill_svc, read_fn = _service_mocks(hash_bill=None)
    blia_svc.read_draft_bill_public_id_by_attachment_hash.return_value = None
    with _patch_gate(), patch.object(
        dfs, "_resolve_sub_cost_code_id", return_value=None
    ):
        out = _run(
            draft_bill_from_attachment(
                attachment_public_id=ATT_UUID,
                attachment_service=att_svc,
                blia_service=blia_svc,
                bill_service=bill_svc,
                read_fn=read_fn,
            )
        )
    assert out["existing"] is False
    read_fn.assert_awaited_once()
    bill_svc.create.assert_called_once()


def test_create_driven_duplicate_accessible_returns_existing():
    dup = SimpleNamespace(public_id=EXISTING_BILL, id=42)
    att_svc, blia_svc, bill_svc, read_fn = _service_mocks()
    bill_svc.create.side_effect = DuplicateBillError(
        "dup",
        existing_bill_public_id=EXISTING_BILL,
    )
    bill_svc.read_by_public_id.return_value = dup
    with _patch_gate(), patch.object(dfs, "_resolve_sub_cost_code_id", return_value=501):
        out = _run(
            draft_bill_from_attachment(
                attachment_public_id=ATT_UUID,
                attachment_service=att_svc,
                blia_service=blia_svc,
                bill_service=bill_svc,
                read_fn=read_fn,
            )
        )
    assert out["existing_reason"] == "same_vendor_number_date"
    bill_svc.create.assert_called_once()


def test_create_driven_duplicate_inaccessible_retries_without_number():
    att_svc, blia_svc, bill_svc, read_fn = _service_mocks(
        create_bill=SimpleNamespace(public_id=EXISTING_BILL)
    )
    bill_svc.create.side_effect = [
        DuplicateBillError("dup", existing_bill_public_id=INACCESSIBLE_DUP),
        SimpleNamespace(public_id=EXISTING_BILL),
    ]

    def _deny(_pid):
        raise EntityNotAccessibleError("bill", 99)

    bill_svc.read_by_public_id.side_effect = _deny
    with _patch_gate(), patch.object(dfs, "_resolve_sub_cost_code_id", return_value=None):
        out = _run(
            draft_bill_from_attachment(
                attachment_public_id=ATT_UUID,
                attachment_service=att_svc,
                blia_service=blia_svc,
                bill_service=bill_svc,
                read_fn=read_fn,
            )
        )
    assert out["existing"] is False
    assert out["bill_public_id"] == EXISTING_BILL.lower()
    assert bill_svc.create.call_count == 2
    assert bill_svc.create.call_args_list[1].kwargs["bill_number"] is None
    leaked = INACCESSIBLE_DUP.lower()
    assert leaked not in str(out).lower()


def test_create_race_duplicate_returns_existing():
    att_svc, blia_svc, bill_svc, read_fn = _service_mocks()
    bill_svc.create.side_effect = DuplicateBillError(
        "dup",
        existing_bill_public_id=EXISTING_BILL,
    )
    bill_svc.read_by_public_id.return_value = SimpleNamespace(
        public_id=EXISTING_BILL, id=42
    )
    with _patch_gate(), patch.object(dfs, "_resolve_sub_cost_code_id", return_value=501):
        out = _run(
            draft_bill_from_attachment(
                attachment_public_id=ATT_UUID,
                attachment_service=att_svc,
                blia_service=blia_svc,
                bill_service=bill_svc,
                read_fn=read_fn,
            )
        )
    assert out["existing"] is True
    assert out["existing_reason"] == "same_vendor_number_date"
    assert out["bill_public_id"] == EXISTING_BILL.lower()


def test_read_ok_create_args_exact():
    att_svc, blia_svc, bill_svc, read_fn = _service_mocks()
    with _patch_gate(), patch.object(
        dfs, "_resolve_sub_cost_code_id", return_value=501
    ), patch.object(dfs, "business_today_iso", return_value="2026-10-19"), patch.object(
        dfs, "_resolve_entered_by_for_memo", new=AsyncMock(return_value="Chris Z")
    ):
        _run(
            draft_bill_from_attachment(
                attachment_public_id=ATT_UUID,
                user_id=17,
                attachment_service=att_svc,
                blia_service=blia_svc,
                bill_service=bill_svc,
                read_fn=read_fn,
            )
        )
    kwargs = bill_svc.create.call_args.kwargs
    assert kwargs["vendor_public_id"] == VENDOR_UUID
    assert kwargs["bill_number"] == "INV-9"
    assert kwargs["bill_date"] == "2026-05-01"
    assert kwargs["due_date"] == "2026-05-01"
    assert kwargs["total_amount"] == Decimal("250.00")
    assert kwargs["line_description"] == "Materials"
    assert kwargs["line_sub_cost_code_id"] == 501
    assert kwargs["line_project_public_id"] == "dddddddd-dddd-dddd-dddd-dddddddddddd"
    assert kwargs["line_coding_prefill_state"] == "prefilled"
    assert kwargs["submit_for_review"] is False
    assert kwargs["intake_source"] == "list_drop"
    assert kwargs["attachment_public_id"] == ATT_UUID
    assert kwargs["memo"] == "Chris Z via PDF drop"
    read_fn.assert_awaited_once()
    assert read_fn.await_args.kwargs["attachment_public_id"] == ATT_UUID
    assert read_fn.await_args.kwargs["intake_source"] == "list_drop"
    assert read_fn.await_args.kwargs.get("pre_gated") is not None


@pytest.mark.parametrize("status", ["unreadable", "timeout", "ai_unavailable", "disabled"])
def test_unreadable_read_still_creates_empty_draft(status):
    att_svc, blia_svc, bill_svc, read_fn = _service_mocks()
    read_fn.return_value = {"status": status, "fields": {}, "coding": None}
    with _patch_gate(), patch.object(dfs, "business_today_iso", return_value="2026-10-19"):
        out = _run(
            draft_bill_from_attachment(
                attachment_public_id=ATT_UUID,
                attachment_service=att_svc,
                blia_service=blia_svc,
                bill_service=bill_svc,
                read_fn=read_fn,
            )
        )
    assert out["read_status"] == status
    kwargs = bill_svc.create.call_args.kwargs
    assert kwargs["bill_date"] == "2026-10-19"
    assert kwargs["vendor_public_id"] is None
    assert kwargs["bill_number"] is None
    assert "line_coding_prefill_state" not in kwargs or kwargs.get(
        "line_coding_prefill_state"
    ) is None


def test_prediction_without_coding_line_state_none():
    att_svc, blia_svc, bill_svc, read_fn = _service_mocks()
    read_fn.return_value = _read_ok()
    read_fn.return_value["coding"] = None
    with _patch_gate(), patch.object(dfs, "_resolve_sub_cost_code_id", return_value=None):
        _run(
            draft_bill_from_attachment(
                attachment_public_id=ATT_UUID,
                attachment_service=att_svc,
                blia_service=blia_svc,
                bill_service=bill_svc,
                read_fn=read_fn,
            )
        )
    kwargs = bill_svc.create.call_args.kwargs
    assert kwargs.get("line_coding_prefill_state") is None
    assert "line_project_public_id" not in kwargs
    assert "line_sub_cost_code_id" not in kwargs


def test_list_drop_coding_scc_unresolved_omits_project_and_prefill():
    """U-732a: project + prefilled only when SubCostCode resolves (matches web list drop)."""
    att_svc, blia_svc, bill_svc, read_fn = _service_mocks()
    with _patch_gate(), patch.object(dfs, "_resolve_sub_cost_code_id", return_value=None):
        _run(
            draft_bill_from_attachment(
                attachment_public_id=ATT_UUID,
                attachment_service=att_svc,
                blia_service=blia_svc,
                bill_service=bill_svc,
                read_fn=read_fn,
            )
        )
    kwargs = bill_svc.create.call_args.kwargs
    assert "line_sub_cost_code_id" not in kwargs
    assert "line_project_public_id" not in kwargs
    assert "line_coding_prefill_state" not in kwargs


def test_linked_attachment_raises_not_found():
    att_svc, blia_svc, bill_svc, read_fn = _service_mocks()
    with _patch_gate(unlinked=False):
        with pytest.raises(AttachmentNotReadable):
            _run(
                draft_bill_from_attachment(
                    attachment_public_id=ATT_UUID,
                    attachment_service=att_svc,
                    blia_service=blia_svc,
                    bill_service=bill_svc,
                    read_fn=read_fn,
                )
            )


def test_route_rbac_denied(client_no_create):
    resp = client_no_create.post(
        "/api/v1/bill/draft-from-attachment",
        json={"attachment_public_id": ATT_UUID},
    )
    assert resp.status_code == 403


def test_route_404_when_attachment_unreadable(client_can_create):
    with patch(
        "entities.bill.api.draft_from_attachment_router.draft_bill_from_attachment",
        new=AsyncMock(side_effect=AttachmentNotReadable("not_found")),
    ):
        resp = client_can_create.post(
            "/api/v1/bill/draft-from-attachment",
            json={"attachment_public_id": ATT_UUID},
        )
    assert resp.status_code == 404


def test_per_hash_lock_second_returns_existing():
    att1 = _attachment(id=99, file_hash="same-hash")
    att2 = _attachment(id=100, file_hash="same-hash", public_id=ATT2_UUID)
    att_svc = MagicMock()
    att_svc.read_by_public_id.side_effect = [att1, att2, att1, att2]
    blia_svc = MagicMock()
    bill_svc = MagicMock()
    bill_svc.create.return_value = SimpleNamespace(public_id=EXISTING_BILL)

    def _hash_lookup(**_kw):
        if bill_svc.create.called:
            return EXISTING_BILL
        return None

    blia_svc.read_draft_bill_public_id_by_attachment_hash.side_effect = _hash_lookup

    async def _read_fn(**_kw):
        await asyncio.sleep(0.05)
        return _read_ok()

    async def _concurrent():
        t1 = asyncio.create_task(
            draft_bill_from_attachment(
                attachment_public_id=ATT_UUID,
                attachment_service=att_svc,
                blia_service=blia_svc,
                bill_service=bill_svc,
                read_fn=_read_fn,
            )
        )
        t2 = asyncio.create_task(
            draft_bill_from_attachment(
                attachment_public_id=ATT2_UUID,
                attachment_service=att_svc,
                blia_service=blia_svc,
                bill_service=bill_svc,
                read_fn=_read_fn,
            )
        )
        return await asyncio.gather(t1, t2)

    with _patch_gate(), patch.object(dfs, "_resolve_sub_cost_code_id", return_value=None):
        r1, r2 = _run(_concurrent())
    assert bill_svc.create.call_count == 1
    assert any(r.get("existing_reason") == "same_pdf" for r in (r1, r2))


def test_unowned_attachment_never_hash_lookup():
    att_svc, blia_svc, bill_svc, read_fn = _service_mocks(hash_bill=EXISTING_BILL)
    with _patch_gate(owned=False), patch.object(dfs, "_resolve_sub_cost_code_id", return_value=None):
        with pytest.raises(AttachmentNotReadable):
            _run(
                draft_bill_from_attachment(
                    attachment_public_id=ATT_UUID,
                    attachment_service=att_svc,
                    blia_service=blia_svc,
                    bill_service=bill_svc,
                    read_fn=read_fn,
                )
            )
    blia_svc.read_draft_bill_public_id_by_attachment_hash.assert_not_called()
    read_fn.assert_not_called()


def test_owned_linked_no_accessible_same_pdf_raises_not_found():
    att_svc, blia_svc, bill_svc, read_fn = _service_mocks(hash_bill=None)
    with _patch_gate(unlinked=False), patch.object(dfs, "_resolve_sub_cost_code_id", return_value=None):
        with pytest.raises(AttachmentNotReadable):
            _run(
                draft_bill_from_attachment(
                    attachment_public_id=ATT_UUID,
                    attachment_service=att_svc,
                    blia_service=blia_svc,
                    bill_service=bill_svc,
                    read_fn=read_fn,
                )
            )
    read_fn.assert_not_called()


def test_undated_read_accessible_duplicate_retries_without_number():
    att_svc, blia_svc, bill_svc, read_fn = _service_mocks()
    payload = _read_ok()
    payload["fields"]["bill_date"] = {"value": None, "confidence": None}
    read_fn.return_value = payload
    dup = SimpleNamespace(public_id=EXISTING_BILL, id=42)
    bill_svc.create.side_effect = [
        DuplicateBillError("dup", existing_bill_public_id=EXISTING_BILL),
        SimpleNamespace(public_id=EXISTING_BILL),
    ]
    bill_svc.read_by_public_id.return_value = dup
    with _patch_gate(), patch.object(
        dfs, "business_today_iso", return_value="2026-10-19"
    ), patch.object(dfs, "_resolve_sub_cost_code_id", return_value=None):
        out = _run(
            draft_bill_from_attachment(
                attachment_public_id=ATT_UUID,
                attachment_service=att_svc,
                blia_service=blia_svc,
                bill_service=bill_svc,
                read_fn=read_fn,
            )
        )
    assert out["existing"] is False
    assert bill_svc.create.call_count == 2
    assert bill_svc.create.call_args_list[1].kwargs["bill_number"] is None
    bill_svc.read_by_public_id.assert_not_called()


def test_draft_path_link_state_gate_plus_one_recheck_under_lock():
    """Gate (ownership, before the lock) + one re-check under the lock — no more."""
    calls: list[str] = []
    state = AttachmentLinkState(is_linked=False, is_taxpayer=False, is_owned=True)

    att_svc, blia_svc, bill_svc, read_fn = _service_mocks()
    with patch.object(
        ras, "read_attachment_link_state", side_effect=lambda _aid: calls.append("gate") or state
    ), patch.object(
        dfs, "read_attachment_link_state", side_effect=lambda _aid: calls.append("lock") or state
    ), patch.object(dfs, "_resolve_sub_cost_code_id", return_value=None):
        _run(
            draft_bill_from_attachment(
                attachment_public_id=ATT_UUID,
                attachment_service=att_svc,
                blia_service=blia_svc,
                bill_service=bill_svc,
                read_fn=read_fn,
            )
        )
    assert calls == ["gate", "lock"]
    assert read_fn.await_args.kwargs["pre_gated"][1] is False


def test_attachment_linked_between_gate_and_lock_is_refused():
    att_svc, blia_svc, bill_svc, read_fn = _service_mocks(hash_bill=None)
    with patch.object(
        ras, "read_attachment_link_state",
        return_value=AttachmentLinkState(is_linked=False, is_taxpayer=False, is_owned=True),
    ), patch.object(
        dfs, "read_attachment_link_state",
        return_value=AttachmentLinkState(is_linked=True, is_taxpayer=False, is_owned=True),
    ):
        with pytest.raises(AttachmentNotReadable):
            _run(
                draft_bill_from_attachment(
                    attachment_public_id=ATT_UUID,
                    attachment_service=att_svc,
                    blia_service=blia_svc,
                    bill_service=bill_svc,
                    read_fn=read_fn,
                )
            )
    read_fn.assert_not_called()
    bill_svc.create.assert_not_called()


def test_unowned_attachment_never_takes_the_hash_lock():
    att_svc, blia_svc, bill_svc, read_fn = _service_mocks(hash_bill=EXISTING_BILL)
    taken: list[str] = []
    real_lock_for = dfs._lock_for_content_hash
    with _patch_gate(owned=False), patch.object(
        dfs, "_lock_for_content_hash", side_effect=lambda h: taken.append(h) or real_lock_for(h)
    ):
        with pytest.raises(AttachmentNotReadable):
            _run(
                draft_bill_from_attachment(
                    attachment_public_id=ATT_UUID,
                    attachment_service=att_svc,
                    blia_service=blia_svc,
                    bill_service=bill_svc,
                    read_fn=read_fn,
                )
            )
    assert taken == []
    blia_svc.read_draft_bill_public_id_by_attachment_hash.assert_not_called()


def test_draft_with_the_real_read_function_forwards_pre_gated():
    """Default read_fn = the production timeout wrapper; the read must NOT re-gate."""
    att_svc, blia_svc, bill_svc, _mock_read = _service_mocks(hash_bill=None)
    calls: list[str] = []
    state = AttachmentLinkState(is_linked=False, is_taxpayer=False, is_owned=True)
    with patch.object(
        ras, "read_attachment_link_state", side_effect=lambda _aid: calls.append("ras") or state
    ), patch.object(
        dfs, "read_attachment_link_state", side_effect=lambda _aid: calls.append("dfs") or state
    ), patch.object(ras.config, "Settings", return_value=MagicMock(
        bill_drop_prefill_enabled=True, bill_drop_prefill_timeout_seconds=25
    )), patch.object(
        ras.claude_budget, "is_claude_day_stopped_async", new=AsyncMock(return_value=False)
    ), patch.object(ras, "AttachmentExtractionService") as ES, patch(
        "entities.bill.business.read_attachment_service.run_bill_header_extraction",
        new=AsyncMock(return_value=MagicMock(accepted=False, needs_human=True, result=None)),
    ), patch.object(dfs, "_resolve_sub_cost_code_id", return_value=None):
        ES.return_value.extract_for_bill_drop_prefill.return_value = {
            "status": "completed",
            "masked_text": "x",
        }
        out = _run(
            draft_bill_from_attachment(
                attachment_public_id=ATT_UUID,
                attachment_service=att_svc,
                blia_service=blia_svc,
                bill_service=bill_svc,
            )
        )
    assert out["existing"] is False
    assert out["read_status"] != "disabled"  # the real read pipeline ran
    # Gate (ras) + re-check under the lock (dfs). A dropped pre_gated would add a 3rd.
    assert calls == ["ras", "dfs"]
    bill_svc.create.assert_called_once()


def test_draft_recheck_refuses_not_owned_under_lock():
    att_svc, blia_svc, bill_svc, read_fn = _service_mocks(hash_bill=None)
    with patch.object(
        ras, "read_attachment_link_state",
        return_value=AttachmentLinkState(is_linked=False, is_taxpayer=False, is_owned=True),
    ), patch.object(
        dfs, "read_attachment_link_state",
        return_value=AttachmentLinkState(is_linked=False, is_taxpayer=False, is_owned=False),
    ):
        with pytest.raises(AttachmentNotReadable):
            _run(
                draft_bill_from_attachment(
                    attachment_public_id=ATT_UUID,
                    attachment_service=att_svc,
                    blia_service=blia_svc,
                    bill_service=bill_svc,
                    read_fn=read_fn,
                )
            )
    read_fn.assert_not_called()


def test_read_attachment_endpoint_single_link_state_call():
    att = _attachment()
    link_calls: list[int] = []

    def _track(aid, **_kw):
        link_calls.append(aid)
        return AttachmentLinkState(is_linked=False, is_taxpayer=False, is_owned=True)

    settings = MagicMock(bill_drop_prefill_enabled=True, bill_drop_prefill_timeout_seconds=25)
    with patch.object(
        ras.claude_budget, "is_claude_day_stopped_async", new=AsyncMock(return_value=False)
    ), patch.object(ras, "AttachmentService") as AS, patch.object(
        ras, "AttachmentExtractionService"
    ) as ES, patch.object(
        ras, "read_attachment_link_state", side_effect=_track
    ), patch(
        "entities.bill.business.read_attachment_service.run_bill_header_extraction",
        new=AsyncMock(return_value=MagicMock(accepted=False, needs_human=True, result=None)),
    ):
        AS.return_value.read_by_public_id.return_value = att
        ES.return_value.extract_for_bill_drop_prefill.return_value = {
            "status": "completed",
            "masked_text": "x",
        }
        _run(ras.run_bill_read_attachment(attachment_public_id=ATT_UUID, settings=settings))
    assert len(link_calls) == 1


def test_read_attachment_endpoint_refuses_unowned():
    with patch.object(ras, "AttachmentService") as AS, patch.object(
        ras, "read_attachment_link_state",
        # Unlinked but not owned: only the ownership check can refuse this (the sproc never
        # emits it — IsLinked folds non-ownership in — but the repo's fail-closed fallback can).
        return_value=AttachmentLinkState(is_linked=False, is_taxpayer=False, is_owned=False),
    ), patch.object(ras.claude_budget, "is_claude_day_stopped_async", new=AsyncMock(return_value=False)):
        AS.return_value.read_by_public_id.return_value = _attachment()
        with pytest.raises(AttachmentNotReadable):
            _run(ras.run_bill_read_attachment(
                attachment_public_id=ATT_UUID,
                settings=MagicMock(bill_drop_prefill_enabled=True, bill_drop_prefill_timeout_seconds=25),
            ))


def test_link_state_helper_fails_closed_on_error():
    repo = MagicMock()
    repo.read_link_state_with_ownership.side_effect = RuntimeError("db down")
    state = ras.read_attachment_link_state(7, repo=repo)
    assert state == AttachmentLinkState(is_linked=True, is_taxpayer=True, is_owned=False)


def test_repo_link_state_missing_row_and_null_is_owned_fail_closed():
    from entities.attachment.persistence import repo as att_repo_mod

    class _Cur:
        def __init__(self, row):
            self._row = row

        def fetchone(self):
            return self._row

        def close(self):
            pass

    class _Conn:
        def __init__(self, row):
            self._row = row

        def cursor(self):
            return _Cur(self._row)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    for row, expected in (
        (None, AttachmentLinkState(True, True, False)),
        (SimpleNamespace(IsLinked=0, IsTaxpayer=0, IsOwned=None), AttachmentLinkState(False, False, False)),
        (SimpleNamespace(IsLinked=0, IsTaxpayer=0, IsOwned=1), AttachmentLinkState(False, False, True)),
    ):
        with patch.object(att_repo_mod, "get_connection", return_value=_Conn(row)), patch.object(
            att_repo_mod, "call_procedure"
        ):
            got = att_repo_mod.AttachmentRepository().read_link_state_with_ownership(7)
        assert got == expected


def test_duplicate_bill_error_default_public_id_is_none():
    err = DuplicateBillError("dup")
    assert err.existing_bill_public_id is None
    import copy

    copy.copy(err)
    import pickle

    pickle.loads(pickle.dumps(err))


def test_linked_attachment_after_draft_exists_returns_same_pdf_not_404():
    att_svc, blia_svc, bill_svc, read_fn = _service_mocks(hash_bill=EXISTING_BILL)
    with _patch_gate(unlinked=False):
        out = _run(
            draft_bill_from_attachment(
                attachment_public_id=ATT_UUID,
                attachment_service=att_svc,
                blia_service=blia_svc,
                bill_service=bill_svc,
                read_fn=read_fn,
            )
        )
    assert out["existing_reason"] == "same_pdf"
    read_fn.assert_not_called()
    bill_svc.create.assert_not_called()


def test_bill_public_id_format_all_paths():
    att_svc, blia_svc, bill_svc, read_fn = _service_mocks(hash_bill=EXISTING_BILL.upper())
    with _patch_gate(), patch.object(dfs, "_resolve_sub_cost_code_id", return_value=None):
        same_pdf = _run(
            draft_bill_from_attachment(
                attachment_public_id=ATT_UUID,
                attachment_service=att_svc,
                blia_service=blia_svc,
                bill_service=bill_svc,
                read_fn=read_fn,
            )
        )
    assert same_pdf["bill_public_id"] == EXISTING_BILL.lower()

    att_svc2, blia_svc2, bill_svc2, read_fn2 = _service_mocks()
    bill_svc2.create.side_effect = DuplicateBillError(
        "dup", existing_bill_public_id=EXISTING_BILL.upper()
    )
    bill_svc2.read_by_public_id.return_value = SimpleNamespace(
        public_id=EXISTING_BILL.upper(), id=1
    )
    with _patch_gate(), patch.object(dfs, "_resolve_sub_cost_code_id", return_value=501):
        triple = _run(
            draft_bill_from_attachment(
                attachment_public_id=ATT_UUID,
                attachment_service=att_svc2,
                blia_service=blia_svc2,
                bill_service=bill_svc2,
                read_fn=read_fn2,
            )
        )
    assert triple["bill_public_id"] == EXISTING_BILL.lower()

    att_svc3, blia_svc3, bill_svc3, read_fn3 = _service_mocks(
        create_bill=SimpleNamespace(public_id=EXISTING_BILL.upper())
    )
    with _patch_gate(), patch.object(dfs, "_resolve_sub_cost_code_id", return_value=501):
        created = _run(
            draft_bill_from_attachment(
                attachment_public_id=ATT_UUID,
                attachment_service=att_svc3,
                blia_service=blia_svc3,
                bill_service=bill_svc3,
                read_fn=read_fn3,
            )
        )
    assert created["bill_public_id"] == EXISTING_BILL.lower()


def test_bill_service_create_raises_duplicate_bill_error():
    repo = MagicMock()
    dup = SimpleNamespace(
        public_id=EXISTING_BILL, id=99, is_draft=True, status="draft"
    )
    repo.read_by_bill_number_and_vendor_id.return_value = dup
    svc = BillService(repo=repo)
    svc.attachment_service = MagicMock()
    svc.attachment_service.read_by_public_id.return_value = SimpleNamespace(
        content_type="application/pdf", public_id=ATT_UUID, id=1
    )
    with patch("entities.bill.business.service.VendorService") as mock_vendor, patch(
        "entities.bill.business.service.can_access_bill",
        return_value=True,
    ):
        mock_vendor.return_value.read_by_public_id.return_value = SimpleNamespace(id=7)
        with pytest.raises(DuplicateBillError) as exc_info:
            svc.create(
                vendor_public_id=VENDOR_UUID,
                bill_date="2026-05-01",
                due_date="2026-05-01",
                bill_number="INV-9",
                attachment_public_id=ATT_UUID,
            )
    assert exc_info.value.existing_bill_public_id == EXISTING_BILL


def test_bill_update_schema_accepts_vendorless_numberless_draft():
    body = BillUpdate(
        row_version="abc=",
        bill_date="2026-10-19",
        due_date="2026-10-19",
        vendor_public_id=None,
        bill_number=None,
    )
    assert body.vendor_public_id is None
    assert body.bill_number is None


def test_bill_create_intake_source_validation():
    BillCreate(
        vendor_public_id="v",
        bill_date="2026-01-01",
        due_date="2026-01-01",
        bill_number="1",
        attachment_public_id="a",
        intake_source="pdf_drop",
    )
    with pytest.raises(ValidationError):
        BillCreate(
            vendor_public_id="v",
            bill_date="2026-01-01",
            due_date="2026-01-01",
            bill_number="1",
            attachment_public_id="a",
            intake_source="list_drop",
        )


def test_draft_update_blank_number_stored_as_none():
    svc = BillService(repo=MagicMock())
    svc.repo.update_by_id.return_value = Bill(
        id=1,
        public_id="p",
        row_version="rv",
        created_datetime=None,
        modified_datetime=None,
        vendor_id=None,
        payment_term_id=None,
        bill_date="2026-01-01",
        due_date="2026-01-01",
        bill_number=None,
        total_amount=None,
        memo=None,
        is_draft=True,
    )
    existing = Bill(
        id=1,
        public_id="p",
        row_version="rv",
        created_datetime=None,
        modified_datetime=None,
        vendor_id=None,
        payment_term_id=None,
        bill_date="2026-01-01",
        due_date="2026-01-01",
        bill_number="OLD",
        total_amount=None,
        memo=None,
        is_draft=True,
    )
    with patch.object(svc, "read_by_public_id", return_value=existing):
        svc.update_by_public_id(
            "p",
            row_version="rv",
            bill_number="   ",
            bill_date="2026-01-01",
            due_date="2026-01-01",
        )
    sent = svc.repo.update_by_id.call_args[0][0]
    assert sent.bill_number is None


def test_non_completed_update_blank_number_stored_as_none():
    svc = BillService(repo=MagicMock())
    svc.repo.update_by_id.return_value = Bill(
        id=1,
        public_id="p",
        row_version="rv",
        created_datetime=None,
        modified_datetime=None,
        vendor_id=5,
        payment_term_id=None,
        bill_date="2026-01-01",
        due_date="2026-01-01",
        bill_number=None,
        total_amount=None,
        memo=None,
        is_draft=False,
        status="submitted",
    )
    existing = Bill(
        id=1,
        public_id="p",
        row_version="rv",
        created_datetime=None,
        modified_datetime=None,
        vendor_id=5,
        payment_term_id=None,
        bill_date="2026-01-01",
        due_date="2026-01-01",
        bill_number="N-1",
        total_amount=None,
        memo=None,
        is_draft=False,
        status="submitted",
    )
    with patch.object(svc, "read_by_public_id", return_value=existing):
        svc.update_by_public_id(
            "p",
            row_version="rv",
            bill_number="  ",
            bill_date="2026-01-01",
            due_date="2026-01-01",
        )
    sent = svc.repo.update_by_id.call_args[0][0]
    assert sent.bill_number is None


def test_completed_bill_update_via_pipeline_preserves_bill_number():
    svc = BillService(repo=MagicMock())
    svc.repo.update_by_id.return_value = Bill(
        id=1,
        public_id="p",
        row_version="rv",
        created_datetime=None,
        modified_datetime=None,
        vendor_id=5,
        payment_term_id=None,
        bill_date="2026-01-01",
        due_date="2026-01-01",
        bill_number="",
        total_amount=None,
        memo=None,
        is_draft=False,
        status="completed",
    )
    existing = Bill(
        id=1,
        public_id="p",
        row_version="rv",
        created_datetime=None,
        modified_datetime=None,
        vendor_id=5,
        payment_term_id=None,
        bill_date="2026-01-01",
        due_date="2026-01-01",
        bill_number="OLD",
        total_amount=None,
        memo=None,
        is_draft=False,
        status="completed",
    )
    with patch.object(svc, "read_by_public_id", return_value=existing):
        svc.update_by_public_id(
            "p",
            row_version="rv",
            bill_number="",
            bill_date="2026-01-01",
            due_date="2026-01-01",
            _via_completion_pipeline=True,
        )
    sent = svc.repo.update_by_id.call_args[0][0]
    assert sent.bill_number == ""

    existing.bill_number = "OLD"
    with patch.object(svc, "read_by_public_id", return_value=existing):
        svc.update_by_public_id(
            "p",
            row_version="rv",
            bill_number="INV-1 ",
            bill_date="2026-01-01",
            due_date="2026-01-01",
            _via_completion_pipeline=True,
        )
    sent2 = svc.repo.update_by_id.call_args[0][0]
    assert sent2.bill_number == "INV-1 "


@pytest.fixture(autouse=True)
def _no_live_user_lookup():
    """U-728: the memo's entered-by lookup must never reach the real DB in this module.
    Tests that exercise the lookup patch these again inside the test (inner patch wins)."""
    blank_user = SimpleNamespace(firstname=None, lastname=None)
    with patch("entities.user.business.service.UserService") as user_cls, patch(
        "entities.auth.business.service.AuthService"
    ) as auth_cls:
        user_cls.return_value.read_by_id.return_value = blank_user
        auth_cls.return_value.read_by_user_id.return_value = None
        yield


@pytest.fixture(autouse=True)
def _no_live_payment_term_lookup():
    """U-731a: default payment-term resolution must never reach the real DB in this module."""
    with patch("entities.payment_term.business.service.PaymentTermService") as pt_cls:
        pt_cls.return_value.read_by_name.return_value = None
        pt_cls.return_value.read_all.return_value = []
        yield pt_cls


def _configure_due_on_receipt(pt_cls, *, name: str = "Due on receipt"):
    pt_cls.return_value.read_by_name.return_value = None
    pt_cls.return_value.read_all.return_value = [
        SimpleNamespace(name=name, public_id=DUE_ON_RECEIPT_TERM),
    ]


def test_list_drop_memo_includes_description_and_reference():
    att_svc, blia_svc, bill_svc, read_fn = _service_mocks()
    read_fn.return_value = _read_ok(
        long_description={"value": "Long desc", "confidence": "0.9"},
        reference={"value": "PO 99", "confidence": "0.9"},
    )
    with _patch_gate(), patch.object(
        dfs, "_resolve_sub_cost_code_id", return_value=None
    ), patch.object(
        dfs, "_resolve_entered_by_for_memo", new=AsyncMock(return_value="Pat")
    ):
        _run(
            draft_bill_from_attachment(
                attachment_public_id=ATT_UUID,
                user_id=5,
                attachment_service=att_svc,
                blia_service=blia_svc,
                bill_service=bill_svc,
                read_fn=read_fn,
            )
        )
    memo = bill_svc.create.call_args.kwargs["memo"]
    assert memo == "Pat via PDF drop | Long desc | Ref: PO 99"


def test_list_drop_memo_omits_doc_when_read_number_matches_bill_number():
    att_svc, blia_svc, bill_svc, read_fn = _service_mocks()
    with _patch_gate(), patch.object(
        dfs, "_resolve_sub_cost_code_id", return_value=None
    ), patch.object(
        dfs, "_resolve_entered_by_for_memo", new=AsyncMock(return_value="U")
    ):
        _run(
            draft_bill_from_attachment(
                attachment_public_id=ATT_UUID,
                attachment_service=att_svc,
                blia_service=blia_svc,
                bill_service=bill_svc,
                read_fn=read_fn,
            )
        )
    memo = bill_svc.create.call_args.kwargs["memo"]
    assert "DOC#" not in memo
    assert memo == "U via PDF drop"


def test_list_drop_memo_retry_includes_doc_when_bill_number_cleared():
    att_svc, blia_svc, bill_svc, read_fn = _service_mocks(
        create_bill=SimpleNamespace(public_id=EXISTING_BILL)
    )
    bill_svc.create.side_effect = [
        DuplicateBillError("dup", existing_bill_public_id=INACCESSIBLE_DUP),
        SimpleNamespace(public_id=EXISTING_BILL),
    ]

    def _deny(_pid):
        raise EntityNotAccessibleError("bill", 99)

    bill_svc.read_by_public_id.side_effect = _deny
    with _patch_gate(), patch.object(
        dfs, "_resolve_sub_cost_code_id", return_value=None
    ), patch.object(
        dfs, "_resolve_entered_by_for_memo", new=AsyncMock(return_value="U")
    ):
        _run(
            draft_bill_from_attachment(
                attachment_public_id=ATT_UUID,
                attachment_service=att_svc,
                blia_service=blia_svc,
                bill_service=bill_svc,
                read_fn=read_fn,
            )
        )
    first_memo = bill_svc.create.call_args_list[0].kwargs["memo"]
    retry_memo = bill_svc.create.call_args_list[1].kwargs["memo"]
    assert "DOC#" not in first_memo
    assert retry_memo == "U via PDF drop | DOC#: INV-9"


def test_list_drop_memo_user_lookup_failure_uses_pdf_drop_head():
    att_svc, blia_svc, bill_svc, read_fn = _service_mocks()
    with _patch_gate(), patch.object(
        dfs, "_resolve_sub_cost_code_id", return_value=None
    ), patch(
        "entities.user.business.service.UserService",
    ) as user_svc_cls:
        user_svc_cls.return_value.read_by_id.side_effect = RuntimeError("db down")
        _run(
            draft_bill_from_attachment(
                attachment_public_id=ATT_UUID,
                user_id=17,
                attachment_service=att_svc,
                blia_service=blia_svc,
                bill_service=bill_svc,
                read_fn=read_fn,
            )
        )
    assert bill_svc.create.call_args.kwargs["memo"] == "PDF drop"


def test_list_drop_memo_no_user_id_uses_pdf_drop_head():
    att_svc, blia_svc, bill_svc, read_fn = _service_mocks()
    with _patch_gate(), patch.object(
        dfs, "_resolve_sub_cost_code_id", return_value=None
    ):
        _run(
            draft_bill_from_attachment(
                attachment_public_id=ATT_UUID,
                user_id=None,
                attachment_service=att_svc,
                blia_service=blia_svc,
                bill_service=bill_svc,
                read_fn=read_fn,
            )
        )
    assert bill_svc.create.call_args.kwargs["memo"] == "PDF drop"


def test_list_drop_record_mode_surfaces_possible_duplicates(monkeypatch):
    """U-723: list-drop uses record-path duplicate gate after create."""
    from decimal import Decimal

    from entities.bill.business.duplicate_check import DuplicateCheckResult, DuplicateMatch

    monkeypatch.setenv("BILL_DUPLICATE_WARN", "record")
    att_svc, blia_svc, bill_svc, read_fn = _service_mocks()
    bill_svc.create.return_value = SimpleNamespace(public_id=EXISTING_BILL, id=77)
    bill_svc.read_by_public_id.return_value = SimpleNamespace(
        id=77,
        public_id=EXISTING_BILL,
        vendor_id=1,
        bill_date="2026-05-01",
        bill_number="INV-9",
        total_amount=Decimal("250.00"),
    )
    match_row = SimpleNamespace(
        PublicId="dup-match",
        BillNumber="INV-9",
        BillDate="2026-05-01",
        TotalAmount=Decimal("250.00"),
        Status="draft",
        VendorName="Acme",
        ProjectNamesCsv=None,
        CreatedDatetime=None,
        ReasonsCsv="vendor_date_amount",
        CallerCanAccess=True,
    )
    repo = MagicMock()
    repo.read_possible_duplicate_bills.return_value = [match_row]
    repo.read_by_public_id.return_value = SimpleNamespace(id=99)

    with _patch_gate(), patch.object(
        dfs, "_resolve_sub_cost_code_id", return_value=501
    ), patch(
        "entities.bill.business.duplicate_check.BillRepository", return_value=repo
    ), patch(
        "entities.bill.business.duplicate_gate.BillLineItemService"
    ) as bli:
        bli.return_value.read_by_bill_id.return_value = []
        out = _run(
            draft_bill_from_attachment(
                attachment_public_id=ATT_UUID,
                attachment_service=att_svc,
                blia_service=blia_svc,
                bill_service=bill_svc,
                read_fn=read_fn,
                user_id=17,
            )
        )
    assert out.get("possible_duplicates")
    assert out["possible_duplicates"][0]["public_id"] == "dup-match"


def test_list_drop_memo_skipped_when_read_failed():
    """Same gate as the web: no memo unless the read status is ok/partial."""
    att_svc, blia_svc, bill_svc, read_fn = _service_mocks()
    read_fn.return_value = {**_read_ok(), "status": "unreadable"}
    with _patch_gate(), patch.object(dfs, "_resolve_sub_cost_code_id", return_value=None):
        _run(
            draft_bill_from_attachment(
                attachment_public_id=ATT_UUID,
                user_id=5,
                attachment_service=att_svc,
                blia_service=blia_svc,
                bill_service=bill_svc,
                read_fn=read_fn,
            )
        )
    assert bill_svc.create.call_args.kwargs["memo"] is None


@pytest.mark.parametrize(
    "user, auth, auth_raises, expected",
    [
        (SimpleNamespace(firstname="Chris", lastname="Z"), None, False, "Chris Z"),
        (SimpleNamespace(firstname=" ", lastname=None), SimpleNamespace(username="czub"), False, "czub"),
        (SimpleNamespace(firstname="Chris", lastname="Z"), None, True, "Chris Z"),
        (SimpleNamespace(firstname=None, lastname=None), None, True, ""),
        (None, None, False, ""),
    ],
)
def test_resolve_entered_by_for_memo(user, auth, auth_raises, expected):
    with patch("entities.user.business.service.UserService") as user_cls, patch(
        "entities.auth.business.service.AuthService"
    ) as auth_cls:
        user_cls.return_value.read_by_id.return_value = user
        if auth_raises:
            auth_cls.return_value.read_by_user_id.side_effect = RuntimeError("db down")
        else:
            auth_cls.return_value.read_by_user_id.return_value = auth
        assert _run(dfs._resolve_entered_by_for_memo(17)) == expected
        user_cls.return_value.read_by_id.assert_called_once_with("17")
        if user is not None and expected and not auth_raises and user.firstname and user.firstname.strip():
            auth_cls.return_value.read_by_user_id.assert_not_called()
