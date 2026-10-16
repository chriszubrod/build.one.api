"""U-655: budget cross-check for bill coding suggestions."""
from __future__ import annotations

import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import UUID

import pyodbc
import pytest
from pydantic import ValidationError

from entities.budget.business.model import Budget
from entities.coding_suggestion.api.bill_router import (
    BillCodingSuggestRequest,
    suggest_bill_coding_router,
)
from entities.coding_suggestion.business.bill_suggestion_service import (
    BillSuggestion,
    apply_budget_check_to_suggestion,
    bill_suggestion_to_features_json,
    record_bill_coding_suggestion_on_save,
)
from entities.coding_suggestion.business.budget_check import (
    FITS_HEADROOM,
    LOW_PROJECT_CONFIDENCE,
    BudgetCheckResult,
    compute_budget_check,
    evaluate_budget_for_suggestion,
    project_confident_for_suggestion,
)
from entities.bill.business.model import Bill
from shared.access import EntityNotAccessibleError

_CURRENT_USER = {"id": 17, "username": "tester", "tenant_id": 1}


def _line(*, project_id=10, sub_cost_code_id=501, amount="100.00"):
    return SimpleNamespace(
        project_id=project_id,
        sub_cost_code_id=sub_cost_code_id,
        amount=Decimal(amount),
        coding_prefill_state=None,
    )


def _variance_row(
    scc_id: int,
    *,
    budget_amount="1000.00",
    actual_cost="500.00",
    cost_variance=None,
):
    cv = cost_variance
    if cv is None:
        cv = Decimal(str(budget_amount)) - Decimal(str(actual_cost))
    return {
        "SubCostCodeId": scc_id,
        "BudgetAmount": Decimal(str(budget_amount)),
        "ActualCost": Decimal(str(actual_cost)),
        "CostVariance": Decimal(str(cv)),
    }


class TestComputeBudgetCheckPure:
    @pytest.mark.parametrize(
        "check,remaining,budget,actual,self_amt,bill_amt",
        [
            ("fits", Decimal("500.00"), "1000", "500", "100", "100"),
            ("tight", Decimal("50.00"), "1000", "500", "100", "550"),
            ("over", Decimal("-50.00"), "1000", "500", "100", "650"),
            ("unbudgeted", None, None, None, "0", "50"),
            ("no_project", None, None, None, "0", "50"),
        ],
    )
    def test_check_outcomes(
        self, check, remaining, budget, actual, self_amt, bill_amt
    ):
        if check == "no_project":
            result = compute_budget_check(
                project_id=10,
                sub_cost_code_id=501,
                variance_rows=[_variance_row(501)],
                lines=[],
                project_confident=False,
                amount_override=Decimal(bill_amt),
            )
        elif check == "unbudgeted":
            result = compute_budget_check(
                project_id=10,
                sub_cost_code_id=999,
                variance_rows=[_variance_row(501)],
                lines=[],
                project_confident=True,
                amount_override=Decimal(bill_amt),
            )
        else:
            rows = [_variance_row(501, budget_amount=budget, actual_cost=actual)]
            lines = [_line(amount=self_amt)] if Decimal(self_amt) > 0 else []
            result = compute_budget_check(
                project_id=10,
                sub_cost_code_id=501,
                variance_rows=rows,
                lines=lines,
                project_confident=True,
                amount_override=Decimal(bill_amt),
            )
        assert result.check == check
        assert result.remaining == remaining

    def test_null_scc_skips_check(self):
        result = compute_budget_check(
            project_id=10,
            sub_cost_code_id=None,
            variance_rows=[_variance_row(501)],
            lines=[_line()],
            project_confident=True,
        )
        assert result.check is None
        assert result.remaining is None
        assert result.evidence == {}

    def test_zero_budget_amount_is_unbudgeted(self):
        rows = [_variance_row(501, budget_amount="0", actual_cost="0", cost_variance="0")]
        result = compute_budget_check(
            project_id=10,
            sub_cost_code_id=501,
            variance_rows=rows,
            lines=[],
            project_confident=True,
            amount_override=Decimal("1"),
        )
        assert result.check == "unbudgeted"

    def test_fits_boundary_exactly_at_ninety_percent(self):
        # remaining_pre = 500 + 0 = 500; fits threshold = 450
        rows = [_variance_row(501, budget_amount="1000", actual_cost="500")]
        result = compute_budget_check(
            project_id=10,
            sub_cost_code_id=501,
            variance_rows=rows,
            lines=[],
            project_confident=True,
            amount_override=Decimal("450.00"),
        )
        assert result.check == "fits"
        assert result.remaining == Decimal("50.00")

    def test_one_cent_over_fits_becomes_tight(self):
        rows = [_variance_row(501, budget_amount="1000", actual_cost="500")]
        result = compute_budget_check(
            project_id=10,
            sub_cost_code_id=501,
            variance_rows=rows,
            lines=[],
            project_confident=True,
            amount_override=Decimal("450.01"),
        )
        assert result.check == "tight"

    def test_self_in_scc_added_to_remaining_pre(self):
        rows = [_variance_row(501, budget_amount="1000", actual_cost="600")]
        lines = [_line(amount="100")]
        result = compute_budget_check(
            project_id=10,
            sub_cost_code_id=501,
            variance_rows=rows,
            lines=lines,
            project_confident=True,
            amount_override=Decimal("450"),
        )
        assert result.evidence["self_in_scc"] == Decimal("100")
        assert result.evidence["remaining_pre"] == Decimal("500")
        assert result.check == "fits"

    def test_ignores_other_project_and_scc_lines(self):
        rows = [_variance_row(501, budget_amount="1000", actual_cost="500")]
        lines = [
            _line(project_id=10, sub_cost_code_id=501, amount="100"),
            _line(project_id=11, sub_cost_code_id=501, amount="999"),
            _line(project_id=10, sub_cost_code_id=502, amount="888"),
        ]
        result = compute_budget_check(
            project_id=10,
            sub_cost_code_id=501,
            variance_rows=rows,
            lines=lines,
            project_confident=True,
        )
        assert result.evidence["self_in_scc"] == Decimal("100")
        assert result.evidence["bill_amount"] == Decimal("100")
        assert result.check == "fits"
        assert result.remaining == Decimal("500.00")

    def test_decimal_only_no_float(self):
        rows = [_variance_row(501, budget_amount="1000.01", actual_cost="333.33")]
        result = compute_budget_check(
            project_id=10,
            sub_cost_code_id=501,
            variance_rows=rows,
            lines=[],
            project_confident=True,
            amount_override=Decimal("600.00"),
        )
        threshold = result.evidence["remaining_pre"] * FITS_HEADROOM
        assert isinstance(threshold, Decimal)
        assert result.check in ("fits", "tight", "over")


