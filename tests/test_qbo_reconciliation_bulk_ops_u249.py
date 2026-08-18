"""U-249: bulk-acknowledge (GAP 1) + Severity/Action filters and keep-newest (GAP 2).

Two kinds of test live here, because the harness is pure-logic / no-live-DB:

1. SQL-STRUCTURE tests that parse the real .sql file. These exist to pin the
   load-bearing decision — the GROUPING KEY. If someone drops QboId from the
   PARTITION BY, keep-newest silently starts collapsing 26 distinct voided bills
   into 1 and resolving 25 real drift rows. A comment cannot prevent that; this
   test can.

2. A Python MIRROR of the sproc's WHERE + ROW_NUMBER semantics, exercised over a
   fixture that reproduces the real 2026-08-18 backlog (including the 12
   known-bogus watermark_hold_bound_exceeded rows). This encodes the behavioural
   spec: exactly one survivor per group, no-op on per-entity drift, and the bogus
   fixtures stay untouched when the caller scopes by drift type.

Both were cross-checked read-only against prod on 2026-08-18: keep-newest takes
qbo_missing_locally/Bill 52 -> 1 survivor (51 resolved), invoice_draw_mismatch 30 -> 1,
Expense 22 -> 1, BillCredit 18 -> 1, and is a verified NO-OP on the 26 qbo_voided
Bill / 20 qbo_voided Expense rows.
"""
from __future__ import annotations

