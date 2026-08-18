"""U-165 guard: repo-wide mechanical detector for the pyodbc break shape fixed in
RegisterDeviceToken — DML (INSERT/UPDATE/DELETE/MERGE without client OUTPUT) followed
by a separate row-returning SELECT, where the caller does cursor.fetchone().

What this detector CANNOT see:
- Dynamic SQL built and executed via sp_executesql (statement structure is invisible).
- Nested EXEC of another sproc (risk may live entirely in the callee).
- Sprocs that exist only in prod and were never checked into the repo.
- Path sensitivity — analysis is source-order over each proc body, not which file
  or entity package hosts it.

The allowlist only shrinks. A new entry needs a written justification in its commit
message (mirrors the ratchet idiom in tests/test_sproc_single_source.py /
tests/sproc_drift_ledger.py): fix the sproc with SET NOCOUNT ON in its canonical home
instead of excepting it.
"""

import re
from pathlib import Path

import pytest

from tests.sql_corpus import DML_KEYWORDS as DML_KWS, iter_repo_sql_files

REPO_ROOT = Path(__file__).resolve().parents[1]

# (repo-relative path, sproc name) pairs exempt from the guard. Empty — repo is clean
# after U-165 RegisterDeviceToken fix. Only shrink; never grow without justification.
NOCOUNT_SHAPE_ALLOWLIST = frozenset()

_SKIP_DIR_PARTS = frozenset({".venv", ".git", "__pycache__", "node_modules"})

# ---------------------------------------------------------------- lexing
# Ported verbatim from the U-165 reference analyzer validated against 1106 repo proc
# definitions and 1175 LIVE prod definitions. Every branch marked INVARIANT exists
# because it removed a real false-positive class during the audit.


def strip_comments_and_strings(sql):
    """Blank comments and string literals with SAME-LENGTH filler so character
    offsets stay valid against the original text."""
    out, i, n = [], 0, len(sql)
    while i < n:
        c = sql[i]
        if c == "-" and i + 1 < n and sql[i + 1] == "-":
            j = sql.find("\n", i)
            j = n if j == -1 else j
            out.append(" " * (j - i))
            i = j
        elif c == "/" and i + 1 < n and sql[i + 1] == "*":
            depth, j = 1, i + 2
            while j < n and depth:
                if sql[j] == "/" and j + 1 < n and sql[j + 1] == "*":
                    depth += 1
                    j += 2
                elif sql[j] == "*" and j + 1 < n and sql[j + 1] == "/":
                    depth -= 1
                    j += 2
                else:
                    j += 1
            out.append("".join(ch if ch == "\n" else " " for ch in sql[i:j]))
            i = j
        elif c == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            seg = sql[i:j]
            out.append(
                "'"
                + "".join(ch if ch == "\n" else "x" for ch in seg[1:-1])
                + "'"
                if len(seg) >= 2
                else " " * len(seg)
            )
            i = j
        elif c == "[":
            j = sql.find("]", i)
            j = n if j == -1 else j + 1
            out.append(sql[i:j])  # bracketed identifiers are kept
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


def depth_map(text):
    """Paren depth at each index — subquery SELECTs live at depth > 0."""
    depths = [0] * (len(text) + 1)
    d = 0
    for i, ch in enumerate(text):
        depths[i] = d
        if ch == "(":
            d += 1
        elif ch == ")":
            d = max(0, d - 1)
    depths[len(text)] = d
    return depths


STMT_STARTERS = {
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "MERGE",
    "DECLARE",
    "SET",
    "IF",
    "ELSE",
    "BEGIN",
    "END",
    "WHILE",
    "EXEC",
    "EXECUTE",
    "RETURN",
    "THROW",
    "RAISERROR",
    "PRINT",
    "WITH",
    "COMMIT",
    "ROLLBACK",
    "TRUNCATE",
    "CREATE",
    "DROP",
    "ALTER",
    "VALUES",
    "TRY",
    "CATCH",
    "GOTO",
    "WAITFOR",
    "OPEN",
    "FETCH",
    "CLOSE",
    "DEALLOCATE",
}