class TestProjectConfident:
    def test_chosen_project_confident_only_when_it_matches_suggestion(self):
        assert project_confident_for_suggestion(
            10, chosen_project_id=10, confidence=None
        )
        assert not project_confident_for_suggestion(
            20, chosen_project_id=10, confidence=None
        )

    @pytest.mark.parametrize(
        "confidence,expected",
        [
            (Decimal("0.59"), False),
            (Decimal("0.60"), True),
            (Decimal("0.95"), True),
        ],
    )
    def test_predicted_project_confidence_gate(self, confidence, expected):
        assert (
            project_confident_for_suggestion(
                10,
                chosen_project_id=None,
                confidence=confidence,
            )
            is expected
        )

    def test_memo_path_also_requires_confidence_gate(self):
        assert not project_confident_for_suggestion(
            10,
            chosen_project_id=None,
            confidence=Decimal("0.59"),
        )

    def test_project_confidence_separate_from_text_scc_confidence(self):
        """U-654b D2: high text SCC confidence must not satisfy a weak history project."""
        assert not project_confident_for_suggestion(
            10,
            chosen_project_id=None,
            confidence=Decimal("0.95"),
            project_confidence=Decimal("0.40"),
        )
        assert project_confident_for_suggestion(
            10,
            chosen_project_id=None,
            confidence=Decimal("0.40"),
            project_confidence=Decimal("0.65"),
        )


class TestEvaluateBudgetLoader:
    def test_draft_budget_yields_no_budget_without_variance_read(self):
        repo = MagicMock()
        repo.read_by_project_id.return_value = Budget(
            id=1,
            public_id="b",
            row_version="rv",
            created_datetime=None,
            modified_datetime=None,
            project_id=10,
            status="draft",
            notes=None,
        )
        with patch(
            "entities.coding_suggestion.business.budget_check.assert_can_access_project"
        ) as mock_access:
            result = evaluate_budget_for_suggestion(
                10,
                501,
                lines=[],
                project_confident=True,
                budget_repo=repo,
                amount_override=Decimal("25"),
            )
        mock_access.assert_called_once_with(10)
        repo.read_variance_by_project_id.assert_not_called()
        assert result.check == "no_budget"

    def test_access_asserted_before_variance_read(self):
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
        call_order = []

        def _access(pid):
            call_order.append("access")

        def _variance(*_a, **_k):
            call_order.append("variance")
            return [_variance_row(501)]

        repo.read_variance_by_project_id.side_effect = _variance
        with patch(
            "entities.coding_suggestion.business.budget_check.assert_can_access_project",
            side_effect=_access,
        ):
            evaluate_budget_for_suggestion(
                10,
                501,
                lines=[],
                project_confident=True,
                budget_repo=repo,
            )
        assert call_order == ["access", "variance"]

    def test_access_denied_does_not_call_variance(self):
        repo = MagicMock()
        with patch(
            "entities.coding_suggestion.business.budget_check.assert_can_access_project",
            side_effect=EntityNotAccessibleError("Project", 10),
        ):
            with pytest.raises(EntityNotAccessibleError):
                evaluate_budget_for_suggestion(
                    10,
                    501,
                    lines=[],
                    project_confident=True,
                    budget_repo=repo,
                )
        repo.read_by_project_id.assert_not_called()


