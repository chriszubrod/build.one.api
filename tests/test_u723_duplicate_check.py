"""U-723 — bill possible-duplicate gates (pure / fakes)."""
from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import BackgroundTasks

from entities.bill.business.duplicate_check import (
    DuplicateCheckResult,
    DuplicateMatch,
    PossibleDuplicateRefused,
    confirmation_covers_matches,
    effective_duplicate_amount,
    effective_mode_for_path,
    run_possible_duplicate_check,
)
from entities.bill.business.service import BillService
from shared.api.errors import ApiError, ErrorCode


def test_effective_amount_prefers_header():
    assert effective_duplicate_amount(
        header_total=Decimal("1500.00"),
        line_amounts=[Decimal("9")],
    ) == Decimal("1500.00")


def test_effective_mode_record_path_under_warn_global(monkeypatch):
    monkeypatch.setenv("BILL_DUPLICATE_WARN", "warn")
    assert effective_mode_for_path(path="record_path", explicit="off") == "record"


def test_global_off_is_a_kill_switch_even_for_explicit_record(monkeypatch):
    """U-723 r2: BILL_DUPLICATE_WARN=off must disable the explicit record paths too
    (list drop, agent create) - no duplicate reads anywhere."""
    monkeypatch.setenv("BILL_DUPLICATE_WARN", "off")
    assert effective_mode_for_path(path="record_path", explicit="record") == "off"
    assert effective_mode_for_path(path="human_create", explicit="warn") == "off"


def test_confirmation_requires_superset():
    matches = [
        DuplicateMatch(
            "a", None, None, None, None, None, None, [], None, ["vendor_date_amount"]
        )
    ]
    assert not confirmation_covers_matches(
        confirm_not_duplicate=True,
        confirmed_public_ids=[],
        visible_matches=matches,
    )
    assert confirmation_covers_matches(
        confirm_not_duplicate=True,
        confirmed_public_ids=["a"],
        visible_matches=matches,
    )


def test_run_possible_duplicate_check_splits_hidden(monkeypatch):
    rows = [
        SimpleNamespace(
            PublicId="vis",
            BillNumber="1",
            BillDate="2026-10-09",
            TotalAmount=Decimal("1500"),
            Status="draft",
            VendorName="V",
            ProjectNamesCsv="P1",
            CreatedDatetime=None,
            ReasonsCsv="vendor_date_amount",
            CallerCanAccess=True,
        ),
        SimpleNamespace(
            PublicId="hid",
            BillNumber="2",
            BillDate="2026-10-09",
            TotalAmount=Decimal("1500"),
            Status="draft",
            VendorName="V2",
            ProjectNamesCsv=None,
            CreatedDatetime=None,
            ReasonsCsv="same_number_amount_date",
            CallerCanAccess=False,
        ),
    ]
    repo = MagicMock()
    repo.read_possible_duplicate_bills.return_value = rows
    result = run_possible_duplicate_check(
        vendor_id=1,
        bill_date="2026-10-09",
        total_amount=Decimal("1500"),
        bill_number="9242 Nina Ct",
        text_fingerprints=[],
        exclude_bill_id=99,
        project_ids=[1],
        repo=repo,
    )
    assert len(result.visible_matches) == 1
    assert result.hidden_match_count == 1
    assert result.visible_matches[0].reasons == ["vendor_date_amount"]


def test_possible_duplicate_refused_carries_extra():
    err = PossibleDuplicateRefused(
        "Possible duplicate bill: x",
        duplicate_matches=[{"public_id": "p"}],
        hidden_match_count=2,
    )
    assert err.error_code == ErrorCode.POSSIBLE_DUPLICATE
    assert err.extra["hidden_match_count"] == 2
    assert err.detail == "Possible duplicate bill: x"


def test_record_mode_never_raises_from_assert():
    from entities.bill.business.duplicate_check import assert_duplicate_check_passes_or_raise

    result = DuplicateCheckResult(
        visible_matches=[
            DuplicateMatch(
                "p", None, "n", "d", "1", "draft", "v", [], None, ["vendor_date_amount"]
            )
        ],
        hidden_match_count=0,
    )
    assert_duplicate_check_passes_or_raise(
        mode="record",
        result=result,
        confirm_not_duplicate=False,
        confirmed_duplicate_public_ids=[],
        agent_caller=False,
    )


