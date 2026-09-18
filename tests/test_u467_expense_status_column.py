"""U-467 — Expense gets a real `Status` column, shipped as one slice.

Bill's lifecycle took U-445 (column + filter) then U-446 (computed IsDraft).
Expense ships both halves together: IsDraft ends as a PERSISTED NOT NULL
computed column over Status, FinalizeExpenseById replaces the is_draft=False
PUT in complete_expense, and GET /get/expenses filters `?status=` /
`?start_date=` / `?end_date=` with page+total from ONE materialized set.

Cloned from tests/test_u445_bill_status_column.py (Bill -> Expense) plus the
U-434 finalize pins and the U-446c transition reopen fence.
"""

from datetime import date as date_type
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import inspect
import re

import pytest

from entities.expense.api.router import get_expenses_router
from entities.expense.business.model import Expense
from entities.expense.business.service import ExpenseService
from shared.lifecycle.resolver import LIFECYCLE_STATUSES, attach_lifecycle

USER = {"id": 1, "username": "tester"}


def _expense(id=1, *, is_draft=True, status=None):
    return Expense(
        id=id, public_id=f"exp-{id}", row_version="AAAA",
        created_datetime=None, modified_datetime=None,
        vendor_id=10, expense_date="2026-09-01",
        reference_number=f"E-{id}", total_amount=Decimal("1.00"), memo=None,
        is_draft=is_draft, is_credit=False, status=status,
    )


def _call(expenses, **kwargs):
    service = MagicMock()
    service.read_paginated.return_value = (expenses, len(expenses))
    review_repo = MagicMock()
    review_repo.read_current_by_expense_ids.return_value = {}
    coding_repo = MagicMock()
    coding_repo.read_state_by_expense_ids.return_value = {}
    with patch("entities.expense.api.router.ExpenseService", return_value=service), \
         patch("entities.review.persistence.repo.ReviewRepository", return_value=review_repo), \
         patch("entities.expense_coding_item.persistence.repo.ExpenseCodingItemRepository", return_value=coding_repo):
        params = dict(page=1, page_size=50, search=None, vendor_id=None,
                      is_draft=None, status=None, start_date=None, end_date=None,
                      current_user=USER)
        params.update(kwargs)
        response = get_expenses_router(**params)
    return response, service


# ---------------------------------------------------------------------------
# The filter reaches SQL — the entire point
# ---------------------------------------------------------------------------


def test_status_reaches_sql_and_the_count_CANNOT_use_a_different_predicate():
    _, service = _call([_expense(status="submitted")], status="submitted")
    assert service.read_paginated.call_args.kwargs["status"] == "submitted"
    assert service.read_paginated.call_count == 1
    assert service.count.call_count == 0, (
        "the list route must not ask for the total separately — that is the "
        "second snapshot U-447 removed"
    )


def test_no_status_filters_nothing():
    _, service = _call([_expense()])
    assert service.read_paginated.call_args.kwargs["status"] is None
    assert service.count.call_count == 0


@pytest.mark.parametrize("status", list(LIFECYCLE_STATUSES))
def test_every_canonical_status_is_accepted(status):
    _, service = _call([], status=status)
    assert service.read_paginated.call_args.kwargs["status"] == status


def test_an_unknown_status_is_422_not_a_silent_full_list():
    from shared.api.errors import ApiError

    with pytest.raises(ApiError) as exc:
        _call([_expense()], status="billed")
    assert exc.value.status_code == 422
    assert "billed" in str(exc.value.detail)


def test_the_rejected_message_names_the_legal_values():
    from shared.api.errors import ApiError

    with pytest.raises(ApiError) as exc:
        _call([], status="Draft")
    for s in LIFECYCLE_STATUSES:
        assert s in str(exc.value.detail)
    assert "Expected one of:" in str(exc.value.detail)


def test_a_direct_call_without_fastapi_does_not_trip_the_guard():
    from fastapi import Query

    response, service = _call([_expense()], status=Query(default=None))
    assert response["count"] == 1
    assert service.read_paginated.call_args.kwargs["status"] is None


# ---------------------------------------------------------------------------
# Stored beats derived
# ---------------------------------------------------------------------------


def test_the_stored_status_WINS_over_the_derived_one():
    payload = attach_lifecycle({}, is_draft=True, review=None, stored_status="approved")
    assert payload["status"] == "approved"
    assert payload["review_status_kind"] == "none"


def test_the_derivation_still_runs_where_no_column_exists_yet():
    payload = attach_lifecycle({}, is_draft=False, review=None, stored_status=None)
    assert payload["status"] == "completed"


def test_the_list_emits_the_stored_value():
    response, _ = _call([_expense(id=1, is_draft=False, status="completed"),
                         _expense(id=2, is_draft=True, status="declined")])
    by_id = {e["id"]: e["status"] for e in response["data"]}
    assert by_id == {1: "completed", 2: "declined"}


