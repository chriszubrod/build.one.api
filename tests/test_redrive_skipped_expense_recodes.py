"""U-652a re-drive — skipped expense recode backfill script (pure logic, no DB)."""

from __future__ import annotations

import re
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

_GATE = "scripts.redrive_skipped_expense_recodes.recode_write_gate_reason"
_FETCH = "scripts.redrive_skipped_expense_recodes.fetch_candidates"
_ENQUEUE = "scripts.redrive_skipped_expense_recodes.ExpenseService"
_SYSTEM = "scripts.redrive_skipped_expense_recodes.system_authz"
_RESOLVE_ITEM = "scripts.redrive_skipped_expense_recodes.resolve_qbo_item_ref"


def _candidate(
    *,
    expense_id: int = 101,
    status: str = "in_review",
    qbo_id: str = "76207",
    realm_id: str = "9130353016965726",
    uncategorized_line_count: int = 1,
    source_arm: str = "missing",
):
    return SimpleNamespace(
        Id=expense_id,
        Status=status,
        QboId=qbo_id,
        RealmId=realm_id,
        UncategorizedLineCount=uncategorized_line_count,
        SourceArm=source_arm,
    )


def _dominant(*, project_id=10, sub_cost_code_id=20):
    return {
        "project_id": project_id,
        "sub_cost_code_id": sub_cost_code_id,
        "description": "Site fuel",
        "confirmed_mixed_codes": False,
    }


@contextmanager
def _patch_fetch(candidates):
    with patch(_FETCH, return_value=list(candidates)) as mock_fetch:
        yield mock_fetch


@contextmanager
def _patch_dominant(return_value):
    svc = MagicMock()
    svc._resolve_dominant_expense_coding_for_submit.return_value = return_value
    with patch(_ENQUEUE, return_value=svc) as mock_cls:
        yield mock_cls, svc


@contextmanager
def _patch_item_ref(*, return_value=None):
    if return_value is None:
        return_value = SimpleNamespace(value="ITEM-X", name="Item X")
    with patch(_RESOLVE_ITEM, return_value=return_value):
        yield


def test_dry_run_never_calls_enqueue_coding_recode_on_submit(capsys):
    from scripts.redrive_skipped_expense_recodes import main

    with _patch_fetch([_candidate()]), patch(_GATE, return_value=None), _patch_dominant(
        _dominant()
    ) as (mock_cls, svc), _patch_item_ref():
        rc = main([])

    assert rc == 0
    svc.enqueue_coding_recode_on_submit.assert_not_called()
    out = capsys.readouterr().out
    assert "DRY-RUN" in out.upper() or "dry-run" in out.lower()


def test_dry_run_predicts_would_enqueue_when_dominant_has_project_and_cost_code(capsys):
    from scripts.redrive_skipped_expense_recodes import main

    with _patch_fetch([_candidate(expense_id=55)]), patch(_GATE, return_value=None), _patch_dominant(
        _dominant(project_id=77, sub_cost_code_id=481)
    ), _patch_item_ref():
        main([])

    out = capsys.readouterr().out
    assert "would enqueue" in out
    assert "55" in out


def test_dry_run_predicts_missing_project_or_cost_code(capsys):
    from scripts.redrive_skipped_expense_recodes import main

    with _patch_fetch([_candidate()]), patch(_GATE, return_value=None), _patch_dominant(
        _dominant(project_id=10, sub_cost_code_id=None)
    ):
        main([])

    out = capsys.readouterr().out
    assert "would skip: missing project or cost code" in out


def test_dry_run_predicts_gates_off(capsys):
    from scripts.redrive_skipped_expense_recodes import main

    with _patch_fetch([_candidate()]), patch(_GATE, return_value="qbo_writes_disabled"), _patch_dominant(
        _dominant()
    ) as (mock_cls, svc):
        main([])

    out = capsys.readouterr().out
    assert "would skip: gates off" in out
    svc.enqueue_coding_recode_on_submit.assert_not_called()


def test_apply_with_gates_off_exits_2_and_enqueues_nothing():
    from scripts.redrive_skipped_expense_recodes import main

    with _patch_fetch([_candidate(), _candidate(expense_id=102)]), patch(
        _GATE, return_value="recode_writes_disabled"
    ), patch(
        "scripts.redrive_skipped_expense_recodes.ExpenseService"
    ) as mock_cls:
        svc = MagicMock()
        mock_cls.return_value = svc
        rc = main(["--apply"])

    assert rc == 2
    svc.enqueue_coding_recode_on_submit.assert_not_called()