def test_agent_cannot_confirm_in_warn_mode():
    from entities.bill.business.duplicate_check import assert_duplicate_check_passes_or_raise

    result = DuplicateCheckResult(
        visible_matches=[
            DuplicateMatch(
                "p", None, "n", "d", "1", "draft", "v", [], None, ["vendor_date_amount"]
            )
        ],
    )
    with pytest.raises(PossibleDuplicateRefused):
        assert_duplicate_check_passes_or_raise(
            mode="warn",
            result=result,
            confirm_not_duplicate=True,
            confirmed_duplicate_public_ids=["p"],
            agent_caller=True,
        )


def _create_body_mock(**over):
    body = MagicMock()
    defaults = dict(
        line_coding_prefill_state=None,
        line_project_public_id=None,
        submit_for_review=None,
        vendor_public_id="v",
        bill_number="n",
        bill_date="2026-10-09",
        source_email_message_public_id=None,
        intake_source=None,
        total_amount=None,
        line_amount=Decimal("1500"),
        attachment_public_id=None,
        confirm_not_duplicate=False,
        confirmed_duplicate_public_ids=[],
        payment_term_public_id=None,
        due_date="2026-10-09",
        memo=None,
        line_description=None,
        line_quantity=None,
        line_rate=None,
        line_markup=None,
        line_price=None,
        line_is_billable=None,
        line_sub_cost_code_id=None,
    )
    defaults.update(over)
    for k, v in defaults.items():
        setattr(body, k, v)
    return body


def test_create_router_hard_duplicate_wins_before_possible(monkeypatch):
    monkeypatch.setenv("BILL_DUPLICATE_WARN", "warn")
    from entities.bill.api.router import create_bill_router

    body = _create_body_mock()
    possible_called = {"n": 0}

    def _possible_gate(**kwargs):
        possible_called["n"] += 1
        return DuplicateCheckResult(), "warn"

    with patch(
        "entities.bill.api.router.BillService.assert_create_submit_reviewed"
    ), patch("entities.bill.api.router.resolve_user_id", return_value=17), patch(
        "entities.bill.api.router.assert_hard_duplicate_before_create",
        side_effect=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("hard dup")),
    ), patch(
        "entities.bill.api.router.run_create_possible_duplicate_gate_advisory",
        side_effect=_possible_gate,
    ):
        with pytest.raises(RuntimeError, match="hard dup"):
            asyncio.run(
                create_bill_router(
                    body=body,
                    current_user={"username": "chris", "tenant_id": 1, "sub": "u"},
                )
            )
    assert possible_called["n"] == 0


def test_hard_duplicate_maps_to_409_via_real_helper():
    """Red proof for duplicate_gate: raise_workflow_error needs default_message."""
    svc = BillService(repo=MagicMock())
    existing = SimpleNamespace(
        id=1,
        public_id="dup-pid",
        is_draft=True,
        status="draft",
    )
    svc.repo.read_by_bill_number_and_vendor_id.return_value = existing
    with patch("entities.bill.business.service.VendorService") as vs:
        vs.return_value.read_by_public_id.return_value = SimpleNamespace(id=7)
        with patch("entities.bill.business.service.can_access_bill", return_value=True):
            from entities.bill.business.duplicate_gate import assert_hard_duplicate_before_create

            with pytest.raises(ApiError) as exc:
                assert_hard_duplicate_before_create(
                    svc,
                    vendor_public_id="v",
                    bill_number="n",
                    bill_date="2026-10-09",
                )
    assert exc.value.status_code == 409
    assert "already exists" in exc.value.detail.lower()


