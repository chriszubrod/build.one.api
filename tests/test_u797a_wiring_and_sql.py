"""U-797a: static checks on the SQL, config, admin routes and the scheduler timer."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.sproc_text import REPO_ROOT

SQL_PATH = REPO_ROOT / "entities" / "contract_labor" / "sql" / "dbo.contract_labor_coding_suggestion.sql"
EXPECTED_SPROCS = {
    "EnqueueContractLaborCodingSuggestions",
    "ClaimNextContractLaborCodingSuggestion",
    "ReadContractLaborCodingShadowInputs",
    "CompleteContractLaborCodingSuggestion",
    "ReleaseContractLaborCodingSuggestion",
    "CountContractLaborCodingCallsToday",
    "ReadContractLaborCodingScorecardRows",
    "ReadContractLaborCodingIdfCorpus",
    "ReadContractLaborCodingSuggestionsByProjectAndDate",  # U-797b1
}


def _batches(text: str) -> list[str]:
    parts = re.split(r"^\s*GO\s*$", text, flags=re.MULTILINE | re.IGNORECASE)
    return [p for p in parts if p.strip()]


def _sproc_batch(text: str, name: str) -> str:
    match = re.search(rf"CREATE OR ALTER PROCEDURE\s+dbo\.{name}\b.*?(?=^\s*GO\s*$)", text, re.DOTALL | re.MULTILINE)
    assert match is not None, name
    return match.group(0)


def test_sql_file_exists_with_contract_header():
    text = SQL_PATH.read_text(encoding="utf-8")
    lines = text.splitlines()
    assert lines[0].startswith("-- Contract: dbo.ContractLaborCodingSuggestion")
    assert lines[1].startswith("-- Owns:")
    assert lines[2].startswith("-- Invariants:")
    sprocs_lines = [ln for ln in lines if ln.startswith("-- Sprocs:")]
    assert len(sprocs_lines) == 1
    listed = {s.strip() for s in sprocs_lines[0].split(":", 1)[1].split(",")}
    assert listed == EXPECTED_SPROCS


def test_sql_defines_each_expected_sproc_exactly_once():
    text = SQL_PATH.read_text(encoding="utf-8")
    for name in EXPECTED_SPROCS:
        assert len(re.findall(rf"CREATE OR ALTER PROCEDURE\s+dbo\.{name}\b", text)) == 1, name


def test_every_batch_is_go_terminated():
    text = SQL_PATH.read_text(encoding="utf-8").rstrip()
    assert re.search(r"^\s*GO\s*$", text, flags=re.MULTILINE | re.IGNORECASE)
    assert text.splitlines()[-1].strip().upper() == "GO"


def test_procedures_set_nocount_and_never_rollback():
    text = SQL_PATH.read_text(encoding="utf-8")
    assert "ROLLBACK" not in text.upper()
    for batch in _batches(text):
        if re.search(r"CREATE OR ALTER PROCEDURE", batch, re.IGNORECASE):
            assert re.search(r"SET NOCOUNT ON", batch, re.IGNORECASE), batch[:120]


def test_table_has_unique_index_on_contract_labor_and_project():
    text = SQL_PATH.read_text(encoding="utf-8")
    assert "CREATE TABLE" in text
    assert re.search(r"CREATE UNIQUE (NONCLUSTERED )?INDEX", text, re.IGNORECASE)
    index_block = re.search(r"CREATE UNIQUE (?:NONCLUSTERED )?INDEX.*?\(([^)]*)\)", text, re.IGNORECASE | re.DOTALL)
    assert index_block is not None
    assert "ContractLaborId" in index_block.group(1) and "ProjectId" in index_block.group(1)


def test_status_and_decision_check_constraints_present():
    text = SQL_PATH.read_text(encoding="utf-8")
    for token in ("'pending'", "'done'", "'failed'", "'skipped'", "'history'", "'model'", "'none'",
                  "'single'", "'split'", "'abstain'"):
        assert token in text, token
    assert "u797a-cl-v1" in text


def test_claim_sproc_uses_updlock_readpast_and_stale_sweep():
    text = SQL_PATH.read_text(encoding="utf-8")
    claim = re.search(r"CREATE OR ALTER PROCEDURE dbo\.ClaimNextContractLaborCodingSuggestion.*?(?=GO)", text, re.DOTALL)
    assert claim is not None
    body = claim.group(0)
    assert "UPDLOCK" in body and "READPAST" in body
    assert "stale" in body and "24" in body
    assert "15" in body  # claim lease


def test_claim_comment_does_not_claim_the_stale_sweep_prevents_leaks():
    text = SQL_PATH.read_text(encoding="utf-8")
    assert "cannot leak" not in text
    claim_comment = re.search(r"((?:^--.*\n)+)CREATE OR ALTER PROCEDURE dbo\.ClaimNextContractLaborCodingSuggestion", text, re.MULTILINE)
    assert claim_comment is not None
    assert "staleness only" in claim_comment.group(1)


def test_inputs_header_returns_the_own_contract_labor_status():
    text = SQL_PATH.read_text(encoding="utf-8")
    assert re.search(r"\[TotalHours\],\s*cl\.\[Status\]", text)


def test_inputs_exclude_precedent_lines_dated_on_the_query_work_date():
    text = SQL_PATH.read_text(encoding="utf-8")
    assert re.search(r"COALESCE\(li\.\[LineDate\],\s*cl2\.\[WorkDate\]\)\s*<>\s*@WorkDate", text)


def test_release_sproc_leases_the_row_so_a_deferred_row_is_not_reclaimed_at_once():
    text = SQL_PATH.read_text(encoding="utf-8")
    body = _sproc_batch(text, "ReleaseContractLaborCodingSuggestion")
    assert re.search(r"\[ClaimedDatetime\]\s*=\s*SYSUTCDATETIME\(\)", body)


def test_idf_corpus_sproc_returns_note_rows_keyed_by_line_id():
    text = SQL_PATH.read_text(encoding="utf-8")
    body = _sproc_batch(text, "ReadContractLaborCodingIdfCorpus")
    assert "li.[Id] AS [LineId]" in body
    assert "li.[Hours]" not in body  # U-750/U-673 _LINES_SQL corpus: every labelled line, zero-hour included
    assert "tl.[Note]" in body
    assert re.search(r"CREATE OR ALTER PROCEDURE dbo\.ReadContractLaborCodingIdfCorpus\s*\nAS\b", body)  # no params


def test_no_note_text_or_reply_text_column_is_stored():
    text = SQL_PATH.read_text(encoding="utf-8")
    table = re.search(r"CREATE TABLE \[dbo\]\.\[ContractLaborCodingSuggestion\].*?\);", text, re.DOTALL)
    assert table is not None
    assert "Note" not in table.group(0) and "Reply" not in table.group(0)


def test_scorecard_sproc_returns_failure_reason_and_positive_hour_final_codes_only():
    text = SQL_PATH.read_text(encoding="utf-8")
    body = _sproc_batch(text, "ReadContractLaborCodingScorecardRows")
    assert "s.[FailureReason]" in body
    assert "li.[Hours] > 0" in body


def test_repo_maps_failure_reason_into_scorecard_rows():
    text = (REPO_ROOT / "entities" / "contract_labor" / "persistence" / "coding_suggestion_repo.py").read_text(encoding="utf-8")
    assert '"failure_reason": r.FailureReason' in text


def test_config_flags_default_off_and_cap_sixty():
    text = (REPO_ROOT / "config.py").read_text(encoding="utf-8")
    assert re.search(r"cl_coding_shadow_enabled:\s*bool\s*=\s*False", text)
    assert re.search(r"cl_coding_shadow_daily_call_cap:\s*int\s*=\s*60", text)


def test_admin_routes_registered_with_expected_guards():
    text = (REPO_ROOT / "shared" / "api" / "admin.py").read_text(encoding="utf-8")
    tick = re.search(r'@router\.post\("/contract-labor/coding/tick"[^\n]*\)\s*\nasync def', text)
    assert tick is not None
    assert "_require_drain_secret" in text[tick.start() : tick.end()]
    scorecard = re.search(r'@router\.get\("/intelligence/cl-coding-scorecard"\)', text)
    assert scorecard is not None
    window = text[scorecard.start() : scorecard.start() + 500]
    assert "require_system_admin()" in window


def test_scheduler_timer_present_with_two_minute_schedule_and_limit_one():
    scheduler = REPO_ROOT.parent / "u-797a-sched" / "function_app.py"
    if not scheduler.exists():
        pytest.skip("scheduler worktree not present")
    text = scheduler.read_text(encoding="utf-8")
    match = re.search(
        r'@app\.timer_trigger\(schedule="0 \*/2 \* \* \* \*"[^\n]*\)\s*\ndef contract_labor_coding_tick',
        text,
    )
    assert match is not None
    assert "/api/v1/admin/contract-labor/coding/tick?limit=1" in text
    assert "limit=5" not in text[match.start() : match.start() + 600]
    assert text.count("def contract_labor_coding_tick") == 1


# --- U-797a fix round 2 -----------------------------------------------------


def test_claim_orders_never_claimed_rows_first_so_a_retrying_row_cannot_starve_the_queue():
    # P1 starvation: a row released with a lease carries ClaimedDatetime and must sort behind
    # never-claimed rows, otherwise one repeatedly failing row can hold the head of the queue.
    text = SQL_PATH.read_text(encoding="utf-8")
    body = _sproc_batch(text, "ClaimNextContractLaborCodingSuggestion")
    assert re.search(
        r"ORDER BY\s+CASE WHEN s\.\[ClaimedDatetime\] IS NULL THEN 0 ELSE 1 END,\s*s\.\[CreatedDatetime\],\s*s\.\[Id\]",
        body,
    )
    # the lease filter and the 24 h stale sweep are kept
    assert "DATEADD(minute, -15, GETUTCDATE())" in body
    assert "DATEADD(hour, -24, GETUTCDATE())" in body


def test_claim_comment_explains_the_never_claimed_first_order():
    text = SQL_PATH.read_text(encoding="utf-8")
    claim_comment = re.search(r"((?:^--.*\n)+)CREATE OR ALTER PROCEDURE dbo\.ClaimNextContractLaborCodingSuggestion", text, re.MULTILINE)
    assert claim_comment is not None
    assert "never-claimed" in claim_comment.group(1).lower() or "never claimed" in claim_comment.group(1).lower()
    assert "staleness only" in claim_comment.group(1)


def test_claim_returns_attempt_count_for_the_three_strike_rule():
    text = SQL_PATH.read_text(encoding="utf-8")
    body = _sproc_batch(text, "ClaimNextContractLaborCodingSuggestion")
    assert re.search(r"s\.\[AttemptCount\]", body)


def test_attempt_count_is_defined_once_in_create_table():
    text = SQL_PATH.read_text(encoding="utf-8")
    assert re.search(r"\[AttemptCount\]\s+INT\s+NOT NULL\s+CONSTRAINT\s+\[DF_ContractLaborCodingSuggestion_AttemptCount\]\s+DEFAULT\s+0", text)
    assert "COL_LENGTH" not in text  # never applied anywhere before: no migration ALTER needed


def test_pending_rows_have_a_filtered_index_for_the_claim():
    text = SQL_PATH.read_text(encoding="utf-8")
    assert re.search(
        r"CREATE NONCLUSTERED INDEX \[IX_ContractLaborCodingSuggestion_Pending\]\s+ON \[dbo\]\.\[ContractLaborCodingSuggestion\]"
        r" \(\[ClaimedDatetime\], \[CreatedDatetime\], \[Id\]\)\s+WHERE \[Status\] = N'pending';",
        text,
    )


def test_every_labelled_status_list_matches_the_python_set():
    """One labelled set: the 3 new sprocs and U-749's ReadContractLaborSubCostCodeCounts must equal
    LABELLED_CL_STATUSES, so a status rename cannot silently drift one of the five copies."""
    from entities.contract_labor.business.coding_shadow import LABELLED_CL_STATUSES

    cl_status = r"\bcl2?\.\[Status\] IN \(([^)]*)\)"
    lists = re.findall(cl_status, SQL_PATH.read_text(encoding="utf-8"))
    assert len(lists) == 3  # inputs (precedent), IDF corpus, scorecard
    base = Path("entities/contract_labor/sql/dbo.contract_labor.sql").read_text(encoding="utf-8")
    u749 = base[base.index("CREATE OR ALTER PROCEDURE dbo.ReadContractLaborSubCostCodeCounts"):]
    lists += re.findall(cl_status, u749)[:1]
    assert len(lists) >= 4
    for raw in lists:
        assert {x.strip().lstrip("N").strip("'") for x in raw.split(",")} == set(LABELLED_CL_STATUSES), raw


def test_release_sproc_takes_count_attempt_bit_defaulting_to_zero_and_counts_only_when_set():
    text = SQL_PATH.read_text(encoding="utf-8")
    body = _sproc_batch(text, "ReleaseContractLaborCodingSuggestion")
    assert re.search(r"@CountAttempt\s+BIT\s*=\s*0", body)
    assert re.search(r"\[AttemptCount\]\s*=\s*CASE WHEN @CountAttempt = 1 THEN \[AttemptCount\] \+ 1", body)
    assert re.search(r"\[CallDatetime\]\s*=\s*CASE WHEN @CountAttempt = 1 THEN SYSUTCDATETIME\(\)", body)
    assert re.search(r"\[ClaimedDatetime\]\s*=\s*SYSUTCDATETIME\(\)", body)  # lease kept


def test_count_calls_today_counts_completed_model_calls_and_released_attempts_once_per_row():
    text = SQL_PATH.read_text(encoding="utf-8")
    body = _sproc_batch(text, "CountContractLaborCodingCallsToday")
    assert re.search(r"\(\s*\[Model\] IS NOT NULL\s+OR\s+\[AttemptCount\]\s*>\s*0\s*\)", body)
    assert re.search(r"\[CallDatetime\]\s*>=\s*CAST\(CAST\(GETUTCDATE\(\) AS DATE\) AS DATETIME2\(3\)\)", body)
    assert "COUNT(*)" in body  # one row per suggestion: a row counts once a day, however many attempts
