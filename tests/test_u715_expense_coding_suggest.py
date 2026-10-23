"""U-715 — on-demand expense coding suggest (service + route, fakes only)."""
from __future__ import annotations

import inspect
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from entities.coding_suggestion.business.model import CodingSuggestion
from entities.coding_suggestion.business.service import (
    CodingSuggestionService,
    _map_engine_result_to_contract,
    _map_stored_item_to_contract,
)
import shared.access as access_mod
from shared.access import EntityNotAccessibleError
from shared.api.errors import install_error_handlers
from shared.rbac_constants import Modules

EXPENSE_PUB = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
CS_PUB = "11111111-1111-1111-1111-111111111111"


@pytest.fixture(autouse=True)
def _u715_no_live_db():
    """Block accidental pyodbc from QBO purchase reads and project access checks."""
    qbo_purchase = SimpleNamespace(
        id=10,
        private_note="memo",
        sync_token="1",
        entity_ref_value="vendor-qbo",
    )
    with patch(
        "integrations.intuit.qbo.purchase.persistence.repo.QboPurchaseRepository"
    ) as qbo_repo_cls, patch(
        "entities.coding_suggestion.business.service.can_access_project",
        return_value=True,
    ):
        qbo_repo_cls.return_value.read_by_qbo_id_and_realm_id.return_value = qbo_purchase
        yield qbo_purchase


def _expense(**over):
    base = dict(
        id=5,
        public_id=EXPENSE_PUB,
        is_draft=True,
        qbo_id="purchase-1",
        realm_id="realm-1",
        vendor_id=9,
        memo="Chris Zubrod - HE12 - Dumpster - 2.01",
    )
    base.update(over)
    return SimpleNamespace(**base)


def _coding_item(**over):
    base = dict(
        id=1,
        public_id=CS_PUB,
        row_version=None,
        target_kind="expense",
        bill_id=None,
        bill_credit_id=None,
        expense_id=5,
        attachment_id=None,
        qbo_purchase_id=10,
        qbo_purchase_qbo_id="purchase-1",
        realm_id="realm-1",
        vendor_id=9,
        sync_token_at_suggest=None,
        status="pending",
        suggested_project_id=None,
        suggested_sub_cost_code_id=None,
        suggested_description=None,
        suggestion_source=None,
        suggestion_reason=None,
        suggestion_confidence=None,
        suggested_at=None,
        features_json=None,
        budget_check=None,
        budget_remaining=None,
        route=None,
        model_version=None,
        confirmed_project_id=None,
        confirmed_sub_cost_code_id=None,
        confirmed_description=None,
        was_overridden=None,
        confirmed_by_user_id=None,
        confirmed_at=None,
        confirmed_mixed_codes=None,
        prefill_state=None,
        flag_reason=None,
        flagged_at=None,
        written_at=None,
        write_error=None,
        claimed_by_user_id=None,
        claimed_at=None,
        company_id=1,
        created_by_user_id=17,
        created_datetime=None,
        modified_datetime=None,
    )
    base.update(over)
    return CodingSuggestion(**base)


def _engine_suggested(**over):
    base = {
        "status": "suggested",
        "project_id": 10,
        "sub_cost_code_id": 20,
        "description": "Dumpster",
        "source": "code+scc_shorthand",
        "reason": "matched project code HE12; cost-code shorthand 2.01",
        "confidence": Decimal("0.92"),
        "project_source": "code",
        "project_confidence": Decimal("0.95"),
        "project_reason": "matched project code HE12",
        "scc_source": "scc_shorthand",
        "scc_confidence": Decimal("0.92"),
        "scc_reason": "cost-code shorthand 2.01",
    }
    base.update(over)
    return base


# --- service ----------------------------------------------------------------


def test_not_draft_returns_without_repo_writes():
    svc = CodingSuggestionService()
    svc.repo = MagicMock()
    svc.ensure_for_expense = MagicMock()
    with patch(
        "entities.expense.business.service.ExpenseService"
    ) as expense_svc_cls:
        expense_svc_cls.return_value.read_by_public_id.return_value = _expense(
            is_draft=False
        )
        out = svc.suggest_for_expense(expense_public_id=EXPENSE_PUB, user_id=17)

    assert out == {
        "status": "not_draft",
        "sub_cost_code": None,
        "project": None,
        "confidence": None,
    }
    svc.repo.read_state_by_expense_ids.assert_not_called()
    svc.ensure_for_expense.assert_not_called()