def test_create_router_warn_mode_match_returns_422(monkeypatch):
    monkeypatch.setenv("BILL_DUPLICATE_WARN", "warn")
    from entities.bill.api.router import create_bill_router

    match_row = SimpleNamespace(
        PublicId="match-1",
        BillNumber="9242 Nina Ct",
        BillDate="2026-10-09",
        TotalAmount=Decimal("1500"),
        Status="completed",
        VendorName="Wilson",
        ProjectNamesCsv="HE7",
        CreatedDatetime=None,
        ReasonsCsv="vendor_date_amount",
        CallerCanAccess=True,
    )
    repo = MagicMock()
    repo.read_possible_duplicate_bills.return_value = [match_row]

    execute_called = {"n": 0}

    def _engine(*a, **k):
        execute_called["n"] += 1
        return {"success": True, "data": {"id": 1}}

    body = _create_body_mock(vendor_public_id="vendor-pid", line_project_public_id="proj-pid")
    mock_bill_svc = MagicMock()
    with patch("entities.bill.api.router.resolve_user_id", return_value=17), patch(
        "entities.bill.api.router.BillService"
    ) as bill_service_cls, patch(
        "entities.bill.business.duplicate_gate.VendorService"
    ) as vs, patch(
        "entities.bill.business.duplicate_gate.ProjectService"
    ) as ps, patch(
        "entities.bill.business.duplicate_check.BillRepository", return_value=repo
    ), patch(
        "entities.bill.api.router.ProcessEngine"
    ) as pe:
        bill_service_cls.assert_create_submit_reviewed = MagicMock()
        bill_service_cls.return_value = mock_bill_svc
        mock_bill_svc.assert_no_hard_duplicate_on_create = MagicMock(return_value=None)
        vs.return_value.read_by_public_id.return_value = SimpleNamespace(id=10)
        ps.return_value.read_by_public_id.return_value = SimpleNamespace(id=1)
        pe.return_value.execute_synchronous.side_effect = _engine

        with pytest.raises(ApiError) as exc:
            asyncio.run(
                create_bill_router(
                    body=body,
                    current_user={"username": "chris", "tenant_id": 1, "sub": "u"},
                )
            )
    assert exc.value.status_code == 422
    assert exc.value.error_code == ErrorCode.POSSIBLE_DUPLICATE
    assert exc.value.extra["duplicate_matches"][0]["public_id"] == "match-1"
    assert execute_called["n"] == 0


def test_create_router_record_mode_proceeds_with_possible_duplicates(monkeypatch):
    monkeypatch.setenv("BILL_DUPLICATE_WARN", "record")
    from entities.bill.api.router import create_bill_router

    match_row = SimpleNamespace(
        PublicId="match-1",
        BillId=99,
        BillNumber="9242 Nina Ct",
        BillDate="2026-10-09",
        TotalAmount=Decimal("1500"),
        Status="draft",
        VendorName="V",
        ProjectNamesCsv=None,
        CreatedDatetime=None,
        ReasonsCsv="same_number_amount_date",
        CallerCanAccess=True,
    )
    repo = MagicMock()
    repo.read_possible_duplicate_bills.return_value = [match_row]

    body = _create_body_mock()
    mock_bill_svc = MagicMock()
    with patch("entities.bill.api.router.resolve_user_id", return_value=17), patch(
        "entities.bill.api.router.BillService"
    ) as bill_service_cls, patch(
        "entities.bill.business.duplicate_check.BillRepository", return_value=repo
    ), patch(
        "entities.bill.api.router.ProcessEngine"
    ) as pe, patch(
        "entities.bill.api.router._with_vendor_public_id", side_effect=lambda d: d
    ), patch(
        "entities.bill.business.duplicate_gate.VendorService"
    ) as vs, patch(
        "entities.bill.business.duplicate_gate.ProjectService"
    ) as ps:
        bill_service_cls.assert_create_submit_reviewed = MagicMock()
        bill_service_cls.return_value = mock_bill_svc
        mock_bill_svc.assert_no_hard_duplicate_on_create = MagicMock(return_value=None)
        vs.return_value.read_by_public_id.return_value = None
        ps.return_value.read_by_public_id.return_value = None
        pe.return_value.execute_synchronous.return_value = {
            "success": True,
            "data": {"id": 50, "public_id": "new-bill"},
        }
        resp = asyncio.run(
            create_bill_router(
                body=body,
                current_user={"username": "chris", "tenant_id": 1, "sub": "u"},
            )
        )
    assert "possible_duplicates" in resp
    assert resp["possible_duplicates"][0]["public_id"] == "match-1"
    pe.return_value.execute_synchronous.assert_called_once()