def test_a_row_predating_the_backfill_still_resolves():
    response, _ = _call([_expense(id=1, is_draft=False, status=None)])
    assert response["data"][0]["status"] == "completed"


# ---------------------------------------------------------------------------
# The SQL contract
# ---------------------------------------------------------------------------


def _expense_sql():
    from tests.sproc_text import REPO_ROOT
    return (REPO_ROOT / "entities/expense/sql/dbo.expense.sql").read_text()


from tests.sproc_text import strip_sql_comments


def _unquoted(text):
    return text.replace("\'\'", "\'")


def _executable_sql():
    return strip_sql_comments(_expense_sql())


def test_the_consistency_constraint_exists_and_is_validated():
    sql = _expense_sql()
    assert "CK_Expense_Status_IsDraft" in sql
    assert "(CASE WHEN [Status] = 'completed' THEN 0 ELSE 1 END) = [IsDraft]" in sql
    assert sql.count("WITH CHECK ADD CONSTRAINT") == 3, (
        "all three CHECKs must validate existing rows, not just future writes"
    )


def test_ck_expense_status_matches_lifecycle_statuses():
    sql = _expense_sql()
    match = re.search(
        r"ADD CONSTRAINT \[CK_Expense_Status\]\s+CHECK \(\[Status\] IN \(([^)]+)\)\)",
        sql,
    )
    assert match, "CK_Expense_Status value list not found"
    values = [v.strip().strip("'") for v in match.group(1).split(",")]
    assert values == list(LIFECYCLE_STATUSES)


def test_constraints_are_added_AFTER_the_backfill():
    sql = _executable_sql()
    add_column = sql.index("ALTER TABLE [dbo].[Expense] ADD [Status]")
    backfill = sql.index("SET e.[Status] = CASE")
    check = sql.index("ADD CONSTRAINT [CK_Expense_Status_IsDraft]")
    assert add_column < backfill < check, (
        f"order is column({add_column}) -> backfill({backfill}) -> check({check})"
    )


def test_the_backfill_cannot_run_twice():
    sql = _expense_sql()
    assert "AND NOT EXISTS (SELECT 1 FROM dbo.[Expense] WHERE [StatusDatetime] IS NOT NULL)" in sql


def test_the_backfill_loop_stops_when_unstamped_rows_run_out():
    sql = _unquoted(_expense_sql())
    start = sql.index("WHILE @Done > 0")
    done = sql.index("SET @Done = @@ROWCOUNT")
    loop = sql[start:done + len("SET @Done = @@ROWCOUNT")]
    assert "WHERE e.[StatusDatetime] IS NULL" in loop
    assert loop.index("UPDATE TOP (@Batch) e") < loop.index("WHERE e.[StatusDatetime] IS NULL")
    assert loop.index("WHERE e.[StatusDatetime] IS NULL") < loop.index("SET @Done = @@ROWCOUNT")


def test_the_backfill_expression_matches_the_python_resolver():
    sql = _unquoted(_expense_sql())
    block = sql[sql.index("SET e.[Status] = CASE"):sql.index("WHERE e.[StatusDatetime] IS NULL")]
    order = [block.index(f"rs.[{f}]") for f in ("IsDeclined", "IsFinal", "IsInitial")]
    assert order == sorted(order), "precedence differs from review_kind_from_flags"
    assert "e.[IsDraft] = 0            THEN 'completed'" in block, (
        "completed must be tested FIRST — a finalized expense is `completed` "
        "whatever its review says (U-443)"
    )
    assert block.index("e.[IsDraft] = 0") < min(order)


def test_the_backfill_kind_prefers_frozen_review_kind():
    sql = _unquoted(_expense_sql())
    apply = sql[sql.index("OUTER APPLY ("):sql.index(") cur")]
    assert "COALESCE(r.[ReviewKind]," in apply
    assert "r.[ReviewKind]" in apply
    status_case = sql[sql.index("SET e.[Status] = CASE"):sql.index("e.[StatusDatetime] = SYSUTCDATETIME()")]
    assert "WHEN cur.[Kind] IS NOT NULL THEN cur.[Kind]" in status_case


def test_finalize_writes_status_and_nothing_else():
    from tests.sproc_text import REPO_ROOT, sproc_body

    body = sproc_body(REPO_ROOT / "entities/expense/sql/dbo.expense.sql", "FinalizeExpenseById")
    assert "[Status] = 'completed'" in body
    assert "[StatusOrigin] = 'completion'" in body
    assert "[IsDraft] = 0" not in body, "IsDraft is computed — it cannot be assigned"