class TestFeaturesAllowlist:
    def test_budget_fields_in_features_json(self):
        suggestion = BillSuggestion(
            project_id=10,
            sub_cost_code_id=501,
            sub_cost_code_public_id=None,
            sub_cost_code_number="05",
            sub_cost_code_name="Secret Name",
            source="vendor_history",
            reason="human text",
            confidence=Decimal("0.75"),
            raw_score=Decimal("0.75"),
            top_count=3,
            second_count=1,
            total_count=4,
            budget_check="fits",
            budget_remaining=Decimal("12.34"),
            budget_evidence={
                "budget_amount": Decimal("1000"),
                "remaining_pre": Decimal("500"),
            },
        )
        features = json.loads(bill_suggestion_to_features_json(suggestion))
        assert features["budget_check"] == "fits"
        assert features["budget_remaining"] == "12.34"
        assert features["budget_amount"] == "1000"
        assert "Secret Name" not in json.dumps(features)
        assert "reason" not in features


class TestSuggestEndpointBudget:
    def _base_suggestion(self, **kwargs):
        defaults = dict(
            project_id=10,
            sub_cost_code_id=501,
            sub_cost_code_public_id="scc",
            sub_cost_code_number="05",
            sub_cost_code_name="N",
            source="vendor_history",
            reason="r",
            confidence=Decimal("0.75"),
            raw_score=Decimal("0.75"),
            top_count=3,
            second_count=1,
            total_count=4,
        )
        defaults.update(kwargs)
        return BillSuggestion(**defaults)

    def test_amount_used_when_no_bill_lines(self):
        vendor = SimpleNamespace(id=1, public_id="v")
        project = SimpleNamespace(id=10, public_id="p", name="House")
        suggestion = self._base_suggestion()
        with patch("entities.coding_suggestion.api.bill_router.VendorService") as VS:
            VS.return_value.read_by_public_id.return_value = vendor
            with patch("entities.coding_suggestion.api.bill_router.ProjectService") as PS:
                PS.return_value.read_by_id.return_value = project
                with patch(
                    "entities.coding_suggestion.api.bill_router.BillCodingSuggester"
                ) as Sugg:
                    Sugg.return_value.suggest_with_text_tier.return_value = (
                            suggestion,
                            "none",
                        )
                    with patch(
                        "entities.coding_suggestion.api.bill_router.apply_budget_check_to_suggestion"
                    ) as apply_bc:
                        body = BillCodingSuggestRequest(
                            vendor_public_id=UUID(
                                "550e8400-e29b-41d4-a716-446655440001"
                            ),
                            amount=Decimal("250.00"),
                        )
                        suggest_bill_coding_router(body=body, _=_CURRENT_USER)
                        apply_bc.assert_called_once()
                        assert apply_bc.call_args.kwargs["amount_override"] == Decimal(
                            "250.00"
                        )

    def test_bill_path_loads_lines(self):
        vendor = SimpleNamespace(id=1, public_id="v")
        project = SimpleNamespace(id=10, public_id="p", name="House")
        bill = SimpleNamespace(id=5, public_id="b")
        lines = [_line()]
        suggestion = self._base_suggestion()
        with patch("entities.coding_suggestion.api.bill_router.VendorService") as VS:
            VS.return_value.read_by_public_id.return_value = vendor
            with patch("entities.coding_suggestion.api.bill_router.ProjectService") as PS:
                PS.return_value.read_by_id.return_value = project
                with patch("entities.coding_suggestion.api.bill_router.BillService") as BS:
                    BS.return_value.read_by_public_id.return_value = bill
                    with patch(
                        "entities.coding_suggestion.api.bill_router.BillLineItemService"
                    ) as BLS:
                        BLS.return_value.read_by_bill_id.return_value = lines
                        with patch(
                            "entities.coding_suggestion.api.bill_router.BillCodingSuggester"
                        ) as Sugg:
                            Sugg.return_value.suggest_with_text_tier.return_value = (
                            suggestion,
                            "none",
                        )
                            with patch(
                                "entities.coding_suggestion.api.bill_router.apply_budget_check_to_suggestion"
                            ) as apply_bc:
                                body = BillCodingSuggestRequest(
                                    vendor_public_id=UUID(
                                        "550e8400-e29b-41d4-a716-446655440001"
                                    ),
                                    bill_public_id=UUID(
                                        "550e8400-e29b-41d4-a716-446655440002"
                                    ),
                                )
                                suggest_bill_coding_router(body=body, _=_CURRENT_USER)
                                assert apply_bc.call_args.args[1] == lines
                                assert apply_bc.call_args.kwargs["amount_override"] is None

    def test_bill_path_amount_override_when_supplied(self):
        vendor = SimpleNamespace(id=1, public_id="v")
        project = SimpleNamespace(id=10, public_id="p", name="House")
        bill = SimpleNamespace(id=5, public_id="b")
        lines = [_line(amount="650.00")]
        suggestion = self._base_suggestion()
        with patch("entities.coding_suggestion.api.bill_router.VendorService") as VS:
            VS.return_value.read_by_public_id.return_value = vendor
            with patch("entities.coding_suggestion.api.bill_router.ProjectService") as PS:
                PS.return_value.read_by_id.return_value = project
                with patch("entities.coding_suggestion.api.bill_router.BillService") as BS:
                    BS.return_value.read_by_public_id.return_value = bill
                    with patch(
                        "entities.coding_suggestion.api.bill_router.BillLineItemService"
                    ) as BLS:
                        BLS.return_value.read_by_bill_id.return_value = lines
                        with patch(
                            "entities.coding_suggestion.api.bill_router.BillCodingSuggester"
                        ) as Sugg:
                            Sugg.return_value.suggest_with_text_tier.return_value = (
                            suggestion,
                            "none",
                        )
                            with patch(
                                "entities.coding_suggestion.api.bill_router.apply_budget_check_to_suggestion"
                            ) as apply_bc:
                                suggest_bill_coding_router(
                                    body=BillCodingSuggestRequest(
                                        vendor_public_id=UUID(
                                            "550e8400-e29b-41d4-a716-446655440001"
                                        ),
                                        bill_public_id=UUID(
                                            "550e8400-e29b-41d4-a716-446655440002"
                                        ),
                                        amount=Decimal("99.00"),
                                    ),
                                    _=_CURRENT_USER,
                                )
                                assert apply_bc.call_args.kwargs["amount_override"] == Decimal(
                                    "99.00"
                                )

    def test_budget_failure_isolated(self):
        vendor = SimpleNamespace(id=1, public_id="v")
        project = SimpleNamespace(id=10, public_id="p", name="House")
        suggestion = self._base_suggestion(budget_check=None, budget_remaining=None)
        with patch("entities.coding_suggestion.api.bill_router.VendorService") as VS:
            VS.return_value.read_by_public_id.return_value = vendor
            with patch("entities.coding_suggestion.api.bill_router.ProjectService") as PS:
                PS.return_value.read_by_id.return_value = project
                with patch(
                    "entities.coding_suggestion.api.bill_router.BillCodingSuggester"
                ) as Sugg:
                    Sugg.return_value.suggest_with_text_tier.return_value = (
                            suggestion,
                            "none",
                        )
                    with patch(
                        "entities.coding_suggestion.api.bill_router.apply_budget_check_to_suggestion",
                        side_effect=RuntimeError("db down"),
                    ):
                        payload = suggest_bill_coding_router(
                            body=BillCodingSuggestRequest(
                                vendor_public_id=UUID(
                                    "550e8400-e29b-41d4-a716-446655440001"
                                ),
                            ),
                            _=_CURRENT_USER,
                        )
        assert payload["data"]["budget_check"] is None
        assert payload["data"]["budget_remaining"] is None

    def test_negative_amount_rejected(self):
        with pytest.raises(ValidationError):
            BillCodingSuggestRequest(
                vendor_public_id=UUID("550e8400-e29b-41d4-a716-446655440001"),
                amount=Decimal("-1"),
            )


