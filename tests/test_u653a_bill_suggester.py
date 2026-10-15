"""U-653a BITE 2: bill coding suggester, endpoint, record-on-save."""
from __future__ import annotations

import inspect
import json
import logging
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest
from fastapi.params import Depends as DependsParam
from pydantic import ValidationError

from entities.bill.business.model import Bill
from entities.bill.business.service import BillService
from entities.coding_suggestion.api.bill_router import (
    suggest_bill_coding_router,
    BillCodingSuggestRequest,
)
from entities.coding_suggestion.business.bill_suggestion_service import (
    MODEL_VERSION,
    BillCodingSuggester,
    BillSuggestion,
    bill_suggestion_to_features_json,
    chosen_project_id_from_lines,
    record_bill_coding_suggestion_on_save,
    rollup_prefill_state,
)
from entities.coding_suggestion.business.hint_extractor import extract_bill_hints, extract_hints
from entities.review.business.service import ReviewService
from entities.coding_suggestion.business.model import (
    VendorDominantProjectForBills,
    VendorDominantSubCostCodeForBills,
)
from shared.access import EntityNotAccessibleError
from shared.rbac_constants import Modules

_CURRENT_USER = {"id": 17, "username": "tester", "tenant_id": 1}


def _bill(**overrides):
    base = dict(
        id=1,
        public_id="b",
        row_version="rv",
        created_datetime=None,
        modified_datetime=None,
        vendor_id=1,
        payment_term_id=None,
        bill_date=None,
        due_date=None,
        bill_number=None,
        total_amount=None,
        memo=None,
        is_draft=True,
        status="draft",
    )
    base.update(overrides)
    return Bill(**base)


_HISTORY_HIT = VendorDominantSubCostCodeForBills(
    top_sub_cost_code_id=501,
    top_count=3,
    second_count=1,
    total_count=4,
    public_id="11111111-1111-1111-1111-000000000501",
    number="05.100",
    name="Framing",
)


def _mock_coding_svc(**methods):
    svc = MagicMock()
    for name, value in methods.items():
        setattr(svc, name, MagicMock(return_value=value) if not callable(value) else value)
    return svc


