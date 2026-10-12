"""U-565 — Expense find-by-conversation-id (reviewer-reply lookup twin of Bill).

Pure-logic pins: sproc text, repo/service wiring, router RBAC + order, agent allowlist.
No live DB.
"""

from __future__ import annotations

import re
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from entities.expense.persistence.repo import ExpenseRepository
from shared.access import EntityNotAccessibleError
from shared.api.responses import item_response
from shared.rbac_constants import Modules
from tests.sproc_text import REPO_ROOT, sproc_body, strip_sql_comments
from tests.test_review_route_rbac import _assert_current_user_rbac

EXPENSE_SQL = REPO_ROOT / "entities" / "expense" / "sql" / "dbo.expense.sql"
EXPENSE_SPECIALIST_PROMPT = REPO_ROOT / "intelligence" / "agents" / "expense_specialist" / "prompt.md"
FIND_ROUTE = "/api/v1/get/expense/find-by-conversation-id"
PUBLIC_ID_ROUTE = "/api/v1/get/expense/{public_id}"


# ---------------------------------------------------------------------------
# 1a — FindExpenseForReviewerReply sproc shape (file text)
# ---------------------------------------------------------------------------


def test_find_expense_for_reviewer_reply_sproc_is_sole_definition_in_base_file():
    text = strip_sql_comments(EXPENSE_SQL.read_text())
    matches = re.findall(
        r"CREATE\s+OR\s+ALTER\s+PROCEDURE\s+(?:dbo\.)?FindExpenseForReviewerReply\b",
        text,
        re.IGNORECASE,
    )
    assert len(matches) == 1, f"expected exactly one FindExpenseForReviewerReply, got {len(matches)}"


def test_find_expense_for_reviewer_reply_sproc_shape():
    exec_body = strip_sql_comments(sproc_body(EXPENSE_SQL, "FindExpenseForReviewerReply"))

    assert re.search(r"\bSET\s+NOCOUNT\s+ON\b", exec_body, re.IGNORECASE)

    # strict branch: thread identity via the source email, case-sensitive
    assert re.search(
        r"INNER\s+JOIN\s+dbo\.\[EmailMessage\]\s+em\s+ON\s+em\.\[Id\]\s*=\s*e\.\[SourceEmailMessageId\]",
        exec_body,
        re.IGNORECASE,
    )
    assert "em.[ConversationId] COLLATE Latin1_General_BIN = @ConversationId" in exec_body
    # newest-first — asserted on the strict statement only (the fuzzy branch has its
    # own DESC, which is why an unanchored search could not pin this one)
    strict = exec_body[: exec_body.index("IF @MatchedExpenseId IS NULL")]
    assert re.search(r"SELECT\s+TOP\s+1\s+@MatchedExpenseId", strict, re.IGNORECASE), (
        "strict branch must pick exactly one row (TOP 1) — a wider TOP lets the LAST row win the assignment"
    )
    assert "ORDER BY e.[CreatedDatetime] DESC;" in strict, "strict branch must order newest-first"

    # fuzzy branch: draft Expenses only, exact reference number, project-name hint
    assert "e.[IsDraft] = 1" in exec_body
    assert "e.[ReferenceNumber] = @ReferenceNumberNorm" in exec_body
    assert "dbo.[ExpenseLineItem]" in exec_body
    assert "eli.[ExpenseId] = e.[Id]" in exec_body
    assert "eli.[ProjectId]" in exec_body
    assert "LOWER(p.[Name]) LIKE @ProjectHintLike" in exec_body

    # single-result requirement
    assert re.search(r"SELECT\s+TOP\s+2", exec_body, re.IGNORECASE)
    assert "(SELECT COUNT(*) FROM @CandidateIds) = 1" in exec_body

    # match kinds, and ConversationId only on a strict hit
    assert "'conversation'" in exec_body
    assert "'fuzzy'" in exec_body
    assert re.search(
        r"CASE\s+WHEN\s+@MatchKind\s*=\s*'conversation'\s+THEN\s+em\.\[ConversationId\]\s+ELSE\s+NULL\s+END",
        exec_body,
        re.IGNORECASE,
    )