def test_qbo_pending_row_runs_engine_once_and_persists():
    svc = CodingSuggestionService()
    svc.repo = MagicMock()
    svc.repo.read_state_by_expense_ids.return_value = {
        5: [{"public_id": CS_PUB, "status": "pending"}]
    }
    pending = _coding_item()
    stored = _coding_item(
        status="suggested",
        suggestion_source="code+scc_shorthand",
        suggested_project_id=10,
        suggested_sub_cost_code_id=20,
        suggestion_reason="matched project code HE12; cost-code shorthand 2.01",
        suggestion_confidence=Decimal("0.92"),
    )
    svc.read_by_public_id = MagicMock(return_value=pending)
    svc.ensure_for_expense = MagicMock(return_value=True)

    engine = MagicMock()
    engine.suggest_for_item.return_value = _engine_suggested()
    engine.persist_result.return_value = stored
    item_shape = SimpleNamespace(public_id=CS_PUB, vendor_id=9, sync_token="3")

    with patch(
        "entities.expense.business.service.ExpenseService"
    ) as expense_svc_cls, patch.object(
        svc, "_engine_item_for_expense", return_value=(item_shape, "realm-1")
    ):
        expense_svc_cls.return_value.read_by_public_id.return_value = _expense()
        out = svc.suggest_for_expense(
            expense_public_id=EXPENSE_PUB, user_id=17, engine=engine
        )

    assert out["status"] == "suggested"
    assert out["project"]["source"] == "memo_code"
    assert out["sub_cost_code"]["source"] == "memo_shorthand"
    engine.suggest_for_item.assert_called_once()
    engine.persist_result.assert_called_once()
    kwargs = engine.persist_result.call_args.kwargs
    assert kwargs["public_id"] == CS_PUB
    assert kwargs["sync_token"] == "3"
    assert kwargs["only_from_pending_like"] is True
    svc.ensure_for_expense.assert_not_called()


def test_ensure_runs_only_when_no_coding_state_row():
    svc = CodingSuggestionService()
    svc.repo = MagicMock()
    svc.repo.read_exists_for_purchase.return_value = None
    svc.repo.read_state_by_expense_ids.side_effect = [
        {},
        {5: [{"public_id": CS_PUB, "status": "pending"}]},
    ]
    pending = _coding_item()
    stored = _coding_item(
        status="suggested",
        suggestion_source="vendor_history",
        suggested_sub_cost_code_id=30,
        suggestion_confidence=Decimal("0.8"),
    )
    svc.read_by_public_id = MagicMock(return_value=pending)
    svc.ensure_for_expense = MagicMock(return_value=True)

    engine = MagicMock()
    engine.suggest_for_item.return_value = _engine_suggested(project_id=None, source="scc_shorthand")
    engine.persist_result.return_value = stored

    qbo_purchase = SimpleNamespace(id=10, private_note="memo", sync_token="1", entity_ref_value="v1")

    with patch(
        "entities.expense.business.service.ExpenseService"
    ) as expense_svc_cls, patch(
        "integrations.intuit.qbo.purchase.persistence.repo.QboPurchaseRepository"
    ) as qbo_repo_cls, patch.object(
        svc, "_engine_item_for_expense", return_value=(SimpleNamespace(sync_token="1"), "realm-1")
    ) as engine_item:
        expense_svc_cls.return_value.read_by_public_id.return_value = _expense()
        qbo_repo_cls.return_value.read_by_qbo_id_and_realm_id.return_value = qbo_purchase
        svc.suggest_for_expense(expense_public_id=EXPENSE_PUB, user_id=17, engine=engine)

    svc.ensure_for_expense.assert_called_once()
    ensure_kwargs = svc.ensure_for_expense.call_args.kwargs
    assert ensure_kwargs["qbo_purchase"] is qbo_purchase
    engine_item.assert_called_once()
    assert engine_item.call_args.kwargs.get("qbo_purchase") is qbo_purchase