def test_update_translates_is_draft_for_callers_that_predate_status():
    from tests.sproc_text import REPO_ROOT, sproc_body

    body = sproc_body(REPO_ROOT / "entities/expense/sql/dbo.expense.sql", "UpdateExpenseById")
    executable = strip_sql_comments(body)
    status_case = executable[executable.index("[Status] = CASE"):executable.index("[StatusDatetime]")]
    assert (
        "[Status] = CASE\n"
        "            WHEN @IsDraft = 0 AND [Status] <> 'completed' THEN 'completed'\n"
        "            ELSE [Status] END"
        in status_case
    )
    assert "@IsDraft = 1" not in status_case
    assert "[IsDraft] =" not in body, "IsDraft is computed — it cannot be assigned"


def test_the_transition_sproc_is_guarded_and_idempotent():
    from tests.sproc_text import REPO_ROOT, sproc_body, sproc_params

    base = REPO_ROOT / "entities/expense/sql/dbo.expense.sql"
    params = sproc_params(base, "TransitionExpenseStatus")
    body = sproc_body(base, "TransitionExpenseStatus")
    for p in ("@FromStatuses", "@ToStatus", "@Origin", "@SourceRef", "@RowVersion"):
        assert p in params, p
    assert "STRING_SPLIT(@FromStatuses" in body, "the legal FROM set is enforced in SQL"
    assert "AND [Status] <> @ToStatus" in body, "a repeat transition must be a no-op"
    assert "SET NOCOUNT ON" in body
    executable = strip_sql_comments(body).upper()
    assert "ROLLBACK" not in executable, "pyodbc autocommit-off: rollback raises 266"


def test_the_status_transition_sproc_cannot_reopen_a_completed_expense():
    """U-446c reopen fence, ported. `TransitionExpenseStatus` takes its allowed
    source states from the CALLER, so `@FromStatuses = 'completed'` would move
    a finalised Expense back out of the terminal state."""
    from tests.sproc_text import REPO_ROOT, sproc_body

    body = sproc_body(REPO_ROOT / "entities/expense/sql/dbo.expense.sql", "TransitionExpenseStatus")
    assert "AND [Status] <> 'completed'" in body, (
        "the terminal state must be fenced in the UPDATE predicate, not left to "
        "whatever @FromStatuses the caller supplies"
    )


def test_create_does_not_name_the_computed_column():
    from tests.sproc_text import REPO_ROOT, sproc_body, sproc_params

    base = REPO_ROOT / "entities/expense/sql/dbo.expense.sql"
    params = sproc_params(base, "CreateExpense")
    assert "@Status NVARCHAR(20) = NULL" in params
    assert "@IsDraft" in params, "older callers must still bind successfully"

    body = sproc_body(base, "CreateExpense")
    insert_cols = body[body.index("INSERT INTO dbo.[Expense]"):body.index("OUTPUT")]
    executable = strip_sql_comments(insert_cols)
    assert "[IsDraft]" not in executable, (
        "the INSERT column list must not name IsDraft (error 271)"
    )
    assert (
        "COALESCE(@Status, CASE WHEN @IsDraft = 0 THEN 'completed' ELSE 'draft' END)"
        in body
    ), "@IsDraft survives only as the fallback, with completed on IsDraft=0"


def test_the_status_index_is_filtered_to_the_working_set():
    sql = _expense_sql()
    assert "CREATE NONCLUSTERED INDEX [IX_Expense_Status]" in sql
    assert "WHERE [Status] <> 'completed'" in sql


def test_list_and_count_sprocs_both_take_the_filter():
    from tests.sproc_text import REPO_ROOT, sproc_body, sproc_params

    base = REPO_ROOT / "entities/expense/sql/dbo.expense.sql"
    for sproc in ("ReadExpensesPaginated", "CountExpenses"):
        assert "@Status" in sproc_params(base, sproc), sproc
        assert "(@Status IS NULL OR e.[Status] = @Status)" in sproc_body(base, sproc), sproc


# ---------------------------------------------------------------------------
# Status hops: service -> repo -> sproc / _from_db
# ---------------------------------------------------------------------------


def test_expense_service_create_forwards_the_status_triple_to_the_repo():
    repo = MagicMock()
    repo.read_by_reference_number_and_vendor_id.return_value = None
    repo.create.return_value = _expense()
    svc = ExpenseService(repo=repo)
    with patch("entities.expense.business.service.VendorService") as VS:
        VS.return_value.read_by_public_id.return_value = SimpleNamespace(id=10)
        svc.create(
            vendor_public_id="vendor-1",
            expense_date="2026-09-01",
            reference_number="E-1",
            status="completed",
            status_origin="qbo_pull",
            status_source_ref="qbo:realm/id",
        )
    kw = repo.create.call_args.kwargs
    assert kw["status"] == "completed"
    assert kw["status_origin"] == "qbo_pull"
    assert kw["status_source_ref"] == "qbo:realm/id"


def test_expense_service_read_paginated_forwards_the_status_filter():
    repo = MagicMock()
    repo.read_paginated.return_value = ([], 0)
    ExpenseService(repo=repo).read_paginated(status="submitted")
    assert repo.read_paginated.call_args.kwargs["status"] == "submitted"


