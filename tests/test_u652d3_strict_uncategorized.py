"""U-652d3 — one definition of "uncategorized" (strict).

A QBO purchase line needs categorizing when it carries the 58999 NEED TO CATEGORIZE label AND has
no QBO Item ref. That definition lives in ONE place: the computed column
qbo.PurchaseLine.IsUncodedPlaceholder (SQL) and integrations/intuit/qbo/purchase/placeholder.py
(Python). Every site says `pl.[IsUncodedPlaceholder] = 1` instead of re-typing the loose LIKE.
"""

import ast
import re
import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from entities.coding_suggestion.business.service import CodingSuggestionService
from entities.expense.business.service import ExpenseService
from integrations.intuit.qbo.outbox.business.worker import QboOutboxWorker
from integrations.intuit.qbo.purchase.connector.expense.business.service import (
    PurchaseExpenseConnector,
)
from integrations.intuit.qbo.purchase.placeholder import (
    PLACEHOLDER_ACCOUNT_LABEL,
    is_uncoded_placeholder_line,
)
from tests.sproc_text import REPO_ROOT, sproc_body, strip_sql_comments

QBO_PURCHASE_SQL = REPO_ROOT / "integrations/intuit/qbo/purchase/sql/qbo.purchase.sql"
EXPENSE_CODING_QUEUE_SQL = REPO_ROOT / "integrations/intuit/qbo/purchase/sql/qbo.expense_coding_queue.sql"
CODING_SUGGESTION_SQL = REPO_ROOT / "entities/coding_suggestion/sql/dbo.coding_suggestion.sql"
EXPENSE_SQL = REPO_ROOT / "entities/expense/sql/dbo.expense.sql"
REDRIVE_SCRIPT = REPO_ROOT / "scripts/redrive_skipped_expense_recodes.py"
BACKFILL_SCRIPT = REPO_ROOT / "scripts/backfill_complete_coded_qbo_drafts.py"

COLUMN_EXPR = (
    "CAST(CASE WHEN [AccountRefName] LIKE N'%NEED TO CATEGORIZE%' AND [ItemRefValue] IS NULL "
    "THEN 1 ELSE 0 END AS BIT) PERSISTED"
)
PREDICATE = "pl.[IsUncodedPlaceholder] = 1"
# Broad on purpose: the N prefix is optional, and the label is matched inside ANY quote (a re-typed
# predicate, a re-typed label constant, or a fixture string all count).
LOOSE_LIKE = re.compile(r"LIKE\s+N?'%NEED TO CATEGORIZE%'", re.IGNORECASE)


# --- placeholder.py: the Python home ----------------------------------------------------------


def test_placeholder_label_constant():
    assert PLACEHOLDER_ACCOUNT_LABEL == "NEED TO CATEGORIZE"


@pytest.mark.parametrize(
    "name",
    [
        "Cost of construction : NEED TO CATEGORIZE",
        "need to categorize",
        "COST OF CONSTRUCTION : need to categorize",
    ],
)
@pytest.mark.parametrize("item", [None, ""])
def test_predicate_true_for_label_match_and_no_item(name, item):
    assert is_uncoded_placeholder_line(name, item) is True


def test_predicate_false_once_item_ref_is_set():
    assert is_uncoded_placeholder_line("Cost of construction : NEED TO CATEGORIZE", "123") is False


@pytest.mark.parametrize("name", ["Materials", "Cost of construction : NEED TO UPDATE", "", None])
def test_predicate_false_without_label(name):
    assert is_uncoded_placeholder_line(name, None) is False


def _line(name, item):
    return SimpleNamespace(account_ref_name=name, item_ref_value=item)


