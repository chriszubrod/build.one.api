"""U-654b bite 2: bill text-tier read path + finalize (pure, mocked)."""
from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest

from entities.coding_suggestion.api.bill_router import (
    BillCodingSuggestRequest,
    suggest_bill_coding_router,
)
from entities.coding_suggestion.business.bill_suggestion_service import (
    TEXT_LLM_SUGGESTION_REASON,
    BillCodingSuggester,
    BillSuggestion,
    apply_bill_text_tier_to_suggestion,
    bill_suggestion_to_features_json,
    history_blocks_text_tier,
    record_bill_coding_suggestion_on_save,
)
from entities.coding_suggestion.persistence.bill_text_suggestion_repo import (
    BillLatestTextExtractionState,
    BillTextCodingSuggestionRow,
)
from shared.access import EntityNotAccessibleError

_EXTRACTED = datetime(2026, 5, 1, 12, 0, 0)
_CURRENT_USER = {"sub": "test"}


def _history_suggestion(**kwargs) -> BillSuggestion:
    defaults = dict(
        project_id=10,
        sub_cost_code_id=501,
        sub_cost_code_public_id="scc-pub",
        sub_cost_code_number="05.100",
        sub_cost_code_name="Framing",
        source="vendor_project_history",
        reason="history reason",
        confidence=Decimal("0.50"),
        raw_score=Decimal("0.50"),
        top_count=3,
        second_count=1,
        total_count=4,
    )
    defaults.update(kwargs)
    return BillSuggestion(**defaults)


def _cold_suggestion() -> BillSuggestion:
    return BillSuggestion(
        project_id=None,
        sub_cost_code_id=None,
        sub_cost_code_public_id=None,
        sub_cost_code_number=None,
        sub_cost_code_name=None,
        source="none",
        reason=None,
        confidence=None,
        raw_score=None,
        top_count=None,
        second_count=None,
        total_count=None,
    )


def _text_row(**kwargs) -> BillTextCodingSuggestionRow:
    defaults = dict(
        bill_id=7,
        attachment_id=1,
        extracted_datetime=_EXTRACTED,
        status="done",
        sub_cost_code_id=888,
        project_id=None,
        confidence=Decimal("0.82"),
        mode="A",
        winning_model="gpt-test",
        model_version="u654b-text-v1",
        failure_reason=None,
    )
    defaults.update(kwargs)
    return BillTextCodingSuggestionRow(**defaults)


def _extraction(
    *,
    latest: datetime | None = _EXTRACTED,
    attachment_id: int = 1,
    incomplete: bool = False,
) -> BillLatestTextExtractionState:
    att = attachment_id if latest is not None else None
    return BillLatestTextExtractionState(att, latest, incomplete)


def _mock_text_repo(
    text_row: BillTextCodingSuggestionRow | None,
    extraction: BillLatestTextExtractionState,
) -> MagicMock:
    repo = MagicMock()
    repo.read_by_bill_id.return_value = text_row
    repo.read_latest_text_extraction_state.return_value = extraction
    return repo


def _scc_service():
    svc = MagicMock()
    svc.read_by_id.return_value = SimpleNamespace(
        id=888,
        public_id="text-scc-pub",
        number="03.01",
        name="Text SCC",
    )
    svc.is_holding_code.return_value = False
    return svc


def test_history_blocks_at_sixty():
    strong = _history_suggestion(raw_score=Decimal("0.60"), confidence=Decimal("0.60"))
    assert history_blocks_text_tier(strong) is True
    weak = _history_suggestion(raw_score=Decimal("0.59"), confidence=Decimal("0.59"))
    assert history_blocks_text_tier(weak) is False


def test_flag_off_leaves_history_unchanged():
    history = _cold_suggestion()
    out, status = apply_bill_text_tier_to_suggestion(
        history,
        bill_id=7,
        request_project_id=None,
        text_enabled=False,
        text_repo=_mock_text_repo(_text_row(), _extraction()),
    )
    assert out is history
    assert status == "none"


