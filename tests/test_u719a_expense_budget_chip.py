"""U-719a — live budget chip on expense coding suggest (fakes only)."""
from __future__ import annotations

import logging
from contextlib import ExitStack
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from entities.budget.business.model import Budget
from entities.coding_suggestion.business.budget_check import (
    compute_budget_check,
    evaluate_budget_for_expense,
)
from entities.coding_suggestion.business.model import CodingSuggestion
from entities.coding_suggestion.business.service import CodingSuggestionService
from shared.access import EntityNotAccessibleError

EXPENSE_PUB = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
CS_PUB = "11111111-1111-1111-1111-111111111111"
PROJECT_ID = 10
ALT_PROJECT_ID = 20
LINE_ONLY_PROJECT_ID = 88
COMMON_ONLY_PROJECT_ID = 99
SCC_ID = 501


def _expense(**over):
    base = dict(
        id=5,
        public_id=EXPENSE_PUB,
        is_draft=True,
        is_credit=False,
        qbo_id="purchase-1",
        realm_id="realm-1",
        vendor_id=9,
        memo="memo",
    )
    base.update(over)
    return SimpleNamespace(**base)


def _line(
    *,
    project_id=PROJECT_ID,
    sub_cost_code_id=SCC_ID,
    amount="100.00",
):
    return SimpleNamespace(
        project_id=project_id,
        sub_cost_code_id=sub_cost_code_id,
        amount=Decimal(amount),
    )


def _variance_row(
    scc_id: int = SCC_ID,
    *,
    budget_amount="1000.00",
    actual_cost="500.00",
):
    ba = Decimal(budget_amount)
    ac = Decimal(actual_cost)
    return {
        "SubCostCodeId": scc_id,
        "BudgetAmount": ba,
        "ActualCost": ac,
        "CostVariance": ba - ac,
    }


def _active_budget_repo(*, variance_rows):
    repo = MagicMock()
    repo.read_by_project_id.return_value = Budget(
        id=2,
        public_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        row_version="rv",
        created_datetime=None,
        modified_datetime=None,
        project_id=PROJECT_ID,
        status="active",
        notes=None,
    )
    repo.read_variance_by_project_id.return_value = variance_rows
    return repo


def _engine_suggested(**over):
    base = {
        "status": "suggested",
        "project_id": PROJECT_ID,
        "sub_cost_code_id": SCC_ID,
        "description": "Dumpster",
        "source": "code+scc_shorthand",
        "reason": "matched",
        "confidence": Decimal("0.92"),
        "project_source": "code",
        "project_confidence": Decimal("0.95"),
        "project_reason": "code",
        "scc_source": "scc_shorthand",
        "scc_confidence": Decimal("0.92"),
        "scc_reason": "shorthand",
    }
    base.update(over)
    return base


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
        status="suggested",
        suggested_project_id=PROJECT_ID,
        suggested_sub_cost_code_id=SCC_ID,
        suggested_description=None,
        suggestion_source="code+scc_shorthand",
        suggestion_reason="code; shorthand",
        suggestion_confidence=Decimal("0.92"),
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


def _budget_payload(check, remaining, project_id=PROJECT_ID):
    return {
        "check": check,
        "remaining": remaining,
        "project_id": project_id,
    }


@pytest.fixture(autouse=True)
def _u719a_no_live_db():
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
    ), patch(
        "entities.coding_suggestion.business.budget_check.assert_can_access_project",
    ):
        qbo_repo_cls.return_value.read_by_qbo_id_and_realm_id.return_value = qbo_purchase
        yield


@pytest.fixture
def mock_eli_service():
    with patch(
        "entities.expense_line_item.business.service.ExpenseLineItemService"
    ) as eli_cls:
        eli_cls.return_value.read_by_expense_id.return_value = [_line()]
        yield eli_cls


@pytest.fixture
def mock_budget_repo():
    with patch(
        "entities.coding_suggestion.business.budget_check.BudgetRepository"
    ) as repo_cls:
        repo_cls.return_value = _active_budget_repo(
            variance_rows=[_variance_row()]
        )
        yield repo_cls


def _run_engine_suggest(
    svc,
    *,
    expense=None,
    engine_result=None,
    lines=None,
):
    svc.repo = MagicMock()
    svc.repo.read_state_by_expense_ids.return_value = {}
    svc.read_by_public_id = MagicMock()
    svc.ensure_for_expense = MagicMock()
    engine = MagicMock()
    engine.suggest_for_item.return_value = engine_result or _engine_suggested()
    expense = expense or _expense()
    with ExitStack() as stack:
        if lines is not None:
            eli_cls = stack.enter_context(
                patch(
                    "entities.expense_line_item.business.service.ExpenseLineItemService"
                )
            )
            eli_cls.return_value.read_by_expense_id.return_value = lines
        expense_svc_cls = stack.enter_context(
            patch("entities.expense.business.service.ExpenseService")
        )
        stack.enter_context(
            patch.object(
                svc,
                "_engine_item_for_expense",
                return_value=(SimpleNamespace(vendor_id=9), None),
            )
        )
        expense_svc_cls.return_value.read_by_public_id.return_value = expense
        return svc.suggest_for_expense(
            expense_public_id=EXPENSE_PUB, user_id=17, engine=engine
        )