@pytest.mark.parametrize(
    "name,item",
    [
        (None, None),
        ("", None),
        ("Cost of construction : NEED TO CATEGORIZE", None),
        ("Cost of construction : NEED TO CATEGORIZE", ""),
        ("Cost of construction : NEED TO CATEGORIZE", "77"),
        ("Materials", None),
    ],
)
def test_connector_predicate_delegates_to_placeholder_module(name, item):
    """_qbo_purchase_line_is_uncoded_58999_placeholder keeps its semantics by delegating."""
    line = _line(name, item)
    expected = is_uncoded_placeholder_line(name, item)
    assert PurchaseExpenseConnector._qbo_purchase_line_is_uncoded_58999_placeholder(line) is expected


# --- the SQL column ------------------------------------------------------------------------------


def test_column_defined_idempotently_in_its_own_batch():
    text = QBO_PURCHASE_SQL.read_text()
    assert "[IsUncodedPlaceholder] AS " + COLUMN_EXPR in text
    start = text.index("[IsUncodedPlaceholder] AS ")
    head = text[:start]
    guard = head[head.rfind("IF OBJECT_ID"):]
    assert "sys.columns" in guard and "'IsUncodedPlaceholder'" in guard, "add must be guarded by sys.columns"
    assert "NOT EXISTS" in guard
    tail = text[start:]
    assert tail.split("\n")[1].strip() == "GO", "the column ALTER must sit in its own GO-terminated batch"


def test_column_is_added_after_the_purchaseline_create():
    text = QBO_PURCHASE_SQL.read_text()
    assert text.index("CREATE TABLE [qbo].[PurchaseLine]") < text.index("[IsUncodedPlaceholder] AS ")


def test_column_expression_semantics_match_placeholder_predicate():
    """SQLite stands in for the T-SQL CASE: same label LIKE (case-insensitive), same IS NULL."""
    db = sqlite3.connect(":memory:")
    db.execute(
        "CREATE TABLE pl (Id INTEGER PRIMARY KEY, AccountRefName TEXT, ItemRefValue TEXT, "
        "IsUncodedPlaceholder INTEGER GENERATED ALWAYS AS (CASE WHEN AccountRefName LIKE "
        "'%NEED TO CATEGORIZE%' AND ItemRefValue IS NULL THEN 1 ELSE 0 END) STORED)"
    )
    rows = [
        (1, "Cost of construction : NEED TO CATEGORIZE", None),  # genuine
        (2, "Cost of construction : NEED TO CATEGORIZE", "77"),  # recoded: label kept, item set
        (3, "Materials", None),  # not a placeholder
        (4, "cost of construction : need to categorize", None),  # label case-insensitive
    ]
    db.executemany("INSERT INTO pl (Id, AccountRefName, ItemRefValue) VALUES (?, ?, ?)", rows)
    flagged = {r[0] for r in db.execute("SELECT Id FROM pl WHERE IsUncodedPlaceholder = 1")}
    assert flagged == {1, 4}
    for pk, name, item in rows:
        assert bool(is_uncoded_placeholder_line(name, item)) == (pk in flagged)


# --- the sites use the column --------------------------------------------------------------------


@pytest.mark.parametrize(
    "path,sproc",
    [
        (EXPENSE_CODING_QUEUE_SQL, "ReadExpenseCodingQueue"),
        (EXPENSE_CODING_QUEUE_SQL, "ReadExpenseCodingMetrics"),
        (CODING_SUGGESTION_SQL, "ReadExternallyResolvedCodingSuggestionCandidates"),
        (EXPENSE_SQL, "ReadUncodedCompletedExpenseCandidates"),
    ],
)
def test_sql_sproc_site_uses_column(path, sproc):
    body = strip_sql_comments(sproc_body(path, sproc))
    assert PREDICATE in body, f"{sproc} must filter on {PREDICATE}"
    assert not LOOSE_LIKE.search(body), f"{sproc} still carries the loose label LIKE"