def test_create_gate_fail_open_when_candidate_read_raises(monkeypatch, caplog):
    monkeypatch.setenv("BILL_DUPLICATE_WARN", "warn")
    from entities.bill.api.router import create_bill_router

    body = _create_body_mock()
    mock_bill_svc = MagicMock()
    with patch("entities.bill.api.router.resolve_user_id", return_value=17), patch(
        "entities.bill.api.router.BillService"
    ) as bill_service_cls, patch(
        "entities.bill.business.duplicate_gate.run_possible_duplicate_check",
        side_effect=RuntimeError("db down"),
    ), patch(
        "entities.bill.business.duplicate_gate.VendorService"
    ) as vs, patch(
        "entities.bill.business.duplicate_gate.ProjectService"
    ) as ps, patch(
        "entities.bill.api.router.ProcessEngine"
    ) as pe, patch(
        "entities.bill.api.router._with_vendor_public_id", side_effect=lambda d: d
    ):
        bill_service_cls.assert_create_submit_reviewed = MagicMock()
        bill_service_cls.return_value = mock_bill_svc
        mock_bill_svc.assert_no_hard_duplicate_on_create = MagicMock(return_value=None)
        vs.return_value.read_by_public_id.return_value = None
        ps.return_value.read_by_public_id.return_value = None
        pe.return_value.execute_synchronous.return_value = {
            "success": True,
            "data": {"id": 1, "public_id": "b"},
        }
        with caplog.at_level(logging.WARNING):
            resp = asyncio.run(
                create_bill_router(
                    body=body,
                    current_user={"username": "chris", "tenant_id": 1, "sub": "u"},
                )
            )
    assert resp["data"]["public_id"] == "b"
    assert any("create_gate_failed" in r.getMessage() for r in caplog.records)


def test_agent_username_uses_record_path_not_intake_source(monkeypatch):
    monkeypatch.setenv("BILL_DUPLICATE_WARN", "warn")
    from entities.bill.api.router import create_bill_router

    modes: list[str] = []

    def _capture(**kwargs):
        modes.append(kwargs.get("mode"))
        return DuplicateCheckResult(), "off"

    body = _create_body_mock(intake_source="manual")
    mock_bill_svc = MagicMock()
    with patch("entities.bill.api.router.resolve_user_id", return_value=17), patch(
        "entities.bill.api.router.BillService"
    ) as bill_service_cls, patch(
        "entities.bill.api.router.run_create_possible_duplicate_gate_advisory",
        side_effect=_capture,
    ), patch(
        "entities.bill.api.router.ProcessEngine"
    ) as pe, patch(
        "entities.bill.api.router._with_vendor_public_id", side_effect=lambda d: d
    ):
        bill_service_cls.assert_create_submit_reviewed = MagicMock()
        bill_service_cls.return_value = mock_bill_svc
        mock_bill_svc.assert_no_hard_duplicate_on_create = MagicMock(return_value=None)
        pe.return_value.execute_synchronous.return_value = {
            "success": True,
            "data": {"id": 1},
        }
        asyncio.run(
            create_bill_router(
                body=body,
                current_user={"username": "invoice_agent", "tenant_id": 1, "sub": "u"},
            )
        )
    assert modes == ["record"]


def test_submit_review_bill_warn_mode_422(monkeypatch):
    monkeypatch.setenv("BILL_DUPLICATE_WARN", "warn")
    from entities.review.api.router import submit_review_bill_router
    from entities.review.api.schemas import ReviewSubmitRequest

    bill = SimpleNamespace(
        id=5,
        public_id="b1",
        vendor_id=1,
        bill_date="2026-10-09",
        bill_number="n",
        total_amount=Decimal("1500"),
    )
    match_row = SimpleNamespace(
        PublicId="m1",
        BillNumber="n",
        BillDate="2026-10-09",
        TotalAmount=Decimal("1500"),
        Status="draft",
        VendorName="V",
        ProjectNamesCsv=None,
        CreatedDatetime=None,
        ReasonsCsv="vendor_date_amount",
        CallerCanAccess=True,
    )
    repo = MagicMock()
    repo.read_possible_duplicate_bills.return_value = [match_row]

    with patch(
        "entities.review.api.router._execute_create",
        side_effect=lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not submit")),
    ), patch(
        "entities.review.api.router.resolve_user_id", return_value=17
    ), patch(
        "entities.review.api.router.ReviewService"
    ) as rs, patch(
        "entities.bill.business.service.BillService"
    ) as bs, patch(
        "entities.bill.business.duplicate_gate.BillLineItemService"
    ) as bli, patch(
        "entities.bill.business.duplicate_check.BillRepository", return_value=repo
    ):
        rs.return_value.build_submit_payload.return_value = {"parent_type": "bill"}
        bs.return_value.read_by_public_id.return_value = bill
        bli.return_value.read_by_bill_id.return_value = []
        with pytest.raises(ApiError) as exc:
            submit_review_bill_router(
                public_id="b1",
                body=ReviewSubmitRequest(),
                current_user={"username": "chris", "sub": "u", "tenant_id": 1},
            )
    assert exc.value.status_code == 422
    assert exc.value.error_code == ErrorCode.POSSIBLE_DUPLICATE


