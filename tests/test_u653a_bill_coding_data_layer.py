"""U-653a BITE 1: bill coding suggestion data layer (static + unit)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from pydantic import ValidationError

from entities.bill_line_item.api.router import (
    create_bill_line_item_router,
    update_bill_line_item_by_public_id_router,
)
from entities.bill_line_item.api.schemas import BillLineItemCreate, BillLineItemUpdate
from entities.bill_line_item.business.service import BillLineItemService
from entities.bill_line_item.persistence.repo import BillLineItemRepository
from entities.coding_suggestion.business.service import CodingSuggestionService
from entities.coding_suggestion.persistence.repo import CodingSuggestionRepository
from shared.access import EntityNotAccessibleError
from tests.sproc_text import REPO_ROOT, sproc_body, sproc_params, strip_sql_comments

_CURRENT_USER = {"id": 17, "username": "tester", "tenant_id": 1}
_BLI_ROUTER = "entities.bill_line_item.api.router"

BILL_CODING_SQL = REPO_ROOT / "entities/coding_suggestion/sql/bill_coding_suggestion.sql"
BILL_LINE_ITEM_SQL = REPO_ROOT / "entities/bill_line_item/sql/dbo.bill_line_item.sql"


class TestBillHistorySprocPins:
    def test_read_vendor_dominant_sub_cost_code_for_bills_contract(self):
        body = strip_sql_comments(sproc_body(BILL_CODING_SQL, "ReadVendorDominantSubCostCodeForBills"))
        params = sproc_params(BILL_CODING_SQL, "ReadVendorDominantSubCostCodeForBills")
        assert "@AsOfDatetime" in params
        assert "@ExcludeBillId" in params
        sql_text = strip_sql_comments(BILL_CODING_SQL.read_text(encoding="utf-8"))
        assert "CreatedDatetime" in sql_text
        assert "[IsDraft] = 0" in sql_text
        assert "[TotalCount] >= 3" in body
        assert "ABS" in sql_text
        assert "MinLineId" in sql_text
        assert "BillDominantVoteForVendor" in body
        assert "SubCostCodePublicId" in body

    def test_read_vendor_dominant_project_for_bills_uses_same_clock(self):
        body = strip_sql_comments(sproc_body(BILL_CODING_SQL, "ReadVendorDominantProjectForBills"))
        assert "BillDominantVoteForVendor" in body
        assert "[TotalCount] >= 3" in body

    def test_upsert_coding_suggestion_for_bill_targets_bill_kind(self):
        body = strip_sql_comments(sproc_body(BILL_CODING_SQL, "UpsertCodingSuggestionForBill"))
        assert "N'bill'" in body
        matched = body.split("WHEN MATCHED")[1].split("WHEN NOT MATCHED")[0]
        assert "ConfirmedProjectId" not in matched
        assert "WasOverridden" not in matched

    def test_bill_coding_suggestion_readers_return_features_json_and_model_version(self):
        for proc in ("UpsertCodingSuggestionForBill", "ReadCodingSuggestionByBillId"):
            body = strip_sql_comments(sproc_body(BILL_CODING_SQL, proc))
            assert "FeaturesJson" in body
            assert "ModelVersion" in body

    def test_bill_vendor_created_datetime_index_guard(self):
        text = BILL_CODING_SQL.read_text(encoding="utf-8")
        assert "IX_Bill_VendorId_CreatedDatetime" in text
        assert "NOT EXISTS" in text and "sys.indexes" in text
        assert "INCLUDE ([IsDraft])" in text

    def test_bill_line_item_vote_chain_covering_index(self):
        text = BILL_LINE_ITEM_SQL.read_text(encoding="utf-8")
        assert "IX_BillLineItem_BillId_SubCostCodeId" in text
        assert "INCLUDE ([Amount], [ProjectId])" in text


class TestBillLineItemCodingPrefillState:
    def test_update_preserves_coding_prefill_state_when_null(self):
        body = strip_sql_comments(sproc_body(BILL_LINE_ITEM_SQL, "UpdateBillLineItemById"))
        assert "@CodingPrefillState" in sproc_params(BILL_LINE_ITEM_SQL, "UpdateBillLineItemById")
        assert (
            "CASE WHEN @CodingPrefillState IS NULL THEN [CodingPrefillState] ELSE @CodingPrefillState END"
            in body.replace("\n", " ")
        )
        raw = BILL_LINE_ITEM_SQL.read_text(encoding="utf-8")
        assert "NULL preserves the stored value" in raw

    @pytest.mark.parametrize(
        "proc",
        [
            "ReadBillLineItems",
            "ReadBillLineItemById",
            "ReadBillLineItemByPublicId",
            "ReadBillLineItemsByBillId",
            "ReadBillLineItemsByProjectId",
            "ReadBillLineItemByBillIdAndQboId",
        ],
    )
    def test_readers_select_coding_prefill_state(self, proc: str):
        body = strip_sql_comments(sproc_body(BILL_LINE_ITEM_SQL, proc))
        assert "CodingPrefillState" in body

    def test_schema_accepts_only_three_prefill_values(self):
        BillLineItemCreate(bill_public_id="x", coding_prefill_state="prefilled")
        BillLineItemUpdate(
            row_version="abc",
            bill_public_id="x",
            coding_prefill_state="accepted",
        )
        with pytest.raises(ValidationError):
            BillLineItemUpdate(
                row_version="abc",
                bill_public_id="x",
                coding_prefill_state="bogus",
            )


class TestBillLineItemRouterCodingPrefillState:
    @staticmethod
    def _instant_orchestrator_patch():
        workflow = MagicMock()
        workflow.id = 1
        workflow.public_id = "wf-test"
        orchestrator = MagicMock()
        orchestrator.create_workflow.return_value = (workflow, None)
        return patch(
            "core.workflow.business.instant.InstantWorkflowHandler.orchestrator",
            new_callable=PropertyMock,
            return_value=orchestrator,
        )

    def test_create_prefill_state_reaches_service_create(self):
        captured = {}

        def _create(self, **kwargs):
            captured.update(kwargs)
            return MagicMock(to_dict=lambda: {"public_id": "new"})

        body = BillLineItemCreate(bill_public_id="b-1", coding_prefill_state="prefilled")
        with self._instant_orchestrator_patch():
            with patch.object(BillLineItemService, "create", _create):
                create_bill_line_item_router(body=body, current_user=_CURRENT_USER)
        assert captured.get("coding_prefill_state") == "prefilled"

    def test_update_prefill_state_reaches_service_update(self):
        captured = {}

        def _update(self, **kwargs):
            captured.update(kwargs)
            return MagicMock(to_dict=lambda: {"public_id": kwargs["public_id"]})

        body = BillLineItemUpdate(
            row_version="rv",
            bill_public_id="b-1",
            coding_prefill_state="accepted",
        )
        with self._instant_orchestrator_patch():
            with patch.object(BillLineItemService, "update_by_public_id", _update):
                update_bill_line_item_by_public_id_router(
                    public_id="bli-x", body=body, current_user=_CURRENT_USER
                )
        assert captured.get("coding_prefill_state") == "accepted"


class TestBillLineItemRepoCodingPrefillDeployOrder:
    @patch("entities.bill_line_item.persistence.repo.call_procedure")
    @patch("entities.bill_line_item.persistence.repo.get_connection")
    def test_create_omits_coding_prefill_state_when_none(
        self, mock_get_connection, mock_call_procedure
    ):
        cursor = MagicMock()
        cursor.fetchone.return_value = _mock_bli_row()
        mock_get_connection.return_value.__enter__.return_value.cursor.return_value = cursor

        BillLineItemRepository().create(bill_id=1, is_draft=True)

        params = mock_call_procedure.call_args.kwargs["params"]
        assert "CodingPrefillState" not in params

    @patch("entities.bill_line_item.persistence.repo.call_procedure")
    @patch("entities.bill_line_item.persistence.repo.get_connection")
    def test_create_passes_coding_prefill_state_when_set(
        self, mock_get_connection, mock_call_procedure
    ):
        cursor = MagicMock()
        cursor.fetchone.return_value = _mock_bli_row(CodingPrefillState="prefilled")
        mock_get_connection.return_value.__enter__.return_value.cursor.return_value = cursor

        BillLineItemRepository().create(
            bill_id=1, is_draft=True, coding_prefill_state="prefilled"
        )

        params = mock_call_procedure.call_args.kwargs["params"]
        assert params["CodingPrefillState"] == "prefilled"


class TestCodingSuggestionBillAccess:
    def test_upsert_for_bill_denies_without_bill_access(self):
        svc = CodingSuggestionService()
        with patch(
            "entities.coding_suggestion.business.service.assert_can_access_bill"
        ) as mock_assert:
            mock_assert.side_effect = EntityNotAccessibleError("Bill", 99)
            with patch.object(svc.repo, "upsert_for_bill") as mock_repo:
                with pytest.raises(EntityNotAccessibleError):
                    svc.upsert_for_bill(bill_id=99)
                mock_repo.assert_not_called()

    def test_read_by_bill_id_denies_without_bill_access(self):
        svc = CodingSuggestionService()
        with patch(
            "entities.coding_suggestion.business.service.assert_can_access_bill"
        ) as mock_assert:
            mock_assert.side_effect = EntityNotAccessibleError("Bill", 3)
            with patch.object(svc.repo, "read_by_bill_id") as mock_repo:
                with pytest.raises(EntityNotAccessibleError):
                    svc.read_by_bill_id(3)
                mock_repo.assert_not_called()

    def test_bill_methods_proceed_when_access_check_passes(self):
        svc = CodingSuggestionService()
        with patch(
            "entities.coding_suggestion.business.service.assert_can_access_bill"
        ) as mock_assert:
            with patch.object(svc.repo, "read_by_bill_id", return_value=None) as mock_repo:
                svc.read_by_bill_id(8)
                mock_assert.assert_called_once_with(8)
                mock_repo.assert_called_once_with(8)


class TestCodingSuggestionRepoPlumbing:
    def test_upsert_for_bill_params(self, monkeypatch):
        captured = {}

        def fake_call_procedure(*, cursor, name, params):
            captured["name"] = name
            captured["params"] = params

        monkeypatch.setattr(
            "entities.coding_suggestion.persistence.repo.call_procedure",
            fake_call_procedure,
        )
        monkeypatch.setattr(
            "entities.coding_suggestion.persistence.repo.get_connection",
            lambda: _FakeConn(),
        )

        repo = CodingSuggestionRepository()
        repo.upsert_for_bill(
            bill_id=99,
            vendor_id=1,
            suggested_sub_cost_code_id=2,
            suggestion_confidence=Decimal("0.75"),
            features_json='{"k":1}',
            model_version="hist-v1",
        )
        assert captured["name"] == "UpsertCodingSuggestionForBill"
        assert captured["params"]["BillId"] == 99
        assert captured["params"]["FeaturesJson"] == '{"k":1}'
        assert captured["params"]["ReplaceSuggestion"] == 0

    def test_upsert_for_bill_replace_flag(self, monkeypatch):
        captured = {}

        def fake_call_procedure(*, cursor, name, params):
            captured["params"] = params

        monkeypatch.setattr(
            "entities.coding_suggestion.persistence.repo.call_procedure",
            fake_call_procedure,
        )
        monkeypatch.setattr(
            "entities.coding_suggestion.persistence.repo.get_connection",
            lambda: _FakeConn(),
        )

        repo = CodingSuggestionRepository()
        repo.upsert_for_bill(bill_id=1, replace=True)
        assert captured["params"]["ReplaceSuggestion"] == 1

    def test_read_vendor_dominant_sub_cost_code_for_bills_params(self, monkeypatch):
        captured = {}

        def fake_call_procedure(*, cursor, name, params):
            captured["name"] = name
            captured["params"] = params

        monkeypatch.setattr(
            "entities.coding_suggestion.persistence.repo.call_procedure",
            fake_call_procedure,
        )
        monkeypatch.setattr(
            "entities.coding_suggestion.persistence.repo.get_connection",
            lambda: _FakeConn(),
        )

        as_of = datetime(2026, 1, 1, 12, 0, 0)
        repo = CodingSuggestionRepository()
        repo.read_vendor_dominant_sub_cost_code_for_bills(
            5,
            project_id=10,
            as_of=as_of,
            exclude_bill_id=77,
        )
        assert captured["name"] == "ReadVendorDominantSubCostCodeForBills"
        assert captured["params"]["ProjectId"] == 10
        assert captured["params"]["AsOfDatetime"] == as_of
        assert captured["params"]["ExcludeBillId"] == 77

    def test_read_vendor_dominant_sub_cost_code_normalizes_tz_aware_as_of(self, monkeypatch):
        captured = {}

        def fake_call_procedure(*, cursor, name, params):
            captured["params"] = params

        monkeypatch.setattr(
            "entities.coding_suggestion.persistence.repo.call_procedure",
            fake_call_procedure,
        )
        monkeypatch.setattr(
            "entities.coding_suggestion.persistence.repo.get_connection",
            lambda: _FakeConn(),
        )

        as_of = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone(timedelta(hours=5)))
        repo = CodingSuggestionRepository()
        repo.read_vendor_dominant_sub_cost_code_for_bills(5, as_of=as_of)
        assert captured["params"]["AsOfDatetime"] == datetime(2026, 1, 1, 7, 0, 0)

    def test_upsert_for_bill_round_trips_features_json_and_model_version(self, monkeypatch):
        row = SimpleNamespace(
            Id=1,
            PublicId="00000000-0000-0000-0000-000000000001",
            RowVersion=b"\x00" * 8,
            TargetKind="bill",
            BillId=99,
            BillCreditId=None,
            ExpenseId=None,
            AttachmentId=None,
            VendorId=1,
            QboPurchaseId=None,
            QboPurchaseQboId=None,
            RealmId=None,
            SyncTokenAtSuggest=None,
            Status="pending",
            SuggestedProjectId=None,
            SuggestedSubCostCodeId=2,
            SuggestedDescription=None,
            SuggestionSource="vendor_history",
            SuggestionReason=None,
            SuggestionConfidence=Decimal("0.75"),
            SuggestedAt=datetime(2026, 1, 1),
            FeaturesJson='{"grain":"vendor"}',
            BudgetCheck=None,
            BudgetRemaining=None,
            Route=None,
            ModelVersion="hist-v1",
            ConfirmedProjectId=None,
            ConfirmedSubCostCodeId=None,
            ConfirmedDescription=None,
            WasOverridden=None,
            ConfirmedByUserId=None,
            ConfirmedAt=None,
            ConfirmedMixedCodes=None,
            PrefillState=None,
            ConfirmationSource=None,
            ConfirmationSeq=None,
            FlagReason=None,
            FlaggedAt=None,
            WrittenAt=None,
            WriteError=None,
            ClaimedByUserId=None,
            ClaimedAt=None,
            CompanyId=1,
            CreatedByUserId=17,
            CreatedDatetime=datetime(2026, 1, 1),
            ModifiedDatetime=datetime(2026, 1, 1),
        )

        def fake_call_procedure(*, cursor, name, params):
            assert name == "UpsertCodingSuggestionForBill"
            assert params["FeaturesJson"] == '{"grain":"vendor"}'
            assert params["ModelVersion"] == "hist-v1"

        class _RowCursor(_FakeCursor):
            def fetchone(self):
                return row

        monkeypatch.setattr(
            "entities.coding_suggestion.persistence.repo.call_procedure",
            fake_call_procedure,
        )
        monkeypatch.setattr(
            "entities.coding_suggestion.persistence.repo.get_connection",
            lambda: _FakeConnWithCursor(_RowCursor()),
        )

        repo = CodingSuggestionRepository()
        result = repo.upsert_for_bill(
            bill_id=99,
            features_json='{"grain":"vendor"}',
            model_version="hist-v1",
        )
        assert result is not None
        assert result.features_json == '{"grain":"vendor"}'
        assert result.model_version == "hist-v1"


class TestCodingSuggestionServiceMethods:
    def test_service_delegates_to_repo(self, monkeypatch):
        calls = []

        class FakeRepo:
            def read_by_bill_id(self, bill_id):
                calls.append(("read", bill_id))
                return None

            def read_vendor_dominant_project_for_bills(self, vendor_id, *, as_of=None, exclude_bill_id=None):
                calls.append(("project", vendor_id))
                return None

        svc = CodingSuggestionService()
        monkeypatch.setattr(svc, "repo", FakeRepo())
        monkeypatch.setattr(
            "entities.coding_suggestion.business.service.assert_can_access_bill",
            lambda bill_id: None,
        )
        svc.read_by_bill_id(3)
        svc.read_vendor_dominant_project_for_bills(8)
        assert calls == [("read", 3), ("project", 8)]


class _FakeConn:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def cursor(self):
        return _FakeCursor()


class _FakeConnWithCursor(_FakeConn):
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


class _FakeCursor:
    def fetchone(self):
        return None


def _mock_bli_row(**overrides):
    base = dict(
        Id=1,
        PublicId="00000000-0000-0000-0000-000000000002",
        RowVersion=b"\x00" * 8,
        CreatedDatetime=datetime(2026, 1, 1),
        ModifiedDatetime=datetime(2026, 1, 1),
        BillId=1,
        SubCostCodeId=None,
        ProjectId=None,
        Description=None,
        Quantity=None,
        Rate=None,
        Amount=None,
        IsBillable=None,
        IsBilled=None,
        Markup=None,
        Price=None,
        IsDraft=True,
        CodingPrefillState=None,
        QboId=None,
        RealmId=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)



# --- fix round 2: history methods respect project access; full-row OUTPUTs ---- #

from entities.coding_suggestion.business.model import VendorDominantProjectForBills  # noqa: E402

_ACCESS = "entities.coding_suggestion.business.service.assert_can_access_project"


class TestHistoryProjectScoping:
    def test_scc_history_for_inaccessible_project_raises_before_repo(self):
        svc = CodingSuggestionService()
        with patch(_ACCESS, side_effect=EntityNotAccessibleError("Project", 99)):
            with patch.object(svc.repo, "read_vendor_dominant_sub_cost_code_for_bills") as repo:
                with pytest.raises(EntityNotAccessibleError):
                    svc.read_vendor_dominant_sub_cost_code_for_bills(5, project_id=99)
                repo.assert_not_called()

    def test_vendor_wide_scc_history_needs_no_project(self):
        svc = CodingSuggestionService()
        with patch(_ACCESS) as access:
            with patch.object(svc.repo, "read_vendor_dominant_sub_cost_code_for_bills", return_value=None):
                svc.read_vendor_dominant_sub_cost_code_for_bills(5)
            access.assert_called_once_with(None)  # helper returns early for None

    def test_predicted_project_hidden_when_inaccessible(self):
        svc = CodingSuggestionService()
        hit = VendorDominantProjectForBills(top_project_id=99, top_count=3, second_count=0, total_count=3)
        with patch(_ACCESS, side_effect=EntityNotAccessibleError("Project", 99)):
            with patch.object(svc.repo, "read_vendor_dominant_project_for_bills", return_value=hit):
                assert svc.read_vendor_dominant_project_for_bills(5) is None

    def test_predicted_project_returned_when_accessible(self):
        svc = CodingSuggestionService()
        hit = VendorDominantProjectForBills(top_project_id=7, top_count=3, second_count=1, total_count=4)
        with patch(_ACCESS) as access:
            with patch.object(svc.repo, "read_vendor_dominant_project_for_bills", return_value=hit):
                assert svc.read_vendor_dominant_project_for_bills(5) is hit
            access.assert_called_once_with(7)


def test_every_full_row_output_returns_features_json_and_model_version():
    sql = strip_sql_comments(
        (REPO_ROOT / "entities/coding_suggestion/sql/dbo.coding_suggestion.sql").read_text(encoding="utf-8")
    )
    outputs = [chunk for chunk in sql.split("OUTPUT")[1:] if "INSERTED.[ConfirmationSeq]" in chunk.split(";")[0]]
    assert outputs, "expected full-row OUTPUT lists"
    for chunk in outputs:
        head = chunk.split("VALUES")[0].split("WHERE")[0]
        assert "INSERTED.[FeaturesJson]" in head and "INSERTED.[ModelVersion]" in head