@pytest.mark.parametrize(
    "lines,check,remaining",
    [
        ([_line(amount="100.00")], "fits", "500.00"),
        (
            [
                _line(amount="100.00"),
                _line(amount="450.00", sub_cost_code_id=None),
            ],
            "tight",
            "50.00",
        ),
        (
            [
                _line(amount="100.00"),
                _line(amount="550.00", sub_cost_code_id=None),
            ],
            "over",
            "-50.00",
        ),
    ],
)
def test_budget_check_outcomes_fits_tight_over(
    lines, check, remaining, mock_budget_repo, mock_eli_service
):
    out = _run_engine_suggest(CodingSuggestionService(), lines=lines)
    assert out["budget"] == _budget_payload(check, remaining)


def test_unbudgeted_scc(mock_budget_repo, mock_eli_service):
    mock_budget_repo.return_value.read_variance_by_project_id.return_value = [
        _variance_row(budget_amount="0", actual_cost="0")
    ]
    out = _run_engine_suggest(CodingSuggestionService())
    assert out["budget"] == _budget_payload("unbudgeted", None)


def test_no_budget_inactive_project_budget(mock_budget_repo, mock_eli_service):
    mock_budget_repo.return_value.read_by_project_id.return_value = Budget(
        id=2,
        public_id="b",
        row_version="rv",
        created_datetime=None,
        modified_datetime=None,
        project_id=PROJECT_ID,
        status="draft",
        notes=None,
    )
    out = _run_engine_suggest(CodingSuggestionService())
    assert out["budget"] == _budget_payload("no_budget", None)
    mock_budget_repo.return_value.read_variance_by_project_id.assert_not_called()


def test_credit_expense_budget_null_repo_never_called(mock_eli_service):
    with patch(
        "entities.coding_suggestion.business.budget_check.BudgetRepository"
    ) as repo_cls:
        out = _run_engine_suggest(
            CodingSuggestionService(),
            expense=_expense(is_credit=True),
        )
        assert out["budget"] is None
        repo_cls.assert_not_called()


def test_no_scc_in_contract_yields_null_budget():
    contract = {"status": "suggested", "sub_cost_code": None, "project": None, "confidence": None}
    payload = evaluate_budget_for_expense(_expense(), contract, [_line()])
    assert payload is None


def test_not_draft_skips_line_and_budget_reads():
    svc = CodingSuggestionService()
    svc.repo = MagicMock()
    with patch(
        "entities.expense.business.service.ExpenseService"
    ) as expense_svc_cls, patch(
        "entities.expense_line_item.business.service.ExpenseLineItemService"
    ) as eli_cls, patch(
        "entities.coding_suggestion.business.budget_check.BudgetRepository"
    ) as budget_cls:
        expense_svc_cls.return_value.read_by_public_id.return_value = _expense(
            is_draft=False
        )
        out = svc.suggest_for_expense(expense_public_id=EXPENSE_PUB, user_id=17)
    assert out["status"] == "not_draft"
    assert "budget" not in out
    eli_cls.assert_not_called()
    budget_cls.assert_not_called()


def test_redacted_suggestion_project_uses_common_line_project(mock_budget_repo):
    svc = CodingSuggestionService()
    lines = [_line(project_id=COMMON_ONLY_PROJECT_ID)]
    with patch(
        "entities.coding_suggestion.business.service.can_access_project",
        return_value=False,
    ), patch(
        "entities.coding_suggestion.business.budget_check.assert_can_access_project"
    ) as assert_access:
        out = _run_engine_suggest(svc, lines=lines)
    assert out["project"] is None
    assert_access.assert_called_once_with(COMMON_ONLY_PROJECT_ID)
    assert out["budget"] is not None


def test_common_line_project_when_suggestion_has_no_project(mock_budget_repo):
    svc = CodingSuggestionService()
    lines = [_line(project_id=LINE_ONLY_PROJECT_ID)]
    repo = _active_budget_repo(variance_rows=[_variance_row()])
    with patch(
        "entities.coding_suggestion.business.budget_check.BudgetRepository",
        return_value=repo,
    ), patch(
        "entities.coding_suggestion.business.budget_check.assert_can_access_project"
    ) as assert_access:
        out = _run_engine_suggest(
            svc,
            engine_result=_engine_suggested(project_id=None, project_source=None),
            lines=lines,
        )
    assert out["project"] is None
    assert_access.assert_called_once_with(LINE_ONLY_PROJECT_ID)
    assert out["budget"]["check"] == "fits"
    assert out["budget"]["project_id"] == LINE_ONLY_PROJECT_ID