class TestBillCodingSuggesterResolution:
    def test_chosen_project_uses_vendor_project_history_first(self):
        coding_svc = _mock_coding_svc(
            read_vendor_dominant_sub_cost_code_for_bills=_HISTORY_HIT,
            read_vendor_dominant_project_for_bills=None,
        )
        suggester = BillCodingSuggester(coding_suggestion_service=coding_svc)
        result = suggester.suggest(vendor_id=9, project_id=42)

        coding_svc.read_vendor_dominant_sub_cost_code_for_bills.assert_called_once_with(
            9, project_id=42, as_of=None, exclude_bill_id=None
        )
        coding_svc.read_vendor_dominant_project_for_bills.assert_not_called()
        assert result.project_id == 42
        assert result.source == "vendor_project_history"
        assert result.sub_cost_code_id == 501

    def test_chosen_project_never_overridden_when_vendor_only_wins(self):
        vendor_only = VendorDominantSubCostCodeForBills(
            top_sub_cost_code_id=777,
            top_count=3,
            second_count=0,
            total_count=3,
            public_id=None,
            number="02.010",
            name="Demo",
        )

        def _read_scc(vendor_id, *, project_id=None, as_of=None, exclude_bill_id=None):
            if project_id == 42:
                return None
            if project_id is None:
                return vendor_only
            return None

        coding_svc = MagicMock()
        coding_svc.read_vendor_dominant_sub_cost_code_for_bills.side_effect = _read_scc
        coding_svc.read_vendor_dominant_project_for_bills.return_value = None

        result = BillCodingSuggester(coding_suggestion_service=coding_svc).suggest(
            vendor_id=1, project_id=42
        )
        assert result.project_id == 42
        assert result.source == "vendor_history"
        assert result.sub_cost_code_id == 777

    def test_predicts_project_then_vendor_project_history(self):
        coding_svc = MagicMock()
        coding_svc.read_vendor_dominant_project_for_bills.return_value = (
            VendorDominantProjectForBills(
                top_project_id=88, top_count=5, second_count=1, total_count=6
            )
        )
        coding_svc.read_vendor_dominant_sub_cost_code_for_bills.side_effect = [
            _HISTORY_HIT,
        ]

        result = BillCodingSuggester(coding_suggestion_service=coding_svc).suggest(vendor_id=3)
        assert result.project_id == 88
        assert result.source == "vendor_project_history"
        coding_svc.read_vendor_dominant_sub_cost_code_for_bills.assert_called_with(
            3, project_id=88, as_of=None, exclude_bill_id=None
        )

    def test_inaccessible_predicted_project_falls_through_vendor_only_no_project(self):
        coding_svc = MagicMock()
        coding_svc.read_vendor_dominant_project_for_bills.return_value = None
        coding_svc.read_vendor_dominant_sub_cost_code_for_bills.return_value = _HISTORY_HIT

        result = BillCodingSuggester(coding_suggestion_service=coding_svc).suggest(vendor_id=3)
        assert result.project_id is None
        assert result.source == "vendor_history"

    def test_vendor_only_history_rung(self):
        coding_svc = MagicMock()
        coding_svc.read_vendor_dominant_project_for_bills.return_value = None
        coding_svc.read_vendor_dominant_sub_cost_code_for_bills.return_value = _HISTORY_HIT

        result = BillCodingSuggester(coding_suggestion_service=coding_svc).suggest(vendor_id=2)
        assert result.source == "vendor_history"
        assert result.sub_cost_code_number == "05.100"

    def test_bill_memo_scans_leading_project_code_segment(self):
        coding_svc = MagicMock()
        coding_svc.read_vendor_dominant_project_for_bills.return_value = None
        coding_svc.read_vendor_dominant_sub_cost_code_for_bills.return_value = None
        project_svc = MagicMock()
        project_svc.read_by_abbreviation.return_value = SimpleNamespace(id=12, public_id="p")
        sub_svc = MagicMock()
        sub_svc.find_for_reply.return_value = [
            {"sub_cost_code": {"id": 901, "number": "1.01", "name": "Mob"}, "confidence": 0.9}
        ]
        with patch(
            "entities.coding_suggestion.business.bill_suggestion_service.assert_can_access_project"
        ):
            result = BillCodingSuggester(
                coding_suggestion_service=coding_svc,
                project_service=project_svc,
                sub_cost_code_service=sub_svc,
            ).suggest(vendor_id=1, memo="HE12 - 1.01 - materials")
        assert result.source == "memo"
        assert result.project_id == 12
        assert result.sub_cost_code_id == 901

    def test_bill_memo_scans_leading_scc_segment(self):
        hints = extract_bill_hints("1.01 - materials")
        assert hints["scc_hint"] == "1.01"
        assert extract_hints("1.01 - materials")["scc_hint"] is None

    def test_chosen_project_without_memo_or_history_is_none_not_memo(self):
        coding_svc = MagicMock()
        coding_svc.read_vendor_dominant_sub_cost_code_for_bills.return_value = None
        coding_svc.read_vendor_dominant_project_for_bills.return_value = None
        result = BillCodingSuggester(coding_suggestion_service=coding_svc).suggest(
            vendor_id=1, project_id=42, memo="unrelated note"
        )
        assert result.source == "none"
        assert result.project_id == 42
        assert result.sub_cost_code_id is None
        assert result.confidence is None

    def test_memo_confidence_is_minimum_of_matched_signals(self):
        coding_svc = MagicMock()
        coding_svc.read_vendor_dominant_project_for_bills.return_value = None
        coding_svc.read_vendor_dominant_sub_cost_code_for_bills.return_value = None
        project_svc = MagicMock()
        project_svc.read_by_abbreviation.return_value = SimpleNamespace(id=12)
        project_svc.find_for_invoice.return_value = [
            {
                "project": {"id": 12, "name": "Moran Estate"},
                "confidence": 0.55,
            }
        ]
        sub_svc = MagicMock()
        sub_svc.find_for_reply.return_value = [
            {"sub_cost_code": {"id": 901, "number": "1.01", "name": "Mob"}, "confidence": 0.9}
        ]
        with patch(
            "entities.coding_suggestion.business.bill_suggestion_service.assert_can_access_project"
        ):
            result = BillCodingSuggester(
                coding_suggestion_service=coding_svc,
                project_service=project_svc,
                sub_cost_code_service=sub_svc,
            ).suggest(vendor_id=1, memo="1577 Moran Rd - 1.01")
        assert result.confidence == Decimal("0.55")

    def test_memo_scc_hint_rung(self):
        coding_svc = MagicMock()
        coding_svc.read_vendor_dominant_project_for_bills.return_value = None
        coding_svc.read_vendor_dominant_sub_cost_code_for_bills.return_value = None

        sub_svc = MagicMock()
        sub_svc.find_for_reply.return_value = [
            {"sub_cost_code": {"id": 901, "number": "1.01", "name": "Mobilization"}, "confidence": 0.9}
        ]
        suggester = BillCodingSuggester(
            coding_suggestion_service=coding_svc,
            sub_cost_code_service=sub_svc,
        )
        result = suggester.suggest(vendor_id=1, memo="1.01 - materials")
        assert result.source == "memo"
        assert result.sub_cost_code_id == 901
        assert result.confidence == Decimal("0.9")

    def test_memo_project_inaccessible_dropped(self):
        coding_svc = MagicMock()
        coding_svc.read_vendor_dominant_sub_cost_code_for_bills.return_value = None
        coding_svc.read_vendor_dominant_project_for_bills.return_value = None

        project_svc = MagicMock()
        project_svc.read_by_abbreviation.return_value = SimpleNamespace(id=55)
        sub_svc = MagicMock()
        sub_svc.find_for_reply.return_value = [
            {"sub_cost_code": {"id": 901, "number": "1.01", "name": "Mob"}, "confidence": 0.9}
        ]

        with patch(
            "entities.coding_suggestion.business.bill_suggestion_service.assert_can_access_project",
            side_effect=EntityNotAccessibleError("Project", 55),
        ):
            result = BillCodingSuggester(
                coding_suggestion_service=coding_svc,
                project_service=project_svc,
                sub_cost_code_service=sub_svc,
            ).suggest(vendor_id=1, memo="HE12 - 1.01 - note")

        assert result.source == "memo"
        assert result.project_id is None
        assert result.sub_cost_code_id == 901

    def test_smoothed_score_and_reason(self):
        coding_svc = _mock_coding_svc(read_vendor_dominant_sub_cost_code_for_bills=_HISTORY_HIT)
        result = BillCodingSuggester(coding_suggestion_service=coding_svc).suggest(
            vendor_id=1, project_id=10
        )
        assert result.raw_score == Decimal("3") / Decimal("6")
        assert result.confidence == result.raw_score
        assert "3 of 4 prior bills" in (result.reason or "")
        assert "05.100" in (result.reason or "")

    def test_model_version_constant(self):
        assert MODEL_VERSION == "u653a-history-v1"
        result = BillCodingSuggester(
            coding_suggestion_service=_mock_coding_svc(
                read_vendor_dominant_sub_cost_code_for_bills=_HISTORY_HIT
            )
        ).suggest(vendor_id=1, project_id=1)
        assert result.model_version == MODEL_VERSION

    def test_none_source_when_no_signal(self):
        coding_svc = MagicMock()
        coding_svc.read_vendor_dominant_project_for_bills.return_value = None
        coding_svc.read_vendor_dominant_sub_cost_code_for_bills.return_value = None
        result = BillCodingSuggester(coding_suggestion_service=coding_svc).suggest(vendor_id=1)
        assert result.source == "none"


