"""U-797c4b (review round A) — BillCredit reply finder via ReviewNotification.

find_for_reviewer_reply resolves a reply's ConversationId to a BillCredit only when
exactly one notification matches, its Kind is "bill_credit", and it carries exactly
one BillCredit target. The credit is read through the access-checked service read.
Fakes only."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from entities.bill_credit.api import router as bc_router
from entities.bill_credit.business.service import BillCreditService
from shared.access import EntityNotAccessibleError

NS = "entities.review.business.review_notification_service.ReviewNotificationRecordService"
CREDIT = SimpleNamespace(id=7, public_id="bc-7", credit_number="VC-1", is_draft=True)


def _lookup(matches):
    svc = MagicMock()
    svc.lookup_for_reply.return_value = matches
    return patch(NS, MagicMock(return_value=svc)), svc


def _find(matches, read=None):
    p, svc = _lookup(matches)
    with p, patch.object(BillCreditService, "read_by_id", return_value=read) as rd:
        result = BillCreditService(repo=MagicMock()).find_for_reviewer_reply("conv-1")
    return result, rd, svc


NOTE = SimpleNamespace(Id=1, Kind="bill_credit")


def test_no_notification_is_none():
    result, rd, _ = _find([])
    assert result is None
    rd.assert_not_called()


def test_several_notifications_is_none():
    result, rd, _ = _find([(NOTE, {("bill_credit", 7)}), (SimpleNamespace(Id=2, Kind="bill_credit"), {("bill_credit", 8)})])
    assert result is None
    rd.assert_not_called()


def test_other_kind_is_none():
    result, rd, _ = _find([(SimpleNamespace(Id=1, Kind="bill"), {("bill_credit", 7)})])
    assert result is None
    rd.assert_not_called()


def test_several_targets_is_none():
    result, rd, _ = _find([(NOTE, {("bill_credit", 7), ("bill_credit", 8)})])
    assert result is None
    rd.assert_not_called()


def test_a_non_credit_target_is_none():
    result, rd, _ = _find([(NOTE, {("bill", 7)})])
    assert result is None
    rd.assert_not_called()


def test_happy_path_returns_the_credit_read_through_the_access_checked_service():
    result, rd, svc = _find([(NOTE, {("bill_credit", 7)})], read=CREDIT)
    assert result is CREDIT
    rd.assert_called_once_with(7)
    svc.lookup_for_reply.assert_called_once_with("conv-1")


def test_access_denied_answers_as_no_match_and_logs_ids_only(caplog):
    p, _ = _lookup([(NOTE, {("bill_credit", 7)})])
    with p, patch.object(BillCreditService, "read_by_id", side_effect=EntityNotAccessibleError("BillCredit", 7)), \
            caplog.at_level("WARNING"):
        result = BillCreditService(repo=MagicMock()).find_for_reviewer_reply("conv-1")
    assert result is None
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("not accessible" in m and "7" in m for m in warnings)
    assert not any("conv-1" in m for m in warnings)


def test_route_is_registered_as_get_before_the_public_id_route():
    paths = [r.path for r in bc_router.router.routes if "GET" in getattr(r, "methods", set())]
    path = "/api/v1/get/bill-credit/find-by-conversation-id"
    assert path in paths
    assert paths.index(path) < paths.index("/api/v1/get/bill-credit/{public_id}")


def test_route_answers_200_with_null_when_no_credit_matches():
    # Bill's finder contract: a miss is item_response(None) with 200, not a 404.
    with patch.object(BillCreditService, "find_for_reviewer_reply", return_value=None):
        result = bc_router.find_bill_credit_by_conversation_id_router("conv-1", current_user={})
    assert result == {"data": None}


def test_route_returns_a_slim_payload_for_a_match():
    with patch.object(BillCreditService, "find_for_reviewer_reply", return_value=CREDIT):
        result = bc_router.find_bill_credit_by_conversation_id_router("conv-1", current_user={})
    data = result["data"] if isinstance(result, dict) and "data" in result else result
    assert data["public_id"] == "bc-7"
    assert data["id"] == 7


def test_agent_tool_is_registered_and_listed_by_the_specialist():
    import entities.bill_credit.intelligence.tools as bc_tools
    from intelligence.agents.bill_credit_specialist.definition import bill_credit_specialist
    from intelligence.tools.registry import get

    assert get("find_bill_credit_by_conversation_id") is bc_tools.find_bill_credit_by_conversation_id
    assert "find_bill_credit_by_conversation_id" in bill_credit_specialist.tools


def test_agent_tool_calls_the_get_route_with_the_conversation_id():
    import entities.bill_credit.intelligence.tools as bc_tools

    ctx = MagicMock()
    ctx.call_api = AsyncMock(return_value={"ok": True})
    asyncio.run(bc_tools._find_bill_credit_by_conversation_id({"conversation_id": "conv-1"}, ctx))
    method, path = ctx.call_api.call_args.args[:2]
    assert method == "GET"
    assert path == "/api/v1/get/bill-credit/find-by-conversation-id?conversation_id=conv-1"