class TestRecordOnSaveBudget:
    def test_upsert_includes_budget_fields_when_set(self):
        bill = Bill(
            id=7,
            public_id="b7",
            row_version="rv",
            created_datetime=None,
            modified_datetime=None,
            vendor_id=3,
            payment_term_id=None,
            bill_date=None,
            due_date=None,
            bill_number=None,
            total_amount=None,
            memo=None,
            is_draft=False,
            status="completed",
        )
        lines = [_line()]
        suggestion = BillSuggestion(
            project_id=10,
            sub_cost_code_id=501,
            sub_cost_code_public_id=None,
            sub_cost_code_number="05",
            sub_cost_code_name="N",
            source="vendor_history",
            reason="r",
            confidence=Decimal("0.5"),
            raw_score=Decimal("0.5"),
            top_count=3,
            second_count=1,
            total_count=4,
            budget_check="tight",
            budget_remaining=Decimal("1.00"),
        )
        coding_svc = MagicMock()
        line_svc = MagicMock()
        line_svc.read_by_bill_id.return_value = lines
        suggester = MagicMock()
        suggester.suggest_with_text_tier.return_value = (suggestion, "none")

        with patch(
            "entities.coding_suggestion.business.bill_suggestion_service.apply_budget_check_to_suggestion",
            return_value=suggestion,
        ) as apply_bc:
            record_bill_coding_suggestion_on_save(
                bill,
                suggester=suggester,
                coding_suggestion_service=coding_svc,
                line_item_service=line_svc,
            )
            apply_bc.assert_called_once()

        kwargs = coding_svc.upsert_for_bill.call_args.kwargs
        assert kwargs["budget_check"] == "tight"
        assert kwargs["budget_remaining"] == Decimal("1.00")