class TestRollupAndLineProject:
    @pytest.mark.parametrize(
        "states,expected",
        [
            ([None, None], None),
            (["accepted", "accepted"], "accepted"),
            (["prefilled", "accepted"], "prefilled"),
            (["accepted", "changed"], "changed"),
        ],
    )
    def test_rollup_prefill_state(self, states, expected):
        assert rollup_prefill_state(states) == expected

    def test_chosen_project_from_lines_single(self):
        lines = [
            SimpleNamespace(project_id=10),
            SimpleNamespace(project_id=10),
        ]
        assert chosen_project_id_from_lines(lines) == 10

    def test_chosen_project_from_lines_mixed_is_none(self):
        lines = [SimpleNamespace(project_id=10), SimpleNamespace(project_id=11)]
        assert chosen_project_id_from_lines(lines) is None


class TestBillFeaturesJson:
    def test_features_json_allowlist_excludes_display_text(self):
        suggestion = BillSuggestion(
            project_id=10,
            sub_cost_code_id=501,
            sub_cost_code_public_id=None,
            sub_cost_code_number="05.100",
            sub_cost_code_name="Framing Lumber",
            source="memo",
            reason="address match Moran Estate",
            confidence=Decimal("0.55"),
            raw_score=Decimal("0.55"),
            top_count=None,
            second_count=None,
            total_count=None,
        )
        features = json.loads(bill_suggestion_to_features_json(suggestion))
        assert "Moran Estate" not in json.dumps(features)
        assert "Framing Lumber" not in features
        assert "sub_cost_code_name" not in features
        assert "reason" not in features
        assert features["project_id"] == 10
        assert features["confidence"] == "0.55"