def test_complete_router_gate_order_before_enqueue():
    from entities.bill.api.router import complete_bill_router

    svc = MagicMock()
    bill = SimpleNamespace(id=5, public_id="b1", is_draft=True)
    svc.read_by_public_id.return_value = bill
    order: list[str] = []

    with patch("entities.bill.api.router.BillService", return_value=svc), patch(
        "entities.bill.api.router.gate_existing_bill_fail_open",
        side_effect=lambda **k: order.append("dup"),
    ), patch(
        "entities.bill.api.router.gate_completion",
        side_effect=lambda **k: order.append("gate"),
    ), patch(
        "entities.completion_job.business.service.CompletionJobService"
    ) as job_cls:
        job_cls.return_value.enqueue.return_value = SimpleNamespace(
            was_created=True, public_id="job-1"
        )
        with patch("entities.bill.api.router.resolve_user_id", return_value=17):
            complete_bill_router(
                public_id="b1",
                background_tasks=BackgroundTasks(),
                body=None,
                current_user={"username": "chris", "sub": "u"},
            )
    assert order.index("dup") < order.index("gate")
    job_cls.return_value.enqueue.assert_called_once()


def test_qbo_create_never_calls_possible_duplicate_gate():
    from integrations.intuit.qbo.bill.connector.bill.business.service import BillBillConnector

    svc = MagicMock()
    connector = BillBillConnector(bill_service=svc)
    connector.bill_service.create.return_value = SimpleNamespace(id=1)
    with patch(
        "entities.bill.business.duplicate_gate.run_create_possible_duplicate_gate"
    ) as gate:
        connector._create_bill(
            qbo_bill=SimpleNamespace(id=1, realm_id="r"),
            qbo_bill_lines=[],
            vendor_public_id="v",
            bill_number="1",
            bill_date="2026-10-09",
            due_date="2026-10-09",
            memo=None,
            total_amount=Decimal("1"),
        )
    gate.assert_not_called()
    svc.create.assert_called_once()


def _submit_with(monkeypatch, mode, repo, execute_create):
    monkeypatch.setenv("BILL_DUPLICATE_WARN", mode)
    from entities.review.api.router import submit_review_bill_router
    from entities.review.api.schemas import ReviewSubmitRequest

    bill = SimpleNamespace(
        id=5, public_id="b1", vendor_id=1, bill_date="2026-10-09",
        bill_number="n", total_amount=Decimal("1500"),
    )
    with patch(
        "entities.review.api.router._execute_create", side_effect=execute_create
    ), patch(
        "entities.review.api.router.resolve_user_id", return_value=17
    ), patch(
        "entities.review.api.router.ReviewService"
    ) as rs, patch(
        "entities.bill.business.service.BillService"
    ) as bs, patch(
        "entities.bill.business.duplicate_gate.BillLineItemService"
    ) as bli, patch(
        "entities.bill.business.duplicate_check.BillRepository", return_value=repo
    ):
        rs.return_value.build_submit_payload.return_value = {"parent_type": "bill"}
        bs.return_value.read_by_public_id.return_value = bill
        bli.return_value.read_by_bill_id.return_value = []
        out = submit_review_bill_router(
            public_id="b1",
            body=ReviewSubmitRequest(),
            current_user={"username": "chris", "sub": "u", "tenant_id": 1},
        )
        return out, bs