import re
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from scripts.manage_qbo_reconciliation_issues import (
    _build_bulk_filters,
    cmd_bulk_acknowledge,
    cmd_bulk_resolve,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SQL_PATH = (
    REPO_ROOT
    / "integrations/intuit/qbo/reconciliation/sql/qbo.reconciliation_issue.sql"
)

# The one decision this whole unit turns on. Mirrored from the sproc comment.
GROUPING_KEY = (
    "RealmId",
    "DriftType",
    "EntityType",
    "QboId",
    "EntityPublicId",
    "Severity",
    "Action",
)

BULK_RESOLVE = "BulkResolveQboReconciliationIssuesByFilter"
BULK_ACK = "BulkAcknowledgeQboReconciliationIssuesByFilter"


# --------------------------------------------------------------------------
# SQL parsing helpers
# --------------------------------------------------------------------------
def _sql_text() -> str:
    return SQL_PATH.read_text(encoding="utf-8")


def _batches() -> list[str]:
    return [b for b in re.split(r"(?im)^\s*GO\s*$", _sql_text()) if b.strip()]


def _proc_batch(name: str) -> str:
    """The single GO-delimited batch defining `name`. Fails if absent or if the
    batch is not GO-terminated (the T-SQL trap called out in CLAUDE.md: an
    un-terminated CREATE PROCEDURE swallows whatever follows into its body)."""
    pattern = re.compile(
        r"(?is)CREATE\s+(?:OR\s+ALTER\s+)?PROCEDURE\s+(?:\[?\w+\]?\s*\.\s*)?\[?"
        + name
        + r"\]?\b"
    )
    hits = [b for b in _batches() if pattern.search(b)]
    assert len(hits) == 1, f"expected exactly 1 GO-terminated batch defining {name}, got {len(hits)}"
    return hits[0]


def _strip_comments(sql: str) -> str:
    """Drop -- line comments. These batches carry long rationale comments that
    legitimately quote param names and literals, so structural assertions must be
    made against CODE, not prose."""
    return "\n".join(re.sub(r"--.*$", "", line) for line in sql.splitlines())


def _params_block(batch: str) -> str:
    """Text between the proc's opening '(' and its matching ')'.

    Anchored AFTER the CREATE PROCEDURE token — the leading rationale comment
    contains parentheses of its own, so a naive first-'(' scan lands in prose.
    """
    m = re.search(r"(?is)CREATE\s+(?:OR\s+ALTER\s+)?PROCEDURE\s+[\[\]\w.]+", batch)
    assert m, "no CREATE PROCEDURE in batch"
    start = batch.index("(", m.end())
    depth, i = 0, start
    while i < len(batch):
        if batch[i] == "(":
            depth += 1
        elif batch[i] == ")":
            depth -= 1
            if depth == 0:
                return batch[start + 1 : i]
        i += 1
    raise AssertionError("unbalanced parens in param block")


# --------------------------------------------------------------------------
# 1. SQL structure — the grouping key is the load-bearing decision
# --------------------------------------------------------------------------
def test_bulk_resolve_partition_by_is_exactly_the_documented_grouping_key():
    """THE guard. QboId in the key is what makes keep-newest non-destructive:
    per-entity drift (qbo_voided) carries a distinct QboId, so each such row is
    its own group and can never be collapsed. Severity/Action in the key stop a
    critical/manual_review row being resolved in favour of a newer low/flagged
    one (measured: 210 identity groups in prod span mixed classifications)."""
    batch = _strip_comments(_proc_batch(BULK_RESOLVE))
    m = re.search(r"(?is)PARTITION\s+BY\s+(.*?)\s+ORDER\s+BY", batch)
    assert m, "keep-newest must be implemented with a PARTITION BY window"
    cols = tuple(
        c.strip().strip("[]") for c in m.group(1).replace("\n", " ").split(",")
    )
    assert cols == GROUPING_KEY, (
        f"grouping key changed: {cols!r} != {GROUPING_KEY!r}. "
        "Dropping QboId makes keep-newest collapse distinct real drift; dropping "
        "Severity/Action lets it resolve a critical row in favour of a low one."
    )


def test_keep_newest_ordering_has_deterministic_tiebreak():
    """DATETIME2(3) ties are real — the 20 qbo_voided Expense rows were emitted
    inside a 28-second window. Without the Id tiebreak, 'exactly one survivor'
    is not deterministic."""
    batch = _strip_comments(_proc_batch(BULK_RESOLVE))
    m = re.search(
        r"(?is)PARTITION\s+BY\s+.*?ORDER\s+BY\s+(.*?)\)\s*(?:AS\s*)?\[?RecencyRank",
        batch,
    )
    assert m, "could not locate the window ORDER BY"
    order = re.sub(r"\s+", " ", m.group(1)).strip()
    assert re.search(r"(?i)\[?CreatedDatetime\]?\s+DESC", order), order
    assert re.search(r"(?i)\[?Id\]?\s+DESC", order), f"missing Id DESC tiebreak: {order}"


def test_rank_one_is_withheld_only_when_keep_newest_is_on():
    """RecencyRank = 1 is the survivor; it must be excluded from the candidate
    set only under @KeepNewestPerGroup = 1, so default behaviour is unchanged."""
    batch = _strip_comments(_proc_batch(BULK_RESOLVE))
    assert re.search(
        r"(?is)WHERE\s+@KeepNewestPerGroup\s*=\s*0\s+OR\s+\[?RecencyRank\]?\s*>\s*1",
        batch,
    ), "candidate derivation must be: keep-newest off OR rank > 1"


# --------------------------------------------------------------------------
# 1b. SQL structure — new params, guards, dry-run contract
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "param, default",
    [("@Severity", "NULL"), ("@Action", "NULL"), ("@KeepNewestPerGroup", "0")],
)
def test_new_bulk_resolve_params_have_backwards_safe_defaults(param, default):
    """Per CLAUDE.md: new sproc params take defaults so older callers stay safe.
    @KeepNewestPerGroup defaulting to 0 preserves the pre-U-249 behaviour exactly."""
    block = _params_block(_proc_batch(BULK_RESOLVE))
    m = re.search(rf"(?is){param}\s+\w+(?:\(\w+\))?\s*=\s*(\S+?)\s*[,)\n]", block)
    assert m, f"{param} not declared with a default in {BULK_RESOLVE}"
    assert m.group(1).upper().rstrip(",") == default.upper()


def test_bulk_acknowledge_sproc_exists_and_is_go_terminated():
    batch = _strip_comments(_proc_batch(BULK_ACK))  # asserts exactly one GO batch
    assert re.search(r"(?i)SET\s+NOCOUNT\s+ON", batch), "pyodbc requires SET NOCOUNT ON"