@pytest.mark.parametrize(
    "fn",
    [
        ExpenseService._resolve_dominant_expense_coding_for_submit,
        ExpenseService._expense_has_genuine_uncoded_staged_lines,
        CodingSuggestionService._count_uncategorized_purchase_lines,
        CodingSuggestionService._uncategorized_staging_qbo_line_ids,
        QboOutboxWorker._read_uncategorized_staging_line_ids,
    ],
)
def test_python_inline_site_uses_column(fn):
    import inspect

    source = inspect.getsource(fn)
    assert PREDICATE in source, f"{fn.__qualname__} must filter on {PREDICATE}"
    assert not LOOSE_LIKE.search(source), f"{fn.__qualname__} still carries the loose label LIKE"


@pytest.mark.parametrize("path", [REDRIVE_SCRIPT, BACKFILL_SCRIPT])
def test_script_sites_use_column(path):
    text = path.read_text()
    assert "IsUncodedPlaceholder] = 1" in text
    assert not LOOSE_LIKE.search(text)


# --- static: no site re-types the loose label ----------------------------------------------------

_SCANNED_DIRS = ("entities", "integrations", "shared", "scripts", "intelligence", "core")
_SCANNED_FILES = (REPO_ROOT / "app.py",)
# The two justified homes of the label text: the SQL column definition and the Python constant.
_JUSTIFIED_HOMES = {QBO_PURCHASE_SQL, REPO_ROOT / "integrations/intuit/qbo/purchase/placeholder.py"}


def _scanned_sources():
    for d in _SCANNED_DIRS:
        for path in (REPO_ROOT / d).rglob("*"):
            if path.suffix in {".py", ".sql"} and "/.venv/" not in str(path):
                yield path
    for path in _SCANNED_FILES:
        yield path


# Strings the label may appear in, in code: the uppercase label, the lowercase form, or a LIKE wildcard.
_CODE_LABEL_VALUE = re.compile(r"NEED\s+TO\s+CATEGORIZE|%CATEGORIZE%", re.IGNORECASE)
# The label phrase, a LIKE wildcard around it, or a bare 'CATEGORIZE'. NOT a column name such as
# 'CategorizedDatetime', which only shares a prefix with the word.
_SQL_QUOTED_CATEGORIZE = re.compile(
    r"""['"][^'"\n]*(?:NEED\s+TO\s+CATEGORIZE|%CATEGORIZE%)[^'"\n]*['"]|['"]CATEGORIZE['"]""", re.IGNORECASE
)
# The one place a placeholder reference may live, per file. None = any function (the file's own home).
_REFERENCE_ALLOWLIST = {
    "integrations/intuit/qbo/purchase/placeholder.py": "*",
    "integrations/intuit/qbo/purchase/connector/expense/business/service.py": {
        None,  # the module-level import
        "_qbo_purchase_line_is_uncoded_58999_placeholder",
        "_raw_line_is_categorize_placeholder",
    },
    "integrations/intuit/qbo/outbox/business/worker.py": {"_read_label_staging_line_ids"},
}
_REFERENCE_NAMES = frozenset({"PLACEHOLDER_ACCOUNT_LABEL", "has_placeholder_label"})


def _python_code_strings(source: str) -> list:
    """Every string-literal value in a module EXCEPT real docstrings (the first statement of the module,
    a class, or a function). Triple-quoted SQL, f-string parts and bare literals are all code."""
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
    ]


def _retypes_label(text: str, suffix: str) -> bool:
    """True when a source re-types the label as a predicate (LIKE / CHARINDEX / equality) or a literal."""
    if suffix == ".sql":
        text = strip_sql_comments(text)
        return bool(LOOSE_LIKE.search(text) or _SQL_QUOTED_CATEGORIZE.search(text))
    if LOOSE_LIKE.search(text):
        return True
    return any(_CODE_LABEL_VALUE.search(value) for value in _python_code_strings(text))


