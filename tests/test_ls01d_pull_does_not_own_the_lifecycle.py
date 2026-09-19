"""LS-01d — the QBO pull stopped owning the local lifecycle field.

The defect, in one line: every QBO pull connector's HIT-path UPDATE sent
`is_draft=False`. On Bill that was not a passive field write — `UpdateBillById`
carries

    [Status] = CASE WHEN @IsDraft = 0 AND [Status] <> 'completed'
                    THEN 'completed' ELSE [Status] END

so the unattended 15-minute pull FORCE-COMPLETED any draft it touched. A human
who started a Bill in the web UI and left it open could come back to a
completed document they never completed, with its AP already fanned out. The
pull's job is to mirror QBO's fields; the lifecycle is the app's, and only a
human moves it.

The fix is a removal, which is exactly the kind of change that rots silently —
nothing fails when someone re-adds a kwarg. So the pins here are structural and
read the connectors' own ASTs:

1. No UPDATE call in any pull connector passes `is_draft`.
2. CREATE calls still do — a pulled document is born complete, because QBO is
   the system of record for a document that already exists there. Blanket-
   removing the kwarg would have flipped 20k+ historical bills to draft.
   Expense's header connector (U-467) passes `status='completed'` instead of
   `is_draft`; every other connector still passes `is_draft`. Either form
   is the "born complete" contract for documents that already exist finished
   in QBO. Expense exception (U-486 Phase F, CREATE-only): the Purchase header
   connector may now be born `draft` when the pulled purchase still has a
   genuinely uncoded 58999 placeholder line (NEED TO CATEGORIZE account label
   AND ItemRef NULL). QBO card spend in that state is not a finished document
   — marking it `completed` was the false statement and produced 330
   completed-but-uncoded expenses. The UPDATE prohibition in pin 1 is
   unchanged; re-pulling an existing Expense never overrides a human lifecycle
   decision.
3. The two Bill-side terminal-lock exemptions SURVIVE the removal. They sat on
   the same call as the dropped kwarg and read like part of the same idea; they
   are not. 20,219 of 20,223 QBO-linked bills are completed, so dropping the
   exemption alongside the kwarg raises StatusLockedError on essentially every
   routine pull update and breaks the unattended job wholesale.

Plus the observability the removal made possible: `QboId IS NOT NULL AND
IsDraft = 1` was previously papered over by the force-completion, so it could
never be measured. It is now a real signal.
"""

import ast
import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# Every pull connector whose HIT path used to force the local lifecycle.
CONNECTORS = [
    "integrations/intuit/qbo/bill/connector/bill/business/service.py",
    "integrations/intuit/qbo/bill/connector/bill_line_item/business/service.py",
    "integrations/intuit/qbo/purchase/connector/expense/business/service.py",
    "integrations/intuit/qbo/purchase/connector/expense_line_item/business/service.py",
    "integrations/intuit/qbo/invoice/connector/invoice/business/service.py",
    "integrations/intuit/qbo/invoice/connector/invoice_line_item/business/service.py",
    "integrations/intuit/qbo/vendorcredit/connector/bill_credit/business/service.py",
    "integrations/intuit/qbo/vendorcredit/connector/bill_credit_line_item/business/service.py",
    "integrations/intuit/qbo/vendor/connector/vendor/business/service.py",
]