class TestRepoBudgetParams:
    def test_upsert_omits_budget_params_when_none(self, monkeypatch):
        captured = {}

        def fake_call_procedure(*, cursor, name, params):
            captured["params"] = params

        monkeypatch.setattr(
            "entities.coding_suggestion.persistence.repo.call_procedure",
            fake_call_procedure,
        )
        monkeypatch.setattr(
            "entities.coding_suggestion.persistence.repo.get_connection",
            lambda: MagicMock(
                __enter__=lambda s: s,
                __exit__=lambda *a: None,
                cursor=lambda: MagicMock(fetchone=lambda: None),
            ),
        )
        from entities.coding_suggestion.persistence.repo import CodingSuggestionRepository

        CodingSuggestionRepository().upsert_for_bill(bill_id=1)
        assert "BudgetCheck" not in captured["params"]
        assert "BudgetRemaining" not in captured["params"]

    def test_upsert_sends_budget_params_when_set(self, monkeypatch):
        captured = {}

        def fake_call_procedure(*, cursor, name, params):
            captured["params"] = params

        monkeypatch.setattr(
            "entities.coding_suggestion.persistence.repo.call_procedure",
            fake_call_procedure,
        )
        monkeypatch.setattr(
            "entities.coding_suggestion.persistence.repo.get_connection",
            lambda: MagicMock(
                __enter__=lambda s: s,
                __exit__=lambda *a: None,
                cursor=lambda: MagicMock(fetchone=lambda: None),
            ),
        )
        from entities.coding_suggestion.persistence.repo import CodingSuggestionRepository

        CodingSuggestionRepository().upsert_for_bill(
            bill_id=1,
            budget_check="over",
            budget_remaining=Decimal("-10.00"),
        )
        assert captured["params"]["BudgetCheck"] == "over"
        assert captured["params"]["BudgetRemaining"] == Decimal("-10.00")


