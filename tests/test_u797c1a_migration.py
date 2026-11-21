"""U-797c1a: static checks on the one-row data correction (never executed by the suite)."""
from __future__ import annotations

import re

from tests.sproc_text import REPO_ROOT

_PATH = REPO_ROOT / "scripts" / "migrations" / "2026_11_21_u797c1a_clear_repurposed_draft_conversation.sql"


def _text() -> str:
    return _PATH.read_text(encoding="utf-8")


def _code() -> str:
    """The migration with `--` comment lines removed."""
    return "\n".join(line for line in _text().splitlines() if not line.lstrip().startswith("--"))


def test_migration_file_exists():
    assert _PATH.is_file()


def test_exactly_one_update_statement():
    assert len(re.findall(r"\bUPDATE\b", _code(), flags=re.IGNORECASE)) == 1


def test_update_targets_only_review_notification():
    code = _code()
    assert re.search(r"UPDATE\s+dbo\.\[ReviewNotification\]", code, flags=re.IGNORECASE)
    tables = set(re.findall(r"\b(?:UPDATE|INSERT\s+INTO|DELETE\s+FROM|MERGE\s+INTO|FROM|JOIN)\s+dbo\.\[?(\w+)\]?",
                            code, flags=re.IGNORECASE))
    assert tables == {"ReviewNotification"}


def test_update_clears_the_three_draft_identity_columns_only():
    code = _code()
    for col in ("ConversationId", "InternetMessageId", "DraftGraphMessageId"):
        assert re.search(rf"\[{col}\]\s*=\s*NULL", code), col
    set_clause = re.search(r"SET\s+(.*?)\bWHERE\b", code, flags=re.IGNORECASE | re.DOTALL).group(1)
    assigned = re.findall(r"\[(\w+)\]\s*=", set_clause)
    assert sorted(assigned) == ["ConversationId", "DraftGraphMessageId", "InternetMessageId"]


def test_all_guards_are_present_in_the_where_clause():
    code = _code()
    where = re.search(r"\bWHERE\b(.*?);", code, flags=re.IGNORECASE | re.DOTALL).group(1)
    assert re.search(r"\[Id\]\s*=\s*1089", where)
    assert re.search(r"\[Kind\]\s*=\s*N'contract_labor'", where)
    assert "N'Contract Labor - MR2-MAIN - 2026-10-07'" in where
    assert re.search(r"\[ProjectId\]\s*=\s*93\b", where)
    assert re.search(r"\[WorkDate\]\s*=\s*'2026-10-07'", where)
    assert re.search(r"\[ConversationId\]\s+IS\s+NOT\s+NULL", where, flags=re.IGNORECASE)


def test_batch_is_go_terminated_and_has_no_rollback():
    text = _text()
    assert "rollback" not in text.lower()
    assert text.rstrip().splitlines()[-1].strip() == "GO"
    assert re.search(r"^\s*GO\s*$", text, flags=re.MULTILINE)


def test_ends_with_a_select_of_the_row_and_whether_conversation_is_null():
    code = _code().rstrip()
    tail = code[code.upper().rfind("SELECT"):]
    assert "[Id] = 1089" in tail or "[Id]" in tail
    assert "ConversationId" in tail and "IS NULL" in tail.upper()
    assert "FROM dbo.[ReviewNotification]" in tail


def test_header_states_why_the_correction_is_needed():
    head = "\n".join(_text().splitlines()[:25])
    assert "MR2-MAIN" in head and "HP2" in head and ("10/7" in head or "2026-10-07" in head)