def test_budget_payload_carries_project_id_without_a_project_read(mock_budget_repo):
    """U-719 r2: the payload names the evaluated project by its internal id only — no
    ProjectService read (r1 called read_by_id(project_id=...) — wrong kwarg — and every
    real budget came back null)."""
    contract = {
        "status": "suggested",
        "sub_cost_code": {"id": SCC_ID},
        "project": {"id": PROJECT_ID, "confidence": 0.95, "source": "memo_code", "reason": "code"},
        "confidence": 0.92,
    }
    with patch(
        "entities.project.business.service.ProjectService", autospec=True
    ) as proj_cls:
        payload = evaluate_budget_for_expense(
            _expense(),
            contract,
            [_line()],
            budget_repo=_active_budget_repo(variance_rows=[_variance_row()]),
        )
    assert payload is not None and payload["check"] == "fits"
    assert payload["project_id"] == PROJECT_ID
    assert "project_public_id" not in payload
    proj_cls.assert_not_called()


def test_lines_on_a_memo_suggests_b_yields_no_project_no_budget_read(
    mock_budget_repo, mock_eli_service
):
    """Bill rule: chosen line project ≠ suggested project → no_project, no variance read."""
    lines = [_line(project_id=PROJECT_ID)]
    mock_eli_service.return_value.read_by_expense_id.return_value = lines
    out = _run_engine_suggest(
        CodingSuggestionService(),
        engine_result=_engine_suggested(
            project_id=ALT_PROJECT_ID,
            project_confidence=Decimal("0.95"),
        ),
        lines=lines,
    )
    assert out["budget"] == _budget_payload("no_project", None, project_id=None)
    mock_budget_repo.return_value.read_by_project_id.assert_not_called()
    mock_budget_repo.return_value.read_variance_by_project_id.assert_not_called()


def test_mixed_line_projects_yields_no_project(mock_budget_repo, mock_eli_service):
    mock_eli_service.return_value.read_by_expense_id.return_value = [
        _line(project_id=10),
        _line(project_id=11),
    ]
    out = _run_engine_suggest(
        CodingSuggestionService(),
        engine_result=_engine_suggested(project_id=None, project_source=None),
    )
    assert out["budget"] == _budget_payload("no_project", None, project_id=None)


def test_access_denied_budget_null_suggestion_still_returned(mock_eli_service):
    with patch(
        "entities.coding_suggestion.business.budget_check.BudgetRepository"
    ), patch(
        "entities.coding_suggestion.business.budget_check.assert_can_access_project",
        side_effect=EntityNotAccessibleError("Project", PROJECT_ID),
    ):
        out = _run_engine_suggest(CodingSuggestionService())
    assert out["status"] == "suggested"
    assert out["budget"] is None


def test_repo_exception_budget_null_warning_has_no_amounts(
    mock_eli_service, caplog
):
    with patch(
        "entities.coding_suggestion.business.budget_check.BudgetRepository"
    ) as repo_cls:
        repo_cls.return_value.read_by_project_id.side_effect = RuntimeError("db down")
        with caplog.at_level(logging.WARNING):
            out = _run_engine_suggest(CodingSuggestionService())
    assert out["status"] == "suggested"
    assert out["budget"] is None
    joined = " ".join(caplog.messages)
    assert "1000" not in joined and "500" not in joined
    assert "RuntimeError" in joined or "budget failed" in joined


def test_stored_materialized_path_computes_fresh_budget(mock_budget_repo, mock_eli_service):
    svc = CodingSuggestionService()
    svc.repo = MagicMock()
    svc.repo.read_state_by_expense_ids.return_value = {
        5: [{"public_id": CS_PUB, "status": "suggested"}]
    }
    stored = _coding_item()
    svc.read_by_public_id = MagicMock(return_value=stored)
    with patch(
        "entities.expense.business.service.ExpenseService"
    ) as expense_svc_cls:
        expense_svc_cls.return_value.read_by_public_id.return_value = _expense()
        mock_eli_service.return_value.read_by_expense_id.return_value = [_line()]
        out = svc.suggest_for_expense(expense_public_id=EXPENSE_PUB, user_id=17)
    assert out["budget"] == _budget_payload("fits", "500.00")
    mock_budget_repo.return_value.read_by_project_id.assert_called()


def test_self_exclusion_increases_remaining_pre():
    """Line already on target SCC+project is excluded from spend via self_in_scc."""
    lines = [_line(amount="100.00")]
    without_self = compute_budget_check(
        project_id=PROJECT_ID,
        sub_cost_code_id=SCC_ID,
        variance_rows=[_variance_row()],
        lines=[],
        project_confident=True,
        amount_override=Decimal("100"),
    )
    with_self = compute_budget_check(
        project_id=PROJECT_ID,
        sub_cost_code_id=SCC_ID,
        variance_rows=[_variance_row()],
        lines=lines,
        project_confident=True,
    )
    assert without_self.remaining == Decimal("400.00")
    assert with_self.remaining == Decimal("500.00")


def test_no_coding_suggestion_table_writes_on_suggest(mock_eli_service):
    svc = CodingSuggestionService()
    _run_engine_suggest(svc)
    for name in (
        "upsert_for_bill",
        "upsert_from_queue",
        "record_suggestion",
        "record_flag",
        "record_confirmation",
    ):
        assert getattr(svc.repo, name).call_count == 0
