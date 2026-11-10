"""U-652d6 projection parity: the coding-suggestion row mapper ``_from_db`` reads its
columns with ``getattr(row, "X", None)``, so a column missing from a proc's SELECT /
OUTPUT list silently becomes ``None`` instead of failing. This test locks, for every
full-row CodingSuggestion projection, that the projected column set equals the
mapper's column set minus an explicit per-proc ALLOW dict (each allowed gap carries a
one-line reason). The mapper set is derived from the source with ``ast``, never
hand-copied. Procs are parsed from the SQL text; the list of full-row procs is explicit,
and a discovery test fails if a new full-row proc appears outside it.
"""

import ast
import re
from functools import lru_cache
from pathlib import Path

import pytest

from tests.test_update_read_column_parity import (
    _split_top_level_commas,
    _strip_sql_comments,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
MAPPER_PATH = REPO_ROOT / "entities/coding_suggestion/persistence/repo.py"
SQL_FILES = (
    REPO_ROOT / "entities/coding_suggestion/sql/dbo.coding_suggestion.sql",
    REPO_ROOT / "entities/coding_suggestion/sql/bill_coding_suggestion.sql",
)

# Every proc whose result is mapped by CodingSuggestionRepository._from_db.
FULL_ROW_PROCS = frozenset(
    {
        "ReadCodingSuggestionByPublicId",
        "ReadCodingSuggestionByBillId",
        "RecordCodingSuggestion",
        "RecordCodingSuggestionFlag",
        "ClaimCodingSuggestion",
        "ReleaseCodingSuggestion",
        "RecordCodingSuggestionConfirmation",
        "MarkCodingSuggestionEnqueued",
        "MarkCodingSuggestionWritten",
        "MarkCodingSuggestionChangedInQbo",
        "MarkCodingSuggestionError",
        "AdoptCodingSuggestionWrittenRecord",
        "UpsertCodingSuggestionForBill",
        "UpsertCodingSuggestionForPurchase",
        "MarkCodingSuggestionResolvedExternally",
    }
)

# Columns that identify a full-row CodingSuggestion projection. A select/output list
# carrying all of these is treated as a full-row projection by the discovery test.
_FULL_ROW_MARKERS = frozenset(
    {"PublicId", "SuggestedProjectId", "ConfirmedProjectId", "WrittenAt"}
)

# Non-mapped procs that nonetheless carry a full-row-shaped list (none today).
_NOT_MAPPED_FULL_ROW: dict[str, str] = {}

# Reasons for intentional gaps. Each was verified against production readers: every
# reader of a write-state column reaches it through read_by_public_id (which projects
# it), and no production code reads budget_* off a repo row or route off a suggestion.
_R_BUDGET = (
    "bill-only budget gate; no production reader takes budget off a purchase or "
    "by-public-id row (presenter consumes the domain BillSuggestion)"
)
_R_REPOINT = (
    "purchase repoint targets; read only via read_by_public_id (service.py:952/965, "
    "_confirm_under_lock) and the setter result is not read for them"
)
_R_WRITTEN = (
    "written-record fence; read only via read_by_public_id (service.py:741/962/1003/"
    "1390/1463, worker.py:1488, expense/service.py:1166/1219), setter result unread"
)
_R_WRITTEN_BILL = (
    "purchase-only written record; bill rows are never QBO-written through the coding "
    "queue and read_by_bill_id has no consumer of it"
)
_R_REPOINT_BILL = "purchase-only repoint targets; bill rows never repoint"
_R_ROUTE = "dead column (no writer, no reader)"

_REASON_FOR = {
    "BudgetCheck": _R_BUDGET,
    "BudgetRemaining": _R_BUDGET,
    "RepointTargetsJson": _R_REPOINT,
    "WrittenLinesJson": _R_WRITTEN,
    "WrittenSyncToken": _R_WRITTEN,
    "Route": _R_ROUTE,
}
_REASON_FOR_BILL = dict(_REASON_FOR, RepointTargetsJson=_R_REPOINT_BILL, WrittenLinesJson=_R_WRITTEN_BILL, WrittenSyncToken=_R_WRITTEN_BILL)

# Gap columns per proc (mapper columns the proc's full-row projection omits).
_ALLOW_COLUMNS: dict[str, frozenset[str]] = {
    "ReadCodingSuggestionByPublicId": frozenset({"BudgetCheck", "BudgetRemaining", "Route"}),
    "ReadCodingSuggestionByBillId": frozenset({"RepointTargetsJson", "Route", "WrittenLinesJson", "WrittenSyncToken"}),
    "RecordCodingSuggestion": frozenset({"BudgetCheck", "BudgetRemaining", "RepointTargetsJson", "Route", "WrittenLinesJson", "WrittenSyncToken"}),
    "RecordCodingSuggestionFlag": frozenset({"BudgetCheck", "BudgetRemaining", "RepointTargetsJson", "Route", "WrittenLinesJson", "WrittenSyncToken"}),
    "ClaimCodingSuggestion": frozenset({"BudgetCheck", "BudgetRemaining", "RepointTargetsJson", "Route", "WrittenLinesJson", "WrittenSyncToken"}),
    "ReleaseCodingSuggestion": frozenset({"BudgetCheck", "BudgetRemaining", "RepointTargetsJson", "Route", "WrittenLinesJson", "WrittenSyncToken"}),
    "RecordCodingSuggestionConfirmation": frozenset({"BudgetCheck", "BudgetRemaining", "Route", "WrittenLinesJson", "WrittenSyncToken"}),
    "MarkCodingSuggestionEnqueued": frozenset({"BudgetCheck", "BudgetRemaining", "RepointTargetsJson", "Route", "WrittenLinesJson", "WrittenSyncToken"}),
    "MarkCodingSuggestionWritten": frozenset({"BudgetCheck", "BudgetRemaining", "RepointTargetsJson", "Route"}),
    "MarkCodingSuggestionChangedInQbo": frozenset({"BudgetCheck", "BudgetRemaining", "RepointTargetsJson", "Route", "WrittenLinesJson", "WrittenSyncToken"}),
    "MarkCodingSuggestionError": frozenset({"BudgetCheck", "BudgetRemaining", "RepointTargetsJson", "Route", "WrittenLinesJson", "WrittenSyncToken"}),
    "AdoptCodingSuggestionWrittenRecord": frozenset({"BudgetCheck", "BudgetRemaining", "RepointTargetsJson", "Route"}),
    "UpsertCodingSuggestionForBill": frozenset({"RepointTargetsJson", "Route", "WrittenLinesJson", "WrittenSyncToken"}),
    "UpsertCodingSuggestionForPurchase": frozenset({"BudgetCheck", "BudgetRemaining", "RepointTargetsJson", "Route", "WrittenLinesJson", "WrittenSyncToken"}),
    "MarkCodingSuggestionResolvedExternally": frozenset({"BudgetCheck", "BudgetRemaining", "RepointTargetsJson", "Route", "WrittenLinesJson", "WrittenSyncToken"}),
}
_BILL_PROCS = frozenset({"ReadCodingSuggestionByBillId", "UpsertCodingSuggestionForBill"})

# Per-proc allow-list: mapper column -> one-line reason it is intentionally absent
# from that proc's projection. Stale entries (allowed but actually projected, or no
# longer in the mapper) fail the parity test, so this stays exact.
ALLOW: dict[str, dict[str, str]] = {
    proc: {
        col: (_REASON_FOR_BILL if proc in _BILL_PROCS else _REASON_FOR)[col]
        for col in cols
    }
    for proc, cols in _ALLOW_COLUMNS.items()
}

_PROC_HEADER = re.compile(
    r"CREATE\s+(?:OR\s+ALTER\s+)?PROCEDURE\s+(?:\[?dbo\]?\.)?\[?(\w+)\]?",
    re.IGNORECASE,
)
_SCOPE_TOKEN = re.compile(
    r"[()\;]|\b(?:SELECT|OUTPUT|FROM|INTO|WHERE|SET|UNION)\b", re.IGNORECASE
)
_LIST_STOP = frozenset({"FROM", "INTO", "WHERE", "SET", "UNION", ";"})
_ASSIGNMENT_ITEM = re.compile(r"^@\w+\s*=")
_TOP_PREFIX = re.compile(r"^(?:DISTINCT\s+|TOP\s*\([^)]*\)\s*)+", re.IGNORECASE)
_ALIAS_TAIL = re.compile(r"\bAS\s+\[?(\w+)\]?\s*$", re.IGNORECASE)
_NAME_TAIL = re.compile(r"\[?(\w+)\]?\s*$")


@lru_cache(maxsize=1)
def _mapper_columns() -> frozenset[str]:
    """Columns read by CodingSuggestionRepository._from_db, derived via ast."""
    tree = ast.parse(MAPPER_PATH.read_text(encoding="utf-8"))
    cls = next(
        n
        for n in tree.body
        if isinstance(n, ast.ClassDef) and n.name == "CodingSuggestionRepository"
    )
    fn = next(
        n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_from_db"
    )
    cols: set[str] = set()
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            cols.add(node.args[1].value)
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "row"
        ):
            cols.add(node.attr)
    return frozenset(cols)