# Token vocabulary is DERIVED from the consulted sets so STMT_STARTERS and the lexer
# regex can never drift — adding a statement starter without updating KW_RE is impossible.
_CLAUSE_KWS = frozenset({"FROM", "WHERE", "INTO", "GROUP", "ORDER", "HAVING", "CASE"})
_SET_OPS = frozenset({"UNION", "EXCEPT", "INTERSECT"})
KW_RE = re.compile(
    r"\b(" + "|".join(sorted(STMT_STARTERS | _CLAUSE_KWS | _SET_OPS)) + r")\b",
    re.I,
)

# INVARIANT 2: SET / VALUES / SELECT / WITH are clauses *inside* SOME DML verbs.
# Terminators are per-verb — a global exclusion hid OUTPUT on UPDATE/INSERT and
# swallowed trailing SELECTs when DML was not semicolon-terminated.
_DML_EXTENT_BASE = STMT_STARTERS - {"SET", "VALUES", "SELECT", "WITH"}

_DML_EXTENT_TERMINATORS = {
    "UPDATE": _DML_EXTENT_BASE | {"SELECT"},  # SET is an UPDATE clause
    "INSERT": _DML_EXTENT_BASE | {"SET"},  # SELECT/VALUES are INSERT clauses
    "DELETE": _DML_EXTENT_BASE | {"SELECT", "SET"},  # DELETE has neither clause
    # MERGE uses mandatory ';' — handled separately via mandatory ';'
}

_SELECT_EXTENT_TERMINATORS = frozenset(
    {"FROM", "INTO", "WHERE", "GROUP", "ORDER", "HAVING", "UNION"}
) | STMT_STARTERS

_CLIENT_OUTPUT_INTO_RE = re.compile(r"\bINTO\b", re.I)
_CLIENT_OUTPUT_BREAK_RE = re.compile(
    r"\b(VALUES|FROM|WHERE|SELECT|WHEN)\b", re.I
)


def _insert_source_select_end(toks, select_tok_idx: int, body_len: int) -> int:
    """Char position where an INSERT..SELECT's single source SELECT ends.

    The first depth-0 SELECT after INSERT belongs to it; a subsequent depth-0
    SELECT starts a new statement unless it continues a UNION/EXCEPT/INTERSECT
    chain (including UNION ALL — ALL is not a statement starter).
    """
    j = select_tok_idx + 1
    expect_select_continuation = False
    while j < len(toks):
        pos2, kw2, cd2 = toks[j]
        if cd2 != 0:
            j += 1
            continue
        if kw2 in _SET_OPS:
            expect_select_continuation = True
            j += 1
            continue
        if kw2 == "SELECT":
            if expect_select_continuation:
                expect_select_continuation = False
                j += 1
                continue
            return pos2
        if kw2 in STMT_STARTERS:
            return pos2
        j += 1
    return body_len


def _select_is_insert_source_continuation(toks, idx: int) -> bool:
    """True when this depth-0 SELECT is still part of a preceding INSERT..SELECT."""
    prev = None
    for pos2, kw2, cd2 in reversed(toks[:idx]):
        if cd2 == 0 and kw2 in STMT_STARTERS:
            prev = kw2
            break
    if prev == "INSERT":
        return True
    if prev in _SET_OPS:
        return True
    if prev == "SELECT":
        for pos2, kw2, cd2 in reversed(toks[:idx]):
            if cd2 == 0 and kw2 in _SET_OPS:
                return True
            if cd2 == 0 and kw2 in STMT_STARTERS:
                break
    return False


def tokens_at_depth0(body, dm=None):
    """Depth-0 keywords tagged with CASE-expression nesting.

    INVARIANT 3: `CASE WHEN ... ELSE ... END` is an EXPRESSION. Its ELSE/END must
    never read as statement structure — an UPDATE's SET clause is full of them.
    """
    if dm is None:
        dm = depth_map(body)
    toks, case_depth = [], 0
    for m in KW_RE.finditer(body):
        if dm[m.start()] != 0:
            continue
        kw = m.group(1).upper()
        if kw == "END" and case_depth > 0:
            case_depth -= 1
            toks.append((m.start(), kw, case_depth + 1))
            continue
        toks.append((m.start(), kw, case_depth))
        if kw == "CASE":
            case_depth += 1
    return toks