def test_strong_history_wins_over_done_text():
    history = _history_suggestion(raw_score=Decimal("0.70"), confidence=Decimal("0.70"))
    repo = _mock_text_repo(_text_row(), _extraction())
    out, status = apply_bill_text_tier_to_suggestion(
        history,
        bill_id=7,
        request_project_id=10,
        text_enabled=True,
        text_repo=repo,
        sub_cost_code_service=_scc_service(),
    )
    assert out.source == "vendor_project_history"
    assert out.sub_cost_code_id == 501
    assert status == "none"


def test_cold_history_uses_done_text_llm():
    history = _cold_suggestion()
    out, status = apply_bill_text_tier_to_suggestion(
        history,
        bill_id=7,
        request_project_id=None,
        text_enabled=True,
        text_repo=_mock_text_repo(_text_row(), _extraction()),
        sub_cost_code_service=_scc_service(),
    )
    assert status == "ready"
    assert out.source == "llm"
    assert out.sub_cost_code_public_id == "text-scc-pub"
    assert out.reason == TEXT_LLM_SUGGESTION_REASON
    assert out.confidence == Decimal("0.82")
    assert out.model_version == "u654b-text-v1"


def test_weak_history_uses_done_text():
    history = _history_suggestion(raw_score=Decimal("0.59"), confidence=Decimal("0.59"))
    out, status = apply_bill_text_tier_to_suggestion(
        history,
        bill_id=7,
        request_project_id=10,
        text_enabled=True,
        text_repo=_mock_text_repo(_text_row(), _extraction()),
        sub_cost_code_service=_scc_service(),
    )
    assert status == "ready"
    assert out.source == "llm"
    assert out.sub_cost_code_id == 888


def test_stale_extraction_row_ignored_pending():
    history = _cold_suggestion()
    stale = _text_row(extracted_datetime=datetime(2026, 1, 1, 0, 0, 0))
    out, status = apply_bill_text_tier_to_suggestion(
        history,
        bill_id=7,
        request_project_id=None,
        text_enabled=True,
        text_repo=_mock_text_repo(stale, _extraction(latest=_EXTRACTED)),
        sub_cost_code_service=_scc_service(),
    )
    assert out.source == "none"
    assert status == "pending"


def test_pending_row_text_status_pending():
    history = _cold_suggestion()
    pending = _text_row(status="pending", sub_cost_code_id=None)
    out, status = apply_bill_text_tier_to_suggestion(
        history,
        bill_id=7,
        request_project_id=None,
        text_enabled=True,
        text_repo=_mock_text_repo(pending, _extraction()),
        sub_cost_code_service=_scc_service(),
    )
    assert out.source == "none"
    assert status == "pending"


def test_mode_b_project_when_accessible():
    history = _history_suggestion(
        project_id=20,
        predicted_project=True,
        raw_score=Decimal("0.40"),
        confidence=Decimal("0.40"),
    )
    row = _text_row(mode="B", project_id=99)
    repo = _mock_text_repo(row, _extraction())
    proj_svc = MagicMock()
    proj_svc.read_by_id.return_value = SimpleNamespace(id=99, public_id="p99", name="Nine")
    with patch(
        "entities.coding_suggestion.business.bill_suggestion_service.assert_can_access_project"
    ):
        out, status = apply_bill_text_tier_to_suggestion(
            history,
            bill_id=7,
            request_project_id=None,
            text_enabled=True,
            text_repo=repo,
            sub_cost_code_service=_scc_service(),
            project_service=proj_svc,
        )
    assert status == "ready"
    assert out.project_id == 99
    assert out.predicted_project is True