@pytest.mark.parametrize("name", [BULK_RESOLVE, BULK_ACK])
def test_both_bulk_sprocs_require_at_least_one_scoping_filter(name):
    """Blast-radius bound. Severity/Action are narrowing-only and deliberately do
    NOT appear in this guard — '@Severity = low' alone would match every
    low-severity row of every drift type."""
    code = _strip_comments(_proc_batch(name))
    m = re.search(
        r"(?is)IF\s+@DriftType\s+IS\s+NULL\s+AND\s+@EntityType\s+IS\s+NULL\s+"
        r"AND\s+@CreatedBefore\s+IS\s+NULL(.*?)RAISERROR",
        code,
    )
    assert m, f"{name} lost its at-least-one-filter RAISERROR guard"
    guard = code[: code.index("RAISERROR")]
    assert "@Severity IS NULL" not in guard, "Severity must not satisfy the filter guard"
    assert "@Action IS NULL" not in guard, "Action must not satisfy the filter guard"


@pytest.mark.parametrize("name", [BULK_RESOLVE, BULK_ACK])
def test_dry_run_returns_before_any_mutation(name):
    """DRY-RUN BY DEFAULT contract: the @DryRun block must RETURN before the
    sproc reaches its UPDATE, so a preview can never mutate."""
    code = _strip_comments(_proc_batch(name))
    dry = re.search(r"(?is)IF\s+@DryRun\s*=\s*1\b", code)
    assert dry, f"{name} has no @DryRun branch"
    ret = re.search(r"(?is)\bRETURN\s*;", code[dry.start() :])
    assert ret, f"{name} dry-run branch does not RETURN"
    upd = re.search(r"(?i)\bUPDATE\s+ri\b", code)
    assert upd, f"{name} has no UPDATE"
    assert dry.start() + ret.end() < upd.start(), (
        f"{name}: the dry-run RETURN must precede the UPDATE"
    )


@pytest.mark.parametrize("name", [BULK_RESOLVE, BULK_ACK])
def test_bulk_sprocs_recheck_status_inside_the_transaction(name):
    """The candidate set is a snapshot taken before the transaction opened; the
    UPDATE must re-check Status so a concurrent call cannot have its timestamps
    clobbered."""
    code = _strip_comments(_proc_batch(name))
    tail = code[code.index("BEGIN TRANSACTION") :]
    assert re.search(r"(?is)UPDATE\s+ri\b.*?WHERE\s+ri\.\[Status\]", tail), (
        f"{name} UPDATE lacks the concurrency re-check on ri.[Status]"
    )


def test_bulk_acknowledge_only_transitions_from_open():
    """open is the only legal source state for open -> acknowledged. Intersecting
    the caller's @Status with 'open' makes a stray @Status a safe no-op rather
    than an illegal backwards transition out of 'resolved'."""
    code = _strip_comments(_proc_batch(BULK_ACK))
    assert re.search(r"(?is)\[Status\]\s*=\s*@Status\s+AND\s+\[Status\]\s*=\s*'open'", code)
    tail = code[code.index("BEGIN TRANSACTION") :]
    assert re.search(r"(?is)WHERE\s+ri\.\[Status\]\s*=\s*'open'", tail)
    assert "'resolved'" not in tail, "acknowledge must never touch resolved rows"


def test_bulk_acknowledge_sets_acknowledged_not_resolved():
    """GAP 1 is precisely the acknowledge/resolve distinction — guard it."""
    code = _strip_comments(_proc_batch(BULK_ACK))
    assert re.search(r"(?is)SET\s+\[Status\]\s*=\s*'acknowledged'", code)
    assert re.search(r"(?i)\[AcknowledgedAt\]\s*=\s*@Now", code)
    assert "[ResolvedAt]" not in code, "bulk-acknowledge must not stamp ResolvedAt"


