"""U-484 — exclude terminal ExpenseCodingItem statuses from the coding queue.

Pure-logic / source pins — no live DB. Finished work (`written`,
`resolved_externally`) must not appear in the operator work list, while NULL-status
rows (no ExpenseCodingItem yet) must stay visible so lazy reseed fires.
"""

import re
from unittest.mock import MagicMock, patch

import pytest

from integrations.intuit.qbo.purchase.business.service import QboPurchaseService
from tests.sproc_text import REPO_ROOT, sproc_body, strip_sql_comments

QUEUE_SQL = (
    REPO_ROOT / "integrations/intuit/qbo/purchase/sql/qbo.expense_coding_queue.sql"
)

TERMINAL_QUEUE_EXCLUDED_STATUSES = frozenset({"written", "resolved_externally"})

FULL_CODING_STATUS_VOCABULARY = (
    "pending",
    "suggested",
    "flagged",
    "confirmed",
    "enqueued",
    "written",
    "changed_in_qbo",
    "error",
    "resolved_externally",
    None,
)

_TERMINAL_EXCLUSION_PATTERN = re.compile(
    r"eci\.\[Status\]\s+IS\s+NULL\s+OR\s+eci\.\[Status\]\s+NOT\s+IN\s*\([^)]*\)",
    re.IGNORECASE | re.DOTALL,
)

_GUARDED_NOT_IN_GROUP_PATTERN = re.compile(
    r"\(\s*eci\.\[Status\]\s+IS\s+NULL\s+OR\s+eci\.\[Status\]\s+NOT\s+IN\s*\([^)]*\)\s*\)",
    re.IGNORECASE | re.DOTALL,
)


def _queue_body() -> str:
    return strip_sql_comments(sproc_body(QUEUE_SQL, "ReadExpenseCodingQueue"))


def _metrics_body() -> str:
    return strip_sql_comments(sproc_body(QUEUE_SQL, "ReadExpenseCodingMetrics"))


def _collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _queue_join_on_and_where() -> tuple[str, str]:
    """Return (LEFT JOIN ... ON span, WHERE ... ORDER BY span) for the queue sproc."""
    body = _queue_body()
    join_match = re.search(
        r"LEFT\s+JOIN\s+\[dbo\]\.\[ExpenseCodingItem\]\s+eci\s+ON\s+(.+?)\s+WHERE\b",
        body,
        re.IGNORECASE | re.DOTALL,
    )
    assert join_match is not None, (
        "ReadExpenseCodingQueue must LEFT JOIN ExpenseCodingItem before WHERE"
    )
    where_match = re.search(
        r"\bWHERE\b(.+?)\bORDER\s+BY\b",
        body,
        re.IGNORECASE | re.DOTALL,
    )
    assert where_match is not None, (
        "ReadExpenseCodingQueue must have WHERE ... ORDER BY"
    )
    return join_match.group(1), where_match.group(1)


def _terminal_exclusion_list(body: str) -> str:
    """Return the NOT IN (...) chunk for the queue's terminal-status exclusion."""
    match = re.search(
        r"eci\.\[Status\]\s+IS\s+NULL\s+OR\s+eci\.\[Status\]\s+NOT\s+IN\s*\(([^)]+)\)",
        body,
        re.IGNORECASE | re.DOTALL,
    )
    assert match is not None, (
        "ReadExpenseCodingQueue must use NULL-safe terminal exclusion "
        "(eci.[Status] IS NULL OR eci.[Status] NOT IN (...))"
    )
    return match.group(1)


# ---------------------------------------------------------------------------
# 1 — NULL-safe terminal exclusion (highest-value pin)
# ---------------------------------------------------------------------------


def test_queue_terminal_exclusion_is_null_safe():
    body = _queue_body()
    assert re.search(
        r"eci\.\[Status\]\s+IS\s+NULL\s+OR\s+eci\.\[Status\]\s+NOT\s+IN\s*\(",
        body,
        re.IGNORECASE,
    ), (
        "ReadExpenseCodingQueue must guard NOT IN with eci.[Status] IS NULL OR — "
        "NULL NOT IN (...) drops unseeded LEFT JOIN rows and breaks lazy reseed"
    )
    guarded_spans = [
        (m.start(), m.end()) for m in _GUARDED_NOT_IN_GROUP_PATTERN.finditer(body)
    ]
    assert guarded_spans, (
        "ReadExpenseCodingQueue must wrap terminal exclusion in "
        "(eci.[Status] IS NULL OR eci.[Status] NOT IN (...))"
    )
    for match in re.finditer(r"eci\.\[Status\]\s+NOT\s+IN", body, re.IGNORECASE):
        pos = match.start()
        in_guarded_group = any(start <= pos < end for start, end in guarded_spans)
        assert in_guarded_group, (
            "Found eci.[Status] NOT IN outside its own (IS NULL OR NOT IN) group — "
            "proximity to a neighbour guard is not sufficient; each NOT IN needs "
            "its own IS NULL OR within the same parenthesized expression"
        )