@lru_cache(maxsize=1)
def _proc_bodies() -> dict[str, str]:
    bodies: dict[str, str] = {}
    for path in SQL_FILES:
        text = _strip_sql_comments(path.read_text(encoding="utf-8"))
        headers = list(_PROC_HEADER.finditer(text))
        for idx, match in enumerate(headers):
            end = headers[idx + 1].start() if idx + 1 < len(headers) else len(text)
            bodies[match.group(1)] = text[match.start() : end]
    return bodies


def _item_name(item: str) -> str | None:
    item = _TOP_PREFIX.sub("", item.strip()).strip()
    if item == "*" or item.endswith(".*"):
        return "*"
    alias = _ALIAS_TAIL.search(item)
    if alias:
        return alias.group(1)
    tail = _NAME_TAIL.search(item)
    return tail.group(1) if tail else None


def _projection_lists(body: str) -> list[frozenset[str]]:
    """Every SELECT / OUTPUT projection list in a proc body, at any nesting depth.

    Assignment selects (``SELECT @x = ...``) are skipped; a list with a bare star is
    returned with ``*`` in its names so the caller can fail loudly on it.
    """
    events: list[tuple[int, str, int, int]] = []  # (pos, kind, depth, end)
    depth = 0
    for match in _SCOPE_TOKEN.finditer(body):
        token = match.group(0).upper()
        if token == "(":
            depth += 1
        elif token == ")":
            depth -= 1
            events.append((match.start(), ")", depth, match.end()))
        else:
            events.append((match.start(), token, depth, match.end()))

    lists: list[frozenset[str]] = []
    for idx, (pos, kind, lvl, end) in enumerate(events):
        if kind not in ("SELECT", "OUTPUT"):
            continue
        stop = None
        for later in events[idx + 1 :]:
            _, lkind, llvl, _ = later
            if lkind == ")" and llvl < lvl:
                stop = later
                break
            if llvl == lvl and lkind in _LIST_STOP:
                stop = later
                break
        stop_pos = stop[0] if stop else len(body)
        segment = body[end:stop_pos]
        items = [i for i in _split_top_level_commas(segment) if i.strip()]
        if any(_ASSIGNMENT_ITEM.match(_TOP_PREFIX.sub("", i.strip())) for i in items):
            continue
        names = {_item_name(i) for i in items}
        names.discard(None)
        lists.append(frozenset(names))
    return lists