def client_output(stmt):
    """True when this DML's OUTPUT clause streams rows to the CLIENT.
    `OUTPUT ... INTO @t/#t` captures them instead and returns nothing."""
    om = re.search(r"\bOUTPUT\b", stmt, re.I)
    if om is None:
        return False
    sub = stmt[om.end() :]
    subdepth = 0
    for i, ch in enumerate(sub):
        if ch == "(":
            subdepth += 1
        elif ch == ")":
            subdepth = max(0, subdepth - 1)
        if subdepth == 0 and _CLIENT_OUTPUT_INTO_RE.match(sub[i:]):
            return False
        if subdepth == 0 and _CLIENT_OUTPUT_BREAK_RE.match(sub[i:]):
            break
    return True


def analyse(body):
    """Model the proc body as the token stream the CLIENT sees.

    COUNT  = a rows-affected done-token (what pyodbc trips on under NOCOUNT OFF)
    ROWSET = an actual result set

    A plain DML emits [COUNT]; a DML with a client OUTPUT emits [ROWSET, COUNT]
    (rowset first — this is why single-statement `INSERT..OUTPUT` survives);
    a top-level SELECT emits [ROWSET].
    """
    body_c = strip_comments_and_strings(body)
    dm = depth_map(body_c)
    toks = tokens_at_depth0(body_c, dm)
    events = []
    consumed_to = -1

    for idx, (pos, kw, cd) in enumerate(toks):
        if cd > 0 or pos < consumed_to:
            continue
        if kw == "MERGE":
            # INVARIANT 4: a MERGE is ONE statement — its WHEN MATCHED THEN UPDATE /
            # WHEN NOT MATCHED THEN INSERT are clauses, and a trailing OUTPUT belongs
            # to the whole MERGE. Reading them as separate statements produced
            # spurious COUNTs (UpsertEmailMessage / UpsertEmailAttachment).
            # T-SQL REQUIRES MERGE to end in ';', so the extent is unambiguous.
            end, j = len(body_c), pos
            while True:
                j = body_c.find(";", j + 1)
                if j == -1:
                    break
                if dm[j] == 0:
                    end = j
                    break
            consumed_to = end
            if client_output(body_c[pos:end]):
                events.append((pos, "ROWSET", "MERGE...OUTPUT"))
            events.append((pos + 1, "COUNT", "MERGE"))
        elif kw in DML_KWS:
            terminators = _DML_EXTENT_TERMINATORS[kw]
            source_select_tok_idx = None
            if kw == "INSERT":
                # INSERT..VALUES ends before a trailing SELECT; INSERT..SELECT has at
                # most one top-level source SELECT (set-operator continuations count).
                saw_values = False
                for j, (pos2, kw2, cd2) in enumerate(toks[idx + 1 :], start=idx + 1):
                    if cd2 != 0:
                        continue
                    if kw2 == "VALUES":
                        saw_values = True
                    elif kw2 == "SELECT":
                        if saw_values:
                            terminators = terminators | {"SELECT"}
                            break
                        if source_select_tok_idx is None:
                            source_select_tok_idx = j
                        else:
                            terminators = terminators | {"SELECT"}
                            break
                    elif kw2 in terminators:
                        break
            end = len(body_c)
            if source_select_tok_idx is not None:
                end = _insert_source_select_end(
                    toks, source_select_tok_idx, len(body_c)
                )
            else:
                for pos2, kw2, cd2 in toks[idx + 1 :]:
                    if cd2 == 0 and kw2 in terminators:
                        end = pos2
                        break
            semi = body_c.find(";", pos)
            if semi != -1 and semi < end and dm[semi] == 0:
                end = semi
            consumed_to = end
            if client_output(body_c[pos:end]):
                events.append((pos, "ROWSET", kw + "...OUTPUT"))
            events.append((pos + 1, "COUNT", kw))
        elif kw == "SELECT":
            if _select_is_insert_source_continuation(toks, idx):
                continue
            end = len(body_c)
            for pos2, kw2, cd2 in toks[idx + 1 :]:
                if cd2 == 0 and kw2 in _SELECT_EXTENT_TERMINATORS:
                    end = pos2
                    break
            head = body_c[pos:end]
            # `SELECT @var = ...` assigns; it does not return a result set.
            # (U-164 proved empirically this shape does NOT break the caller.)
            if re.search(
                r"\bSELECT\b\s*(TOP\s*\(?\s*\d+\s*\)?\s*)?@[A-Za-z0-9_]+\s*=",
                head,
                re.I,
            ):
                continue
            into_at = None
            for pos2, kw2, cd2 in toks[idx + 1 :]:
                if cd2 == 0 and kw2 == "INTO":
                    into_at = pos2
                    break
                if cd2 == 0 and (kw2 == "FROM" or kw2 in STMT_STARTERS):
                    break
            if into_at is not None:
                events.append((pos, "COUNT", "SELECT..INTO"))  # SELECT INTO builds a table
                continue
            events.append((pos, "ROWSET", "SELECT"))

    events.sort(key=lambda e: e[0])
    return events