def test_bulk_acknowledge_has_no_keep_newest():
    """Acknowledgement applies to per-entity findings where every row is a
    distinct real item; thinning them would defeat the point."""
    assert "@KeepNewestPerGroup" not in _strip_comments(_proc_batch(BULK_ACK))


# --------------------------------------------------------------------------
# 2. Behavioural mirror of the sproc semantics
# --------------------------------------------------------------------------
def _row(id, drift, entity, qbo_id, created, severity="low", action="flagged",
         status="open", realm="R1", entity_public_id=None):
    return SimpleNamespace(
        Id=id, DriftType=drift, EntityType=entity, QboId=qbo_id,
        CreatedDatetime=created, Severity=severity, Action=action,
        Status=status, RealmId=realm, EntityPublicId=entity_public_id,
    )


def _matches(r, *, drift_type=None, entity_type=None, severity=None,
             action=None, status="open", realm_id=None, created_before=None):
    """Mirror of the sproc WHERE clause. EntityType/DriftType comparisons are
    case-insensitive because SQL Server's default collation is."""
    def ci_eq(a, b):
        return a is not None and b is not None and a.lower() == b.lower()

    if r.Status != status or r.Status not in ("open", "acknowledged"):
        return False
    if drift_type is not None and not ci_eq(r.DriftType, drift_type):
        return False
    if entity_type is not None and not ci_eq(r.EntityType, entity_type):
        return False
    if severity is not None and not ci_eq(r.Severity, severity):
        return False
    if action is not None and not ci_eq(r.Action, action):
        return False
    if realm_id is not None and r.RealmId != realm_id:
        return False
    if created_before is not None and not r.CreatedDatetime < created_before:
        return False
    return True


def _group_key(r):
    """Mirror of the sproc's PARTITION BY.

    Text components are case-folded because SQL Server's default collation is
    case-INSENSITIVE, so PARTITION BY puts EntityType 'bill' and 'Bill' in the
    SAME group. Modelling that faithfully matters: it is why the 12 watermark
    fixtures form 3 groups (vc-1 / staging-only / NULL) rather than 5, and hence
    why an unscoped --entity-type Bill sweep would resolve 9 of them. Confirmed
    against prod 2026-08-18.
    """
    def ci(v):
        return v.lower() if isinstance(v, str) else v

    return tuple(ci(getattr(r, col)) for col in GROUPING_KEY)


def _ranked(rows, **filters):
    """Mirror of ROW_NUMBER() OVER (PARTITION BY key ORDER BY Created DESC, Id DESC)."""
    matched = [r for r in rows if _matches(r, **filters)]
    groups: dict[tuple, list] = {}
    for r in matched:
        groups.setdefault(_group_key(r), []).append(r)
    survivors, candidates = [], []
    for members in groups.values():
        members.sort(key=lambda r: (r.CreatedDatetime, r.Id), reverse=True)
        survivors.append(members[0])
        candidates.extend(members[1:])
    return matched, survivors, candidates


@pytest.fixture
def backlog():
    """Reproduces the shape of the real open backlog measured 2026-08-18."""
    rows = []
    n = 0
    # Recurring SUMMARY rows: QboId NULL, one per reconcile run.
    for drift, entity, sev, count in [
        ("qbo_missing_locally", "Bill", "low", 52),
        ("invoice_draw_mismatch", "Invoice", "medium", 30),
        ("qbo_missing_locally", "Expense", "low", 22),
        ("qbo_missing_locally", "BillCredit", "low", 18),
    ]:
        for i in range(count):
            n += 1
            rows.append(_row(n, drift, entity, None, 1_000 + i, severity=sev))
    # Real PER-ENTITY drift: distinct QboId per row.
    for entity, count in [("Bill", 26), ("Expense", 20)]:
        for i in range(count):
            n += 1
            rows.append(_row(n, "qbo_voided", entity, f"{entity}-{i}", 2_000 + i))
    # The 12 known-bogus watermark fixtures — note lowercase 'bill' on 10 of them.
    for i in range(10):
        n += 1
        rows.append(_row(n, "watermark_hold_bound_exceeded", "bill",
                         "vc-1" if i % 2 else "staging-only", 3_000 + i,
                         severity="critical", action="manual_review"))
    for i in range(2):
        n += 1
        rows.append(_row(n, "watermark_hold_bound_exceeded", "Bill",
                         None if i == 0 else "staging-only", 3_100 + i,
                         severity="critical", action="manual_review"))
    return rows