class TestBillCodingSuggestEndpoint:
    def test_bills_can_read_gate(self):
        sig = inspect.signature(suggest_bill_coding_router)
        rbac_param = sig.parameters["_"]
        dep = rbac_param.default
        assert isinstance(dep, DependsParam)
        inner = dep.dependency
        nonlocals = inspect.getclosurevars(inner).nonlocals
        assert nonlocals.get("module_name") == Modules.BILLS
        assert nonlocals.get("permission") == "can_read"

    def test_suggest_resolves_public_ids(self):
        vendor = SimpleNamespace(id=5, public_id="v-pub")
        project = SimpleNamespace(id=10, public_id="p-pub", name="House")
        suggestion = BillSuggestion(
            project_id=10,
            sub_cost_code_id=501,
            sub_cost_code_public_id="scc-pub",
            sub_cost_code_number="05.100",
            sub_cost_code_name="Framing",
            source="vendor_project_history",
            reason="reason",
            confidence=Decimal("0.5"),
            raw_score=Decimal("0.5"),
            top_count=3,
            second_count=1,
            total_count=4,
        )

        with patch("entities.coding_suggestion.api.bill_router.VendorService") as VendorSvc:
            VendorSvc.return_value.read_by_public_id.return_value = vendor
            with patch("entities.coding_suggestion.api.bill_router.ProjectService") as ProjectSvc:
                project_inst = ProjectSvc.return_value
                project_inst.read_by_public_id.return_value = project
                project_inst.read_by_id.return_value = project
                with patch(
                    "entities.coding_suggestion.api.bill_router.assert_can_access_project"
                ):
                    with patch(
                        "entities.coding_suggestion.api.bill_router.BillCodingSuggester"
                    ) as Suggester:
                        Suggester.return_value.suggest.return_value = suggestion
                        body = BillCodingSuggestRequest(
                            vendor_public_id=UUID("550e8400-e29b-41d4-a716-446655440001"),
                            project_public_id=UUID("550e8400-e29b-41d4-a716-446655440002"),
                            memo="m",
                        )
                        payload = suggest_bill_coding_router(body=body, _=_CURRENT_USER)

        data = payload["data"]
        assert data["project_public_id"] == "p-pub"  # from ProjectService mock
        assert data["sub_cost_code_public_id"] == "scc-pub"
        assert "project_id" not in data
        assert "sub_cost_code_id" not in data
        assert "vendor_id" not in data

    def test_malformed_public_ids_rejected_at_schema(self):
        with pytest.raises(ValidationError):
            BillCodingSuggestRequest(vendor_public_id="not-a-uuid")
        with pytest.raises(ValidationError):
            BillCodingSuggestRequest(
                vendor_public_id=UUID("550e8400-e29b-41d4-a716-446655440001"),
                project_public_id="bad",
            )

    def test_inaccessible_project_returns_404(self):
        project = SimpleNamespace(id=99, public_id="p-x")
        vendor_uuid = UUID("550e8400-e29b-41d4-a716-446655440010")
        project_uuid = UUID("550e8400-e29b-41d4-a716-446655440011")
        with patch("entities.coding_suggestion.api.bill_router.VendorService") as VendorSvc:
            VendorSvc.return_value.read_by_public_id.return_value = SimpleNamespace(id=1)
            with patch("entities.coding_suggestion.api.bill_router.ProjectService") as ProjectSvc:
                ProjectSvc.return_value.read_by_public_id.return_value = project
                with patch(
                    "entities.coding_suggestion.api.bill_router.assert_can_access_project",
                    side_effect=EntityNotAccessibleError("Project", 99),
                ):
                    with pytest.raises(Exception) as exc:
                        suggest_bill_coding_router(
                            body=BillCodingSuggestRequest(
                                vendor_public_id=vendor_uuid,
                                project_public_id=project_uuid,
                            ),
                            _=_CURRENT_USER,
                        )
        assert exc.value.status_code == 404

    def test_inaccessible_bill_returns_404(self):
        vendor_uuid = UUID("550e8400-e29b-41d4-a716-446655440020")
        bill_uuid = UUID("550e8400-e29b-41d4-a716-446655440021")
        with patch("entities.coding_suggestion.api.bill_router.VendorService") as VendorSvc:
            VendorSvc.return_value.read_by_public_id.return_value = SimpleNamespace(id=1)
            with patch("entities.coding_suggestion.api.bill_router.BillService") as BillSvc:
                BillSvc.return_value.read_by_public_id.side_effect = EntityNotAccessibleError(
                    "Bill", 3
                )
                with pytest.raises(Exception) as exc:
                    suggest_bill_coding_router(
                        body=BillCodingSuggestRequest(
                            vendor_public_id=vendor_uuid,
                            bill_public_id=bill_uuid,
                        ),
                        _=_CURRENT_USER,
                    )
        assert getattr(exc.value, "status_code", None) == 404


