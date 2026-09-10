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
from functools import lru_cache
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


@lru_cache(maxsize=1)
def _class_index() -> dict[str, tuple[set[str], tuple[str, ...]]]:
    """One pass over production sources -> {class name: (own methods, bases)}.

    Cached: the citation check resolves ~20 classes, and re-walking ~1.4k files
    per class made this the slowest test in the suite.
    """
    index: dict[str, tuple[set[str], tuple[str, ...]]] = {}
    for py in iter_prod_python_sources():
        text = py.read_text(encoding="utf-8", errors="ignore")
        if "class " not in text:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:  # pragma: no cover - prod sources parse
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef) or node.name in index:
                continue
            index[node.name] = (
                {n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))},
                tuple(b.id for b in node.bases if isinstance(b, ast.Name)),
            )
    return index


def _class_methods(class_name: str, _seen: frozenset[str] = frozenset()) -> set[str] | None:
    """Method names on a class including inherited ones, or None when this repo
    does not define the class. Resolved by AST, never by importing it."""
    entry = _class_index().get(class_name)
    if entry is None:
        return None
    own, bases = entry
    names = set(own)
    for base in bases:
        if base in _seen:  # defensive: never loop on a cyclic base chain
            continue
        inherited = _class_methods(base, _seen | {class_name})
        if inherited:
            names |= inherited
    return names


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

ATTACHMENT_LINK_SERVICES = [
    ("bill_line_item_attachment", "BillLineItemAttachmentService", "read_by_bill_line_item_id"),
    ("expense_line_item_attachment", "ExpenseLineItemAttachmentService", "read_by_expense_line_item_id"),
    ("bill_credit_line_item_attachment", "BillCreditLineItemAttachmentService", "read_by_bill_credit_line_item_id"),
]


@pytest.mark.parametrize("pkg,service,reader", ATTACHMENT_LINK_SERVICES)
def test_attachment_create_dedupes_and_returns_existing(pkg: str, service: str, reader: str) -> None:
    """A second create() on an already-linked line RETURNS THE EXISTING link — it
    does not raise and does not replace.

    U-425 shipped the opposite claim for a few hours. It matters because the
    playbook's KI-40 remediation (trim a contaminated multi-page scan, re-link)
    is exactly a second create: told it raises, an operator gets no error, the
    STALE untrimmed PDF stays attached, coverage still counts 1, and the packet
    re-ships another project's pages to the customer. If this behaviour ever
    changes to raise-or-replace, the playbook text must change with it.
    """
    src = (REPO_ROOT / "entities" / pkg / "business" / "service.py").read_text(encoding="utf-8")
    create = src[src.index("    def create("): src.index("    def read_all(")]
    assert f"self.repo.{reader}(" in create, f"{service}.create no longer looks up the existing link"
    assert "return existing" in create, (
        f"{service}.create no longer returns the existing link — if it now raises or replaces, "
        f"update CRITICAL #5 in the invoice playbook, which documents the delete-then-create sequence"
    )


def test_playbook_does_not_claim_attachment_create_raises(playbook: str) -> None:
    """The corrected CRITICAL #5 must keep telling operators to delete first."""
    assert "does NOT raise" in playbook, "playbook lost the silent-dedupe warning for attachment re-linking"
    # Scope the replace-path assertion to the warning's own paragraph: the symbol
    # also appears in the Delta re-run section, which would make a bare
    # `in playbook` check pass even with CRITICAL #5's remedy deleted.
    warn = playbook.index("does NOT raise")
    para = playbook[warn: playbook.index("\n\n", warn)]
    assert "delete_by_public_id" in para, (
        "the silent-dedupe warning no longer names delete_by_public_id as the replace path"
    )

# Staging tables the playbook must never tell an operator to READ. `qbo.Outbox`
# and `qbo.ReconciliationIssue` are operational tables, not pulled-document
# staging, so they stay legal.
QBO_STAGING_TABLES = (
    "qbo.Invoice", "qbo.InvoiceLine", "qbo.Bill", "qbo.BillLine",
    "qbo.Purchase", "qbo.PurchaseLine", "qbo.VendorCredit", "qbo.VendorCreditLine",
)


def test_playbook_never_reads_qbo_staging(playbook: str) -> None:
    """LIVE QBO is the only authority for a QBO-owned fact (Shared invariant 4).

    Staging holds whatever the last pull fetched and can agree with dbo to the
    cent while both trail the live document — U-425/KI-48, where a fresh
    watermark plus a perfect staging-vs-dbo reconciliation produced a confident
    and completely wrong $27K finding on SHT-25. A SQL read against a staging
    table is the shape that produces that error, so the playbook must not
    contain one.
    """
    offenders = []
    for i, line in enumerate(playbook.splitlines(), 1):
        stripped = line.strip()
        # A comment may legitimately NAME a staging table in order to forbid it.
        if stripped.startswith("--") or stripped.startswith("#"):
            continue
        low = line.lower()
        if not any(kw in low for kw in ("from ", "join ", "select ")):
            continue
        for tbl in QBO_STAGING_TABLES:
            if re.search(rf"(from|join)\s+{re.escape(tbl)}\b", line, re.IGNORECASE):
                offenders.append(f"line {i}: {line.strip()[:110]}")
    assert not offenders, (
        "playbook reads QBO staging instead of live QBO:\n  " + "\n  ".join(offenders)
    )


def test_playbook_states_the_live_authority_invariant(playbook: str) -> None:
    """The rule itself must stay stated, not just followed."""
    assert "QBO LIVE is the ONLY authority" in playbook
    assert "2a-LIVE" in playbook, "the mandatory live staleness check lost its anchor"
    assert "KI-48" in playbook, "the stale-staging incident is no longer booked"