def test_expense_repository_create_binds_the_status_triple():
    from entities.expense.persistence.repo import ExpenseRepository

    cursor = MagicMock()
    cursor.fetchone.return_value = SimpleNamespace(
        Id=1, PublicId="pub-1", RowVersion=b"AAAA",
        CreatedDatetime=None, ModifiedDatetime=None,
        VendorId=10, ExpenseDate=None, ReferenceNumber="E-1",
        TotalAmount=None, Memo=None, IsDraft=True, IsCredit=False,
        Status="completed", StatusDatetime="t",
        StatusOrigin="qbo_pull", StatusSourceRef="qbo:realm/id",
    )
    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)

    with patch("entities.expense.persistence.repo.get_connection", return_value=conn), \
         patch("entities.expense.persistence.repo.call_procedure") as cp:
        ExpenseRepository().create(
            vendor_id=10,
            expense_date="2026-09-01",
            reference_number="E-1",
            status="completed",
            status_origin="qbo_pull",
            status_source_ref="qbo:realm/id",
        )
    params = cp.call_args.kwargs["params"]
    assert params["Status"] == "completed"
    assert params["StatusOrigin"] == "qbo_pull"
    assert params["StatusSourceRef"] == "qbo:realm/id"


def test_expense_repository_read_paginated_binds_the_status_filter():
    from entities.expense.persistence.repo import ExpenseRepository

    cursor = MagicMock()
    cursor.fetchall.return_value = []
    cursor.nextset.return_value = True
    cursor.fetchone.return_value = (0,)
    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)

    with patch("entities.expense.persistence.repo.call_procedure") as cp:
        ExpenseRepository().read_paginated(conn=conn, status="declined")
    assert cp.call_args.kwargs["params"]["Status"] == "declined"


def test_from_db_hydrates_the_four_status_columns():
    from entities.expense.persistence.repo import ExpenseRepository

    row = SimpleNamespace(
        Id=1, PublicId="pub-1", RowVersion=b"AAAA",
        CreatedDatetime=None, ModifiedDatetime=None,
        VendorId=10, ExpenseDate=None, ReferenceNumber="E-1",
        TotalAmount=None, Memo=None, IsDraft=True, IsCredit=False,
        Status="in_review",
        StatusDatetime="2026-09-16T12:00:00",
        StatusOrigin="user",
        StatusSourceRef="review:1",
    )
    expense = ExpenseRepository()._from_db(row)
    assert expense.status == "in_review"
    assert expense.status_datetime == "2026-09-16T12:00:00"
    assert expense.status_origin == "user"
    assert expense.status_source_ref == "review:1"


# ---------------------------------------------------------------------------
# CreateReview mirror + atomic apply
# ---------------------------------------------------------------------------


def test_creating_a_review_MIRRORS_the_new_state_onto_the_expense():
    from tests.sproc_text import REPO_ROOT, sproc_body

    body = sproc_body(REPO_ROOT / "entities/review/sql/dbo.review.sql", "CreateReview")
    assert "UPDATE e" in body and "dbo.[Expense] e" in body, "CreateReview must mirror into Expense"
    assert "IF @ExpenseId IS NOT NULL" in body

    executable = strip_sql_comments(body)
    mirror = executable[executable.index("UPDATE e"):executable.index("UPDATE b")]
    assert "SET e.[Status] = @ReviewKind" in mirror
    for flag in ("IsDeclined", "IsFinal", "IsInitial"):
        assert flag not in mirror, f"the mirror must not re-read {flag}"

    assert "e.[IsDraft] = 1" in body, (
        "the IsDraft=1 guard is mandatory: a review on a finished expense "
        "must not drag Status back into the pipeline"
    )


def test_the_mirror_never_reopens_a_completed_expense():
    from tests.sproc_text import REPO_ROOT, sproc_body

    body = sproc_body(REPO_ROOT / "entities/review/sql/dbo.review.sql", "CreateReview")
    mirror = body[body.index("UPDATE e"):body.index("UPDATE b")]
    assert "AND e.[IsDraft] = 1" in mirror
    assert "'completed'" not in mirror, "the mirror must never assign completed"


def test_the_transition_reports_a_guarded_miss_as_an_empty_result():
    from tests.sproc_text import REPO_ROOT, sproc_body

    body = sproc_body(REPO_ROOT / "entities/expense/sql/dbo.expense.sql", "TransitionExpenseStatus")
    assert "WHERE [Id] = @Id AND [Status] = @ToStatus;" in body, (
        "the result-set must be keyed on the destination, not just the id"
    )


def test_the_backfill_orders_reviews_the_same_way_the_live_read_does():
    from tests.sproc_text import REPO_ROOT, sproc_body

    live = sproc_body(REPO_ROOT / "entities/review/sql/dbo.review.sql",
                      "ReadCurrentReviewByExpenseId")
    assert "ORDER BY [CreatedDatetime] DESC, [Id] DESC" in live

    backfill = _unquoted(_expense_sql())
    assert "ORDER BY r.[CreatedDatetime] DESC, r.[Id] DESC" in backfill