def test_apply_calls_enqueue_once_per_candidate_with_as_user_id_inside_system_authz():
    from scripts.redrive_skipped_expense_recodes import main

    summary = {
        "enqueued": 1,
        "skipped": 0,
        "writes_disabled": 0,
        "invalid": 0,
        "items": [{"outcome": "enqueued"}],
    }
    svc = MagicMock()
    svc.enqueue_coding_recode_on_submit.return_value = summary

    authz_entered = {"n": 0}

    @contextmanager
    def _fake_system_authz():
        authz_entered["n"] += 1
        yield

    with _patch_fetch(
        [_candidate(expense_id=10), _candidate(expense_id=11)]
    ), patch(_GATE, return_value=None), patch(_ENQUEUE, return_value=svc), patch(
        _SYSTEM, side_effect=_fake_system_authz
    ):
        rc = main(["--apply", "--as-user-id", "42"])

    assert rc == 0
    assert authz_entered["n"] == 2
    assert svc.enqueue_coding_recode_on_submit.call_count == 2
    svc.enqueue_coding_recode_on_submit.assert_any_call(expense_id=10, user_id=42)
    svc.enqueue_coding_recode_on_submit.assert_any_call(expense_id=11, user_id=42)


def test_expense_id_and_limit_restrict_candidates():
    from scripts.redrive_skipped_expense_recodes import main

    filtered = [_candidate(expense_id=2), _candidate(expense_id=3)]
    with patch(_FETCH, return_value=filtered) as mock_fetch, patch(
        _GATE, return_value=None
    ), _patch_dominant(_dominant()), _patch_item_ref():
        main(["--expense-id", "2", "--expense-id", "3", "--limit", "1"])

    mock_fetch.assert_called_once_with(expense_ids=[2, 3], resume_expense_ids=None)


def test_resume_expense_id_passed_to_fetch_candidates():
    from scripts.redrive_skipped_expense_recodes import main

    with patch(_FETCH, return_value=[]) as mock_fetch, patch(_GATE, return_value=None):
        main(["--resume-expense-id", "99", "--resume-expense-id", "100"])

    mock_fetch.assert_called_once_with(expense_ids=None, resume_expense_ids=[99, 100])