def test_ensure_skipped_when_unscoped_exists_but_scoped_state_empty():
    svc = CodingSuggestionService()
    svc.repo = MagicMock()
    svc.repo.read_state_by_expense_ids.return_value = {}
    svc.repo.read_exists_for_purchase.return_value = (99, CS_PUB)
    svc.read_by_public_id = MagicMock()
    svc.ensure_for_expense = MagicMock()

    engine = MagicMock()
    engine.suggest_for_item.return_value = _engine_suggested()

    with patch(
        "entities.expense.business.service.ExpenseService"
    ) as expense_svc_cls, patch.object(
        svc,
        "_engine_item_for_expense",
        return_value=(SimpleNamespace(vendor_id=9), "realm-1"),
    ):
        expense_svc_cls.return_value.read_by_public_id.return_value = _expense()
        svc.suggest_for_expense(expense_public_id=EXPENSE_PUB, user_id=17, engine=engine)

    svc.ensure_for_expense.assert_not_called()
    svc.repo.upsert_from_queue.assert_not_called()


def test_second_call_returns_stored_without_rerunning_engine_or_ensure():
    svc = CodingSuggestionService()
    svc.repo = MagicMock()
    svc.repo.read_state_by_expense_ids.return_value = {
        5: [{"public_id": CS_PUB, "status": "suggested"}]
    }
    stored = _coding_item(
        status="suggested",
        suggestion_source="vendor_history",
        suggested_sub_cost_code_id=30,
        suggestion_reason="vendor coded to SCC 4.10 in 8/10 priors",
        suggestion_confidence=Decimal("0.8"),
    )
    svc.read_by_public_id = MagicMock(return_value=stored)
    svc.ensure_for_expense = MagicMock()
    engine = MagicMock()

    with patch(
        "entities.expense.business.service.ExpenseService"
    ) as expense_svc_cls:
        expense_svc_cls.return_value.read_by_public_id.return_value = _expense()
        out = svc.suggest_for_expense(
            expense_public_id=EXPENSE_PUB, user_id=17, engine=engine
        )

    assert out["status"] == "suggested"
    assert out["sub_cost_code"]["source"] == "vendor_history"
    assert out["project"] is None
    engine.suggest_for_item.assert_not_called()
    svc.ensure_for_expense.assert_not_called()


def test_flagged_path_uses_persist_result_only_from_pending():
    svc = CodingSuggestionService()
    svc.repo = MagicMock()
    svc.repo.read_state_by_expense_ids.return_value = {
        5: [{"public_id": CS_PUB, "status": "pending"}]
    }
    pending = _coding_item()
    flagged = _coding_item(status="flagged", flag_reason="needs cardholder follow-up")
    svc.read_by_public_id = MagicMock(return_value=pending)
    svc.ensure_for_expense = MagicMock(return_value=True)

    engine = MagicMock()
    engine.suggest_for_item.return_value = {
        "status": "flagged",
        "project_id": None,
        "sub_cost_code_id": None,
        "description": None,
        "source": None,
        "reason": "needs cardholder follow-up: no project or cost-code signal",
        "confidence": None,
        "project_source": None,
        "project_confidence": None,
        "project_reason": None,
        "scc_source": None,
        "scc_confidence": None,
        "scc_reason": None,
    }
    engine.persist_result.return_value = flagged

    with patch(
        "entities.expense.business.service.ExpenseService"
    ) as expense_svc_cls, patch.object(
        svc,
        "_engine_item_for_expense",
        return_value=(SimpleNamespace(public_id=CS_PUB, sync_token="1"), "realm-1"),
    ):
        expense_svc_cls.return_value.read_by_public_id.return_value = _expense()
        out = svc.suggest_for_expense(
            expense_public_id=EXPENSE_PUB, user_id=17, engine=engine
        )

    assert out["status"] == "flagged"
    engine.persist_result.assert_called_once()
    assert engine.persist_result.call_args.kwargs["only_from_pending_like"] is True