def has_risky_shape(body):
    """True when a rows-affected token precedes a result set — i.e. a fetching
    caller would read the done-token instead of the rows."""
    events = analyse(body)
    counts = [e for e in events if e[1] == "COUNT"]
    rowsets = [e for e in events if e[1] == "ROWSET"]
    return bool(counts and rowsets and counts[0][0] < rowsets[-1][0])


# INVARIANT 1: a proc body ends at the first line-anchored GO, NOT at the next
# CREATE PROCEDURE. Loose ad-hoc script appended after a file's final GO was
# otherwise attributed to the preceding proc (false-flagged DeleteQboBillLineById
# and DeleteQboAttachableByQboId).
GO_RE = re.compile(r"^\s*GO\s*(?:--.*)?$", re.I | re.M)
# Matches optional schema ([dbo]. / dbo. / [qbo]. / qbo.) then the object name,
# bracketed or bare. Only the bare name is captured (mirrors test_sproc_single_source).
_SCHEMA = r"(?:\[\w+\]|\w+)\s*\.\s*"
_OBJECT_NAME = r"(?:\[(\w+)\]|(\w+))"
PROC_RE = re.compile(
    rf"\bCREATE\s+(?:OR\s+ALTER\s+)?PROC(?:EDURE)?\s+(?:{_SCHEMA})?{_OBJECT_NAME}",
    re.I,
)
SET_NOCOUNT_ON = re.compile(r"\bSET\s+NOCOUNT\s+ON\b", re.I)


def _proc_executable_body(proc_block: str) -> str:
    """Text from AS through the batch's first GO (what analyse() tokenizes)."""
    as_match = re.search(r"\bAS\b", proc_block, re.I)
    if as_match:
        return proc_block[as_match.end() :]
    return proc_block


def _iter_sql_files(root: Path):
    return iter_repo_sql_files(root, skip_dir_names=_SKIP_DIR_PARTS)


def collect_nocount_shape_failures(root: Path) -> list[str]:
    """Return 'path:line: dbo.Name' for each sproc failing the guard under root."""
    failures: list[str] = []
    for path in _iter_sql_files(root):
        rel = path.relative_to(root).as_posix()
        for name, executable_body, line_no in _extract_procedures(
            path.read_text(encoding="utf-8")
        ):
            if (rel, name) in NOCOUNT_SHAPE_ALLOWLIST:
                continue
            if _needs_set_nocount(executable_body):
                failures.append(f"{rel}:{line_no}: dbo.{name}")
    return failures


def _extract_procedures(text: str) -> list[tuple[str, str, int]]:
    """Return (name, executable_body, line_no) for each sproc in text."""
    matches = list(PROC_RE.finditer(text))
    procedures: list[tuple[str, str, int]] = []
    for i, match in enumerate(matches):
        name = match.group(1) or match.group(2)
        line_no = text[: match.start()].count("\n") + 1
        rest = text[match.start() :]
        go_match = GO_RE.search(rest)
        go_end = go_match.start() if go_match else len(rest)
        if i + 1 < len(matches):
            next_start = matches[i + 1].start() - match.start()
            end = min(go_end, next_start)
        else:
            end = go_end
        proc_block = rest[:end]
        procedures.append((name, _proc_executable_body(proc_block), line_no))
    return procedures


def _needs_set_nocount(executable_body: str) -> bool:
    events = analyse(executable_body)
    counts = [e[0] for e in events if e[1] == "COUNT"]
    rowsets = [e[0] for e in events if e[1] == "ROWSET"]
    if not (counts and rowsets and counts[0] < rowsets[-1]):
        return False
    nocount = SET_NOCOUNT_ON.search(strip_comments_and_strings(executable_body))
    return nocount is None or nocount.start() > counts[0]