def _placeholder_reference_offenders(rel: str, source: str) -> list:
    """Each reference to the placeholder label or its predicate that falls outside the allowlist."""
    allowed = _REFERENCE_ALLOWLIST.get(rel)
    tree = ast.parse(source)
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    offenders = []
    for node in ast.walk(tree):
        name = None
        if isinstance(node, ast.Name) and node.id in _REFERENCE_NAMES:
            name = node.id
        elif isinstance(node, ast.Attribute) and node.attr in _REFERENCE_NAMES:
            name = node.attr
        elif isinstance(node, ast.alias) and node.name in _REFERENCE_NAMES:
            name = node.name
        if name is None:
            continue
        function, cur = None, node
        while cur in parents:
            cur = parents[cur]
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                function = cur.name
                break
        if allowed is None:
            ok = False
        elif allowed == "*":
            ok = True
        else:
            ok = function in allowed
        if not ok:
            offenders.append(f"{rel}:{getattr(node, 'lineno', '?')} {name} in {function or 'module'}")
    return offenders


def test_no_source_retypes_the_label_anywhere_but_the_column_and_the_constant():
    offenders = []
    for path in _scanned_sources():
        if path in _JUSTIFIED_HOMES:
            continue
        if _retypes_label(path.read_text(errors="ignore"), path.suffix):
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == [], f"label re-typed outside the column / placeholder module: {offenders}"


def test_placeholder_names_are_referenced_only_by_the_allowlisted_homes():
    offenders = []
    for path in _scanned_sources():
        if path.suffix != ".py":
            continue
        rel = str(path.relative_to(REPO_ROOT))
        offenders += _placeholder_reference_offenders(rel, path.read_text(errors="ignore"))
    assert offenders == [], f"placeholder reference outside its allowlisted home: {offenders}"


# Planted examples: each guard must catch a known-bad source and pass a known-good one. A guard that
# passes its own planted cases proves nothing about the repo.
_PLANTED_RETYPES = [
    'SQL = """SELECT 1 FROM x WHERE pl.[AccountRefName] = N\'NEED TO CATEGORIZE\'"""',
    'SQL = """SELECT 1 WHERE CHARINDEX(N\'NEED TO CATEGORIZE\', pl.[AccountRefName]) > 0"""',
    'SQL = """SELECT 1 WHERE pl.[AccountRefName] LIKE N\'%CATEGORIZE%\'"""',
    'SQL = """SELECT 1 WHERE pl.[AccountRefName] LIKE N\'%NEED TO CATEGORIZE%\'"""',
    'LABEL = "NEED TO CATEGORIZE"',
]


@pytest.mark.parametrize("planted", _PLANTED_RETYPES)
def test_planted_retype_is_caught(planted):
    assert _retypes_label(planted, ".py"), f"guard missed: {planted}"


def test_planted_sql_file_retype_is_caught():
    assert _retypes_label("SELECT 1 WHERE pl.[AccountRefName] LIKE N'%CATEGORIZE%';", ".sql")


@pytest.mark.parametrize(
    "benign",
    [
        'def f():\n    """NEED TO CATEGORIZE prose, never a predicate."""\n    return 1',
        "x = 1  # N'NEED TO CATEGORIZE' in a comment is not code",
        'msg = "Could not categorize the vendor"',
    ],
)
def test_planted_benign_source_is_not_flagged(benign):
    assert not _retypes_label(benign, ".py"), f"guard over-flagged: {benign}"


def test_planted_column_name_sharing_the_word_is_not_flagged():
    assert not _retypes_label("SELECT 'CategorizedDatetime' FROM dbo.Attachment;", ".sql")


def test_planted_sql_comment_is_not_flagged():
    assert not _retypes_label("-- label: N'NEED TO CATEGORIZE'\nSELECT 1;", ".sql")


def test_planted_reference_outside_the_allowlist_is_caught():
    src = "from integrations.intuit.qbo.purchase.placeholder import has_placeholder_label\n" \
          "def helper(x):\n    return has_placeholder_label(x)\n"
    assert _placeholder_reference_offenders("entities/expense/business/service.py", src)


def test_planted_reference_in_a_wrong_function_of_an_allowed_file_is_caught():
    src = "def other_fn(x):\n    return PLACEHOLDER_ACCOUNT_LABEL\n"
    assert _placeholder_reference_offenders(
        "integrations/intuit/qbo/outbox/business/worker.py", src
    )