class TestR1RegressionFixes:
    def test_r1_1_other_scc_lines_do_not_inflate_bill_amount(self):
        rows = [_variance_row(501, budget_amount="1000", actual_cost="500")]
        lines = [
            _line(project_id=10, sub_cost_code_id=501, amount="100"),
            _line(project_id=10, sub_cost_code_id=502, amount="888"),
        ]
        result = compute_budget_check(
            project_id=10,
            sub_cost_code_id=501,
            variance_rows=rows,
            lines=lines,
            project_confident=True,
        )
        assert result.check == "fits"
        assert result.remaining == Decimal("500.00")
        assert result.evidence["bill_amount"] == Decimal("100")

    def test_u655b_upsert_rejected_budget_params_fail_loudly(self, monkeypatch):
        """U-655b: no silent retry without the budget params — a proc that lacks
        them is a deploy-order defect and must surface, not drop budget data."""
        calls = []

        def fake_call_procedure(*, cursor, name, params):
            calls.append(dict(params))
            raise pyodbc.Error(
                "42000",
                "[42000] [Microsoft][ODBC Driver 18 for SQL Server]"
                "[SQL Server]@BudgetCheck is not a parameter for procedure "
                "'UpsertCodingSuggestionForBill'. (8145)",
            )

        monkeypatch.setattr(
            "entities.coding_suggestion.persistence.repo.call_procedure",
            fake_call_procedure,
        )
        monkeypatch.setattr(
            "entities.coding_suggestion.persistence.repo.get_connection",
            lambda: MagicMock(
                __enter__=lambda s: s,
                __exit__=lambda *a: None,
                cursor=lambda: MagicMock(fetchone=lambda: None),
            ),
        )
        from entities.coding_suggestion.persistence.repo import CodingSuggestionRepository
        from shared.database import DatabaseOperationError

        with pytest.raises(DatabaseOperationError):
            CodingSuggestionRepository().upsert_for_bill(bill_id=1, budget_check="no_budget")
        assert len(calls) == 1
        assert calls[0]["BudgetCheck"] == "no_budget"

    def test_r1_2_upsert_does_not_retry_other_sql_errors(self, monkeypatch):
        def fake_call_procedure(*, cursor, name, params):
            raise pyodbc.Error("42000", "[SQL Server]Some other failure (50000)")

        monkeypatch.setattr(
            "entities.coding_suggestion.persistence.repo.call_procedure",
            fake_call_procedure,
        )
        monkeypatch.setattr(
            "entities.coding_suggestion.persistence.repo.get_connection",
            lambda: MagicMock(
                __enter__=lambda s: s,
                __exit__=lambda *a: None,
                cursor=lambda: MagicMock(fetchone=lambda: None),
            ),
        )
        from entities.coding_suggestion.persistence.repo import CodingSuggestionRepository
        from shared.database import DatabaseOperationError

        with pytest.raises(DatabaseOperationError):
            CodingSuggestionRepository().upsert_for_bill(
                bill_id=1,
                budget_check="fits",
            )

    def test_r1_3_low_confidence_predicted_not_bypassed_by_unrelated_chosen(self):
        assert not project_confident_for_suggestion(
            20,
            chosen_project_id=10,
            confidence=Decimal("0.40"),
        )

    def test_r1_3_router_passes_chosen_only_from_request_project(self):
        vendor = SimpleNamespace(id=1, public_id="v")
        project = SimpleNamespace(id=10, public_id="p", name="House")
        bill = SimpleNamespace(id=5, public_id="b")
        lines = [_line(project_id=10)]
        suggestion = BillSuggestion(
            project_id=20,
            sub_cost_code_id=501,
            sub_cost_code_public_id="scc",
            sub_cost_code_number="05",
            sub_cost_code_name="N",
            source="vendor_history",
            reason="r",
            confidence=Decimal("0.40"),
            raw_score=Decimal("0.40"),
            top_count=3,
            second_count=1,
            total_count=4,
            predicted_project=True,
        )
        with patch("entities.coding_suggestion.api.bill_router.VendorService") as VS:
            VS.return_value.read_by_public_id.return_value = vendor
            with patch("entities.coding_suggestion.api.bill_router.ProjectService") as PS:
                PS.return_value.read_by_id.return_value = project
                with patch("entities.coding_suggestion.api.bill_router.BillService") as BS:
                    BS.return_value.read_by_public_id.return_value = bill
                    with patch(
                        "entities.coding_suggestion.api.bill_router.BillLineItemService"
                    ) as BLS:
                        BLS.return_value.read_by_bill_id.return_value = lines
                        with patch(
                            "entities.coding_suggestion.api.bill_router.BillCodingSuggester"
                        ) as Sugg:
                            Sugg.return_value.suggest_with_text_tier.return_value = (
                            suggestion,
                            "none",
                        )
                            with patch(
                                "entities.coding_suggestion.api.bill_router.apply_budget_check_to_suggestion"
                            ) as apply_bc:
                                suggest_bill_coding_router(
                                    body=BillCodingSuggestRequest(
                                        vendor_public_id=UUID(
                                            "550e8400-e29b-41d4-a716-446655440001"
                                        ),
                                        bill_public_id=UUID(
                                            "550e8400-e29b-41d4-a716-446655440002"
                                        ),
                                    ),
                                    _=_CURRENT_USER,
                                )
                                assert apply_bc.call_args.kwargs["chosen_project_id"] is None

    def test_r1_4_budget_read_failure_still_upserts_suggestion(self):
        bill = Bill(
            id=7,
            public_id="b7",
            row_version="rv",
            created_datetime=None,
            modified_datetime=None,
            vendor_id=3,
            payment_term_id=None,
            bill_date=None,
            due_date=None,
            bill_number=None,
            total_amount=None,
            memo=None,
            is_draft=False,
            status="completed",
        )
        lines = [_line()]
        suggestion = BillSuggestion(
            project_id=10,
            sub_cost_code_id=501,
            sub_cost_code_public_id=None,
            sub_cost_code_number="05",
            sub_cost_code_name="N",
            source="vendor_history",
            reason="r",
            confidence=Decimal("0.75"),
            raw_score=Decimal("0.75"),
            top_count=3,
            second_count=1,
            total_count=4,
        )
        coding_svc = MagicMock()
        line_svc = MagicMock()
        line_svc.read_by_bill_id.return_value = lines
        suggester = MagicMock()
        suggester.suggest_with_text_tier.return_value = (suggestion, "none")

        with patch(
            "entities.coding_suggestion.business.bill_suggestion_service.apply_budget_check_to_suggestion",
            side_effect=RuntimeError("variance read failed"),
        ):
            record_bill_coding_suggestion_on_save(
                bill,
                suggester=suggester,
                coding_suggestion_service=coding_svc,
                line_item_service=line_svc,
            )

        coding_svc.upsert_for_bill.assert_called_once()
        kwargs = coding_svc.upsert_for_bill.call_args.kwargs
        assert "budget_check" not in kwargs
        assert "budget_remaining" not in kwargs

    def test_r1_5_invalid_amount_rejected_by_validation(self):
        with pytest.raises(ValidationError):
            BillCodingSuggestRequest(
                vendor_public_id=UUID("550e8400-e29b-41d4-a716-446655440001"),
                amount="not-a-number",
            )