def test_mode_b_inaccessible_drops_history_predicted_project():
    history = _history_suggestion(
        project_id=20,
        predicted_project=True,
        raw_score=Decimal("0.40"),
        confidence=Decimal("0.40"),
    )
    row = _text_row(mode="B", project_id=99)
    repo = _mock_text_repo(row, _extraction())
    proj_svc = MagicMock()
    proj_svc.read_by_id.return_value = SimpleNamespace(id=99, public_id="p99", name="Nine")
    with patch(
        "entities.coding_suggestion.business.bill_suggestion_service.assert_can_access_project",
        side_effect=EntityNotAccessibleError("Project", 99),
    ):
        out, status = apply_bill_text_tier_to_suggestion(
            history,
            bill_id=7,
            request_project_id=None,
            text_enabled=True,
            text_repo=repo,
            sub_cost_code_service=_scc_service(),
            project_service=proj_svc,
        )
    assert status == "ready"
    assert out.project_id is None
    assert out.predicted_project is False
    assert out.sub_cost_code_id == 888


def test_text_read_exception_returns_history_none():
    history = _history_suggestion()
    repo = MagicMock()
    repo.read_by_bill_id.side_effect = RuntimeError("db")
    out, status = apply_bill_text_tier_to_suggestion(
        history,
        bill_id=7,
        request_project_id=None,
        text_enabled=True,
        text_repo=repo,
    )
    assert out is history
    assert status == "none"


def test_holding_text_scc_not_applied():
    history = _cold_suggestion()
    row = _text_row()
    repo = _mock_text_repo(row, _extraction())
    scc_svc = MagicMock()
    scc_svc.read_by_id.return_value = SimpleNamespace(
        id=888, public_id="p", number="00.02", name="Hold"
    )
    scc_svc.is_holding_code.return_value = True
    out, status = apply_bill_text_tier_to_suggestion(
        history,
        bill_id=7,
        request_project_id=None,
        text_enabled=True,
        text_repo=repo,
        sub_cost_code_service=scc_svc,
    )
    assert out.source == "none"
    assert status == "none"


def test_mode_b_project_dropped_when_inaccessible():
    history = _cold_suggestion()
    row = _text_row(mode="B", project_id=99)
    repo = _mock_text_repo(row, _extraction())
    proj_svc = MagicMock()
    proj_svc.read_by_id.return_value = SimpleNamespace(id=99, public_id="p99", name="Nine")
    with patch(
        "entities.coding_suggestion.business.bill_suggestion_service.assert_can_access_project",
        side_effect=EntityNotAccessibleError("Project", 99),
    ):
        out, status = apply_bill_text_tier_to_suggestion(
            history,
            bill_id=7,
            request_project_id=None,
            text_enabled=True,
            text_repo=repo,
            sub_cost_code_service=_scc_service(),
            project_service=proj_svc,
        )
    assert status == "ready"
    assert out.source == "llm"
    assert out.project_id is None
    assert out.predicted_project is False
    assert out.sub_cost_code_id == 888


def test_finalize_writes_llm_features_allowlisted():
    bill = SimpleNamespace(id=7, public_id="b7", vendor_id=3, memo=None)
    lines = [SimpleNamespace(project_id=None, coding_prefill_state=None)]
    history = _cold_suggestion()
    llm = BillSuggestion(
        project_id=None,
        sub_cost_code_id=888,
        sub_cost_code_public_id="text-scc-pub",
        sub_cost_code_number="03.01",
        sub_cost_code_name="Text SCC",
        source="llm",
        reason=TEXT_LLM_SUGGESTION_REASON,
        confidence=Decimal("0.82"),
        raw_score=None,
        top_count=None,
        second_count=None,
        total_count=None,
        model_version="u654b-text-v1",
        text_used=True,
        text_mode="A",
        text_model="gpt-test",
    )
    coding_svc = MagicMock()
    line_svc = MagicMock()
    line_svc.read_by_bill_id.return_value = lines
    suggester = MagicMock()
    suggester.suggest_with_text_tier.return_value = (llm, "ready")

    with patch(
        "entities.coding_suggestion.business.bill_suggestion_service.apply_budget_check_to_suggestion",
        side_effect=lambda s, *_a, **_k: s,
    ):
        record_bill_coding_suggestion_on_save(
            bill,
            suggester=suggester,
            coding_suggestion_service=coding_svc,
            line_item_service=line_svc,
        )

    kwargs = coding_svc.upsert_for_bill.call_args.kwargs
    assert kwargs["suggestion_source"] == "llm"
    assert kwargs["model_version"] == "u654b-text-v1"
    features = json.loads(kwargs["features_json"])
    assert features["text_used"] is True
    assert features["text_mode"] == "A"
    assert features["text_model"] == "gpt-test"
    assert "Suggested from" not in json.dumps(features)
    assert "Text SCC" not in json.dumps(features)