def test_the_backfill_cannot_break_a_from_scratch_build():
    sql = _expense_sql()
    assert "EXEC sp_executesql N'" in sql, (
        "the backfill must be dynamic — a static reference to dbo.[Review] "
        "breaks the from-scratch build regardless of any IF guard"
    )
    guard = sql[sql.index("-- Guarded on the JOINED tables existing"):sql.index("EXEC sp_executesql")]
    for table in ("dbo.Review", "dbo.ReviewStatus", "dbo.CompletionJob"):
        assert f"OBJECT_ID('{table}', 'U') IS NOT NULL" in guard, table


def test_expense_and_review_demand_an_atomic_apply():
    """Expense schema and the CreateReview Expense mirror are coupled.

    scripts/run_sql.py commits per invocation, so applying them separately
    leaves a live window. The banner has to be in the first 2000 chars.
    """
    from tests.sproc_text import REPO_ROOT, sproc_body

    head = (REPO_ROOT / "entities/expense/sql/dbo.expense.sql").read_text()[:2000]
    flat = " ".join(head.replace("--", " ").split())
    assert "ONE** TRANSACTION" in flat, (
        "dbo.expense.sql must carry the atomic-apply warning in its first 2000 chars"
    )
    assert "271" in flat, "dbo.expense.sql must name the failure mode"

    body = sproc_body(REPO_ROOT / "entities/review/sql/dbo.review.sql", "CreateReview")
    assert "UPDATE e" in body and "dbo.[Expense] e" in body
    assert "IF @ExpenseId IS NOT NULL" in body


# ---------------------------------------------------------------------------
# U-447 — page and total from one materialized set
# ---------------------------------------------------------------------------


def test_the_route_reports_the_total_the_sproc_returned_not_the_page_length():
    service = MagicMock()
    service.read_paginated.return_value = ([_expense(id=1), _expense(id=2)], 4242)
    review_repo = MagicMock()
    review_repo.read_current_by_expense_ids.return_value = {}
    coding_repo = MagicMock()
    coding_repo.read_state_by_expense_ids.return_value = {}
    with patch("entities.expense.api.router.ExpenseService", return_value=service), \
         patch("entities.review.persistence.repo.ReviewRepository", return_value=review_repo), \
         patch("entities.expense_coding_item.persistence.repo.ExpenseCodingItemRepository", return_value=coding_repo):
        response = get_expenses_router(
            page=1, page_size=50, search=None, vendor_id=None,
            is_draft=None, status=None, start_date=None, end_date=None,
            current_user=USER)
    assert response["count"] == 4242
    assert len(response["data"]) == 2


def test_the_page_and_the_total_come_from_ONE_materialized_set():
    from tests.sproc_text import REPO_ROOT, sproc_body

    body = sproc_body(REPO_ROOT / "entities/expense/sql/dbo.expense.sql", "ReadExpensesPaginated")
    assert "INTO #FilteredExpenses" in body
    assert "SELECT COUNT(*) AS [TotalCount] FROM #FilteredExpenses;" in body, (
        "the total must count the SAME materialized set, not re-run the predicate"
    )
    assert "SET NOCOUNT ON" in body, (
        "mandatory now: SELECT INTO emits a row-count token that would arrive "
        "as the first result set"
    )

    after = body[body.index("INTO #FilteredExpenses"):]
    after = after[after.index("WHERE"):]
    assert "dbo.[Expense]" not in after, (
        "something after the materialize step re-reads live dbo.[Expense] — that is "
        "a second snapshot, which is the entire bug this unit exists to remove"
    )
    assert body.count("FROM dbo.[Expense] e") == 1, "the base table is read exactly once"


def test_the_filter_predicate_is_evaluated_once_per_request():
    from tests.sproc_text import REPO_ROOT, sproc_body

    body = sproc_body(REPO_ROOT / "entities/expense/sql/dbo.expense.sql", "ReadExpensesPaginated")
    assert body.count("dbo.UserCanAccessExpense(") == 1
    assert body.count("(@Status IS NULL OR e.[Status] = @Status)") == 1
    assert body.count("LIKE '%' + @SearchTerm + '%'") == 5, "one search block, five columns"


def test_the_repo_reads_the_total_from_the_SECOND_result_set():
    from unittest.mock import MagicMock as MM

    from entities.expense.persistence.repo import ExpenseRepository

    cursor = MM()
    cursor.fetchall.return_value = []
    cursor.nextset.return_value = True
    cursor.fetchone.return_value = (4242,)
    conn = MM(); conn.cursor.return_value = cursor
    conn.__enter__ = MM(return_value=conn); conn.__exit__ = MM(return_value=False)

    with patch("entities.expense.persistence.repo.call_procedure"):
        rows, total = ExpenseRepository().read_paginated(conn=conn)
    assert total == 4242, "the total must come from the sproc, not from len(page)"
    assert rows == []