def test_find_expense_for_reviewer_reply_project_hint_like_escapes_bracket_first():
    exec_body = strip_sql_comments(sproc_body(EXPENSE_SQL, "FindExpenseForReviewerReply"))
    match = re.search(
        r"DECLARE\s+@ProjectHintLike\s+NVARCHAR\([^)]+\)\s*=\s*(.+?);",
        exec_body,
        re.IGNORECASE | re.DOTALL,
    )
    assert match is not None, "expected @ProjectHintLike assignment"
    expr = match.group(1)
    assert "'[', '[[]'" in expr, "LIKE pattern must escape [ as [[]"
    assert "'%', '[%]'" in expr
    assert "'_', '[_]'" in expr
    assert expr.index("'[', '[[]'") < expr.index("'%', '[%]'"), (
        "escape [ before % and _ (later replacements introduce brackets)"
    )


def test_expense_specialist_prompt_reviewer_reply_allows_conversation_lookup():
    text = EXPENSE_SPECIALIST_PROMPT.read_text()
    section = next(s for s in text.split("\n# ") if s.startswith("Reviewer-reply flow"))
    assert "find_expense_by_conversation_id" in section
    assert "flagged_needs_review" in section
    assert "not built yet" not in text
    assert "do **not** have `find_expense_by_conversation_id`" not in text


# ---------------------------------------------------------------------------
# 1b — ExpenseRepository.find_for_reviewer_reply
# ---------------------------------------------------------------------------


def _find(row, **kwargs):
    """Run the repo method against a stubbed connection whose fetchone returns `row`;
    returns (result, call_procedure mock)."""
    with patch("entities.expense.persistence.repo.get_connection") as gc, patch(
        "entities.expense.persistence.repo.call_procedure"
    ) as cp:
        gc.return_value.__enter__.return_value.cursor.return_value.fetchone.return_value = row
        return ExpenseRepository().find_for_reviewer_reply(**kwargs), cp


def test_expense_repo_find_for_reviewer_reply_maps_row_and_calls_sproc():
    row = SimpleNamespace(
        Id=42,
        PublicId="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        ReferenceNumber="RCT-99",
        TotalAmount=Decimal("123.45"),
        IsDraft=1,
        CreatedDatetime="2026-10-01 12:00:00",
        VendorName="Acme Fuel",
        SourceEmailMessageId=9001,
        ConversationId="conv-strict",
        MatchKind="conversation",
    )
    result, cp = _find(row, conversation_id="conv-strict", reference_number_hint="RCT-99", project_hint="Buffalo")

    cp.assert_called_once()
    assert cp.call_args.kwargs["name"] == "FindExpenseForReviewerReply"
    assert cp.call_args.kwargs["params"] == {
        "ConversationId": "conv-strict",
        "ReferenceNumberHint": "RCT-99",
        "ProjectHint": "Buffalo",
    }
    assert result == {
        "id": 42,
        "public_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "reference_number": "RCT-99",
        "total_amount": Decimal("123.45"),
        "is_draft": True,
        "created_datetime": "2026-10-01 12:00:00",
        "vendor_name": "Acme Fuel",
        "source_email_message_id": 9001,
        "conversation_id": "conv-strict",
        "match_kind": "conversation",
    }
    assert isinstance(result["total_amount"], Decimal)


def test_expense_repo_find_for_reviewer_reply_null_total_amount():
    row = SimpleNamespace(
        Id=1,
        PublicId="11111111-1111-1111-1111-111111111111",
        ReferenceNumber="X",
        TotalAmount=None,
        IsDraft=1,
        CreatedDatetime=None,
        VendorName=None,
        SourceEmailMessageId=None,
        ConversationId=None,
        MatchKind="fuzzy",
    )
    result, _ = _find(row, conversation_id="c")
    assert result is not None
    assert result["total_amount"] is None


def test_expense_repo_find_for_reviewer_reply_returns_none_when_no_row():
    result, _ = _find(None, conversation_id="missing")
    assert result is None