@lru_cache(maxsize=1)
def _discovered_full_row_procs() -> frozenset[str]:
    found = set()
    for name, body in _proc_bodies().items():
        if any(_FULL_ROW_MARKERS <= names for names in _projection_lists(body)):
            found.add(name)
    return frozenset(found)


def test_mapper_column_set_derived_from_source():
    cols = _mapper_columns()
    # Guard against the ast walk silently returning an empty / truncated set.
    assert len(cols) >= 45, f"_from_db parse looks broken, got {len(cols)} columns"
    assert {"Id", "PublicId", "Status", "BudgetCheck", "Route"} <= cols


def test_full_row_proc_list_is_exhaustive():
    unlisted = sorted(
        _discovered_full_row_procs() - FULL_ROW_PROCS - set(_NOT_MAPPED_FULL_ROW)
    )
    assert not unlisted, (
        f"Full-row CodingSuggestion projections not registered in FULL_ROW_PROCS "
        f"(their rows reach _from_db unchecked): {unlisted}"
    )
    missing_defs = sorted(FULL_ROW_PROCS - set(_proc_bodies()))
    assert not missing_defs, f"FULL_ROW_PROCS names no proc in the SQL files: {missing_defs}"


@pytest.mark.parametrize("proc", sorted(FULL_ROW_PROCS))
def test_full_row_projection_matches_mapper(proc):
    body = _proc_bodies()[proc]
    lists = [names for names in _projection_lists(body) if _FULL_ROW_MARKERS <= names]
    assert lists, f"{proc}: no full-row projection list found (parser miss?)"

    mapper = _mapper_columns()
    allowed = ALLOW.get(proc, {})
    for names in lists:
        assert "*" not in names, f"{proc}: bare star projection cannot be parity-checked"
        missing = mapper - names
        extra = names - mapper
        assert set(missing) == set(allowed), (
            f"{proc}: mapper columns absent from projection = {sorted(missing)}; "
            f"allow-listed = {sorted(allowed)}. Unlisted gaps read as None in _from_db."
        )
        assert not extra, f"{proc}: projects columns _from_db never reads: {sorted(extra)}"


def test_allow_list_entries_have_reasons():
    for proc, gaps in ALLOW.items():
        assert proc in FULL_ROW_PROCS, f"ALLOW names unknown proc {proc}"
        for column, reason in gaps.items():
            assert column in _mapper_columns(), f"ALLOW {proc}.{column}: not a mapper column"
            assert reason.strip(), f"ALLOW {proc}.{column} needs a reason"