def test_a_missing_total_RAISES_rather_than_inventing_one():
    from unittest.mock import MagicMock as MM

    from entities.expense.persistence.repo import ExpenseRepository

    cursor = MM()
    cursor.fetchall.return_value = [None] * 50
    cursor.nextset.return_value = False
    conn = MM(); conn.cursor.return_value = cursor
    conn.__enter__ = MM(return_value=conn); conn.__exit__ = MM(return_value=False)

    with patch("entities.expense.persistence.repo.call_procedure"):
        with pytest.raises(Exception) as exc:
            ExpenseRepository().read_paginated(conn=conn)
    assert "U-447" in str(exc.value) or "no total" in str(exc.value)
    assert "dbo.expense.sql" in str(exc.value) or "U-447" in str(exc.value)


# ---------------------------------------------------------------------------
# Date-range filter
# ---------------------------------------------------------------------------


def test_malformed_dates_are_rejected_by_the_schema_not_by_pyodbc():
    params = inspect.signature(get_expenses_router).parameters
    for name in ("start_date", "end_date"):
        ann = params[name].annotation
        assert date_type in getattr(ann, "__args__", (ann,)), (
            f"{name} must be typed `date` so FastAPI validates the format"
        )


def test_both_date_bounds_are_converted_independently():
    for kwargs, expect in (
        ({"start_date": date_type(2026, 1, 1)}, ("2026-01-01", None)),
        ({"end_date": date_type(2026, 3, 31)}, (None, "2026-03-31")),
        ({"start_date": date_type(2026, 1, 1), "end_date": date_type(2026, 3, 31)},
         ("2026-01-01", "2026-03-31")),
    ):
        _, service = _call([_expense()], **kwargs)
        kw = service.read_paginated.call_args.kwargs
        assert (kw["start_date"], kw["end_date"]) == expect, kwargs


def test_omitted_date_args_do_not_leak_the_query_sentinel():
    service = MagicMock()
    service.read_paginated.return_value = ([], 0)
    review_repo = MagicMock()
    review_repo.read_current_by_expense_ids.return_value = {}
    coding_repo = MagicMock()
    coding_repo.read_state_by_expense_ids.return_value = {}
    with patch("entities.expense.api.router.ExpenseService", return_value=service), \
         patch("entities.review.persistence.repo.ReviewRepository", return_value=review_repo), \
         patch("entities.expense_coding_item.persistence.repo.ExpenseCodingItemRepository", return_value=coding_repo):
        get_expenses_router(current_user=USER)
    kw = service.read_paginated.call_args.kwargs
    assert kw["start_date"] is None and kw["end_date"] is None
    assert kw["status"] is None


def test_date_bounds_reach_sql():
    _, service = _call([_expense()], start_date=date_type(2026, 1, 1),
                       end_date=date_type(2026, 3, 31))
    kw = service.read_paginated.call_args.kwargs
    assert kw["start_date"] == "2026-01-01"
    assert kw["end_date"] == "2026-03-31"


# ---------------------------------------------------------------------------
# IsDraft is unwritable
# ---------------------------------------------------------------------------


def test_NO_sproc_assigns_IsDraft_anywhere():
    from tests.sproc_text import REPO_ROOT

    rel = "entities/expense/sql/dbo.expense.sql"
    sql = (REPO_ROOT / rel).read_text()
    executable = strip_sql_comments(sql)
    set_lines = [
        l for l in executable.splitlines()
        if re.search(r"(SET\s+(?:\w+\.)?\[IsDraft\]|^\s*(?:\w+\.)?\[IsDraft\]\s*=)", l)
    ]
    assert not set_lines, f"{rel} assigns IsDraft: {set_lines}"
    insert_lists = re.findall(r"INSERT INTO dbo\.\[Expense\](.*?)(?:OUTPUT|VALUES)",
                              executable, re.S)
    for lst in insert_lists:
        assert "[IsDraft]" not in lst, f"{rel} INSERTs into IsDraft (error 271)"


def test_the_swap_is_idempotent_and_preserves_the_column_contract():
    sql = _expense_sql()
    executable = strip_sql_comments(sql)

    drop_col = executable.index("DROP COLUMN [IsDraft]")
    swap = executable[:drop_col]
    swap = swap[swap.rindex("IF OBJECT_ID('dbo.Expense', 'U') IS NOT NULL"):]
    assert "AND is_computed = 0" in swap, (
        "the swap must be gated on IsDraft still being a real column, or a "
        "re-apply drops and re-adds the computed column under a schema lock"
    )
    assert ") PERSISTED NOT NULL;" in executable
    assert "CASE WHEN [Status] = 'completed' THEN CAST(0 AS BIT) ELSE CAST(1 AS BIT) END" in executable

    assert "sys.default_constraints" in executable
    assert "DF__Expense__IsDraft" not in executable, "the auto-generated name must not be hardcoded"

    drop_col = executable.index("DROP COLUMN [IsDraft]")
    for blocker in ("DROP INDEX [IX_Expense_Status]",
                    "DROP CONSTRAINT [CK_Expense_Status_IsDraft]",
                    "EXEC sp_executesql @DropDefaultSql"):
        assert executable.index(blocker) < drop_col, f"{blocker} must precede the drop"

    after = executable[drop_col:]
    assert "INCLUDE ([VendorId], [ExpenseDate])" in after


