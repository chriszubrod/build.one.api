"""U-075 recurrence guard: UPDATE sprocs are full-row writers; the service layer
re-reads the entity and overlays only non-None request fields before calling UPDATE.
That merge is safe only when every column an Update sproc SETs is returned by the
entity's by-id read sprocs — a missing read column becomes NULL in the merge and
silently wipes stored data (U-052 Project.Notes). Test 1 locks update/read column
parity repo-wide; Test 2 keeps intentional NULL-guard exceptions from regressing.
To register a new deliberately-guarded sproc, add a row to _NULL_GUARD_EXCEPTIONS.
"""

import glob
import re
from functools import lru_cache
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# System-managed columns excluded from parity. Omitting a column here fails
# LOUD (Test 1 demands the read sprocs return it), so under-inclusion is safe.
_AUDIT_COLUMNS = frozenset(
    {"ModifiedDatetime", "UpdatedDatetime", "RowVersion", "ModifiedByUserId"}
)

_PROC_PATTERN = re.compile(
    r"CREATE\s+(?:OR\s+ALTER\s+)?PROCEDURE\s+(?:\[?dbo\]?\.)?\[?(\w+)\]?",
    re.IGNORECASE,
)

_VIEW_PATTERN = re.compile(
    r"CREATE\s+(?:OR\s+ALTER\s+)?VIEW\s+(?:\[?dbo\]?\.)?\[?(\w+)\]?",
    re.IGNORECASE,
)

_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT = re.compile(r"--[^\n]*")

_UPDATE_BY_ID = re.compile(r"^Update(?P<entity>.+)ById$", re.IGNORECASE)
_READ_BY_ID = "Read{entity}ById"
_READ_BY_PUBLIC_ID = "Read{entity}ByPublicId"

_COL_TOKEN = re.compile(r"\[(\w+)\]")

_NULL_GUARD = re.compile(r"CASE\s+WHEN|COALESCE\s*\(|ISNULL\s*\(", re.IGNORECASE)

# Deliberate NULL-guard exceptions (Test 2): sprocs whose SET expressions must
# KEEP their CASE WHEN/COALESCE/ISNULL guard. Value = the guarded column set,
# or None meaning ALL non-audit SET columns must stay guarded. Removing a guard
# changes the sproc from preserve-on-NULL back to pass-through and breaks the
# caller contract documented per row. Register new intentional guards here.
_NULL_GUARD_EXCEPTIONS: dict[str, frozenset[str] | None] = {
    # Line-item edit paths re-pass NULL for the FK they don't know; the guard
    # preserves the CL→BillLineItem link (CLAUDE.md: wiping it re-bills labor).
    "UpdateContractLaborLineItemById": frozenset({"BillLineItemId"}),
    # U-093 entities are fully guarded by design — their update semantics are
    # partial-update (NULL = don't change), unlike the full-row-writer default.
    "UpdateVendorComplianceDocumentById": None,
    "UpdateVendorInsurancePolicyById": None,
}


def _scan_sql_paths() -> list[Path]:
    paths: list[Path] = []
    for pattern in ("entities/*/sql/dbo.*.sql", "shared/sql/dbo.*.sql"):
        for raw in glob.glob(str(REPO_ROOT / pattern)):
            paths.append(Path(raw))
    return sorted(paths)


def _split_batches(text: str) -> list[str]:
    batches: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.strip().upper() == "GO":
            if current:
                batches.append("\n".join(current))
                current = []
        else:
            current.append(line)
    if current:
        batches.append("\n".join(current))
    return batches


def _strip_sql_comments(text: str) -> str:
    text = _BLOCK_COMMENT.sub("", text)
    return _LINE_COMMENT.sub("", text)