def test_finalize_strong_history_not_replaced():
    bill = SimpleNamespace(id=7, public_id="b7", vendor_id=3, memo=None)
    lines = [SimpleNamespace(project_id=10, coding_prefill_state="prefilled")]
    history = _history_suggestion(raw_score=Decimal("0.75"), confidence=Decimal("0.75"))
    coding_svc = MagicMock()
    line_svc = MagicMock()
    line_svc.read_by_bill_id.return_value = lines
    suggester = MagicMock()
    suggester.suggest_with_text_tier.return_value = (history, "ready")

    with patch(
        "entities.coding_suggestion.business.bill_suggestion_service.apply_budget_check_to_suggestion",
        side_effect=lambda s, *_a, **_k: s,
    ):
        record_bill_coding_suggestion_on_save(
            bill,
            suggester=suggester,
            coding_suggestion_service=coding_svc,
            line_item_service=line_svc,
        )

    kwargs = coding_svc.upsert_for_bill.call_args.kwargs
    assert kwargs["suggestion_source"] == "vendor_project_history"
    features = json.loads(kwargs["features_json"])
    assert "text_used" not in features


def test_router_exposes_text_status_on_llm_path():
    vendor = SimpleNamespace(id=5, public_id="v-pub")
    bill = SimpleNamespace(id=7, public_id="b-pub")
    llm = BillSuggestion(
        project_id=None,
        sub_cost_code_id=888,
        sub_cost_code_public_id="text-scc-pub",
        sub_cost_code_number="03.01",
        sub_cost_code_name="Text SCC",
        source="llm",
        reason=TEXT_LLM_SUGGESTION_REASON,
        confidence=Decimal("0.82"),
        raw_score=None,
        top_count=None,
        second_count=None,
        total_count=None,
        model_version="u654b-text-v1",
        text_status="ready",
    )
    with patch("entities.coding_suggestion.api.bill_router.VendorService") as VS:
        VS.return_value.read_by_public_id.return_value = vendor
        with patch("entities.coding_suggestion.api.bill_router.BillService") as BS:
            BS.return_value.read_by_public_id.return_value = bill
            with patch("entities.coding_suggestion.api.bill_router.BillLineItemService") as BLS:
                BLS.return_value.read_by_bill_id.return_value = []
                with patch("entities.coding_suggestion.api.bill_router.BillCodingSuggester") as Sugg:
                    Sugg.return_value.suggest_with_text_tier.return_value = (llm, "ready")
                    with patch(
                        "entities.coding_suggestion.api.bill_router.apply_budget_check_to_suggestion",
                        side_effect=lambda s, *_a, **_k: s,
                    ):
                        body = BillCodingSuggestRequest(
                            vendor_public_id=UUID("550e8400-e29b-41d4-a716-446655440001"),
                            bill_public_id=UUID("550e8400-e29b-41d4-a716-446655440002"),
                        )
                        payload = suggest_bill_coding_router(body=body, _=_CURRENT_USER)
    assert payload["data"]["source"] == "llm"
    assert payload["data"]["text_status"] == "ready"
    assert payload["data"]["reason"] == TEXT_LLM_SUGGESTION_REASON


def test_request_project_kept_when_mode_b_text_row():
    history = _history_suggestion(project_id=99)
    row = _text_row(mode="B", project_id=55)
    repo = _mock_text_repo(row, _extraction())
    scc = _scc_service()
    proj = MagicMock()
    proj.read_by_id.return_value = SimpleNamespace(id=55, public_id="p55")
    with patch(
        "entities.coding_suggestion.business.bill_suggestion_service.assert_can_access_project"
    ):
        out, status = apply_bill_text_tier_to_suggestion(
            history,
            bill_id=7,
            request_project_id=10,
            text_repo=repo,
            sub_cost_code_service=scc,
            project_service=proj,
            text_enabled=True,
        )
    assert out.project_id == 10
    assert status == "ready"