@pytest.mark.parametrize("mode", ["record", "warn"])
def test_submit_fails_open_when_candidate_read_raises(monkeypatch, caplog, mode):
    """U-723 r3: a duplicate-check failure must never block Submit for Review."""
    import logging

    caplog.set_level(logging.WARNING)
    repo = MagicMock()
    repo.read_possible_duplicate_bills.side_effect = RuntimeError("sproc missing")
    out, _ = _submit_with(monkeypatch, mode, repo, lambda *a, **k: {"ok": True})
    assert out == {"ok": True}
    assert any("bill_duplicate.submit_gate_failed" in r.getMessage() for r in caplog.records)
    assert not any("sproc missing" in r.getMessage() for r in caplog.records)


def test_submit_off_mode_does_no_bill_read(monkeypatch):
    """U-723 r3: with BILL_DUPLICATE_WARN=off Submit is byte-identical to master."""
    repo = MagicMock()
    out, bs = _submit_with(monkeypatch, "off", repo, lambda *a, **k: {"ok": True})
    assert out == {"ok": True}
    bs.return_value.read_by_public_id.assert_not_called()
    repo.read_possible_duplicate_bills.assert_not_called()


@pytest.mark.parametrize("mode", ["record", "warn"])
def test_complete_gate_fails_open_when_candidate_read_raises(monkeypatch, caplog, mode):
    """U-723 r3: gate_existing_bill_fail_open swallows infrastructure errors."""
    import logging

    from entities.bill.business.duplicate_gate import gate_existing_bill_fail_open

    monkeypatch.setenv("BILL_DUPLICATE_WARN", mode)
    caplog.set_level(logging.WARNING)
    bill = SimpleNamespace(
        id=9, public_id="b9", vendor_id=1, bill_date="2026-10-09",
        bill_number="n", total_amount=Decimal("1500"),
    )
    dup_mode = effective_mode_for_path(path="human_complete")
    repo = MagicMock()
    repo.read_possible_duplicate_bills.side_effect = RuntimeError("timeout")
    with patch(
        "entities.bill.business.duplicate_gate.BillLineItemService"
    ) as bli, patch(
        "entities.bill.business.duplicate_check.BillRepository", return_value=repo
    ):
        bli.return_value.read_by_bill_id.return_value = []
        gate_existing_bill_fail_open(
            bill=bill,
            stage="complete",
            mode=dup_mode,
            confirm_not_duplicate=False,
            confirmed_duplicate_public_ids=[],
            username="chris",
            actor_user_id_resolver=lambda: 17,
        )
    assert any("bill_duplicate.complete_gate_failed" in r.getMessage() for r in caplog.records)


def test_complete_gate_still_refuses_in_warn_mode_with_a_match(monkeypatch):
    from entities.bill.business.duplicate_gate import gate_existing_bill_fail_open

    monkeypatch.setenv("BILL_DUPLICATE_WARN", "warn")
    bill = SimpleNamespace(
        id=9, public_id="b9", vendor_id=1, bill_date="2026-10-09",
        bill_number="n", total_amount=Decimal("1500"),
    )
    match_row = SimpleNamespace(
        PublicId="m1",
        BillId=42,
        BillNumber="n",
        BillDate="2026-10-09",
        TotalAmount=Decimal("1500"),
        Status="draft",
        VendorName="V",
        ProjectNamesCsv=None,
        CreatedDatetime=None,
        ReasonsCsv="vendor_date_amount",
        CallerCanAccess=True,
    )
    repo = MagicMock()
    repo.read_possible_duplicate_bills.return_value = [match_row]
    with patch(
        "entities.bill.business.duplicate_gate.BillLineItemService"
    ) as bli, patch(
        "entities.bill.business.duplicate_check.BillRepository", return_value=repo
    ):
        bli.return_value.read_by_bill_id.return_value = []
        with pytest.raises(ApiError):
            gate_existing_bill_fail_open(
                bill=bill,
                stage="complete",
                mode=effective_mode_for_path(path="human_complete"),
                confirm_not_duplicate=False,
                confirmed_duplicate_public_ids=[],
                username="chris",
                actor_user_id_resolver=lambda: 17,
            )