def _split_top_level_commas(text: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
            continue
        buf.append(ch)
    if buf:
        parts.append("".join(buf).strip())
    return parts


def _assignment_target(assignment: str) -> str | None:
    assignment = assignment.strip()
    match = re.match(r"(?:\[?\w+\]?\s*\.\s*)?\[(\w+)\]\s*=", assignment)
    return match.group(1) if match else None


def _iter_update_set_clauses(body: str):
    stripped = _strip_sql_comments(body)
    for update_match in re.finditer(
        r"\bUPDATE\b.+?\bSET\b", stripped, re.IGNORECASE | re.DOTALL
    ):
        remainder = stripped[update_match.end() :]
        set_lines: list[str] = []
        for line in remainder.splitlines():
            stripped_line = line.strip()
            if re.match(r"^(WHERE|OUTPUT)\b", stripped_line, re.IGNORECASE):
                break
            set_lines.append(line)
        yield "\n".join(set_lines)


def _extract_update_set_columns(body: str) -> frozenset[str]:
    return frozenset(_extract_assignment_expressions(body))


def _select_list_has_bare_star(select_list: str) -> bool:
    for item in _split_top_level_commas(select_list):
        item = item.strip()
        if item == "*" or re.match(r"^\[\w+\]\.\*$", item) or re.match(r"^\w+\.\*$", item):
            return True
    return False


def _parse_from_source(from_clause: str) -> str:
    match = re.match(r"\s*(?:\[?dbo\]?\.)?\[?(\w+)\]?", from_clause, re.IGNORECASE)
    return match.group(1) if match else ""


def _remove_parenthesized_content(text: str) -> str:
    out: list[str] = []
    depth = 0
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(ch)
    return "".join(out)


_SELECT_KW = re.compile(r"\bSELECT\b", re.IGNORECASE)
_FROM_KW = re.compile(r"\bFROM\b", re.IGNORECASE)


def _find_top_level_select_from_pairs(body: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    i = 0
    n = len(body)
    depth = 0
    while i < n:
        ch = body[i]
        if ch == "(":
            depth += 1
            i += 1
            continue
        if ch == ")":
            depth -= 1
            i += 1
            continue
        if depth == 0:
            select_match = _SELECT_KW.match(body, i)
            if select_match:
                select_start = select_match.end()
                inner_depth = 0
                j = select_start
                from_match = None
                while j < n:
                    if body[j] == "(":
                        inner_depth += 1
                    elif body[j] == ")":
                        inner_depth -= 1
                    elif inner_depth == 0:
                        from_match = _FROM_KW.match(body, j)
                        if from_match:
                            break
                    j += 1
                if from_match:
                    select_list = body[select_start : from_match.start()]
                    pairs.append(
                        (select_list, _parse_from_source(body[from_match.end() :]))
                    )
                i = select_start
                continue
        i += 1
    return pairs


def _resolve_bare_star(
    from_source: str, visited: frozenset[str]
) -> tuple[frozenset[str], bool]:
    if from_source in visited:
        return frozenset(), False
    view_body = _view_index().get(from_source)
    if view_body is None:
        return frozenset(), True
    return _extract_select_columns(view_body, visited | {from_source})


def _collect_select_list_columns(
    select_list: str,
    from_source: str,
    visited: frozenset[str],
) -> tuple[frozenset[str], bool]:
    if _select_list_has_bare_star(select_list):
        return _resolve_bare_star(from_source, visited)
    cleaned = _remove_parenthesized_content(select_list)
    return frozenset(_COL_TOKEN.findall(cleaned)), False


def _extract_select_columns(
    body: str, visited: frozenset[str] = frozenset()
) -> tuple[frozenset[str], bool]:
    columns: set[str] = set()
    covers_all = False
    stripped = _strip_sql_comments(body)
    for select_list, from_source in _find_top_level_select_from_pairs(stripped):
        cols, all_flag = _collect_select_list_columns(select_list, from_source, visited)
        columns.update(cols)
        covers_all = covers_all or all_flag
    return frozenset(columns), covers_all


def _extract_assignment_expressions(body: str) -> dict[str, str]:
    """Map SET target column -> RHS expression (non-audit columns only)."""
    expressions: dict[str, str] = {}
    for set_clause in _iter_update_set_clauses(body):
        for assignment in _split_top_level_commas(set_clause):
            col = _assignment_target(assignment)
            if not col or col in _AUDIT_COLUMNS:
                continue
            rhs = assignment.split("=", 1)[1].strip() if "=" in assignment else ""
            expressions[col] = rhs
    return expressions


@lru_cache(maxsize=1)
def _all_batches() -> tuple[str, ...]:
    batches: list[str] = []
    for path in _scan_sql_paths():
        batches.extend(_split_batches(path.read_text(encoding="utf-8")))
    return tuple(batches)


def _build_index(pattern: re.Pattern[str]) -> dict[str, str]:
    index: dict[str, str] = {}
    for batch in _all_batches():
        for match in pattern.finditer(batch):
            index[match.group(1)] = batch[match.start() :]
    return index


@lru_cache(maxsize=1)
def _proc_index() -> dict[str, str]:
    return _build_index(_PROC_PATTERN)


@lru_cache(maxsize=1)
def _view_index() -> dict[str, str]:
    return _build_index(_VIEW_PATTERN)


@lru_cache(maxsize=1)
def _update_by_id_cases() -> tuple[tuple[str, str, frozenset[str]], ...]:
    cases: list[tuple[str, str, frozenset[str]]] = []
    for name, body in sorted(_proc_index().items()):
        entity_match = _UPDATE_BY_ID.match(name)
        if not entity_match:
            continue
        set_cols = _extract_update_set_columns(body)
        cases.append((name, entity_match.group("entity"), set_cols))
    return tuple(cases)


def _read_sproc_columns(name: str) -> tuple[frozenset[str], bool] | None:
    body = _proc_index().get(name)
    if body is None:
        return None
    return _extract_select_columns(body)


def _parity_failure(
    update_sproc: str, read_sproc: str, missing: frozenset[str]
) -> str:
    return (
        f"{update_sproc} SETs {sorted(missing)}, but {read_sproc} does not return "
        f"them — service-layer merge passes NULL and silently wipes stored values "
        f"(U-052 class silent NULL-wipe path)."
    )


@pytest.mark.parametrize(
    "update_sproc,entity,set_columns",
    [pytest.param(u, e, cols, id=u) for u, e, cols in _update_by_id_cases()],
)
def test_update_read_column_parity(update_sproc, entity, set_columns):
    read_by_id = _READ_BY_ID.format(entity=entity)
    read_by_public_id = _READ_BY_PUBLIC_ID.format(entity=entity)

    by_id = _read_sproc_columns(read_by_id)
    by_public_id = _read_sproc_columns(read_by_public_id)

    assert by_id is not None or by_public_id is not None, (
        f"{update_sproc} has no companion read sproc — expected at least one of "
        f"{read_by_id} or {read_by_public_id}."
    )

    for read_name, read_info in (
        (read_by_id, by_id),
        (read_by_public_id, by_public_id),
    ):
        if read_info is None:
            continue
        read_cols, covers_all = read_info
        if covers_all:
            continue
        missing = set_columns - read_cols
        assert not missing, _parity_failure(update_sproc, read_name, frozenset(missing))


@pytest.mark.parametrize(
    "sproc_name,guarded_columns",
    [pytest.param(s, cols, id=s) for s, cols in _NULL_GUARD_EXCEPTIONS.items()],
)
def test_intentional_null_guard_exceptions_stay_guarded(sproc_name, guarded_columns):
    body = _proc_index()[sproc_name]
    expressions = _extract_assignment_expressions(body)
    assert expressions, f"{sproc_name} has no parsed SET assignments."

    targets = expressions.keys() if guarded_columns is None else guarded_columns
    missing = sorted(col for col in targets if col not in expressions)
    assert not missing, (
        f"{sproc_name} must SET these NULL-guarded columns — guard regression "
        f"(see _NULL_GUARD_EXCEPTIONS): {missing}"
    )
    unguarded = sorted(
        col for col in targets if not _NULL_GUARD.search(expressions[col])
    )
    assert not unguarded, (
        f"{sproc_name}: these columns are registered in _NULL_GUARD_EXCEPTIONS as "
        f"preserve-on-NULL but regressed to pass-through SET — that breaks the "
        f"documented caller contract: {unguarded}"
    )