def _calls(path: str):
    """Yield (call_node, dotted_callee_name) for every call in a connector."""
    tree = ast.parse((REPO_ROOT / path).read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        yield node, node.func.attr


def _kwargs(call) -> set:
    return {kw.arg for kw in call.keywords if kw.arg is not None}


EXPENSE_HEADER_CONNECTOR = (
    "integrations/intuit/qbo/purchase/connector/expense/business/service.py"
)


def _expense_header_create_status_allowed(status_node) -> bool:
    """U-486 Phase F: CREATE may pass status='completed' or a lines-derived if."""
    if status_node is None:
        return False
    if isinstance(status_node, ast.Constant) and status_node.value == "completed":
        return True
    if isinstance(status_node, ast.IfExp):
        body = status_node.body
        orelse = status_node.orelse
        return (
            isinstance(body, ast.Constant)
            and body.value == "draft"
            and isinstance(orelse, ast.Constant)
            and orelse.value == "completed"
        )
    if isinstance(status_node, ast.Call):
        func = status_node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name == "_initial_status_for_purchase_lines":
            return True
    return False


# ---------------------------------------------------------------------------
# 1 — the removal itself, asserted against the AST rather than the file text
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", CONNECTORS)
def test_no_update_call_in_a_pull_connector_writes_is_draft(path):
    """THE core pin. Reading the AST (not the text) means a commented-out
    `is_draft=False` stays green and a real one goes red — the inverse of the
    false-confidence shape that bit this workstream five times."""
    offenders = [
        (name, call.lineno)
        for call, name in _calls(path)
        if name.startswith("update_by") and "is_draft" in _kwargs(call)
    ]
    assert offenders == [], (
        f"{path} re-introduced is_draft on an UPDATE path at {offenders}. On Bill "
        "this force-completes any draft the unattended pull touches."
    )


@pytest.mark.parametrize("path", CONNECTORS)
def test_no_update_call_hides_its_arguments_behind_a_splat(path):
    """Closes the hole the named-keyword scan leaves open (Codex P2).

    `update_by_public_id(..., **payload)` puts `is_draft` back on the wire
    while every assertion above stays green, because a `**` splat is an AST
    keyword whose `arg` is None and carries no statically-readable names. The
    connectors do not use that shape today; forbidding it is what keeps the
    pin above meaningful rather than merely currently-true.
    """
    splatted = [
        (name, call.lineno)
        for call, name in _calls(path)
        if name.startswith("update_by")
        and any(kw.arg is None for kw in call.keywords)
    ]
    assert splatted == [], (
        f"{path} passes **kwargs to an update call at {splatted}, which makes "
        "the is_draft pin unenforceable — pass the fields by name"
    )


def test_every_entity_create_site_still_passes_is_draft_or_status_completed():
    """The other half of the contract, and the reason a blanket removal is wrong.

    A document pulled from QBO already exists there — it is born complete.
    Pinned per connector rather than as a total (Codex P2): a bare count stays
    green when one site loses the kwarg and an unrelated `.create()` gains it,
    and it silently excused Vendor, whose create is in its own connector.

    Each create site must carry EITHER `is_draft` OR `status` whose AST value
    is the Constant `'completed'` — Expense's header connector switched to
    the latter in U-467.
    """
    missing = []
    for path in CONNECTORS:
        ok = False
        for call, name in _calls(path):
            if name != "create":
                continue
            kws = {kw.arg: kw.value for kw in call.keywords if kw.arg is not None}
            if "is_draft" in kws:
                ok = True
                break
            status_node = kws.get("status")
            if isinstance(status_node, ast.Constant) and status_node.value == "completed":
                ok = True
                break
            if path == EXPENSE_HEADER_CONNECTOR:
                owner = call.func.value
                owner_name = (
                    owner.attr
                    if isinstance(owner, ast.Attribute)
                    else getattr(owner, "id", None)
                )
                if owner_name == "expense_service" and _expense_header_create_status_allowed(
                    status_node
                ):
                    ok = True
                    break
        if not ok:
            missing.append(path)
    assert missing == [], (
        "these connectors no longer pass is_draft OR status='completed' on "
        f"CREATE — a pulled document would be born as a local draft: {missing}"
    )


def test_expense_header_connector_create_lands_status_not_is_draft():
    """U-467 / U-486 Phase F: the Purchase header connector binds the status
    triple and does not pass is_draft. Line items are a separate real column
    and stay on is_draft (out of scope). Status on CREATE is either the
    constant 'completed' or the documented conditional (draft when any line is
    a genuinely uncoded 58999 placeholder, else completed).
    """
    path = EXPENSE_HEADER_CONNECTOR
    creates = []
    for call, name in _calls(path):
        if name != "create":
            continue
        # Only the header create (`self.expense_service.create`). The same
        # file also calls `expense_line_item_attachment_service.create` to
        # hang a receipt off a line — that is not a lifecycle write.
        owner = call.func.value
        owner_name = owner.attr if isinstance(owner, ast.Attribute) else getattr(owner, "id", None)
        if owner_name == "expense_service":
            creates.append(call)
    assert creates, f"{path} has no expense_service.create() call"
    for call in creates:
        kws = {kw.arg: kw.value for kw in call.keywords if kw.arg is not None}
        status_node = kws.get("status")
        origin_node = kws.get("status_origin")
        assert _expense_header_create_status_allowed(status_node), (
            "Expense header create must pass status='completed' or the "
            "documented draft/completed conditional from purchase lines"
        )
        assert isinstance(origin_node, ast.Constant) and origin_node.value == "qbo_pull", (
            "Expense header create must pass status_origin='qbo_pull'"
        )
        assert "status_source_ref" in kws, "Expense header create must pass status_source_ref"
        assert "is_draft" not in kws, "Expense header create must not pass is_draft"


# ---------------------------------------------------------------------------
# 2 — the exemptions that sat on the same call and must NOT have gone with it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path,marker",
    [
        (
            "integrations/intuit/qbo/bill/connector/bill/business/service.py",
            "_via_completion_pipeline",
        ),
        (
            "integrations/intuit/qbo/bill/connector/bill_line_item/business/service.py",
            "_via_internal_pipeline",
        ),
        (
            "integrations/intuit/qbo/purchase/connector/expense/business/service.py",
            "_via_completion_pipeline",
        ),
        (
            "integrations/intuit/qbo/purchase/connector/expense_line_item/business/service.py",
            "_via_internal_pipeline",
        ),
    ],
)
def test_the_terminal_lock_exemption_survived_the_is_draft_drop(path, marker):
    """`shared/lifecycle/terminal_lock.py` refuses writes to a completed Bill.

    20,219 of 20,223 QBO-linked bills ARE completed, so without this marker the
    routine 15-minute pull raises StatusLockedError on nearly every update it
    attempts. The marker looked like part of the completion idea being removed;
    it is the opposite — it is what lets a field mirror land on an already-
    finished document.
    """
    exempted = [
        call.lineno
        for call, name in _calls(path)
        if name.startswith("update_by") and marker in _kwargs(call)
    ]
    assert exempted, (
        f"{path} no longer passes {marker}= on any update call — the terminal "
        "lock will refuse the pull on every completed Bill it touches"
    )