def test_manual_expense_no_persistence():
    svc = CodingSuggestionService()
    svc.repo = MagicMock()
    svc.repo.read_state_by_expense_ids.return_value = {}
    svc.read_by_public_id = MagicMock()
    svc.ensure_for_expense = MagicMock()

    engine = MagicMock()
    engine.suggest_for_item.return_value = _engine_suggested()

    with patch(
        "entities.expense.business.service.ExpenseService"
    ) as expense_svc_cls, patch.object(
        svc,
        "_engine_item_for_expense",
        return_value=(SimpleNamespace(vendor_id=9), None),
    ):
        expense_svc_cls.return_value.read_by_public_id.return_value = _expense(
            qbo_id=None, realm_id=None
        )
        out = svc.suggest_for_expense(
            expense_public_id=EXPENSE_PUB, user_id=17, engine=engine
        )

    assert out["status"] == "suggested"
    engine.persist_result.assert_not_called()
    svc.ensure_for_expense.assert_not_called()


def test_confirmed_stored_row_maps_to_none_contract():
    stored = _coding_item(
        status="confirmed",
        suggestion_source="code+scc_shorthand",
        suggested_project_id=10,
        suggested_sub_cost_code_id=20,
        suggestion_confidence=Decimal("0.9"),
    )
    out = _map_stored_item_to_contract(stored)
    assert out == {
        "status": "none",
        "sub_cost_code": None,
        "project": None,
        "confidence": None,
    }


def test_project_without_memo_source_is_dropped():
    raw = _engine_suggested(
        project_source=None,
        project_id=99,
        project_reason="vendor history project",
    )
    out = _map_engine_result_to_contract(raw)
    assert out["project"] is None
    assert out["sub_cost_code"]["source"] == "memo_shorthand"


def test_memo_address_source_mapping():
    raw = _engine_suggested(
        project_source="address",
        project_reason="address match Oak House",
        source="address+scc_shorthand",
    )
    out = _map_engine_result_to_contract(raw)
    assert out["project"]["source"] == "memo_address"


def test_inaccessible_project_redacted_on_engine_path():
    svc = CodingSuggestionService()
    svc.repo = MagicMock()
    svc.repo.read_state_by_expense_ids.return_value = {}
    svc.read_by_public_id = MagicMock()
    svc.ensure_for_expense = MagicMock()

    engine = MagicMock()
    engine.suggest_for_item.return_value = _engine_suggested()

    with patch(
        "entities.expense.business.service.ExpenseService"
    ) as expense_svc_cls, patch.object(
        svc,
        "_engine_item_for_expense",
        return_value=(SimpleNamespace(vendor_id=9), None),
    ), patch(
        "entities.coding_suggestion.business.service.can_access_project",
        return_value=False,
    ):
        expense_svc_cls.return_value.read_by_public_id.return_value = _expense(
            qbo_id=None, realm_id=None
        )
        out = svc.suggest_for_expense(
            expense_public_id=EXPENSE_PUB, user_id=17, engine=engine
        )

    assert out["status"] == "suggested"
    assert out["project"] is None
    assert out["sub_cost_code"] is not None
    assert out["confidence"] == pytest.approx(0.92)


def test_inaccessible_project_only_suggestion_returns_none_via_suggest():
    svc = CodingSuggestionService()
    svc.repo = MagicMock()
    svc.repo.read_state_by_expense_ids.return_value = {}
    svc.read_by_public_id = MagicMock()
    svc.ensure_for_expense = MagicMock()

    engine = MagicMock()
    engine.suggest_for_item.return_value = _engine_suggested(
        sub_cost_code_id=None,
        scc_source=None,
        source="code",
        scc_confidence=None,
        scc_reason=None,
    )

    with patch(
        "entities.expense.business.service.ExpenseService"
    ) as expense_svc_cls, patch.object(
        svc,
        "_engine_item_for_expense",
        return_value=(SimpleNamespace(vendor_id=9), None),
    ), patch(
        "entities.coding_suggestion.business.service.can_access_project",
        return_value=False,
    ):
        expense_svc_cls.return_value.read_by_public_id.return_value = _expense(
            qbo_id=None, realm_id=None
        )
        out = svc.suggest_for_expense(
            expense_public_id=EXPENSE_PUB, user_id=17, engine=engine
        )

    assert out == {
        "status": "none",
        "sub_cost_code": None,
        "project": None,
        "confidence": None,
    }