class TestR2RegressionFixes:
    def test_r2_1_uncoded_line_on_target_project_in_bill_amount(self):
        rows = [_variance_row(501, budget_amount="1000", actual_cost="500")]
        lines = [
            _line(project_id=10, sub_cost_code_id=None, amount="650.00"),
        ]
        result = compute_budget_check(
            project_id=10,
            sub_cost_code_id=501,
            variance_rows=rows,
            lines=lines,
            project_confident=True,
        )
        assert result.evidence["self_in_scc"] == Decimal("0")
        assert result.evidence["bill_amount"] == Decimal("650.00")
        assert result.evidence["remaining_pre"] == Decimal("500.00")
        assert result.check == "over"
        assert result.remaining == Decimal("-150.00")

    def test_r2_1_null_amount_line_counts_as_zero_in_bill_amount(self):
        rows = [_variance_row(501, budget_amount="1000", actual_cost="500")]
        lines = [
            _line(project_id=10, sub_cost_code_id=None, amount="100.00"),
            SimpleNamespace(
                project_id=10,
                sub_cost_code_id=None,
                amount=None,
                coding_prefill_state=None,
            ),
        ]
        result = compute_budget_check(
            project_id=10,
            sub_cost_code_id=501,
            variance_rows=rows,
            lines=lines,
            project_confident=True,
        )
        assert result.evidence["bill_amount"] == Decimal("100.00")

    def test_r2_2_line_derived_bill_amount_without_request_amount(self):
        rows = [_variance_row(501, budget_amount="1000", actual_cost="500")]
        lines = [
            _line(project_id=10, sub_cost_code_id=501, amount="100.00"),
            _line(project_id=10, sub_cost_code_id=None, amount="50.00"),
            _line(project_id=10, sub_cost_code_id=502, amount="999.00"),
        ]
        result = compute_budget_check(
            project_id=10,
            sub_cost_code_id=501,
            variance_rows=rows,
            lines=lines,
            project_confident=True,
        )
        assert result.evidence["bill_amount"] == Decimal("150.00")

    def test_r2_2_request_amount_overrides_line_derived_sum(self):
        rows = [_variance_row(501, budget_amount="1000", actual_cost="500")]
        lines = [_line(project_id=10, sub_cost_code_id=None, amount="650.00")]
        result = compute_budget_check(
            project_id=10,
            sub_cost_code_id=501,
            variance_rows=rows,
            lines=lines,
            project_confident=True,
            amount_override=Decimal("200.00"),
        )
        assert result.evidence["bill_amount"] == Decimal("200.00")
        assert result.check == "fits"

    @pytest.mark.parametrize(
        "amount",
        [
            "NaN",
            "nan",
            "Infinity",
            "infinity",
            "-Infinity",
            "-infinity",
            float("nan"),
            float("inf"),
            float("-inf"),
            Decimal("NaN"),
        ],
    )
    def test_r2_3_non_finite_amount_rejected(self, amount):
        with pytest.raises(ValidationError):
            BillCodingSuggestRequest(
                vendor_public_id=UUID("550e8400-e29b-41d4-a716-446655440001"),
                amount=amount,
            )

    def test_r2_4_save_uses_chosen_project_from_lines_for_budget(self):
        bill = Bill(
            id=7,
            public_id="b7",
            row_version="rv",
            created_datetime=None,
            modified_datetime=None,
            vendor_id=3,
            payment_term_id=None,
            bill_date=None,
            due_date=None,
            bill_number=None,
            total_amount=None,
            memo=None,
            is_draft=False,
            status="completed",
        )
        lines = [_line(project_id=10, sub_cost_code_id=501, amount="100.00")]
        suggestion = BillSuggestion(
            project_id=10,
            sub_cost_code_id=501,
            sub_cost_code_public_id=None,
            sub_cost_code_number="05",
            sub_cost_code_name="N",
            source="vendor_history",
            reason="r",
            confidence=Decimal("0.50"),
            raw_score=Decimal("0.50"),
            top_count=3,
            second_count=1,
            total_count=4,
        )
        coding_svc = MagicMock()
        line_svc = MagicMock()
        line_svc.read_by_bill_id.return_value = lines
        suggester = MagicMock()
        suggester.suggest_with_text_tier.return_value = (suggestion, "none")

        budget_result = BudgetCheckResult(
            check="fits",
            remaining=Decimal("500.00"),
            evidence={"bill_amount": Decimal("100"), "remaining_pre": Decimal("600")},
        )
        with patch(
            "entities.coding_suggestion.business.bill_suggestion_service.apply_budget_check_to_suggestion",
            wraps=apply_budget_check_to_suggestion,
        ) as apply_bc:
            with patch(
                "entities.coding_suggestion.business.bill_suggestion_service.evaluate_budget_for_suggestion",
                return_value=budget_result,
            ):
                record_bill_coding_suggestion_on_save(
                    bill,
                    suggester=suggester,
                    coding_suggestion_service=coding_svc,
                    line_item_service=line_svc,
                )
        assert apply_bc.call_args.kwargs["chosen_project_id"] == 10
        kwargs = coding_svc.upsert_for_bill.call_args.kwargs
        assert kwargs["budget_check"] == "fits"
        assert kwargs["budget_remaining"] == Decimal("500.00")