def test_planted_reference_in_the_allowed_function_is_not_flagged():
    src = "def _read_label_staging_line_ids(self):\n    return has_placeholder_label('x')\n"
    assert not _placeholder_reference_offenders(
        "integrations/intuit/qbo/outbox/business/worker.py", src
    )


# --- behaviour: the Python sites, mocked cursor --------------------------------------------------


def _ctx(cursor):
    conn = MagicMock()
    conn.cursor.return_value = cursor
    ctx = MagicMock()
    ctx.__enter__.return_value = conn
    ctx.__exit__.return_value = False
    return ctx


def test_count_uncategorized_purchase_lines_counts_only_the_column_filter():
    cursor = MagicMock()
    cursor.fetchone.return_value = (1,)
    with patch("shared.database.get_connection", return_value=_ctx(cursor)):
        n = CodingSuggestionService._count_uncategorized_purchase_lines(
            realm_id="R1", qbo_purchase_qbo_id="P9"
        )
    assert n == 1
    sql = cursor.execute.call_args[0][0]
    assert PREDICATE in sql
    assert cursor.execute.call_args[0][1:] == ("P9", "R1")


def test_uncategorized_staging_qbo_line_ids_returns_the_filtered_rows():
    cursor = MagicMock()
    cursor.fetchall.return_value = [("2",), (None,)]
    with patch("shared.database.get_connection", return_value=_ctx(cursor)):
        ids = CodingSuggestionService._uncategorized_staging_qbo_line_ids(
            realm_id="R1", qbo_purchase_qbo_id="P9"
        )
    assert ids == ["2"]
    assert PREDICATE in cursor.execute.call_args[0][0]


def test_worker_read_uncategorized_staging_line_ids_uses_column():
    cursor = MagicMock()
    cursor.fetchall.return_value = [("7",)]
    with patch("shared.database.get_connection", return_value=_ctx(cursor)):
        ids = QboOutboxWorker._read_uncategorized_staging_line_ids(
            None, qbo_purchase_qbo_id="P9", realm_id="R1"
        )
    assert ids == ["7"]
    assert PREDICATE in cursor.execute.call_args[0][0]


def test_resolve_dominant_returns_none_when_every_placeholder_is_recoded():
    """Submit trace: all placeholder lines recoded -> the column filter returns no rows -> None."""
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    with patch("shared.database.get_connection", return_value=_ctx(cursor)):
        assert ExpenseService()._resolve_dominant_expense_coding_for_submit(expense_id=5) is None
    assert PREDICATE in cursor.execute.call_args[0][0]


def test_submit_with_no_dominant_enqueues_nothing_and_does_not_raise():
    """enqueue_coding_recode_on_submit with dominant=None: a live coding item is skipped, never confirmed."""
    svc = ExpenseService()
    state_item = {"public_id": "pub-1", "status": "pending", "suggested_project_id": 1}
    coding_service = MagicMock()
    cursor = MagicMock()
    cursor.fetchall.return_value = []  # column filter: no genuine placeholder line left
    with patch.object(ExpenseService, "expense_line_item_service", new=None), patch(
        "entities.coding_suggestion.persistence.repo.CodingSuggestionRepository"
    ) as repo_cls, patch(
        "entities.coding_suggestion.business.service.CodingSuggestionService",
        return_value=coding_service,
    ), patch("shared.database.get_connection", return_value=_ctx(cursor)):
        repo_cls.return_value.read_state_by_expense_ids.return_value = {5: [state_item]}
        summary = svc.enqueue_coding_recode_on_submit(expense_id=5, user_id=1)
    assert summary["enqueued"] == 0
    assert summary["items"][0]["outcome"] == "skipped"
    coding_service.confirm.assert_not_called()


# --- RED-era guard: the placeholder module is the only Python definition ------------------------