# ---------------------------------------------------------------------------
# 1b — terminal exclusion must live in WHERE, not JOIN ON (H1)
# ---------------------------------------------------------------------------


def test_queue_terminal_exclusion_in_where_not_join():
    join_on, where_clause = _queue_join_on_and_where()
    assert not re.search(r"eci\.\[Status\]", join_on, re.IGNORECASE), (
        "eci.[Status] must not appear in the LEFT JOIN ... ON clause — "
        "join-clause filtering re-admits terminal rows with NULL eci columns "
        "(lazy reseed on already-seeded lines + COALESCE(NULL,NULL) scoping bypass)"
    )
    assert _TERMINAL_EXCLUSION_PATTERN.search(where_clause), (
        "terminal-status exclusion must be a conjunct of the WHERE clause "
        "(after WHERE, before ORDER BY), not relocated into the JOIN ON span"
    )


# ---------------------------------------------------------------------------
# 1c — terminal exclusion must be AND-conjoined, not OR (H2)
# ---------------------------------------------------------------------------


def test_queue_terminal_exclusion_and_not_or():
    _, where_clause = _queue_join_on_and_where()
    assert re.search(
        r"\bAND\s*\(\s*eci\.\[Status\]\s+IS\s+NULL\s+OR\s+eci\.\[Status\]\s+NOT\s+IN",
        where_clause,
        re.IGNORECASE,
    ), (
        "terminal-status exclusion must be introduced with AND — "
        "OR would parse as (A AND B) OR C and admit non-58999 purchase lines"
    )
    assert not re.search(
        r"\bOR\s*\(\s*eci\.\[Status\]\s+IS\s+NULL\s+OR\s+eci\.\[Status\]\s+NOT\s+IN",
        where_clause,
        re.IGNORECASE,
    ), (
        "terminal-status exclusion must not be OR-conjoined — "
        "WHERE A AND B OR C widens the queue beyond NEED TO CATEGORIZE lines"
    )


# ---------------------------------------------------------------------------
# 2 — lazy reseed still fires for unseeded rows
# ---------------------------------------------------------------------------


class _FakeLineRepoReseed:
    def __init__(self, queue_sequences):
        self._sequences = queue_sequences
        self._call_index = 0
        self.queue_calls = []

    def read_expense_coding_queue(
        self, realm_id=None, actor_user_id=None, actor_is_system_admin=None
    ):
        self.queue_calls.append((realm_id, actor_user_id, actor_is_system_admin))
        seq = self._sequences[min(self._call_index, len(self._sequences) - 1)]
        self._call_index += 1
        return seq


@patch("entities.expense_coding_item.business.service.ExpenseCodingItemService")
def test_lazy_reseed_fires_when_coding_item_missing(mock_coding_svc_cls):
    unseeded_row = {
        "qbo_purchase_id": 1,
        "qbo_purchase_line_id": 10,
        "coding_item_public_id": None,
        "qbo_line_id": "1",
        "qbo_purchase_qbo_id": "p1",
        "realm_id": "R1",
        "vendor_qbo_id": "v1",
    }
    seeded_row = {**unseeded_row, "coding_item_public_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"}

    mock_coding_svc = MagicMock()
    mock_coding_svc_cls.return_value = mock_coding_svc

    fake = _FakeLineRepoReseed([[unseeded_row], [seeded_row]])
    svc = QboPurchaseService(line_repo=fake)
    result = svc.get_expense_coding_queue(realm_id="R1")

    mock_coding_svc.upsert_from_queue.assert_called_once()
    assert len(fake.queue_calls) == 2
    assert result == [seeded_row]