def test_inaccessible_project_redacted_on_stored_path():
    from entities.coding_suggestion.business.service import (
        _redact_inaccessible_project_from_contract,
    )

    stored = _coding_item(
        status="suggested",
        suggestion_source="code+scc_shorthand",
        suggested_project_id=10,
        suggested_sub_cost_code_id=20,
        suggestion_reason="matched project code HE12; cost-code shorthand 2.01",
        suggestion_confidence=Decimal("0.92"),
    )
    mapped = _map_stored_item_to_contract(stored)
    with patch(
        "entities.coding_suggestion.business.service.can_access_project",
        return_value=False,
    ):
        out = _redact_inaccessible_project_from_contract(mapped)
    assert out["project"] is None
    assert out["sub_cost_code"]["id"] == 20
    assert "HE12" not in (out.get("sub_cost_code") or {}).get("reason", "")


def test_inaccessible_project_stored_only_project_returns_none_via_suggest():
    svc = CodingSuggestionService()
    svc.repo = MagicMock()
    svc.repo.read_state_by_expense_ids.return_value = {
        5: [{"public_id": CS_PUB, "status": "suggested"}]
    }
    stored = _coding_item(
        status="suggested",
        suggestion_source="code",
        suggested_project_id=10,
        suggestion_reason="matched project code HE12",
        suggestion_confidence=Decimal("0.95"),
    )
    svc.read_by_public_id = MagicMock(return_value=stored)
    svc.ensure_for_expense = MagicMock()
    engine = MagicMock()

    with patch(
        "entities.expense.business.service.ExpenseService"
    ) as expense_svc_cls, patch(
        "entities.coding_suggestion.business.service.can_access_project",
        return_value=False,
    ):
        expense_svc_cls.return_value.read_by_public_id.return_value = _expense()
        out = svc.suggest_for_expense(
            expense_public_id=EXPENSE_PUB, user_id=17, engine=engine
        )

    assert out["status"] == "none"
    assert out["project"] is None
    assert out["sub_cost_code"] is None
    engine.suggest_for_item.assert_not_called()


def test_access_check_exception_redacts_without_500():
    svc = CodingSuggestionService()
    svc.repo = MagicMock()
    svc.repo.read_state_by_expense_ids.return_value = {}
    svc.read_by_public_id = MagicMock()
    svc.ensure_for_expense = MagicMock()

    engine = MagicMock()
    engine.suggest_for_item.return_value = _engine_suggested()

    with patch(
        "entities.expense.business.service.ExpenseService"
    ) as expense_svc_cls, patch.object(
        svc,
        "_engine_item_for_expense",
        return_value=(SimpleNamespace(vendor_id=9), None),
    ), patch(
        "entities.coding_suggestion.business.service.can_access_project",
        access_mod.can_access_project,
    ), patch(
        "shared.access._check",
        side_effect=RuntimeError("db down"),
    ):
        expense_svc_cls.return_value.read_by_public_id.return_value = _expense(
            qbo_id=None, realm_id=None
        )
        out = svc.suggest_for_expense(
            expense_public_id=EXPENSE_PUB, user_id=17, engine=engine
        )

    assert out["status"] == "suggested"
    assert out["project"] is None
    assert out["sub_cost_code"] is not None


def test_suggest_does_not_mutate_expense_service():
    svc = CodingSuggestionService()
    svc.repo = MagicMock()
    svc.repo.read_state_by_expense_ids.return_value = {}
    svc.ensure_for_expense = MagicMock()
    engine = MagicMock()
    engine.suggest_for_item.return_value = _engine_suggested()

    expense_mock = MagicMock()
    expense_mock.read_by_public_id.return_value = _expense(qbo_id=None, realm_id=None)
    expense_mock.update_by_public_id = MagicMock()
    expense_mock.repo = MagicMock()

    with patch(
        "entities.expense.business.service.ExpenseService",
        return_value=expense_mock,
    ), patch.object(
        svc,
        "_engine_item_for_expense",
        return_value=(SimpleNamespace(vendor_id=1), None),
    ):
        svc.suggest_for_expense(expense_public_id=EXPENSE_PUB, user_id=17, engine=engine)

    expense_mock.update_by_public_id.assert_not_called()
    expense_mock.repo.update.assert_not_called()


