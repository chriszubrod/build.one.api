"""U-655c: optional SubCostCodeId filter on ReadBudgetVarianceByProjectId."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

from entities.budget.business.model import Budget
from entities.budget.persistence.repo import BudgetRepository
from entities.coding_suggestion.business.budget_check import evaluate_budget_for_suggestion


def _variance_row(scc_id: int) -> dict:
    return {
        "SubCostCodeId": scc_id,
        "BudgetAmount": Decimal("1000"),
        "ActualCost": Decimal("200"),
        "CostVariance": Decimal("800"),
    }


class TestBudgetRepositoryVarianceFilter:
    @patch("entities.budget.persistence.repo.call_procedure")
    @patch("entities.budget.persistence.repo.get_connection")
    def test_sub_cost_code_id_omitted_when_none(self, mock_get_connection, mock_call_procedure):
        cursor = MagicMock()
        cursor.description = [("SubCostCodeId",)]
        cursor.fetchall.return_value = []
        conn = MagicMock()
        conn.cursor.return_value = cursor
        mock_get_connection.return_value.__enter__.return_value = conn

        BudgetRepository().read_variance_by_project_id(10, budget_id=1)

        mock_call_procedure.assert_called_once()
        params = mock_call_procedure.call_args.kwargs["params"]
        assert "SubCostCodeId" not in params

    @patch("entities.budget.persistence.repo.call_procedure")
    @patch("entities.budget.persistence.repo.get_connection")
    def test_sub_cost_code_id_sent_when_set(self, mock_get_connection, mock_call_procedure):
        cursor = MagicMock()
        cursor.description = [("SubCostCodeId",)]
        cursor.fetchall.return_value = []
        conn = MagicMock()
        conn.cursor.return_value = cursor
        mock_get_connection.return_value.__enter__.return_value = conn

        BudgetRepository().read_variance_by_project_id(10, budget_id=1, sub_cost_code_id=501)

        params = mock_call_procedure.call_args.kwargs["params"]
        assert params["SubCostCodeId"] == 501


class TestBudgetCheckPassesSccFilter:
    def test_evaluate_passes_sub_cost_code_id_to_variance_read(self):
        repo = MagicMock()
        repo.read_by_project_id.return_value = Budget(
            id=2,
            public_id="b2",
            row_version="rv",
            created_datetime=None,
            modified_datetime=None,
            project_id=10,
            status="active",
            notes=None,
        )
        repo.read_variance_by_project_id.return_value = [_variance_row(501)]

        with patch(
            "entities.coding_suggestion.business.budget_check.assert_can_access_project"
        ):
            evaluate_budget_for_suggestion(
                10,
                501,
                lines=[],
                project_confident=True,
                budget_repo=repo,
            )

        repo.read_variance_by_project_id.assert_called_once()
        _, kwargs = repo.read_variance_by_project_id.call_args
        assert kwargs["sub_cost_code_id"] == 501