@pytest.mark.parametrize(
    "drift, entity, severity, expect_matched, expect_resolved",
    [
        ("qbo_missing_locally", "Bill", "low", 52, 51),
        ("invoice_draw_mismatch", "Invoice", "medium", 30, 29),
        ("qbo_missing_locally", "Expense", "low", 22, 21),
        ("qbo_missing_locally", "BillCredit", "low", 18, 17),
    ],
)
def test_keep_newest_keeps_exactly_one_per_group(
    backlog, drift, entity, severity, expect_matched, expect_resolved
):
    """Summary rows share one group (QboId NULL) -> exactly one survivor.
    Counts match the live prod dry-run measured 2026-08-18."""
    matched, survivors, candidates = _ranked(
        backlog, drift_type=drift, entity_type=entity, severity=severity, action="flagged"
    )
    assert len(matched) == expect_matched
    assert len(survivors) == 1
    assert len(candidates) == expect_resolved
    # the survivor is the NEWEST
    assert survivors[0].CreatedDatetime == max(r.CreatedDatetime for r in matched)


def test_keep_newest_never_collapses_distinct_per_entity_drift(backlog):
    """The safety property. qbo_voided rows each carry a distinct QboId, so each
    is its own group and keep-newest resolves NOTHING — verified against prod
    (26 of 26 and 20 of 20 survive)."""
    for entity, count in [("Bill", 26), ("Expense", 20)]:
        matched, survivors, candidates = _ranked(
            backlog, drift_type="qbo_voided", entity_type=entity
        )
        assert len(matched) == count
        assert len(survivors) == count
        assert candidates == [], "keep-newest must be a no-op on per-entity drift"


def test_every_group_retains_at_least_one_unresolved_row(backlog):
    """Whatever the filter, keep-newest must never empty a group — that is what
    'destroying the live signal' would mean."""
    for filters in [
        dict(drift_type="qbo_missing_locally", entity_type="Bill"),
        dict(drift_type="qbo_voided"),
        dict(entity_type="Invoice"),
        dict(drift_type="qbo_missing_locally"),
    ]:
        matched, survivors, candidates = _ranked(backlog, **filters)
        surviving_keys = {_group_key(r) for r in survivors}
        assert surviving_keys == {_group_key(r) for r in matched}
        assert not surviving_keys & {_group_key(r) for r in candidates} - surviving_keys


# --------------------------------------------------------------------------
# 3. The 12 known-bogus watermark rows must not be swept
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "drift, entity",
    [
        ("qbo_missing_locally", "Bill"),
        ("invoice_draw_mismatch", "Invoice"),
        ("qbo_missing_locally", "Expense"),
        ("qbo_missing_locally", "BillCredit"),
        ("qbo_voided", "Bill"),
        ("qbo_voided", "Expense"),
    ],
)
def test_drift_type_scoping_excludes_the_bogus_watermark_rows(backlog, drift, entity):
    """Scoping by drift type is what protects the fixtures. Verified read-only
    against prod: 0 watermark rows in scope for every one of these filters."""
    matched, _, candidates = _ranked(
        backlog, drift_type=drift, entity_type=entity
    )
    assert not [r for r in matched if r.DriftType == "watermark_hold_bound_exceeded"]
    assert not [r for r in candidates if r.DriftType == "watermark_hold_bound_exceeded"]


def test_severity_and_action_are_a_second_independent_guard(backlog):
    """Even wildcarding drift type, --severity low --action flagged cannot reach
    the critical/manual_review watermark rows."""
    matched, _, _ = _ranked(backlog, entity_type="Bill", severity="low", action="flagged")
    assert matched, "sanity: the filter should still match the real Bill drift"
    assert not [r for r in matched if r.DriftType == "watermark_hold_bound_exceeded"]