class TestR2EmHardening:
    """EM-added (round 2): r2_2/r2_3 reproduced at the layer the bug lived on."""

    def test_r2_2_router_forwards_request_amount_when_lines_exist(self):
        vendor = SimpleNamespace(id=1, public_id="v")
        project = SimpleNamespace(id=10, public_id="p", name="House")
        bill = SimpleNamespace(id=5, public_id="b")
        lines = [_line(project_id=10, sub_cost_code_id=None, amount="650.00")]
        suggestion = BillSuggestion(
            project_id=10,
            sub_cost_code_id=501,
            sub_cost_code_public_id="scc",
            sub_cost_code_number="05",
            sub_cost_code_name="N",
            source="vendor_history",
            reason="r",
            confidence=Decimal("0.80"),
            raw_score=Decimal("0.80"),
            top_count=4,
            second_count=0,
            total_count=4,
            predicted_project=False,
        )
        with patch("entities.coding_suggestion.api.bill_router.VendorService") as VS, \
                patch("entities.coding_suggestion.api.bill_router.ProjectService") as PS, \
                patch("entities.coding_suggestion.api.bill_router.BillService") as BS, \
                patch("entities.coding_suggestion.api.bill_router.BillLineItemService") as BLS, \
                patch("entities.coding_suggestion.api.bill_router.BillCodingSuggester") as Sugg, \
                patch("entities.coding_suggestion.api.bill_router.apply_budget_check_to_suggestion") as apply_bc:
            VS.return_value.read_by_public_id.return_value = vendor
            PS.return_value.read_by_id.return_value = project
            BS.return_value.read_by_public_id.return_value = bill
            BLS.return_value.read_by_bill_id.return_value = lines
            Sugg.return_value.suggest_with_text_tier.return_value = (
                            suggestion,
                            "none",
                        )
            suggest_bill_coding_router(
                body=BillCodingSuggestRequest(
                    vendor_public_id=UUID("550e8400-e29b-41d4-a716-446655440001"),
                    bill_public_id=UUID("550e8400-e29b-41d4-a716-446655440002"),
                    amount="200.00",
                ),
                _=_CURRENT_USER,
            )
        assert apply_bc.call_args.kwargs["amount_override"] == Decimal("200.00")