def test_nothing_anywhere_writes_Expense_IsDraft():
    from tests.sproc_text import REPO_ROOT

    SKIP_DIRS = {".venv", ".git", "__pycache__", "node_modules", "dist"}
    OTHER = re.compile(r"Expense(Line|Coding|Folder|Completion)", re.I)

    insert_expense = re.compile(
        r"INSERT\s+(?:INTO\s+)?(?:\[?dbo\]?\.)?\[?Expense\]?\s*\((?P<cols>[^)]*)\)",
        re.I | re.S,
    )
    update_expense = re.compile(
        r"UPDATE\s+(?:TOP\s*\([^)]*\)\s*)?(?:"
        r"(?:\[?dbo\]?\.)?\[?Expense\]?\b"
        r"|"
        r"\w+\b(?=[^;]*?\bFROM\s+(?:\[?dbo\]?\.)?\[?Expense\]?(?!\w))"
        r")(?P<body>.*?)"
        r"(?:\bWHERE\b|\bOUTPUT\b|\bFROM\b|$)",
        re.I | re.S,
    )
    assigns_isdraft = re.compile(
        r"(?:^|\bSET\s+)\s*(?:\w+\.)?\[?IsDraft\]?\s*=", re.I | re.M
    )

    offenders: list[str] = []
    for path in REPO_ROOT.rglob("*"):
        if path.is_dir() or path.suffix not in {".sql", ".py"}:
            continue
        if SKIP_DIRS & set(path.parts):
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel.startswith("tests/"):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "IsDraft" not in text:
            continue
        executable = strip_sql_comments(text)

        for m in insert_expense.finditer(executable):
            if OTHER.search(m.group(0)):
                continue
            if re.search(r"\[?IsDraft\]?", m.group("cols"), re.I):
                offenders.append(f"{rel}: INSERT names IsDraft (SQL error 271)")

        for m in update_expense.finditer(executable):
            if OTHER.search(m.group(0)):
                continue
            for line in m.group("body").splitlines():
                if assigns_isdraft.search(line):
                    offenders.append(f"{rel}: UPDATE assigns IsDraft -> {line.strip()[:60]}")

    assert not offenders, (
        "Expense.IsDraft is a PERSISTED COMPUTED column since U-467 and cannot be "
        f"written: {offenders}"
    )


def test_a_second_apply_does_not_resurrect_the_dropped_constraint():
    sql = _expense_sql()
    executable = strip_sql_comments(sql)

    create_ck = executable.index("ADD CONSTRAINT [CK_Expense_Status_IsDraft]")
    guard = executable[:create_ck]
    guard = guard[guard.rindex("IF OBJECT_ID('dbo.Expense', 'U') IS NOT NULL"):]
    assert "is_computed = 0" in guard, (
        "the CK creation must be gated on IsDraft still being a real column, or "
        "a re-apply brings the constraint back"
    )

    drop_ck = executable.index("DROP CONSTRAINT [CK_Expense_Status_IsDraft]")
    assert create_ck < drop_ck, "creation must come before the drop in file order"
    assert "ADD CONSTRAINT [CK_Expense_Status_IsDraft]" not in executable[drop_ck:]


# ---------------------------------------------------------------------------
# Purchase connector origin
# ---------------------------------------------------------------------------


def test_purchase_app_owned_contains_the_four_status_fields():
    from integrations.intuit.qbo.base.field_ownership import PURCHASE

    for field_name in ("status", "status_datetime", "status_origin", "status_source_ref"):
        assert field_name in PURCHASE.app_owned, field_name


def test_purchase_connector_create_lands_completed_with_qbo_pull_origin():
    from tests.test_u283b_purchase_qbo_identity_repoint import (
        _build_purchase_connector,
        _make_qbo_purchase,
    )

    connector, expense_service, _ = _build_purchase_connector()
    qbo = _make_qbo_purchase(qbo_id="PURCH-99", realm_id="realm-1")
    connector._create_expense(
        qbo_purchase=qbo, vendor_public_id="vendor-pub-1", reference_number="R-1",
    )
    kwargs = expense_service.create.call_args.kwargs
    assert kwargs["status"] == "completed"
    assert kwargs["status_origin"] == "qbo_pull"
    assert kwargs["status_source_ref"] == "qbo:realm-1/PURCH-99"
    assert "is_draft" not in kwargs