def test_no_connector_routes_an_underscore_kwarg_through_a_model_constructor():
    """Pins the pydantic trap that made the first attempt at this silently inert.

    A leading-underscore name passed into a pydantic model becomes a PRIVATE
    ATTRIBUTE and is dropped from `model_dump()` — so routing the terminal-lock
    exemption through an update model and splatting it looks right, imports
    clean, type-checks, runs, and does nothing. The symptom would be
    StatusLockedError on every completed Bill the pull touches, with the
    "fix" plainly visible in the diff.

    Scanned across every connector, not just the two that carry the exemption
    today: the trap belongs to the pattern, not to one call site.
    """
    offenders = []
    for path in CONNECTORS:
        tree = ast.parse((REPO_ROOT / path).read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
            # A pydantic model constructor, by convention: CamelCase callee.
            if not name or not name[0].isupper():
                continue
            for kw in node.keywords:
                if kw.arg and kw.arg.startswith("_"):
                    offenders.append((path, node.lineno, name, kw.arg))
    assert offenders == [], (
        "a leading-underscore kwarg is being passed into a model constructor, "
        f"where pydantic silently drops it: {offenders}"
    )


# ---------------------------------------------------------------------------
# 3 — field ownership no longer names a column that does not exist
# ---------------------------------------------------------------------------


def test_field_ownership_has_no_phantom_review_status_column():
    """`review_status_id` was listed as app-owned on BILL and EXPENSE. Neither
    table has the column — the only ReviewStatus columns in the schema live on
    dbo.Review and dbo.ReviewEntry. A registry that names fields which cannot
    exist teaches the next reader the wrong shape of the data."""
    from integrations.intuit.qbo.base import field_ownership

    # Asserted against the EXECUTABLE text. The module's own comment explains
    # the removal and names the phantom, so an un-stripped check goes RED on a
    # correct file — the mirror image of the false-GREEN that comment text
    # produced five times in this workstream.
    src = inspect.getsource(field_ownership)
    executable = "\n".join(line.split("#")[0] for line in src.splitlines())
    assert "review_status_id" not in executable


# ---------------------------------------------------------------------------
# 4 — the drift the removal made observable
# ---------------------------------------------------------------------------


def test_lifecycle_linkage_drift_type_is_registered_with_a_severity():
    from integrations.intuit.qbo.base.drift_types import DRIFT_QBO_LINKED_NOT_COMPLETED
    from integrations.intuit.qbo.reconciliation.business.service import SEVERITY_BY_DRIFT

    sev = SEVERITY_BY_DRIFT[DRIFT_QBO_LINKED_NOT_COMPLETED]
    assert sev == "medium", (
        "not 'low': there is no auto-fix path, so a human has to look at it"
    )


def _recon(rows_by_call=None, unscoped=0, fail_first=False):
    """A ReconciliationService with a scripted DB and no real __init__.

    The method issues TWO statements per entity — the realm-scoped TOP scan,
    then a COUNT of NULL-realm rows — so the fake dispatches on the SQL text
    rather than on call order.
    """
    from integrations.intuit.qbo.reconciliation.business.service import ReconciliationService

    state = {"conns": 0, "sql": []}
    rows_by_call = rows_by_call if rows_by_call is not None else [("pub-1", "9")]

    def _connect():
        state["conns"] += 1
        if fail_first and state["conns"] == 1:
            raise RuntimeError("Invalid object name")
        cursor = MagicMock()

        def _execute(sql, *params):
            state["sql"].append((sql, params))
            cursor._last = sql
            return cursor

        cursor.execute.side_effect = _execute
        cursor.fetchall.side_effect = lambda: list(rows_by_call)
        cursor.fetchone.side_effect = lambda: (unscoped,)
        conn = MagicMock()
        conn.cursor.return_value = cursor
        conn.__enter__ = lambda s: s
        conn.__exit__ = lambda s, *a: False
        return conn

    svc = ReconciliationService.__new__(ReconciliationService)
    svc._dedupe_key_caches = {}
    svc.repo = MagicMock()
    svc.repo.read_unresolved_issue_keys_by_drift_type.return_value = []
    svc._record_issue = MagicMock(return_value=True)
    return svc, state, _connect


def test_reconcile_lifecycle_linkage_flags_qbo_linked_drafts_on_every_entity():
    from integrations.intuit.qbo.base.drift_types import DRIFT_QBO_LINKED_NOT_COMPLETED

    svc, state, connect = _recon()
    with patch("shared.database.get_connection", side_effect=connect):
        result = svc.reconcile_lifecycle_linkage(realm_id="realm-1")

    assert result["flagged"] == 4, "one per entity: Bill, Expense, Invoice, BillCredit"
    assert result["errors"] == 0
    assert set(result["checked"]) == {"Bill", "Expense", "Invoice", "BillCredit"}

    scans = [(sql, prm) for sql, prm in state["sql"] if "TOP" in sql]
    assert len(scans) == 4
    for sql, params in scans:
        assert "[QboId] IS NOT NULL AND [IsDraft] = 1" in sql, (
            "the invariant must be both halves — a QboId alone is not drift"
        )
        assert "[RealmId] = ?" in sql, "a QBO id is unique only within a realm"
        assert params == (svc.LIFECYCLE_LINKAGE_SCAN_CAP, "realm-1")

    for call in svc._record_issue.call_args_list:
        assert call.kwargs["drift_type"] == DRIFT_QBO_LINKED_NOT_COMPLETED
        assert call.kwargs["action"] == "flagged", "read-only: never auto-fixed"
        assert call.kwargs["realm_id"] == "realm-1"

    assert svc._dedupe_key_caches[DRIFT_QBO_LINKED_NOT_COMPLETED] == {
        ("realm-1", "Bill", "9"), ("realm-1", "Expense", "9"),
        ("realm-1", "Invoice", "9"), ("realm-1", "BillCredit", "9"),
    }, "a durably-written issue must cache its key so the next run suppresses it"


def test_a_draft_in_another_realm_is_never_flagged_against_this_one():
    """Codex P1. QBO ids are unique per realm, so recording an old-realm
    document's issue under the realm being reconciled names a QBO document
    that is not the one the operator will open."""
    svc, state, connect = _recon()
    with patch("shared.database.get_connection", side_effect=connect):
        svc.reconcile_lifecycle_linkage(realm_id="realm-1")

    for sql, params in state["sql"]:
        if "TOP" in sql:
            assert "realm-1" in params, "the scan is not bound to the caller's realm"


def test_null_realm_drafts_are_counted_but_not_flagged():
    """We do not know which realm they belong to; guessing is the bug above.
    Counting them keeps them from disappearing entirely."""
    svc, state, connect = _recon(rows_by_call=[], unscoped=3)
    with patch("shared.database.get_connection", side_effect=connect):
        result = svc.reconcile_lifecycle_linkage(realm_id="realm-1")

    assert result["flagged"] == 0
    assert result["unscoped"] == {
        "Bill": 3, "Expense": 3, "Invoice": 3, "BillCredit": 3,
    }
    assert svc._record_issue.call_count == 0

    # The count must come from a NULL-realm query, not a second realm-scoped
    # one. Asserted on the SQL because the scripted cursor answers any COUNT
    # identically — without this the two are indistinguishable to the fake.
    counters = [sql for sql, _ in state["sql"] if "COUNT(*)" in sql]
    assert len(counters) == 4
    for sql in counters:
        assert "[RealmId] IS NULL" in sql and "[RealmId] = ?" not in sql


def test_an_already_open_issue_is_not_re_minted_on_the_next_run():
    """Codex P2. Without this a single unresolved draft writes one fresh issue
    per daily run forever, and acknowledging it suppresses nothing."""
    from integrations.intuit.qbo.base.drift_types import DRIFT_QBO_LINKED_NOT_COMPLETED

    svc, state, connect = _recon()
    svc.repo.read_unresolved_issue_keys_by_drift_type.return_value = [
        ("realm-1", "Bill", "9"),
    ]
    with patch("shared.database.get_connection", side_effect=connect):
        result = svc.reconcile_lifecycle_linkage(realm_id="realm-1")

    svc.repo.read_unresolved_issue_keys_by_drift_type.assert_called_once_with(
        DRIFT_QBO_LINKED_NOT_COMPLETED
    )
    assert result["flagged"] == 4, "still detected — dedupe suppresses the WRITE only"
    assert result["flagged_deduped"] == 1
    written = [c.kwargs["entity_type"] for c in svc._record_issue.call_args_list]
    assert "Bill" not in written and len(written) == 3


def test_a_failed_issue_write_does_not_suppress_the_next_run():
    """Suppression is only ever justified by a row that really exists."""
    from integrations.intuit.qbo.base.drift_types import DRIFT_QBO_LINKED_NOT_COMPLETED

    svc, state, connect = _recon()
    svc._record_issue = MagicMock(return_value=False)
    with patch("shared.database.get_connection", side_effect=connect):
        result = svc.reconcile_lifecycle_linkage(realm_id="realm-1")
    assert result["flagged_deduped"] == 0
    assert svc._record_issue.call_count == 4

    # The count assertions above cannot see this: the dedupe key carries
    # entity_type, so four different entities never collide WITHIN one run and
    # a wrongly-placed `.add()` stays invisible. Read the cache directly.
    assert svc._dedupe_key_caches[DRIFT_QBO_LINKED_NOT_COMPLETED] == set(), (
        "a key was cached for an issue that was never durably written — the "
        "next run would suppress a flag that does not exist"
    )


def test_the_scan_is_capped_and_says_so():
    """A bulk state must not fetchall() an unbounded set and open one
    issue-write connection per row in silence."""
    from integrations.intuit.qbo.reconciliation.business.service import ReconciliationService

    cap = ReconciliationService.LIFECYCLE_LINKAGE_SCAN_CAP
    svc, state, connect = _recon(rows_by_call=[(f"pub-{i}", str(i)) for i in range(cap)])
    with patch("shared.database.get_connection", side_effect=connect):
        result = svc.reconcile_lifecycle_linkage(realm_id="realm-1")

    assert result["capped"] == ["Bill", "Expense", "Invoice", "BillCredit"]
    assert all(sql_params[1][0] == cap for sql_params in state["sql"] if "TOP" in sql_params[0])


def test_one_entity_failing_does_not_abort_the_other_three():
    """Per-entity isolation. A missing column or a blocked table on one entity
    must not silently take the whole detector down with it."""
    svc, state, connect = _recon(fail_first=True)
    with patch("shared.database.get_connection", side_effect=connect):
        result = svc.reconcile_lifecycle_linkage(realm_id="realm-1")

    assert result["flagged"] == 3
    assert result["errors"] == 1
    assert "Bill" not in result["checked"], "the failed entity reports nothing, not zero"


def test_the_result_speaks_the_family_count_vocabulary():
    """Every other reconciler returns RECONCILE_COUNT_KEYS; a caller that
    aggregates them must not silently read 0 for this one."""
    from integrations.intuit.qbo.reconciliation.business.service import RECONCILE_COUNT_KEYS

    svc, state, connect = _recon()
    with patch("shared.database.get_connection", side_effect=connect):
        result = svc.reconcile_lifecycle_linkage(realm_id="realm-1")
    assert set(RECONCILE_COUNT_KEYS) <= set(result)


@pytest.mark.parametrize(
    "module_path,registry",
    [
        ("shared.api.admin", None),
        ("shared.scheduler", None),
    ],
)
def test_lifecycle_linkage_is_wired_into_both_dispatch_lists(module_path, registry):
    """The detector exists in two places or it runs in neither: the daily
    scheduler tick and the manual admin endpoint."""
    import importlib

    mod = importlib.import_module(module_path)
    src = inspect.getsource(mod)
    assert '"lifecycle_linkage"' in src, f"{module_path} does not dispatch it"
    assert "reconcile_lifecycle_linkage" in src


def test_scheduler_runs_it_alongside_its_siblings():
    """Guards the shape that would make it a no-op: registered under a name
    nothing calls, or in a list the tick does not iterate."""
    import shared.scheduler as sched

    src = inspect.getsource(sched)
    block = src[src.index("reconcile_lifecycle_linkage") - 2000:]
    assert "reconcile_bills" in src and "reconcile_vendor_credits" in src
    # same tuple-of-(name, fn) shape as the siblings, not a lone call
    assert "(\"lifecycle_linkage\"" in src or "('lifecycle_linkage'" in src