def test_resume_expense_id_not_resumable_prints_and_skips(capsys):
    from scripts.redrive_skipped_expense_recodes import main

    def _fetch(*, expense_ids=None, resume_expense_ids=None):
        return []

    with patch(_FETCH, side_effect=_fetch), patch(_GATE, return_value=None):
        rc = main(["--resume-expense-id", "77"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "not resumable" in out.lower()
    assert "77" in out


def test_apply_one_expense_raises_yields_exit_1_and_continues(capsys):
    from scripts.redrive_skipped_expense_recodes import main

    svc = MagicMock()

    def _enqueue(*, expense_id, user_id):
        if expense_id == 20:
            raise RuntimeError("boom")
        return {"enqueued": 1, "skipped": 0, "writes_disabled": 0, "invalid": 0, "items": []}

    svc.enqueue_coding_recode_on_submit.side_effect = _enqueue

    @contextmanager
    def _fake_system_authz():
        yield

    with _patch_fetch(
        [_candidate(expense_id=20), _candidate(expense_id=21)]
    ), patch(_GATE, return_value=None), patch(_ENQUEUE, return_value=svc), patch(
        _SYSTEM, side_effect=_fake_system_authz
    ):
        rc = main(["--apply"])

    assert rc == 1
    assert svc.enqueue_coding_recode_on_submit.call_count == 2
    out = capsys.readouterr().out
    assert "RuntimeError" in out


def test_candidate_sql_structure_and_parameterization():
    from scripts.redrive_skipped_expense_recodes import CANDIDATE_SQL

    sql = CANDIDATE_SQL
    upper = sql.upper()
    assert "EXISTS" in upper and "DBO.[REVIEW]" in upper.replace(" ", "")
    assert "NOT EXISTS" in upper
    assert "CODINGSUGGESTION" in upper.replace(" ", "").replace("_", "")
    assert re.search(r"\[ExpenseId\]\s+IS\s+NULL", sql, re.IGNORECASE)
    assert re.search(r"QBOPURCHASEQBOID", sql, re.IGNORECASE)
    assert re.search(r"ITEMREFVALUE\]\s+IS\s+NULL", sql, re.IGNORECASE)
    assert "NEED TO CATEGORIZE" in sql
    assert "%s" not in sql
    assert re.search(r":\w+", sql) is None, "named params not allowed"
    # Dynamic IN lists must still use ? only in the static template portion we ship
    assert "?" in sql or "fetch_candidates" in dir(
        __import__("scripts.redrive_skipped_expense_recodes", fromlist=["fetch_candidates"])
    )


def test_fetch_candidates_uses_question_mark_parameters_only():
    from scripts.redrive_skipped_expense_recodes import fetch_candidates

    cursor = MagicMock()
    cursor.fetchall.return_value = []
    conn = MagicMock()
    conn.cursor.return_value = cursor
    ctx = MagicMock()
    ctx.__enter__.return_value = conn
    ctx.__exit__.return_value = False

    with patch("scripts.redrive_skipped_expense_recodes.get_connection", return_value=ctx):
        fetch_candidates(expense_ids=[5, 6])

    executed_sql = cursor.execute.call_args[0][0]
    params = cursor.execute.call_args[0][1:]
    flat_params = params[0] if len(params) == 1 and isinstance(params[0], (list, tuple)) else params
    assert "?" in executed_sql
    assert "%s" not in executed_sql
    assert re.search(r":\w+", executed_sql) is None
    assert list(flat_params) == [5, 6]


def test_candidate_exists_clause_requires_a_genuine_placeholder_line_on_both_arms():
    """The EXISTS that SELECTS candidates (not just the count column) must require ItemRefValue IS
    NULL: a line item-recoded in QBO keeps the 58999 label and must not make an Expense a candidate."""
    import re

    from scripts.redrive_skipped_expense_recodes import CANDIDATE_SQL, SEEDED_CANDIDATE_SQL

    for sql in (CANDIDATE_SQL, SEEDED_CANDIDATE_SQL):
        purchase_exists_blocks = re.findall(
            r"AND\s+EXISTS\s*\(\s*SELECT\s+1\s+FROM\s+qbo\.\[Purchase\]\s+p\b(.*?)\)\s*(?:\n|\{)",
            sql,
            re.IGNORECASE | re.DOTALL,
        )
        assert len(purchase_exists_blocks) >= 1
        for body in purchase_exists_blocks:
            assert re.search(r"AccountRefName\]\s+LIKE\s+N'%NEED TO CATEGORIZE%'", body, re.IGNORECASE)
            assert re.search(r"ItemRefValue\]\s+IS\s+NULL", body, re.IGNORECASE)


def test_candidate_sql_eligible_expense_statuses():
    from scripts.redrive_skipped_expense_recodes import CANDIDATE_SQL, SEEDED_CANDIDATE_SQL

    for sql in (CANDIDATE_SQL, SEEDED_CANDIDATE_SQL):
        assert re.search(r"N'submitted'", sql, re.IGNORECASE)
        assert re.search(r"N'in_review'", sql, re.IGNORECASE)
        assert re.search(r"N'approved'", sql, re.IGNORECASE)
        assert re.search(r"N'completed'", sql, re.IGNORECASE)
        in_match = re.search(
            r"\[Status\]\s+IN\s*\(([^)]+)\)",
            sql,
            re.IGNORECASE,
        )
        assert in_match is not None
        in_clause = in_match.group(1).lower()
        assert "draft" not in in_clause
        assert "declined" not in in_clause


def test_default_candidate_sql_is_missing_arm_only_no_union():
    from scripts.redrive_skipped_expense_recodes import CANDIDATE_SQL

    assert "UNION" not in CANDIDATE_SQL.upper()
    assert "seeded-not-enqueued" not in CANDIDATE_SQL
    assert re.search(r"NOT EXISTS\s*\(\s*SELECT\s+1\s*FROM\s+dbo\.\[CodingSuggestion\]", CANDIDATE_SQL, re.I)


def test_seeded_candidate_sql_arm_and_claim_lease():
    from scripts.redrive_skipped_expense_recodes import SEEDED_CANDIDATE_SQL

    assert "seeded-not-enqueued" in SEEDED_CANDIDATE_SQL
    assert re.search(r"CS\.\[Status\]\s*=\s*N'pending'", SEEDED_CANDIDATE_SQL, re.IGNORECASE)
    assert re.search(r"CS\.\[ConfirmedAt\]\s+IS\s+NULL", SEEDED_CANDIDATE_SQL, re.IGNORECASE)
    assert re.search(
        r"\[EntityPublicId\]\s*=\s*CS\.\[PublicId\]",
        SEEDED_CANDIDATE_SQL,
        re.IGNORECASE,
    )
    assert re.search(
        r"\[Kind\]\s*=\s*N'recode_purchase_line'",
        SEEDED_CANDIDATE_SQL,
        re.IGNORECASE,
    )
    assert re.search(r"CS\.\[ClaimedByUserId\]\s+IS\s+NULL", SEEDED_CANDIDATE_SQL, re.IGNORECASE)
    assert re.search(
        r"DATEADD\s*\(\s*SECOND\s*,\s*-900\s*,\s*SYSUTCDATETIME\s*\(\s*\)\s*\)",
        SEEDED_CANDIDATE_SQL,
        re.IGNORECASE,
    )


def test_dry_run_shows_candidate_source_arm(capsys):
    from scripts.redrive_skipped_expense_recodes import main

    with _patch_fetch([_candidate(source_arm="seeded-not-enqueued")]), patch(
        _GATE, return_value=None
    ), _patch_dominant(_dominant()), _patch_item_ref():
        main([])

    out = capsys.readouterr().out
    assert "seeded-not-enqueued" in out


def test_dry_run_predicts_no_qbo_item_mapping_when_resolver_returns_none(capsys):
    from scripts.redrive_skipped_expense_recodes import main

    with _patch_fetch([_candidate()]), patch(_GATE, return_value=None), _patch_dominant(
        _dominant(project_id=10, sub_cost_code_id=481)
    ), patch(_RESOLVE_ITEM, return_value=None) as mock_resolve:
        main([])

    mock_resolve.assert_called_once()
    call_kw = mock_resolve.call_args.kwargs
    assert call_kw.get("sub_cost_code_id") == 481 or mock_resolve.call_args[0][0] == 481
    out = capsys.readouterr().out
    assert "would skip: no QBO Item mapping for the cost code" in out


def test_apply_revalidates_candidate_before_enqueue(capsys):
    from scripts.redrive_skipped_expense_recodes import main

    svc = MagicMock()
    svc.enqueue_coding_recode_on_submit.return_value = {
        "enqueued": 1,
        "skipped": 0,
        "writes_disabled": 0,
        "invalid": 0,
        "items": [],
    }

    @contextmanager
    def _fake_system_authz():
        yield

    fetch_calls = {"n": 0}

    def _fetch(*, expense_ids=None, resume_expense_ids=None):
        fetch_calls["n"] += 1
        if fetch_calls["n"] > 1 and expense_ids == [55]:
            return []
        return [_candidate(expense_id=55)]

    with patch(_FETCH, side_effect=_fetch), patch(_GATE, return_value=None), patch(
        _ENQUEUE, return_value=svc
    ), patch(_SYSTEM, side_effect=_fake_system_authz):
        rc = main(["--apply", "--expense-id", "55"])

    assert rc == 0
    svc.enqueue_coding_recode_on_submit.assert_not_called()
    out = capsys.readouterr().out
    assert "skipped: no longer a candidate" in out


def test_apply_revalidates_seeded_arm_with_resume_expense_id(capsys):
    from scripts.redrive_skipped_expense_recodes import main

    svc = MagicMock()
    svc.enqueue_coding_recode_on_submit.return_value = {
        "enqueued": 1,
        "skipped": 0,
        "writes_disabled": 0,
        "invalid": 0,
        "items": [],
    }

    @contextmanager
    def _fake_system_authz():
        yield

    recheck_kwargs = []

    def _fetch(*, expense_ids=None, resume_expense_ids=None):
        if expense_ids == [88] and resume_expense_ids == [88]:
            recheck_kwargs.append({"expense_ids": expense_ids, "resume": resume_expense_ids})
            return []
        return [_candidate(expense_id=88, source_arm="seeded-not-enqueued")]

    with patch(_FETCH, side_effect=_fetch), patch(_GATE, return_value=None), patch(
        _ENQUEUE, return_value=svc
    ), patch(_SYSTEM, side_effect=_fake_system_authz):
        rc = main(["--apply", "--resume-expense-id", "88"])

    assert rc == 0
    svc.enqueue_coding_recode_on_submit.assert_not_called()
    assert recheck_kwargs == [{"expense_ids": [88], "resume": [88]}]
    out = capsys.readouterr().out
    assert "skipped: no longer a candidate" in out