def test_live_client_surface_cited_by_the_playbook_exists() -> None:
    """The playbook now instructs a live read; its entry points must be real."""
    from integrations.intuit.qbo.invoice.external.client import QboInvoiceClient

    assert hasattr(QboInvoiceClient, "get_invoice")
    assert hasattr(QboInvoiceClient, "query_invoices")
    for mod, cls, meth in (
        ("bill", "QboBillClient", "get_bill"),
        ("purchase", "QboPurchaseClient", "get_purchase"),
        ("vendorcredit", "QboVendorCreditClient", "get_vendor_credit"),
    ):
        src = (REPO_ROOT / "integrations" / "intuit" / "qbo" / mod / "external" / "client.py").read_text(encoding="utf-8")
        assert f"class {cls}" in src and f"def {meth}(" in src, f"{cls}.{meth} missing"

def test_attachable_ref_fields_are_flat_as_the_playbook_says() -> None:
    """The playbook now tells operators to read `ref.entity_ref_type` /
    `ref.entity_ref_value` and warns there is NO nested `entity_ref`.

    KI-49: reading the nested form silently makes every comparison evaluate
    against None, so a realm-wide scan returns zero for any input. If the schema
    ever gains a nested ref, the playbook's warning becomes wrong and this fails.
    """
    from integrations.intuit.qbo.attachable.external.schemas import QboAttachableRef

    fields = QboAttachableRef.model_fields
    assert "entity_ref_type" in fields and "entity_ref_value" in fields
    assert "entity_ref" not in fields, (
        "QboAttachableRef gained a nested entity_ref — CRITICAL #5's flat-field warning is now wrong"
    )


def test_playbook_warns_about_the_flat_attachable_ref(playbook: str) -> None:
    """Scoped to the operative paragraph: the field names also appear in KI-49,
    so a bare `in playbook` check passes even with the instruction itself broken.
    """
    anchor = playbook.index("query_all_attachables()` and inspect every relevant")
    para = playbook[anchor: anchor + 1400]
    assert "ref.entity_ref_type" in para, "the attachable guidance no longer names the flat type field"
    assert "ref.entity_ref_value" in para, "the attachable guidance no longer names the flat value field"
    assert "NO nested" in para, "the flat-field warning lost its explicit negative"


def test_playbook_states_the_zero_result_guard(playbook: str) -> None:
    """KI-49's transferable rule: a zero is only evidence with its coarse count."""
    assert "A zero result must PROVE it can produce a non-zero one" in playbook
    assert "KI-49" in playbook and "KI-50" in playbook


def test_live_source_clients_cited_for_gap_fill_exist() -> None:
    """Step 2c now resolves sources against LIVE QBO before calling one missing."""
    from integrations.intuit.qbo.vendorcredit.external.client import QboVendorCreditClient
    from integrations.intuit.qbo.purchase.external.client import QboPurchaseClient
    from integrations.intuit.qbo.bill.external.client import QboBillClient

    for cls, meth in (
        (QboVendorCreditClient, "query_all_vendor_credits"),
        (QboVendorCreditClient, "get_vendor_credit"),
        (QboPurchaseClient, "query_all_purchases"),
        (QboPurchaseClient, "get_purchase_raw"),
        (QboBillClient, "get_bill"),
    ):
        assert hasattr(cls, meth), f"{cls.__name__}.{meth} is cited by the playbook but missing"

def test_playbook_requires_serialized_qbo_work(playbook: str) -> None:
    """U-425: the per-entity applock does NOT serialize different entities, and the
    attachable path takes no lock at all. Running two realm-scale QBO operations
    concurrently produced a DB login timeout, four unexplained HTTP failures, and a
    communication-link failure on a script that still exited 0."""
    assert "Serialize QBO-heavy work INSIDE a single run" in playbook
    anchor = playbook.index("Serialize QBO-heavy work INSIDE a single run")
    para = playbook[anchor: anchor + 1600]
    assert "no lock at all" in para, "the attachable-path exception lost its warning"
    assert "an exit code of 0 from a sync script is not success" in para, (
        "the playbook no longer warns that a sync script's exit 0 is not success"
    )


def test_attachable_client_has_a_direct_fetch(playbook: str) -> None:
    """The playbook now tells operators to fetch by id rather than scan the realm."""
    from integrations.intuit.qbo.attachable.external.client import QboAttachableClient

    assert hasattr(QboAttachableClient, "get_attachable")
    assert hasattr(QboAttachableClient, "download_attachable")
    assert hasattr(QboAttachableClient, "query_all_attachables")
    assert "get_attachable(id)" in playbook, "the direct-fetch guidance lost its entry point"
    assert "proving ABSENCE" in playbook, "the scan is no longer scoped to absence proofs"


def test_playbook_records_the_server_side_parent_filter(playbook: str) -> None:
    """The measured finding must stay stated with its negative control, since it
    reverses KI-28's premise and a future reader needs to know it was proven."""
    anchor = playbook.index("Server-side parent filter")
    para = playbook[anchor: anchor + 1500]
    assert "AttachableRef.EntityRef.value" in para
    assert "0 rows" in para, "the negative control (the proof it is a real filter) is gone"
    assert "U-433" in para, "the follow-up unit reference is gone"
    # The in-memory type guard must survive the optimisation. Assert the operative
    # sentence, not the bare symbol — `entity_ref_type` also appears in the
    # flat-field guidance immediately above, which would mask its removal here.
    assert "keep the existing exact `(entity_ref_type, entity_ref_value)` check in memory" in para, (
        "the exact-type guard is no longer required alongside the server-side filter"
    )