def test_purchase_connector_create_drops_source_ref_when_either_identity_half_is_missing():
    from tests.test_u283b_purchase_qbo_identity_repoint import (
        _build_purchase_connector,
        _make_qbo_purchase,
    )

    connector, expense_service, _ = _build_purchase_connector()
    qbo = _make_qbo_purchase(qbo_id="PURCH-99", realm_id=None)
    connector._create_expense(
        qbo_purchase=qbo, vendor_public_id="vendor-pub-1", reference_number="R-1",
    )
    kwargs = expense_service.create.call_args.kwargs
    assert kwargs["status_source_ref"] is None


# ---------------------------------------------------------------------------
# complete_expense uses FinalizeExpenseById (U-434 shape)
# ---------------------------------------------------------------------------


def _exp(**over):
    base = dict(
        id=55,
        public_id="pub-55",
        row_version="cm9ja2V0",
        vendor_id=7,
        expense_date="2026-09-01",
        reference_number="INV-1",
        total_amount=None,
        memo=None,
        is_draft=True,
        is_credit=False,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _svc(*, finalize_returns=...):
    repo = MagicMock()
    repo.finalize_by_id.return_value = _exp(is_draft=False) if finalize_returns is ... else finalize_returns
    svc = ExpenseService(repo=repo)
    svc.read_by_public_id = MagicMock(return_value=_exp())
    svc.vendor_service = MagicMock()
    svc.vendor_service.read_by_id.return_value = SimpleNamespace(public_id="vendor-pub-1")
    svc._expense_line_item_service = MagicMock()
    svc._expense_line_item_service.read_by_expense_id.return_value = []
    svc._upload_to_general_receipts_folder = MagicMock(return_value={"errors": []})
    svc._enqueue_box_uploads = MagicMock()
    return svc, repo


def test_finalize_goes_through_the_idempotent_sproc_not_a_rowversion_update():
    svc, repo = _svc()
    with patch.object(ExpenseService, "update_by_public_id") as mock_update:
        result = svc.complete_expense(public_id="pub-55")
    repo.finalize_by_id.assert_called_once_with(id=55)
    assert mock_update.call_count == 0
    assert result["expense_finalized"] is True


def test_finalize_carries_no_row_version():
    svc, repo = _svc()
    svc.complete_expense(public_id="pub-55")
    kwargs = repo.finalize_by_id.call_args.kwargs
    assert "row_version" not in kwargs and "RowVersion" not in kwargs
    assert set(kwargs) == {"id"}


def test_no_sleep_and_no_retry_remain_on_the_finalize_path():
    import entities.expense.business.service as svc_mod

    assert not hasattr(svc_mod, "time"), (
        "entities.expense.business.service re-imported `time` — the sleep-based "
        "retry loop U-467 deleted is likely back"
    )


def test_expense_deleted_mid_finalize_is_a_404_not_a_silent_success():
    svc, repo = _svc(finalize_returns=None)
    result = svc.complete_expense(public_id="pub-55")
    assert result["status_code"] == 404
    assert result["expense_finalized"] is False
    assert "Expense deleted during finalization" in result["message"]


def test_missing_vendor_still_refuses_before_finalizing():
    svc, repo = _svc()
    svc.vendor_service.read_by_id.return_value = None
    result = svc.complete_expense(public_id="pub-55")
    assert result["status_code"] == 400
    assert result["expense_finalized"] is False
    repo.finalize_by_id.assert_not_called()


def test_repo_raise_is_a_500_naming_the_finalize_step():
    svc, repo = _svc()
    repo.finalize_by_id.side_effect = RuntimeError("boom")
    result = svc.complete_expense(public_id="pub-55")
    assert result["status_code"] == 500
    assert result["expense_finalized"] is False
    assert result["message"].startswith("Error finalizing expense:")


def test_finalize_sproc_is_idempotent_and_pyodbc_safe():
    from tests.sproc_text import REPO_ROOT, sproc_body, sproc_params

    base = REPO_ROOT / "entities/expense/sql/dbo.expense.sql"
    params = sproc_params(base, "FinalizeExpenseById")
    body = sproc_body(base, "FinalizeExpenseById")

    assert "@RowVersion" not in params, (
        "FinalizeExpenseById must NOT take a RowVersion"
    )
    executable = strip_sql_comments(body)
    assert "WHERE [Id] = @Id AND [Status] <> 'completed';" in executable, (
        "the transition must still be guarded so a second completion is a no-op"
    )
    assert "SET NOCOUNT ON" in body
    begin_at = body.upper().index("BEGIN")
    after_begin = strip_sql_comments(body[begin_at + 5:]).strip()
    assert after_begin.upper().startswith("SET NOCOUNT ON"), (
        "SET NOCOUNT ON must be the first statement in the BEGIN block"
    )
    assert "[QboId]" in body, "projected here so this path doesn't repeat UpdateExpenseById's omission"
    assert "[IsDraft] = 0" not in body, "IsDraft is computed — it cannot be assigned"