def test_entity_type_alone_DOES_reach_the_watermark_rows(backlog):
    """Encodes the known hazard rather than leaving it to memory: SQL Server's
    default collation is case-insensitive, so '--entity-type Bill' also matches
    the fixtures stored as 'bill'. Measured on prod: 90 rows matched, 12 of them
    watermark fixtures, 9 of which keep-newest would resolve. This is exactly why
    --drift-type is documented as STRONGLY RECOMMENDED."""
    matched, _, candidates = _ranked(backlog, entity_type="Bill")
    bogus = [r for r in matched if r.DriftType == "watermark_hold_bound_exceeded"]
    assert len(bogus) == 12, "case-insensitive match must pick up lowercase 'bill'"
    assert len([r for r in candidates
                if r.DriftType == "watermark_hold_bound_exceeded"]) == 9


# --------------------------------------------------------------------------
# 4. Dry-run contract + param plumbing (mocks — no DB)
# --------------------------------------------------------------------------
def _bulk_args(**over):
    base = dict(
        drift_type="qbo_voided", entity_type="Bill", created_before_days=None,
        created_before_date=None, realm_id=None, severity="low", action="flagged",
        status="open", max_rows=1000, apply=False,
    )
    base.update(over)
    return Namespace(**base)


def test_bulk_acknowledge_dry_run_previews_and_mutates_nothing(capsys):
    repo = MagicMock()
    repo.preview_bulk_acknowledge.return_value = [{
        "id": 7, "drift_type": "qbo_voided", "entity_type": "Bill", "qbo_id": "Q7",
        "severity": "low", "action": "flagged", "created_datetime": "2026-08-11 07:29:41",
        "total_match_count": 26, "total_kept_count": None,
    }]
    with patch(
        "scripts.manage_qbo_reconciliation_issues.ReconciliationIssueRepository",
        return_value=repo,
    ):
        rc = cmd_bulk_acknowledge(_bulk_args())
    out = capsys.readouterr().out
    assert rc == 0
    repo.preview_bulk_acknowledge.assert_called_once()
    repo.bulk_acknowledge.assert_not_called()
    assert "Matched 26 row(s)" in out
    assert "DRY-RUN: no rows modified" in out


def test_bulk_acknowledge_apply_calls_the_mutating_path():
    repo = MagicMock()
    repo.preview_bulk_acknowledge.return_value = []
    repo.bulk_acknowledge.return_value = [1, 2, 3]
    with patch(
        "scripts.manage_qbo_reconciliation_issues.ReconciliationIssueRepository",
        return_value=repo,
    ):
        rc = cmd_bulk_acknowledge(_bulk_args(apply=True))
    assert rc == 0
    repo.bulk_acknowledge.assert_called_once()
    kwargs = repo.bulk_acknowledge.call_args.kwargs
    assert kwargs["drift_type"] == "qbo_voided"
    assert kwargs["severity"] == "low"
    assert kwargs["action"] == "flagged"
    assert kwargs["status"] == "open"
    # acknowledge has no keep-newest — it must not leak in from bulk-resolve
    assert "keep_newest_per_group" not in kwargs


def test_bulk_resolve_dry_run_threads_keep_newest_and_mutates_nothing(capsys):
    repo = MagicMock()
    repo.preview_bulk_resolve.return_value = [{
        "id": 5, "drift_type": "qbo_missing_locally", "entity_type": "Bill",
        "qbo_id": None, "severity": "low", "action": "flagged",
        "created_datetime": "2026-06-21 07:07:41",
        "total_match_count": 51, "total_kept_count": 1,
    }]
    args = _bulk_args(drift_type="qbo_missing_locally", keep_newest_per_group=True)
    with patch(
        "scripts.manage_qbo_reconciliation_issues.ReconciliationIssueRepository",
        return_value=repo,
    ):
        rc = cmd_bulk_resolve(args)
    out = capsys.readouterr().out
    assert rc == 0
    assert repo.preview_bulk_resolve.call_args.kwargs["keep_newest_per_group"] is True
    repo.bulk_resolve.assert_not_called()
    assert "withholding 1 row(s)" in out
    # the grouping key is stated to the operator, not left implicit
    assert "RealmId, DriftType, EntityType, QboId, EntityPublicId, Severity, Action" in out
    assert "DRY-RUN: no rows modified" in out