class TestRecordOnSave:
    def test_skips_vendorless_and_missing_id(self):
        with patch(
            "entities.coding_suggestion.business.bill_suggestion_service.BillCodingSuggester"
        ) as Suggester:
            record_bill_coding_suggestion_on_save(_bill(id=None, vendor_id=5))
            record_bill_coding_suggestion_on_save(_bill(vendor_id=None))
            Suggester.assert_not_called()

    def test_records_after_draft_save_with_features_json(self):
        bill = _bill(id=7, public_id="b7", vendor_id=3, memo="note")
        lines = [SimpleNamespace(project_id=10, coding_prefill_state="prefilled")]
        suggestion = BillSuggestion(
            project_id=10,
            sub_cost_code_id=501,
            sub_cost_code_public_id=None,
            sub_cost_code_number="05.100",
            sub_cost_code_name="Framing",
            source="vendor_project_history",
            reason="r",
            confidence=Decimal("0.5"),
            raw_score=Decimal("0.5"),
            top_count=3,
            second_count=1,
            total_count=4,
        )
        coding_svc = MagicMock()
        line_svc = MagicMock()
        line_svc.read_by_bill_id.return_value = lines
        suggester = MagicMock()
        suggester.suggest.return_value = suggestion

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

        suggester.suggest.assert_called_once_with(
            vendor_id=3,
            project_id=10,
            memo="note",
            exclude_bill_id=7,
        )
        coding_svc.upsert_for_bill.assert_called_once()
        kwargs = coding_svc.upsert_for_bill.call_args.kwargs
        assert kwargs["prefill_state"] == "prefilled"
        assert kwargs["replace"] is True
        features = json.loads(kwargs["features_json"])
        assert features["source"] == "vendor_project_history"
        assert features["top_count"] == 3
        assert "Framing" not in json.dumps(features)

    def test_suggester_exception_does_not_propagate(self):
        bill = _bill(id=1, vendor_id=2)
        line_svc = MagicMock()
        line_svc.read_by_bill_id.return_value = []
        suggester = MagicMock()
        suggester.suggest.side_effect = RuntimeError("boom")

        record_bill_coding_suggestion_on_save(
            bill,
            suggester=suggester,
            line_item_service=line_svc,
            coding_suggestion_service=MagicMock(),
        )

    def test_update_by_public_id_does_not_record_coding_suggestion(self):
        bill = _bill(memo="m")
        svc = BillService()
        with patch.object(svc, "read_by_public_id", return_value=bill):
            with patch.object(svc.repo, "update_by_id", return_value=bill):
                with patch(
                    "entities.coding_suggestion.business.bill_suggestion_service.record_bill_coding_suggestion_on_save"
                ) as record:
                    svc.update_by_public_id("b", row_version="rv")
                    record.assert_not_called()

    def test_review_create_initial_bill_submit_does_not_record_coding(self):
        """Single recording point (Chris, 2026-10-13): the first completion, plus U-653d's
        reconciler for Bills that never complete — the review-submit hook was dropped."""
        review = SimpleNamespace(
            id=1,
            review_status_id=10,
            status_is_final=False,
            status_is_declined=False,
        )
        first = SimpleNamespace(id=10)
        bill = _bill(id=5, vendor_id=1)
        svc = ReviewService()
        svc.repo = MagicMock()
        svc.repo.create.return_value = review
        svc.review_status_service = MagicMock()
        svc.review_status_service.get_first_status.return_value = first

        with patch("entities.bill.business.service.BillService") as BillSvc:
            BillSvc.return_value.repo.read_by_id.return_value = bill
            with patch(
                "entities.review.business.notification_service.ReviewNotificationService"
            ):
                with patch(
                    "entities.coding_suggestion.business.bill_suggestion_service.record_bill_coding_suggestion_on_save"
                ) as record:
                    svc.create(review_status_id=10, user_id=1, bill_id=5)
                    record.assert_not_called()

    def test_complete_bill_finalization_records_coding_suggestion(self):
        svc = BillService()
        draft = _bill(id=1, vendor_id=2, public_id="550e8400-e29b-41d4-a716-446655440099")
        finalized = _bill(id=1, vendor_id=2, is_draft=False, status="completed")
        with patch.object(svc, "read_by_public_id", return_value=draft):
            with patch.object(
                svc.vendor_service,
                "read_by_id",
                return_value=SimpleNamespace(public_id="v", id=2),
            ):
                with patch.object(svc, "_assert_no_duplicate_on_complete"):
                    with patch.object(svc.repo, "finalize_by_id", return_value=finalized):
                        with patch.object(
                            svc.bill_line_item_service, "read_by_bill_id", return_value=[]
                        ):
                            with patch.object(svc, "_rename_invoice_blob_on_complete"):
                                with patch(
                                    "entities.coding_suggestion.business.bill_suggestion_service.record_bill_coding_suggestion_on_save"
                                ) as record:
                                    svc.complete_bill(str(draft.public_id))
                                    record.assert_called_once_with(finalized)