def test_no_risky_sproc_shape_without_set_nocount_on():
    """Every repo sproc with DML → row-returning SELECT must carry SET NOCOUNT ON."""
    failures = collect_nocount_shape_failures(REPO_ROOT)
    assert not failures, (
        "sproc(s) exhibit DML → row-returning SELECT without SET NOCOUNT ON — "
        "add SET NOCOUNT ON as the first statement in the BEGIN block (see "
        "RegisterDeviceToken in entities/device_token/sql/dbo.device_token.sql); "
        "do NOT extend NOCOUNT_SHAPE_ALLOWLIST:\n" + "\n".join(sorted(failures))
    )


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(
            "INSERT INTO dbo.[T] ([X]) OUTPUT INSERTED.[Id] VALUES (1);",
            id="insert-output",
        ),
        pytest.param(
            "UPDATE dbo.[T] SET [X]=1 OUTPUT INSERTED.[Id] WHERE [Id]=1;",
            id="update-output",
        ),
        pytest.param(
            "DELETE FROM dbo.[T] OUTPUT DELETED.[Id] WHERE [Id]=1;",
            id="delete-output",
        ),
        pytest.param(
            "MERGE dbo.[T] AS t USING (SELECT 1 AS x) s ON t.[Id]=s.x "
            "WHEN MATCHED THEN UPDATE SET [X]=1 "
            "WHEN NOT MATCHED THEN INSERT ([X]) VALUES (1) "
            "OUTPUT INSERTED.[Id];",
            id="merge-output",
        ),
        pytest.param(
            "SELECT [Id] FROM dbo.[T];",
            id="select-only",
        ),
        pytest.param(
            "SELECT @x = COUNT(*) FROM dbo.[T];",
            id="select-var-assign",
        ),
        pytest.param(
            "IF NOT EXISTS (SELECT 1 FROM dbo.[T] WHERE [Id]=1) INSERT INTO dbo.[T] ([X]) VALUES (1);",
            id="exists-subquery-select",
        ),
        pytest.param(
            "INSERT INTO dbo.[T] ([X]) OUTPUT INSERTED.[Id] INTO @t ([Id]) VALUES (1);",
            id="output-into-table-var",
        ),
    ],
)
def test_has_risky_shape_safe_cases(body):
    assert not has_risky_shape(body)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(
            "INSERT INTO dbo.[T] ([X]) VALUES (1); SELECT [Id] FROM dbo.[T];",
            id="insert-then-select",
        ),
        pytest.param(
            "UPDATE dbo.[T] SET [X]=1 WHERE [Id]=1; SELECT [Id] FROM dbo.[T];",
            id="update-then-select",
        ),
        pytest.param(
            "DELETE FROM dbo.[T] WHERE [Id]=1; SELECT [Id] FROM dbo.[T];",
            id="delete-then-select",
        ),
        pytest.param(
            "INSERT INTO #t ([X]) VALUES (1); SELECT [Id] FROM #t;",
            id="temp-table-insert-then-select",
        ),
        pytest.param(
            "UPDATE dbo.T SET X = 1 WHERE Id = @Id\nSELECT Id FROM dbo.T WHERE Id = @Id",
            id="update-then-select-no-semicolon",
        ),
        pytest.param(
            "DELETE FROM dbo.T WHERE Id = @Id\nSELECT Id FROM dbo.T WHERE Id = @Id",
            id="delete-then-select-no-semicolon",
        ),
        pytest.param(
            "INSERT INTO dbo.T (X) VALUES (1)\nSELECT Id FROM dbo.T WHERE Id = @Id",
            id="insert-then-select-no-semicolon",
        ),
    ],
)
def test_has_risky_shape_risky_cases(body):
    assert has_risky_shape(body)