@patch("entities.expense_coding_item.business.service.ExpenseCodingItemService")
def test_lazy_reseed_skipped_when_all_rows_seeded(mock_coding_svc_cls):
    seeded_row = {
        "qbo_purchase_id": 1,
        "qbo_purchase_line_id": 10,
        "coding_item_public_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    }

    mock_coding_svc = MagicMock()
    mock_coding_svc_cls.return_value = mock_coding_svc

    fake = _FakeLineRepoReseed([[seeded_row]])
    svc = QboPurchaseService(line_repo=fake)
    result = svc.get_expense_coding_queue(realm_id="R1")

    mock_coding_svc.upsert_from_queue.assert_not_called()
    assert len(fake.queue_calls) == 1
    assert result == [seeded_row]


# ---------------------------------------------------------------------------
# 3 — exclusion list is exactly the two terminal statuses
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", FULL_CODING_STATUS_VOCABULARY)
def test_queue_terminal_exclusion_vocabulary(status):
    body = _queue_body()
    exclusion_chunk = _terminal_exclusion_list(body).lower()
    if status is None:
        assert re.search(r"eci\.\[Status\]\s+IS\s+NULL", body, re.IGNORECASE), (
            "unseeded rows (NULL eci.[Status]) must be guarded by IS NULL, not NOT IN"
        )
        assert "n'null'" not in exclusion_chunk, (
            "NULL must not appear in the NOT IN list — use IS NULL OR instead"
        )
        return
    token = f"n'{status}'"
    if status in TERMINAL_QUEUE_EXCLUDED_STATUSES:
        assert token in exclusion_chunk, (
            f"terminal status {status!r} must appear in queue NOT IN list"
        )
    else:
        assert token not in exclusion_chunk, (
            f"non-terminal status {status!r} must NOT appear in queue NOT IN list"
        )


# ---------------------------------------------------------------------------
# 4 — metrics TotalTargetLines deliberately keeps all targeted lines
# ---------------------------------------------------------------------------


def test_metrics_total_target_lines_keeps_terminal_rows():
    body = _metrics_body()
    total_subquery_match = re.search(
        r"SELECT\s+COUNT\(\*\).*?AS\s+\[TotalTargetLines\]",
        body,
        re.IGNORECASE | re.DOTALL,
    )
    assert total_subquery_match is not None
    total_subquery = total_subquery_match.group(0)
    collapsed = _collapse_whitespace(total_subquery)
    assert _collapse_whitespace("eci.[Status] NOT IN") not in collapsed
    assert not re.search(
        r"eci\.\[Status\]\s+IS\s+NULL\s+OR\s+eci\.\[Status\]\s+NOT\s+IN",
        total_subquery,
        re.IGNORECASE,
    ), "TotalTargetLines must NOT exclude terminal statuses (U-484 divergence)"


def test_metrics_documents_intentional_divergence_from_queue():
    raw = sproc_body(QUEUE_SQL, "ReadExpenseCodingMetrics")
    assert re.search(r"U-484.*diverg", raw, re.IGNORECASE), (
        "ReadExpenseCodingMetrics must document intentional U-484 divergence from queue"
    )


# ---------------------------------------------------------------------------
# 5 — queue hygiene + landmine scoping counts unchanged
# ---------------------------------------------------------------------------


def test_queue_sproc_starts_with_set_nocount_on_and_is_go_terminated():
    body = sproc_body(QUEUE_SQL, "ReadExpenseCodingQueue")
    after_as = body.split("AS", 1)[1]
    assert "SET NOCOUNT ON" in after_as.split("BEGIN", 1)[1][:80]
    text = QUEUE_SQL.read_text()
    match = re.search(
        r"CREATE\s+OR\s+ALTER\s+PROCEDURE\s+(?:dbo\.)?ReadExpenseCodingQueue\b.*?^GO\s*$",
        text,
        re.DOTALL | re.MULTILINE | re.IGNORECASE,
    )
    assert match is not None


def test_landmine_scoping_counts_unchanged():
    sql = QUEUE_SQL.read_text()
    assert sql.count("dbo.UserCanAccessProject(") == 3
    assert sql.count("COALESCE(eci.[ConfirmedProjectId], eci.[SuggestedProjectId]) IS NULL") == 3
    assert sql.count("@ActorIsSystemAdmin = 1") == 3


# ---------------------------------------------------------------------------
# 6 — suggest_pending eligibility triple (first test coverage)
# ---------------------------------------------------------------------------


def test_suggest_pending_eligibility_triple():
    from entities.expense_coding_item.business.suggestion_service import (
        ExpenseCodingSuggestionService,
    )

    rows = [
        {
            "coding_status": "pending",
            "coding_item_public_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "suggestion_source": None,
        },
        {
            "coding_status": "suggested",
            "coding_item_public_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            "suggestion_source": None,
        },
        {
            "coding_status": "pending",
            "coding_item_public_id": None,
            "suggestion_source": None,
        },
        {
            "coding_status": "pending",
            "coding_item_public_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
            "suggestion_source": "vendor_history",
        },
    ]

    svc = ExpenseCodingSuggestionService()
    svc.qbo_purchase_service = MagicMock()
    svc.qbo_purchase_service.get_expense_coding_queue.return_value = rows
    svc.coding_item_service = MagicMock()
    svc.suggest_for_item = MagicMock(
        return_value={"status": "flagged", "reason": "ineligible fixture"}
    )

    svc.suggest_pending(realm_id="R1", max_items=10)

    assert svc.suggest_for_item.call_count == 1
    called_item = svc.suggest_for_item.call_args[0][0]
    assert str(called_item.public_id) == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
