"""U-425 — the invoice playbook's checkable claims must stay true.

`entities/invoice/intelligence/prompt.md` is loaded VERBATIM as the
`invoice_specialist` system prompt AND followed by human-supervised sessions
operating on production QBO / SharePoint / Box / SQL. Its 2026-09-08 rewrite
carried factual drift only a code check catches: a `box.Outbox` column that does
not exist, a drain cadence off by 2x, a deleted helper cited as callable, and a
schema invariant the packet-coverage reasoning now rests on.

These tests pin those claims to the code they describe. No DB, no network.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import REPO_ROOT, iter_prod_python_sources  # noqa: E402

PLAYBOOK = REPO_ROOT / "entities" / "invoice" / "intelligence" / "prompt.md"
BOX_OUTBOX_DDL = REPO_ROOT / "integrations" / "box" / "outbox" / "sql" / "box.outbox.sql"
SCHEDULER_APP = REPO_ROOT.parent / "build.one.scheduler" / "function_app.py"

DRAIN_TIMERS = ("drain_qbo_outbox", "drain_ms_outbox", "drain_box_outbox")
EVERY_60_SECONDS = "0 * * * * *"

# (ddl path, constraint name, column) — spelled out rather than derived, so a
# renamed constraint fails loudly instead of silently matching a built string.
# NB BillLineItemAttachment is also pinned by U-411's
# test_unique_constraint_still_guarantees_at_most_one_link_per_line_item; this
# covers all three tables because the playbook's packet section reasons about
# every vendor source type, not just bills.
ATTACHMENT_LINK_UNIQUES = [
    (
        REPO_ROOT / "entities" / "bill_line_item_attachment" / "sql" / "dbo.bill_line_item_attachment.sql",
        "UQ_BillLineItemAttachment_BillLineItemId",
        "BillLineItemId",
    ),
    (
        REPO_ROOT / "entities" / "expense_line_item_attachment" / "sql" / "dbo.expense_line_item_attachment.sql",
        "UQ_ExpenseLineItemAttachment_ExpenseLineItemId",
        "ExpenseLineItemId",
    ),
    (
        REPO_ROOT / "entities" / "bill_credit_line_item_attachment" / "sql" / "dbo.bill_credit_line_item_attachment.sql",
        "UQ_BillCreditLineItemAttachment_BillCreditLineItemId",
        "BillCreditLineItemId",
    ),
]


@pytest.fixture(scope="module")
def playbook() -> str:
    """Read the string production actually ships as the system prompt."""
    from intelligence.agents.invoice_specialist.definition import invoice_specialist

    return invoice_specialist.system_prompt


def test_playbook_is_loadable_and_substantial(playbook: str) -> None:
    """The agent definition read_text()s this at import time, so a missing or
    truncated file breaks app startup, not just this agent."""
    assert len(playbook) > 50_000
    assert "# PART 1 — invoice_specialist" in playbook
    assert "# PART 2 — InvoiceAgent Playbook" in playbook


def test_box_outbox_attempt_column_name(playbook: str) -> None:
    """Step 5's verification query selects from box.Outbox. The column is
    `Attempts`; `AttemptCount` raises 'Invalid column name' and halts the run."""
    ddl = BOX_OUTBOX_DDL.read_text(encoding="utf-8")
    assert "[Attempts]" in ddl
    assert "[AttemptCount]" not in ddl
    assert "AttemptCount" not in playbook, (
        "playbook cites box.Outbox.AttemptCount; the real column is Attempts"
    )


@pytest.mark.parametrize("ddl_path,constraint,column", ATTACHMENT_LINK_UNIQUES)
def test_attachment_links_are_one_to_one(ddl_path: Path, constraint: str, column: str) -> None:
    """The packet section states a source line carries at most ONE attachment,
    so the generator's TOP-1 pick drops nothing and the coverage query's COUNT
    is only ever 0 or 1. That holds only while these UNIQUE constraints stand."""
    ddl = ddl_path.read_text(encoding="utf-8")
    assert constraint in ddl, f"{constraint} is gone; the playbook's packet-coverage reasoning breaks"
    assert f"UNIQUE ([{column}])" in ddl, f"{constraint} no longer declares UNIQUE ([{column}])"


def _drain_schedules() -> dict[str, str]:
    """Read each drain timer's cron structurally, so a neighbouring decorator
    can never be misattributed."""
    tree = ast.parse(SCHEDULER_APP.read_text(encoding="utf-8"))
    found: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or node.name not in DRAIN_TIMERS:
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            for kw in dec.keywords:
                if kw.arg == "schedule" and isinstance(kw.value, ast.Constant):
                    found[node.name] = kw.value.value
    return found


def test_drain_cadence_claim_matches_scheduler(playbook: str) -> None:
    """The playbook tells operators how long they have to cancel a wrong enqueue.
    Understating that window is the dangerous direction, but any drift is a lie
    an operator plans around."""
    if not SCHEDULER_APP.exists():
        pytest.skip("scheduler repo not checked out alongside the api repo")
    schedules = _drain_schedules()
    assert set(schedules) == set(DRAIN_TIMERS), f"drain timers changed: found {sorted(schedules)}"
    for fn, cron in sorted(schedules.items()):
        assert cron == EVERY_60_SECONDS, f"{fn} cadence changed to {cron!r}; update the playbook"
    assert "every 60 seconds" in playbook, "playbook no longer states the 60s drain cadence"
    assert "every 30 seconds" not in playbook
    assert "30s timer" not in playbook


def test_playbook_does_not_cite_deleted_attachable_helper(playbook: str) -> None:
    """`query_attachables_for_entity` was deleted (U-218e). KI-28 may name it as
    history, but never as a callable instruction, and nothing may redefine it."""
    resurrected = [
        py for py in iter_prod_python_sources()
        if "def query_attachables_for_entity(" in py.read_text(encoding="utf-8", errors="ignore")
    ]
    assert not resurrected, f"query_attachables_for_entity was resurrected in {resurrected}"
    for line in playbook.splitlines():
        if "query_attachables_for_entity" not in line:
            continue
        assert "DELETED" in line or "was deleted" in line.lower(), (
            "playbook cites query_attachables_for_entity without marking it removed: " + line[:160]
        )


def _class_methods(class_name: str) -> set[str] | None:
    """Resolve a class's method names by AST across production sources, without
    importing it (no side effects, no circular-import risk). Returns None when
    the class is not defined in this repo."""
    for py in iter_prod_python_sources():
        text = py.read_text(encoding="utf-8", errors="ignore")
        if f"class {class_name}" not in text:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:  # pragma: no cover - prod sources parse
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                names = {n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
                for base in node.bases:  # one level of inheritance is enough here
                    if isinstance(base, ast.Name):
                        inherited = _class_methods(base.id)
                        if inherited:
                            names |= inherited
                return names
    return None


# `SomeService().method(...)` / `SomeConnector.method(...)` — the shape the
# playbook uses when it tells an operator to CALL something.
_CITATION = re.compile(
    r"(?<![A-Za-z_])([A-Z][A-Za-z0-9_]*(?:Service|Connector|Client|Repository))(?:\(\))?\.(_?[a-z][a-z0-9_]*)"
)


def test_cited_service_methods_exist(playbook: str) -> None:
    """Every `<Something>Service/Connector/Client/Repository.<method>` the
    playbook tells an operator to call must exist on that class.

    This is the general form of the drift U-425 found by hand: a playbook that
    names a method deleted or re-signatured out from under it. Classes this repo
    does not define are reported, not failed, so a third-party citation cannot
    make the suite flaky.
    """
    cited: dict[str, set[str]] = {}
    for cls, method in _CITATION.findall(playbook):
        cited.setdefault(cls, set()).add(method)
    assert len(cited) >= 5, f"expected the playbook to cite several services, got {sorted(cited)}"

    missing: list[str] = []
    unresolved: list[str] = []
    for cls in sorted(cited):
        methods = _class_methods(cls)
        if methods is None:
            unresolved.append(cls)
            continue
        missing += [f"{cls}.{m}" for m in sorted(cited[cls]) if m not in methods]

    assert not missing, f"playbook cites methods that do not exist: {missing}"
    # Guard the guard: if resolution silently stopped working, the test above
    # would pass vacuously.
    assert len(cited) - len(unresolved) >= 5, (
        f"resolved too few cited classes ({sorted(unresolved)} unresolved); the check may be vacuous"
    )