def test_router_passes_bill_id_to_suggester():
    vendor = SimpleNamespace(id=5, public_id="v-pub")
    bill = SimpleNamespace(id=7, public_id="b-pub")
    with patch("entities.coding_suggestion.api.bill_router.ProjectService") as PS:
        PS.return_value.read_by_id.return_value = SimpleNamespace(
            public_id="p-pub", name="P"
        )
        with patch("entities.coding_suggestion.api.bill_router.VendorService") as VS:
            VS.return_value.read_by_public_id.return_value = vendor
            with patch("entities.coding_suggestion.api.bill_router.BillService") as BS:
                BS.return_value.read_by_public_id.return_value = bill
                with patch(
                    "entities.coding_suggestion.api.bill_router.BillLineItemService"
                ) as BLS:
                    BLS.return_value.read_by_bill_id.return_value = []
                    with patch(
                        "entities.coding_suggestion.api.bill_router.BillCodingSuggester"
                    ) as Sugg:
                        Sugg.return_value.suggest_with_text_tier.return_value = (
                            _history_suggestion(),
                            "none",
                        )
                        with patch(
                            "entities.coding_suggestion.api.bill_router.apply_budget_check_to_suggestion",
                            side_effect=lambda s, *_a, **_k: s,
                        ):
                            body = BillCodingSuggestRequest(
                                vendor_public_id=UUID(
                                    "550e8400-e29b-41d4-a716-446655440001"
                                ),
                                bill_public_id=UUID(
                                    "550e8400-e29b-41d4-a716-446655440002"
                                ),
                            )
                            suggest_bill_coding_router(body=body, _=_CURRENT_USER)
                        kwargs = Sugg.return_value.suggest_with_text_tier.call_args.kwargs
                        assert kwargs["bill_id"] == 7
                        assert kwargs["exclude_bill_id"] == 7


def test_extraction_incomplete_pending_without_text_row():
    history = _cold_suggestion()
    repo = _mock_text_repo(None, _extraction(incomplete=True))
    out, status = apply_bill_text_tier_to_suggestion(
        history,
        bill_id=7,
        request_project_id=None,
        text_repo=repo,
        sub_cost_code_service=_scc_service(),
        text_enabled=True,
    )
    assert status == "pending"
    assert out.source == "none"


def test_memo_source_does_not_block_text_tier():
    memo = BillSuggestion(
        project_id=10,
        sub_cost_code_id=501,
        sub_cost_code_public_id="scc-pub",
        sub_cost_code_number="05.100",
        sub_cost_code_name="Framing",
        source="memo",
        reason="memo hint",
        confidence=Decimal("0.90"),
        raw_score=None,
        top_count=None,
        second_count=None,
        total_count=None,
    )
    row = _text_row(mode="A")
    repo = _mock_text_repo(row, _extraction())
    out, status = apply_bill_text_tier_to_suggestion(
        memo,
        bill_id=7,
        request_project_id=None,
        text_repo=repo,
        sub_cost_code_service=_scc_service(),
        text_enabled=True,
    )
    assert out.source == "llm"
    assert status == "ready"


def test_features_json_red_proof_break_text_used():
    """Guard: removing text_used from allowlist should fail test_finalize_writes_llm_features_allowlisted."""
    suggestion = BillSuggestion(
        project_id=None,
        sub_cost_code_id=1,
        sub_cost_code_public_id="p",
        sub_cost_code_number="1",
        sub_cost_code_name="N",
        source="llm",
        reason=TEXT_LLM_SUGGESTION_REASON,
        confidence=Decimal("0.5"),
        raw_score=None,
        top_count=None,
        second_count=None,
        total_count=None,
        model_version="u654b-text-v1",
        text_used=True,
        text_mode="B",
        text_model="m",
    )
    features = json.loads(bill_suggestion_to_features_json(suggestion))
    assert features.get("text_used") is True