class TestBillCodingRepoLogging:
    def test_bill_repo_errors_log_class_name_only(self, caplog):
        from entities.coding_suggestion.persistence.repo import CodingSuggestionRepository

        caplog.set_level(logging.ERROR)
        repo = CodingSuggestionRepository()
        with patch(
            "entities.coding_suggestion.persistence.repo.get_connection",
            side_effect=RuntimeError("secret=bad-uuid"),
        ):
            with pytest.raises(Exception):
                repo.upsert_for_bill(bill_id=1)
        assert "secret=bad-uuid" not in caplog.text
        assert "RuntimeError" in caplog.text


# --- fix round 2: completion idempotence, provenance, sanitized dependency logs ---- #


class TestRound2:
    def test_complete_bill_on_already_completed_bill_does_not_record(self):
        from entities.bill.business.service import BillService

        svc = BillService.__new__(BillService)
        done = MagicMock(id=1, public_id="00000000-0000-0000-0000-000000000001", is_draft=False, status="completed")
        svc.read_by_public_id = MagicMock(return_value=done)
        svc._assert_no_duplicate_on_complete = MagicMock()
        svc.bill_line_item_service = MagicMock()
        svc.bill_line_item_service.read_by_bill_id.return_value = []
        svc.vendor_service = MagicMock()
        svc.repo = MagicMock()
        svc.repo.finalize_by_id.return_value = done
        with patch(
            "entities.coding_suggestion.business.bill_suggestion_service.record_bill_coding_suggestion_on_save"
        ) as rec:
            try:
                svc.complete_bill(done.public_id)
            except Exception:
                pass  # later completion steps are not under test here
            rec.assert_not_called()

    def test_complete_bill_records_on_draft_to_completed_flip(self):
        from entities.bill.business.service import BillService

        svc = BillService.__new__(BillService)
        draft = MagicMock(id=2, public_id="00000000-0000-0000-0000-000000000002", is_draft=True, status="approved")
        finalized = MagicMock(id=2, public_id=draft.public_id, is_draft=False, status="completed")
        svc.read_by_public_id = MagicMock(return_value=draft)
        svc._assert_no_duplicate_on_complete = MagicMock()
        svc.bill_line_item_service = MagicMock()
        svc.bill_line_item_service.read_by_bill_id.return_value = []
        svc.vendor_service = MagicMock()
        svc.repo = MagicMock()
        svc.repo.finalize_by_id.return_value = finalized
        with patch(
            "entities.coding_suggestion.business.bill_suggestion_service.record_bill_coding_suggestion_on_save"
        ) as rec:
            try:
                svc.complete_bill(draft.public_id)
            except Exception:
                pass
            rec.assert_called_once_with(finalized)

    def test_vendor_wide_fallback_keeps_predicted_project_provenance(self):
        from entities.coding_suggestion.business.bill_suggestion_service import BillCodingSuggester
        from entities.coding_suggestion.business.model import (
            VendorDominantProjectForBills,
            VendorDominantSubCostCodeForBills,
        )

        history = MagicMock()
        history.read_vendor_dominant_project_for_bills.return_value = VendorDominantProjectForBills(
            top_project_id=12, top_count=3, second_count=0, total_count=3
        )
        vendor_wide = MagicMock(spec=VendorDominantSubCostCodeForBills)
        vendor_wide.top_sub_cost_code_id, vendor_wide.top_count, vendor_wide.second_count, vendor_wide.total_count = 5, 3, 0, 3
        vendor_wide.number, vendor_wide.name = "05.100", "Framing"
        vendor_wide.public_id = None
        history.read_vendor_dominant_sub_cost_code_for_bills.side_effect = (
            lambda vendor_id, project_id=None, **kw: None if project_id is not None else vendor_wide
        )
        s = BillCodingSuggester(coding_suggestion_service=history).suggest(vendor_id=9)
        assert s.project_id == 12 and s.source == "vendor_history" and s.predicted_project is True

    def test_memo_lookup_repo_errors_log_class_names_only(self, caplog):
        from entities.project.persistence.repo import ProjectRepository
        from entities.sub_cost_code.persistence.repo import SubCostCodeRepository

        with patch("entities.sub_cost_code.persistence.repo.get_connection", side_effect=RuntimeError("PRIVATE_SENTINEL")), \
             patch("entities.project.persistence.repo.get_connection", side_effect=RuntimeError("PRIVATE_SENTINEL")), \
             caplog.at_level("DEBUG"):
            for call in (
                lambda: SubCostCodeRepository().find_for_reply(hint="1.01"),
                lambda: ProjectRepository().find_for_invoice(address_hint="1 Main"),
                lambda: ProjectRepository().read_by_abbreviation("ABC"),
            ):
                try:
                    call()
                except Exception:
                    pass
        assert "PRIVATE_SENTINEL" not in caplog.text


def test_memo_scc_suggestion_carries_public_id():
    from entities.coding_suggestion.business.bill_suggestion_service import BillCodingSuggester

    history = MagicMock()
    history.read_vendor_dominant_project_for_bills.return_value = None
    history.read_vendor_dominant_sub_cost_code_for_bills.return_value = None
    scc_svc = MagicMock()
    scc_svc.find_for_reply.return_value = [
        {"sub_cost_code": {"id": 7, "public_id": "memo-scc", "number": "1.01", "name": "Materials"}, "confidence": 0.8}
    ]
    s = BillCodingSuggester(coding_suggestion_service=history, sub_cost_code_service=scc_svc).suggest(
        vendor_id=3, memo="1.01 - materials"
    )
    assert s.source == "memo" and s.sub_cost_code_public_id == "memo-scc"