# --- route --------------------------------------------------------------------


def _suggest_client(*, allow: bool) -> TestClient:
    from entities.expense.api.router import (
        router as expense_router,
        suggest_expense_coding_for_expense_router,
    )

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(expense_router)
    dep = (
        inspect.signature(suggest_expense_coding_for_expense_router)
        .parameters["current_user"]
        .default.dependency
    )
    if allow:
        app.dependency_overrides[dep] = lambda: {"id": 17, "tenant_id": 1}
    else:

        def _deny():
            raise HTTPException(status_code=403, detail="Forbidden")

        app.dependency_overrides[dep] = _deny
    return TestClient(app, raise_server_exceptions=False)


def test_route_returns_envelope_and_404():
    client = _suggest_client(allow=True)
    with patch(
        "entities.coding_suggestion.business.service.CodingSuggestionService"
    ) as svc_cls:
        svc_cls.return_value.suggest_for_expense.return_value = {
            "status": "suggested",
            "sub_cost_code": {
                "id": 20,
                "source": "memo_shorthand",
                "confidence": 0.9,
                "reason": "cost-code shorthand 2.01",
            },
            "project": None,
            "confidence": 0.9,
        }
        ok = client.post(f"/api/v1/expense/{EXPENSE_PUB}/coding/suggest")
    assert ok.status_code == 200
    body = ok.json()
    assert set(body.keys()) == {"data"}
    assert body["data"]["status"] == "suggested"

    with patch(
        "entities.coding_suggestion.business.service.CodingSuggestionService"
    ) as svc_cls:
        svc_cls.return_value.suggest_for_expense.return_value = None
        missing = client.post(f"/api/v1/expense/{EXPENSE_PUB}/coding/suggest")
    assert missing.status_code == 404


def test_route_inaccessible_expense_returns_404_not_500():
    client = _suggest_client(allow=True)
    with patch(
        "entities.expense.business.service.ExpenseService"
    ) as expense_cls, patch(
        "entities.coding_suggestion.persistence.repo.CodingSuggestionRepository"
    ) as repo_cls:
        expense_cls.return_value.read_by_public_id.side_effect = EntityNotAccessibleError(
            "Expense", 5
        )
        response = client.post(f"/api/v1/expense/{EXPENSE_PUB}/coding/suggest")
    assert response.status_code == 404
    repo_cls.return_value.upsert_from_queue.assert_not_called()
    repo_cls.return_value.read_state_by_expense_ids.assert_not_called()


def test_route_enforces_can_update_permission():
    client = _suggest_client(allow=False)
    response = client.post(f"/api/v1/expense/{EXPENSE_PUB}/coding/suggest")
    assert response.status_code == 403


def test_route_permission_matches_expense_update_route():
    from entities.expense.api.router import (
        suggest_expense_coding_for_expense_router,
        update_expense_by_public_id_router,
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

    assert _perm(suggest_expense_coding_for_expense_router) == (
        Modules.EXPENSES,
        "can_update",
    )
    assert _perm(update_expense_by_public_id_router) == (Modules.EXPENSES, "can_update")


def test_suggest_for_item_exposes_per_field_provenance():
    """Additive engine keys — checked by asserting keys absent before U-715."""
    from entities.coding_suggestion.business.suggestion_service import CodingSuggestionEngine

    engine = CodingSuggestionEngine(
        project_service=MagicMock(read_by_abbreviation=MagicMock(return_value=None)),
        sub_cost_code_service=MagicMock(
            find_for_reply=MagicMock(return_value=[]),
            is_holding_code=MagicMock(return_value=False),
        ),
        suggestion_repo=MagicMock(
            read_vendor_dominant_sub_cost_code=MagicMock(return_value=None)
        ),
        resolve_qbo_item_ref=MagicMock(return_value=None),
    )
    result = engine.suggest_for_item(
        SimpleNamespace(private_note="THE HOME DEPOT", vendor_id=1)
    )
    for key in (
        "project_source",
        "project_confidence",
        "project_reason",
        "scc_source",
        "scc_confidence",
        "scc_reason",
    ):
        assert key in result