@pytest.mark.parametrize(
    "body,expected",
    [
        pytest.param(
            "INSERT INTO dbo.T (X) SELECT X FROM dbo.S\nSELECT Id FROM dbo.T",
            True,
            id="insert-select-then-select-no-semicolon",
        ),
        pytest.param(
            "INSERT INTO dbo.T (X) SELECT X FROM dbo.S;\nSELECT Id FROM dbo.T",
            True,
            id="insert-select-then-select-with-semicolon",
        ),
        pytest.param(
            "INSERT INTO dbo.T (X) SELECT X FROM dbo.S",
            False,
            id="insert-select-alone",
        ),
        pytest.param(
            "INSERT INTO dbo.T (X) SELECT X FROM dbo.S UNION SELECT Y FROM dbo.R",
            False,
            id="insert-select-union-select",
        ),
        pytest.param(
            "INSERT INTO dbo.T (X) SELECT X FROM dbo.S UNION ALL SELECT Y FROM dbo.R",
            False,
            id="insert-select-union-all-select",
        ),
        pytest.param(
            "INSERT INTO dbo.T (X) SELECT X FROM dbo.S UNION SELECT Y FROM dbo.R\nSELECT Id FROM dbo.T",
            True,
            id="insert-select-union-select-then-select",
        ),
    ],
)
def test_has_risky_shape_insert_select_source(body, expected):
    assert has_risky_shape(body) is expected


def test_extract_procedures_finds_bracketed_dbo_name():
    text = "CREATE OR ALTER PROCEDURE [dbo].[Foo] AS BEGIN SELECT 1 END\nGO\n"
    procs = _extract_procedures(text)
    assert len(procs) == 1
    assert procs[0][0] == "Foo"


def test_guard_passes_when_set_nocount_on_present_despite_risky_shape():
    """A body with SET NOCOUNT ON before first DML is safe even when structurally risky."""
    body = "INSERT INTO dbo.[T] ([X]) VALUES (1); SELECT [Id] FROM dbo.[T];"
    proc = f"CREATE OR ALTER PROCEDURE dbo.P AS BEGIN SET NOCOUNT ON; {body} END;"
    executable = _proc_executable_body(proc)
    assert has_risky_shape(executable)
    assert not _needs_set_nocount(executable)


def test_needs_set_nocount_rejects_late_set_nocount_on():
    """SET NOCOUNT ON after the first DML does not suppress the rows-affected token."""
    body = "INSERT INTO dbo.T (X) VALUES (1); SET NOCOUNT ON; SELECT Id FROM dbo.T;"
    proc = f"CREATE OR ALTER PROCEDURE dbo.P AS BEGIN {body} END;"
    executable = _proc_executable_body(proc)
    assert has_risky_shape(executable)
    assert _needs_set_nocount(executable)


@pytest.mark.parametrize(
    "comment_prefix",
    [
        pytest.param(
            "-- pyodbc: SET NOCOUNT ON required — DML then SELECT breaks fetchone()\n    ",
            id="line-comment",
        ),
        pytest.param(
            "/* SET NOCOUNT ON */ ",
            id="block-comment",
        ),
    ],
)
def test_needs_set_nocount_ignores_pragma_only_in_comments(comment_prefix):
    """SET NOCOUNT ON mentioned only in a comment must not satisfy the guard."""
    body = "INSERT INTO dbo.[T] ([X]) VALUES (1); SELECT [Id] FROM dbo.[T];"
    proc = (
        f"CREATE OR ALTER PROCEDURE dbo.P AS BEGIN {comment_prefix}{body} END;"
    )
    executable = _proc_executable_body(proc)
    assert has_risky_shape(executable)
    assert _needs_set_nocount(executable)


def test_collect_nocount_shape_failures_detects_synthetic_bad_proc(tmp_path):
    bad_sql = """CREATE OR ALTER PROCEDURE dbo.BadProc AS
BEGIN
    INSERT INTO dbo.T (X) VALUES (1);
    SELECT Id FROM dbo.T;
END
GO
"""
    (tmp_path / "bad.sql").write_text(bad_sql, encoding="utf-8")
    failures = collect_nocount_shape_failures(tmp_path)
    assert failures == ["bad.sql:1: dbo.BadProc"]


def test_collect_nocount_shape_failures_passes_synthetic_good_proc(tmp_path):
    good_sql = """CREATE OR ALTER PROCEDURE dbo.GoodProc AS
BEGIN
    SET NOCOUNT ON;
    INSERT INTO dbo.T (X) VALUES (1);
    SELECT Id FROM dbo.T;
END
GO
"""
    (tmp_path / "good.sql").write_text(good_sql, encoding="utf-8")
    assert collect_nocount_shape_failures(tmp_path) == []