@pytest.mark.parametrize("verb", ["bulk-resolve", "bulk-acknowledge"])
def test_filter_guard_refuses_an_unscoped_sweep(verb, capsys):
    args = _bulk_args(drift_type=None, entity_type=None, severity="low", action="flagged")
    with pytest.raises(SystemExit) as exc:
        _build_bulk_filters(args, verb)
    assert exc.value.code == 2
    assert f"Refusing {verb}" in capsys.readouterr().out


def test_severity_and_action_alone_do_not_authorise_a_sweep(capsys):
    """Mirrors the sproc guard: narrowing filters are not scoping filters."""
    args = _bulk_args(drift_type=None, entity_type=None, created_before_days=None,
                      severity="low", action="flagged")
    with pytest.raises(SystemExit):
        _build_bulk_filters(args, "bulk-resolve")


def test_bulk_filters_pass_severity_and_action_through():
    kwargs, _ = _build_bulk_filters(_bulk_args(), "bulk-resolve")
    assert kwargs["severity"] == "low"
    assert kwargs["action"] == "flagged"
    assert kwargs["drift_type"] == "qbo_voided"


# --------------------------------------------------------------------------
# 5. Repo layer sends the right DryRun value (the dry-run contract at the seam)
# --------------------------------------------------------------------------
def _repo_with_cursor():
    from integrations.intuit.qbo.reconciliation.persistence.repo import (
        ReconciliationIssueRepository,
    )
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    return ReconciliationIssueRepository(), conn, cursor


@pytest.mark.parametrize(
    "method, sproc, expected_dry_run",
    [
        ("preview_bulk_acknowledge", BULK_ACK, True),
        ("bulk_acknowledge", BULK_ACK, False),
        ("preview_bulk_resolve", BULK_RESOLVE, True),
        ("bulk_resolve", BULK_RESOLVE, False),
    ],
)
def test_repo_sends_correct_sproc_and_dry_run_flag(method, sproc, expected_dry_run):
    repo, conn, _ = _repo_with_cursor()
    with patch(
        "integrations.intuit.qbo.reconciliation.persistence.repo.get_connection",
        return_value=conn,
    ), patch(
        "integrations.intuit.qbo.reconciliation.persistence.repo.call_procedure"
    ) as cp:
        getattr(repo, method)(drift_type="qbo_voided", severity="low", action="flagged")
    assert cp.call_args.kwargs["name"] == sproc
    params = cp.call_args.kwargs["params"]
    assert params["DryRun"] is expected_dry_run
    assert params["Severity"] == "low"
    assert params["Action"] == "flagged"


def test_repo_bulk_acknowledge_never_sends_keep_newest():
    repo, conn, _ = _repo_with_cursor()
    with patch(
        "integrations.intuit.qbo.reconciliation.persistence.repo.get_connection",
        return_value=conn,
    ), patch(
        "integrations.intuit.qbo.reconciliation.persistence.repo.call_procedure"
    ) as cp:
        repo.bulk_acknowledge(drift_type="qbo_voided")
    assert "KeepNewestPerGroup" not in cp.call_args.kwargs["params"]


def test_repo_bulk_resolve_defaults_keep_newest_off():
    """Default must preserve pre-U-249 behaviour."""
    repo, conn, _ = _repo_with_cursor()
    with patch(
        "integrations.intuit.qbo.reconciliation.persistence.repo.get_connection",
        return_value=conn,
    ), patch(
        "integrations.intuit.qbo.reconciliation.persistence.repo.call_procedure"
    ) as cp:
        repo.bulk_resolve(drift_type="qbo_missing_locally")
    assert cp.call_args.kwargs["params"]["KeepNewestPerGroup"] is False