# ---------------------------------------------------------------------------
# 1c — ExpenseService.find_for_reviewer_reply + access gate
# ---------------------------------------------------------------------------


def _service(repo_result):
    """An ExpenseService with only the repo it needs — bypasses the constructor's
    live wiring; the method under test reads `self.repo` alone."""
    from entities.expense.business.service import ExpenseService

    svc = ExpenseService.__new__(ExpenseService)
    svc.repo = MagicMock()
    svc.repo.find_for_reviewer_reply.return_value = repo_result
    return svc


def test_expense_service_find_for_reviewer_reply_gates_and_returns_row():
    slim = {"id": 7, "public_id": "p", "match_kind": "conversation"}
    with patch("entities.expense.business.service.assert_can_access_expense") as gate:
        out = _service(slim).find_for_reviewer_reply(conversation_id="conv-1")
    assert out == slim
    gate.assert_called_once_with(7)


def test_expense_service_find_for_reviewer_reply_skips_gate_when_no_match():
    with patch("entities.expense.business.service.assert_can_access_expense") as gate:
        assert _service(None).find_for_reviewer_reply(conversation_id="x") is None
    gate.assert_not_called()


def test_expense_service_find_for_reviewer_reply_hides_inaccessible_match_as_no_match():
    """A match the caller may not read answers exactly like no match (None → 200/null),
    so the fuzzy hints cannot be used as an existence oracle for drafts outside the
    caller's projects — the same reason EntityNotAccessibleError maps to 404 elsewhere."""
    with patch(
        "entities.expense.business.service.assert_can_access_expense",
        side_effect=EntityNotAccessibleError("Expense", 99),
    ) as gate:
        assert _service({"id": 99, "public_id": "p"}).find_for_reviewer_reply(conversation_id="conv") is None
    gate.assert_called_once_with(99)


# ---------------------------------------------------------------------------
# 1d — Router: path, RBAC, declaration order, handler envelope
# ---------------------------------------------------------------------------


def _routes():
    import entities.expense.api.router as expense_router

    return expense_router.router.routes


def test_find_expense_by_conversation_id_route_registered_with_expenses_rbac():
    endpoint = next((r.endpoint for r in _routes() if r.path == FIND_ROUTE and "GET" in r.methods), None)
    assert endpoint is not None, f"no GET route at {FIND_ROUTE}"
    _assert_current_user_rbac(endpoint, Modules.EXPENSES, "can_read")


def test_find_expense_by_conversation_id_route_is_before_public_id_catchall():
    paths = [r.path for r in _routes()]
    find_idx, public_idx = paths.index(FIND_ROUTE), paths.index(PUBLIC_ID_ROUTE)
    assert find_idx < public_idx, (
        f"find-by-conversation-id must register before the {{public_id}} catch-all "
        f"(find={find_idx}, public_id={public_idx})"
    )


def test_find_expense_by_conversation_id_router_returns_item_response():
    from entities.expense.api.router import find_expense_by_conversation_id_router

    payload = {"public_id": "exp-pub", "reference_number": "R-1", "match_kind": "fuzzy"}
    with patch("entities.expense.api.router.ExpenseService") as Svc:
        Svc.return_value.find_for_reviewer_reply.return_value = payload
        out = find_expense_by_conversation_id_router(
            conversation_id="conv-abc",
            reference_number_hint="R-1",
            project_hint="Site A",
            current_user={"sub": "user"},
        )
    Svc.return_value.find_for_reviewer_reply.assert_called_once_with(
        conversation_id="conv-abc", reference_number_hint="R-1", project_hint="Site A"
    )
    assert out == item_response(payload)


# ---------------------------------------------------------------------------
# 1e — expense_specialist allowlist
# ---------------------------------------------------------------------------


def test_find_expense_by_conversation_id_is_allowlisted_on_expense_specialist():
    from intelligence.agents.expense_specialist.definition import expense_specialist

    assert "find_expense_by_conversation_id" in expense_specialist.tools
