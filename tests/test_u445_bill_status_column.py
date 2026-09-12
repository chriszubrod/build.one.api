"""U-445 — Bill gets a real `Status` column, and `?status=` filters on it.

U-443 derived `status` per request from `IsDraft x the latest Review row`. That
was correct but UNFILTERABLE: the list endpoint paginates in SQL and returns
`count` from a separate COUNT sproc, so filtering the derived value in Python
would have filtered `data` while `count` still described the unfiltered set —
every tab showing the wrong total, and pages silently short. The column is what
makes the filter honest, and the filter is what the Bills page tabs need.

`IsDraft` REMAINS A REAL, WRITTEN COLUMN here. Retiring it — drop and re-add as
a PERSISTED computed column, plus the terminal lock and gate flags — is U-446.
Dual-writing is what removes the deploy window the design's three-step swap
existed to close: `CK_Bill_Status_IsDraft` makes the two columns incapable of
disagreeing, so an API image that has never heard of `Status` still cannot write
an inconsistent row. That constraint is the load-bearing part of this unit.
"""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import asyncio

import pytest

from entities.bill.api.router import get_bills_router
from entities.bill.business.model import Bill
from shared.lifecycle.resolver import LIFECYCLE_STATUSES, attach_lifecycle

USER = {"id": 1, "username": "tester"}


def _bill(id=1, *, is_draft=True, status=None):
    return Bill(
        id=id, public_id=f"bill-{id}", row_version="AAAA",
        created_datetime=None, modified_datetime=None,
        vendor_id=10, payment_term_id=None,
        bill_date="2026-09-01", due_date="2026-09-01",
        bill_number=f"B-{id}", total_amount=Decimal("1.00"), memo=None,
        is_draft=is_draft, status=status,
    )


def _call(bills, **kwargs):
    service = MagicMock()
    # U-447: one call returns (page, total) — the total no longer comes from a
    # second sproc, which is what let it describe a different snapshot.
    service.read_paginated.return_value = (bills, len(bills))
    repo = MagicMock(); repo.read_first_line_item_projects.return_value = {}
    review_repo = MagicMock(); review_repo.read_current_by_bill_ids.return_value = {}
    vendor_repo = MagicMock(); vendor_repo.read_public_ids_by_ids.return_value = {}
    with patch("entities.bill.api.router.BillService", return_value=service), \
         patch("entities.bill.api.router.BillRepository", return_value=repo), \
         patch("entities.bill.api.router.VendorRepository", return_value=vendor_repo), \
         patch("entities.bill.api.router.get_connection", return_value=MagicMock()), \
         patch("entities.review.persistence.repo.ReviewRepository", return_value=review_repo):
        params = dict(page=1, page_size=50, search=None, vendor_id=None,
                      is_draft=None, status=None, start_date=None, end_date=None,
                      current_user=USER)
        params.update(kwargs)
        response = asyncio.run(get_bills_router(**params))
    return response, service


# ---------------------------------------------------------------------------
# The filter reaches SQL — the entire point
# ---------------------------------------------------------------------------


def test_status_reaches_sql_and_the_count_CANNOT_use_a_different_predicate():
    """THE assertion of this unit, strengthened by U-447.

    Filtering the page but not the total is the bug the stored column exists to
    prevent: `data` would carry 8 submitted bills while `count` still said
    20,266. Originally two sproc calls had to be kept in step; since U-447 the
    total comes back from the SAME call over the SAME materialized set, so
    there is no second predicate that could drift — and no second snapshot.
    """
    _, service = _call([_bill(status="submitted")], status="submitted")
    assert service.read_paginated.call_args.kwargs["status"] == "submitted"
    assert service.read_paginated.call_count == 1
    assert service.count.call_count == 0, (
        "the list route must not ask for the total separately — that is the "
        "second snapshot U-447 removed"
    )


def test_no_status_filters_nothing():
    _, service = _call([_bill()])
    assert service.read_paginated.call_args.kwargs["status"] is None
    assert service.count.call_count == 0


@pytest.mark.parametrize("status", list(LIFECYCLE_STATUSES))
def test_every_canonical_status_is_accepted(status):
    _, service = _call([], status=status)
    assert service.read_paginated.call_args.kwargs["status"] == status


def test_an_unknown_status_is_422_not_a_silent_full_list():
    """A typo'd tab quietly returning all 20,266 bills — as an ignored filter
    would — is worse than an error, because nothing looks wrong."""
    from shared.api.errors import ApiError

    with pytest.raises(ApiError) as exc:
        _call([_bill()], status="billed")
    assert exc.value.status_code == 422
    assert "billed" in str(exc.value.detail)


def test_the_rejected_message_names_the_legal_values():
    from shared.api.errors import ApiError

    with pytest.raises(ApiError) as exc:
        _call([], status="Draft")  # case matters
    for s in LIFECYCLE_STATUSES:
        assert s in str(exc.value.detail)


def test_a_direct_call_without_fastapi_does_not_trip_the_guard():
    """The route is called in-process too (tests, and any internal caller),
    where the unresolved `Query(None)` default arrives instead of None. The
    guard validates a value it was actually given, not the sentinel."""
    from fastapi import Query

    response, service = _call([_bill()], status=Query(default=None))
    assert response["count"] == 1
    assert service.read_paginated.call_args.kwargs["status"] is None


# ---------------------------------------------------------------------------
# Stored beats derived
# ---------------------------------------------------------------------------


def test_the_stored_status_WINS_over_the_derived_one():
    """If the response derived a different answer than the WHERE clause
    selected on, a row could appear under a tab it was never filtered into.
    Whatever SQL matched is what the client must see."""
    payload = attach_lifecycle({}, is_draft=True, review=None, stored_status="approved")
    assert payload["status"] == "approved"       # stored
    assert payload["review_status_kind"] == "none"  # still derived from the review


def test_the_derivation_still_runs_where_no_column_exists_yet():
    """Expense, BillCredit, Invoice and ContractLabor have no Status column
    until their own Phase-3 units. They must keep getting a derived answer."""
    payload = attach_lifecycle({}, is_draft=False, review=None, stored_status=None)
    assert payload["status"] == "completed"


def test_the_list_emits_the_stored_value():
    response, _ = _call([_bill(id=1, is_draft=False, status="completed"),
                         _bill(id=2, is_draft=True, status="declined")])
    by_id = {b["id"]: b["status"] for b in response["data"]}
    assert by_id == {1: "completed", 2: "declined"}


def test_a_row_predating_the_backfill_still_resolves():
    """Defensive: a Bill object built without a status (an older payload, a
    fixture) must fall back to the derivation rather than emitting null."""
    response, _ = _call([_bill(id=1, is_draft=False, status=None)])
    assert response["data"][0]["status"] == "completed"


# ---------------------------------------------------------------------------
# The SQL contract
# ---------------------------------------------------------------------------


def _bill_sql():
    from tests.sproc_text import REPO_ROOT
    return (REPO_ROOT / "entities/bill/sql/dbo.bill.sql").read_text()


def _unquoted(text):
    """Collapse the doubled single-quotes that sp_executesql literals require,
    so an assertion reads the same whether the statement is inline or wrapped."""
    return text.replace("\'\'", "\'")


def _executable_sql():
    """Comments stripped. The prose in this file NAMES the constraints and the
    ordering rule, so an un-stripped ordering assertion passes on a file whose
    statements are in the wrong order — it matches the comment, not the DDL."""
    return "\n".join(line.split("--")[0] for line in _bill_sql().splitlines())


def test_the_consistency_constraint_exists_and_is_validated():
    """CK_Bill_Status_IsDraft is what makes dual-writing safe, and WITH CHECK is
    what makes applying this file the parity proof: SQL Server validates all
    20,266 existing rows, so a wrong backfill fails the apply rather than
    shipping silently."""
    sql = _bill_sql()
    assert "CK_Bill_Status_IsDraft" in sql
    assert "(CASE WHEN [Status] = 'completed' THEN 0 ELSE 1 END) = [IsDraft]" in sql
    assert sql.count("WITH CHECK ADD CONSTRAINT") == 3, (
        "all three CHECKs must validate existing rows, not just future writes"
    )


def test_constraints_are_added_AFTER_the_backfill():
    """Ordering is load-bearing: `Status` defaults to 'draft', so the instant
    the column exists all 20,224 completed bills read 'draft' and the
    consistency CHECK would be violated. Columns, then backfill, then CHECKs."""
    sql = _executable_sql()
    add_column = sql.index("ALTER TABLE [dbo].[Bill] ADD [Status]")
    backfill = sql.index("SET b.[Status] = CASE")
    check = sql.index("ADD CONSTRAINT [CK_Bill_Status_IsDraft]")
    assert add_column < backfill < check, (
        f"order is column({add_column}) -> backfill({backfill}) -> check({check})"
    )


def test_the_backfill_cannot_run_twice():
    """Base files are re-applied routinely. A second run must not stamp over
    live transitions."""
    sql = _bill_sql()
    assert "AND NOT EXISTS (SELECT 1 FROM dbo.[Bill] WHERE [StatusDatetime] IS NOT NULL)" in sql


def test_the_backfill_expression_matches_the_python_resolver():
    """Stored and derived have to agree by CONSTRUCTION, not by luck — the
    parity check after the apply is only meaningful if the two are the same
    rule. Both key on IsDeclined -> IsFinal -> IsInitial, in that order."""
    sql = _unquoted(_bill_sql())
    block = sql[sql.index("SET b.[Status] = CASE"):sql.index("b.[StatusDatetime] = SYSUTCDATETIME()")]
    order = [block.index(f"cur.[{f}] = 1") for f in ("IsDeclined", "IsFinal", "IsInitial")]
    assert order == sorted(order), "precedence differs from review_kind_from_flags"
    assert "b.[IsDraft] = 0            THEN 'completed'" in block, (
        "completed must be tested FIRST — a finalized bill is `completed` "
        "whatever its review says (U-443)"
    )


def test_finalize_writes_status_and_nothing_else():
    """U-445 required this sproc to dual-write Status AND IsDraft, because
    CK_Bill_Status_IsDraft would otherwise reject the row. U-446 made IsDraft a
    computed column, so the second write became an ERROR — naming a computed
    column on the left of a SET is refused outright, and this is the completion
    path, so it would break every complete-bill."""
    from tests.sproc_text import REPO_ROOT, sproc_body

    body = sproc_body(REPO_ROOT / "entities/bill/sql/dbo.bill.sql", "FinalizeBillById")
    assert "[Status] = 'completed'" in body
    assert "[StatusOrigin] = 'completion'" in body
    assert "[IsDraft] = 0" not in body, "IsDraft is computed — it cannot be assigned"


def test_update_translates_is_draft_for_callers_that_predate_status():
    """The QBO pull connectors and any old API image still send only @IsDraft.
    Without the translation the CHECK rejects their write outright."""
    from tests.sproc_text import REPO_ROOT, sproc_body

    body = sproc_body(REPO_ROOT / "entities/bill/sql/dbo.bill.sql", "UpdateBillById")
    assert "WHEN @IsDraft = 0 AND [Status] <> 'completed' THEN 'completed'" in body
    # U-446: the `[IsDraft] = CASE ...` assignment is gone — the column is
    # computed. The `@IsDraft = 1` neutralisation U-445 wrote explicitly is now
    # IMPLICIT and stronger: the Status CASE fires only on 0, so 1 cannot
    # un-complete a bill whose AP already reached QBO/SharePoint/Excel/Box, and
    # there is no longer any assignment through which it could try.
    assert "[IsDraft] =" not in body, "IsDraft is computed — it cannot be assigned"


def test_the_transition_sproc_is_guarded_and_idempotent():
    from tests.sproc_text import REPO_ROOT, sproc_body, sproc_params

    base = REPO_ROOT / "entities/bill/sql/dbo.bill.sql"
    params = sproc_params(base, "TransitionBillStatus")
    body = sproc_body(base, "TransitionBillStatus")
    for p in ("@FromStatuses", "@ToStatus", "@Origin", "@SourceRef", "@RowVersion"):
        assert p in params, p
    assert "STRING_SPLIT(@FromStatuses" in body, "the legal FROM set is enforced in SQL"
    assert "AND [Status] <> @ToStatus" in body, "a repeat transition must be a no-op"
    assert "SET NOCOUNT ON" in body
    executable = "\n".join(l.split("--")[0] for l in body.splitlines()).upper()
    assert "ROLLBACK" not in executable, "pyodbc autocommit-off: rollback raises 266"


def test_create_does_not_name_the_computed_column():
    """An INSERT whose column list names a computed column is SQL Server error
    271 — so this is not style, it is whether `POST /create/bill` works at all.
    `@IsDraft` is KEPT as a parameter (older callers still bind it) but only as
    the fallback that resolves @Status."""
    from tests.sproc_text import REPO_ROOT, sproc_body, sproc_params

    base = REPO_ROOT / "entities/bill/sql/dbo.bill_create_source_email.sql"
    params = sproc_params(base, "CreateBill")
    assert "@Status" in params
    assert "@IsDraft" in params, "older callers must still bind successfully"

    body = sproc_body(base, "CreateBill")
    insert_cols = body[body.index("INSERT INTO dbo.[Bill]"):body.index("OUTPUT")]
    executable = "\n".join(l.split("--")[0] for l in insert_cols.splitlines())
    assert "[IsDraft]" not in executable, (
        "the INSERT column list must not name IsDraft (error 271)"
    )
    assert "COALESCE(@Status," in body, "@IsDraft survives only as the fallback"


def test_the_status_index_is_filtered_to_the_working_set():
    """20,224 of 20,266 bills are completed, so an unfiltered index would be
    20k rows to serve 42. Every tab except Billed lives in the filtered one."""
    sql = _bill_sql()
    assert "CREATE NONCLUSTERED INDEX [IX_Bill_Status]" in sql
    assert "WHERE [Status] <> 'completed'" in sql


def test_list_and_count_sprocs_both_take_the_filter():
    from tests.sproc_text import REPO_ROOT, sproc_body, sproc_params

    base = REPO_ROOT / "entities/bill/sql/dbo.bill.sql"
    for sproc in ("ReadBillsPaginated", "CountBills"):
        assert "@Status" in sproc_params(base, sproc), sproc
        assert "(@Status IS NULL OR b.[Status] = @Status)" in sproc_body(base, sproc), sproc



# ---------------------------------------------------------------------------
# Codex round 1 — the four defects it found
# ---------------------------------------------------------------------------


def test_creating_a_review_MIRRORS_the_new_state_onto_the_bill():
    """Codex P1 — without this the unit is broken for every NEW submission.

    `status` used to be DERIVED at read time (U-443), so creating a Review moved
    the bill automatically. Now the read model emits the STORED column and
    nothing else writes it: a bill submitted for review would sit at 'draft'
    forever while `review_status_kind` said 'submitted' — missing from
    `?status=submitted` and mislabelled under `?status=draft`.

    Lives in the sproc, in the INSERT's own transaction, so every caller
    mirrors: UI, the review-reply email agent, the CL crew flow, scripts.
    """
    from tests.sproc_text import REPO_ROOT, sproc_body

    body = sproc_body(REPO_ROOT / "entities/review/sql/dbo.review.sql", "CreateReview")
    assert "UPDATE b" in body and "dbo.[Bill] b" in body, "CreateReview must mirror into Bill"
    assert "IF @BillId IS NOT NULL" in body

    # precedence identical to review_kind_from_flags
    order = [body.index(f"rs.[{f}] = 1") for f in ("IsDeclined", "IsFinal", "IsInitial")]
    assert order == sorted(order)

    assert "b.[IsDraft] = 1" in body, (
        "the IsDraft=1 guard is mandatory, not cosmetic: reviews legitimately "
        "exist on completed bills (39 in prod), and moving such a bill's Status "
        "back would violate CK_Bill_Status_IsDraft and 500 the review write"
    )
    assert "COMMIT TRANSACTION" in body, "the mirror must share the INSERT's transaction"


def test_the_mirror_never_reopens_a_completed_bill():
    """`completed` outranks any review state (U-443). A review arriving on an
    already-completed bill records history; it must not drag the document back
    into the pipeline after its AP reached QBO/SharePoint/Excel/Box."""
    from tests.sproc_text import REPO_ROOT, sproc_body

    body = sproc_body(REPO_ROOT / "entities/review/sql/dbo.review.sql", "CreateReview")
    mirror = body[body.index("IF @BillId IS NOT NULL"):]
    assert "AND b.[IsDraft] = 1" in mirror
    assert "'completed'" not in mirror, "the mirror must never assign completed"


def test_the_transition_reports_a_guarded_miss_as_an_empty_result():
    """Codex P1. An unconditional re-SELECT returns the row even when the UPDATE
    matched nothing — a stale @RowVersion or a status outside @FromStatuses —
    so a refused transition was indistinguishable from a successful one.

    Keying the projection on the DESTINATION makes the contract "the bill is now
    in @ToStatus": a move returns the row, a repeat returns it (idempotent
    success), a guarded miss returns nothing.
    """
    from tests.sproc_text import REPO_ROOT, sproc_body

    body = sproc_body(REPO_ROOT / "entities/bill/sql/dbo.bill.sql", "TransitionBillStatus")
    assert "WHERE [Id] = @Id AND [Status] = @ToStatus;" in body, (
        "the result-set must be keyed on the destination, not just the id"
    )


def test_the_backfill_orders_reviews_the_same_way_the_live_read_does():
    """Codex P2. `ReadCurrentReviewByBillId` orders by `CreatedDatetime DESC,
    Id DESC`. Ordering the backfill by Id alone disagrees whenever two reviews
    were inserted out of timestamp order, storing a status the resolver would
    never derive — permanently, because the backfill runs once."""
    from tests.sproc_text import REPO_ROOT, sproc_body

    live = sproc_body(REPO_ROOT / "entities/review/sql/dbo.review.sql",
                      "ReadCurrentReviewByBillId")
    assert "ORDER BY [CreatedDatetime] DESC, [Id] DESC" in live

    backfill = _unquoted(_bill_sql())
    assert "ORDER BY r.[CreatedDatetime] DESC, r.[Id] DESC" in backfill


def test_the_backfill_cannot_break_a_from_scratch_build():
    """Codex P1. dbo.[Review] carries FK_Review_Bill so it cannot exist before
    this file runs. An IF guard alone does NOT help — SQL Server defers name
    resolution for stored-procedure BODIES, not ad-hoc batches, so the batch
    fails to COMPILE before the IF is evaluated. And run_sql.py commits the
    whole file as one transaction, so that would roll back the entire Status
    schema, not just the backfill.
    """
    sql = _bill_sql()
    assert "EXEC sp_executesql N'" in sql, (
        "the backfill must be dynamic — a static reference to dbo.[Review] "
        "breaks the from-scratch build regardless of any IF guard"
    )
    guard = sql[sql.index("-- Guarded on the JOINED tables existing"):sql.index("EXEC sp_executesql")]
    for table in ("dbo.Review", "dbo.ReviewStatus", "dbo.BillCompletionResult"):
        assert f"OBJECT_ID('{table}', 'U') IS NOT NULL" in guard, table



# ---------------------------------------------------------------------------
# U-447 — page and total from one materialized set
# ---------------------------------------------------------------------------


def test_the_route_reports_the_total_the_sproc_returned_not_the_page_length():
    """`count` must be the size of the whole filtered set, not of this page —
    otherwise pagination breaks the moment there is more than one page."""
    response, _ = _call([_bill(id=1), _bill(id=2)])
    # driver returns (rows, len(rows)); override to prove the route echoes the
    # sproc's number rather than recomputing it from the page
    service = MagicMock()
    service.read_paginated.return_value = ([_bill(id=1), _bill(id=2)], 4242)
    repo = MagicMock(); repo.read_first_line_item_projects.return_value = {}
    review_repo = MagicMock(); review_repo.read_current_by_bill_ids.return_value = {}
    vendor_repo = MagicMock(); vendor_repo.read_public_ids_by_ids.return_value = {}
    with patch("entities.bill.api.router.BillService", return_value=service), \
         patch("entities.bill.api.router.BillRepository", return_value=repo), \
         patch("entities.bill.api.router.VendorRepository", return_value=vendor_repo), \
         patch("entities.bill.api.router.get_connection", return_value=MagicMock()), \
         patch("entities.review.persistence.repo.ReviewRepository", return_value=review_repo):
        response = asyncio.run(get_bills_router(
            page=1, page_size=50, search=None, vendor_id=None,
            is_draft=None, status=None, current_user=USER))
    assert response["count"] == 4242
    assert len(response["data"]) == 2


def test_the_page_and_the_total_come_from_ONE_materialized_set():
    """Merging the two SELECTs into one sproc would NOT have been enough: this
    database runs READ_COMMITTED_SNAPSHOT, where every STATEMENT takes its own
    snapshot even inside one explicit transaction. The fix has to be a set both
    reads share — hence the temp table."""
    from tests.sproc_text import REPO_ROOT, sproc_body

    body = sproc_body(REPO_ROOT / "entities/bill/sql/dbo.bill.sql", "ReadBillsPaginated")
    assert "INTO #FilteredBills" in body
    assert "SELECT COUNT(*) AS [TotalCount] FROM #FilteredBills;" in body, (
        "the total must count the SAME materialized set, not re-run the predicate"
    )
    assert "SET NOCOUNT ON" in body, (
        "mandatory now: SELECT INTO emits a row-count token that would arrive "
        "as the first result set"
    )

    # THE property, and the one my first cut failed (Codex P1). Materializing
    # only ids left the page re-reading live dbo.[Bill] through a JOIN — a NEW
    # statement, therefore a NEW RCSI snapshot — so a bill finalized mid-request
    # came back under the Draft tab displaying `completed`, and a deleted one
    # gave `data: []` with `count: 1`. Nothing after the materialize step may
    # touch the base table.
    after = body[body.index("INTO #FilteredBills"):]
    after = after[after.index("WHERE"):]          # skip the materialize's own FROM
    assert "dbo.[Bill]" not in after, (
        "something after the materialize step re-reads live dbo.[Bill] — that is "
        "a second snapshot, which is the entire bug this unit exists to remove"
    )
    assert body.count("FROM dbo.[Bill] b") == 1, "the base table is read exactly once"


def test_the_filter_predicate_is_evaluated_once_per_request():
    """The 13-line predicate — search, vendor, dates, IsDraft, Status and the
    RBAC scoping UDF — used to be evaluated twice per request and maintained in
    two sprocs. Inside ReadBillsPaginated it now appears exactly once."""
    from tests.sproc_text import REPO_ROOT, sproc_body

    body = sproc_body(REPO_ROOT / "entities/bill/sql/dbo.bill.sql", "ReadBillsPaginated")
    assert body.count("dbo.UserCanAccessBill(") == 1
    assert body.count("(@Status IS NULL OR b.[Status] = @Status)") == 1
    assert body.count("LIKE '%' + @SearchTerm + '%'") == 6, "one search block, six columns"


def test_the_repo_reads_the_total_from_the_SECOND_result_set():
    """A mutation check found this gap: the route-level test mocks the service,
    so nothing exercised the repo's own total logic. Replacing it with
    `len(bills)` left the whole suite green — and would have made `count` equal
    the page size on every request, silently capping pagination at one page.

    The fixture deliberately returns a total that CANNOT be derived from the
    page length.
    """
    from unittest.mock import MagicMock as MM

    from entities.bill.persistence.repo import BillRepository

    cursor = MM()
    cursor.fetchall.return_value = []          # _from_db is skipped for falsy rows
    cursor.nextset.return_value = True
    cursor.fetchone.return_value = (4242,)
    conn = MM(); conn.cursor.return_value = cursor
    conn.__enter__ = MM(return_value=conn); conn.__exit__ = MM(return_value=False)

    with patch("entities.bill.persistence.repo.call_procedure"):
        rows, total = BillRepository().read_paginated(conn=conn)
    assert total == 4242, "the total must come from the sproc, not from len(page)"
    assert rows == []


def test_a_missing_total_RAISES_rather_than_inventing_one():
    """Codex P1, 2026-09-11 — this test used to assert a graceful degradation.

    The obvious fallback, `total = len(bills)`, is silently wrong in exactly the
    case that matters: page 2 of 120 matches would report `count: 50` and an
    out-of-range page `count: 0`, capping every client's pagination at one page
    with nothing in the logs. And the earlier version of this test could not
    have caught that — it used an EMPTY page, where len(bills) and 0 coincide.

    An absent second set means the container is newer than the sproc, which the
    SQL-first deploy order exists to prevent. Fail loudly.
    """
    from unittest.mock import MagicMock as MM

    from entities.bill.persistence.repo import BillRepository

    cursor = MM()
    cursor.fetchall.return_value = [None] * 50   # a FULL page, not an empty one
    cursor.nextset.return_value = False          # old sproc: no second set
    conn = MM(); conn.cursor.return_value = cursor
    conn.__enter__ = MM(return_value=conn); conn.__exit__ = MM(return_value=False)

    with patch("entities.bill.persistence.repo.call_procedure"):
        with pytest.raises(Exception) as exc:
            BillRepository().read_paginated(conn=conn)
    assert "U-447" in str(exc.value) or "no total" in str(exc.value)


# ---------------------------------------------------------------------------
# U-452 — the date-range filter the Bills page needs
# ---------------------------------------------------------------------------


def test_malformed_dates_are_rejected_by_the_schema_not_by_pyodbc():
    """Codex P2. As plain strings these reached pyodbc's DATETIME2 bind and came
    back as a 500 with driver text. Typed `date`, FastAPI rejects them with a
    422 naming the field, before any DB call."""
    import inspect
    from datetime import date as date_type

    from entities.bill.api.router import get_bills_router

    params = inspect.signature(get_bills_router).parameters
    for name in ("start_date", "end_date"):
        ann = params[name].annotation
        assert date_type in getattr(ann, "__args__", (ann,)), (
            f"{name} must be typed `date` so FastAPI validates the format"
        )


def test_both_date_bounds_are_converted_independently():
    """Codex P3: only the start-only case was covered, so a regression dropping
    an end-only bound passed."""
    from datetime import date as date_type

    for kwargs, expect in (
        ({"start_date": date_type(2026, 1, 1)}, ("2026-01-01", None)),
        ({"end_date": date_type(2026, 3, 31)}, (None, "2026-03-31")),
        ({"start_date": date_type(2026, 1, 1), "end_date": date_type(2026, 3, 31)},
         ("2026-01-01", "2026-03-31")),
    ):
        _, service = _call([_bill()], **kwargs)
        kw = service.read_paginated.call_args.kwargs
        assert (kw["start_date"], kw["end_date"]) == expect, kwargs


def test_omitted_date_args_do_not_leak_the_query_sentinel():
    """Codex P3 — the previous version passed `start_date=None` EXPLICITLY, so
    removing the isinstance guards still passed it. Calling the route without
    those arguments is what exercises the unresolved `Query(None)` default that
    an in-process caller actually sees."""
    service = MagicMock()
    service.read_paginated.return_value = ([], 0)
    repo = MagicMock(); repo.read_first_line_item_projects.return_value = {}
    review_repo = MagicMock(); review_repo.read_current_by_bill_ids.return_value = {}
    vendor_repo = MagicMock(); vendor_repo.read_public_ids_by_ids.return_value = {}
    with patch("entities.bill.api.router.BillService", return_value=service), \
         patch("entities.bill.api.router.BillRepository", return_value=repo), \
         patch("entities.bill.api.router.VendorRepository", return_value=vendor_repo), \
         patch("entities.bill.api.router.get_connection", return_value=MagicMock()), \
         patch("entities.review.persistence.repo.ReviewRepository", return_value=review_repo):
        asyncio.run(get_bills_router(current_user=USER))   # everything else defaulted
    kw = service.read_paginated.call_args.kwargs
    assert kw["start_date"] is None and kw["end_date"] is None
    assert kw["status"] is None


def test_date_bounds_reach_sql():
    """`@StartDate`/`@EndDate` have been on ReadBillsPaginated since it was
    written, and the repo has always threaded them — only the HTTP door was
    shut. Filtering must happen in SQL for the same reason `status` does:
    narrowing the page in Python would leave `count` describing the unfiltered
    set, which is the bug U-447 removed."""
    from datetime import date as date_type

    _, service = _call([_bill()], start_date=date_type(2026, 1, 1),
                       end_date=date_type(2026, 3, 31))
    kw = service.read_paginated.call_args.kwargs
    assert kw["start_date"] == "2026-01-01"
    assert kw["end_date"] == "2026-03-31"






# ---------------------------------------------------------------------------
# U-446 — IsDraft is unwritable
# ---------------------------------------------------------------------------


def test_NO_sproc_assigns_IsDraft_anywhere():
    """The pin that stops this regressing.

    U-445 had four sprocs obliged to keep Status and IsDraft in step, with
    CK_Bill_Status_IsDraft to catch the moment one forgot. U-446 deleted that
    whole category by deriving the column — so a future edit that reintroduces
    an assignment does not "break the constraint", it fails at execution with
    "the column IsDraft cannot be modified". Catch it here instead.
    """
    from tests.sproc_text import REPO_ROOT

    import re

    for rel in ("entities/bill/sql/dbo.bill.sql",
                "entities/bill/sql/dbo.bill_create_source_email.sql"):
        sql = (REPO_ROOT / rel).read_text()
        executable = "\n".join(l.split("--")[0] for l in sql.splitlines())
        # Only ASSIGNMENTS are illegal. Comparisons are fine and still used:
        # `b.[IsDraft] = @IsDraft` in the list filters, `WHERE [IsDraft] = 1` in
        # FindBillForReviewerReply — reading a computed column is transparent.
        set_lines = [
            l for l in executable.splitlines()
            if re.search(r"(SET\s+\[IsDraft\]|^\s*\[IsDraft\]\s*=)", l)
        ]
        assert not set_lines, f"{rel} assigns IsDraft: {set_lines}"
        insert_lists = re.findall(r"INSERT INTO dbo\.\[Bill\](.*?)(?:OUTPUT|VALUES)",
                                  executable, re.S)
        for lst in insert_lists:
            assert "[IsDraft]" not in lst, f"{rel} INSERTs into IsDraft (error 271)"


def test_the_swap_is_idempotent_and_preserves_the_column_contract():
    """Base files are re-applied routinely, and this one drops a column."""
    from tests.sproc_text import REPO_ROOT

    sql = (REPO_ROOT / "entities/bill/sql/dbo.bill.sql").read_text()
    executable = "\n".join(l.split("--")[0] for l in sql.splitlines())

    # guarded on "not yet computed", so a second apply is a no-op
    assert "AND name = 'IsDraft'\n                 AND is_computed = 0" in executable

    # NOT NULL is load-bearing: without it SQL Server marks a computed column
    # NULLABLE (it will not prove a CASE total), silently widening
    # `BIT NOT NULL` to `BIT NULL` for every reader across 9 entities.
    assert ") PERSISTED NOT NULL;" in executable

    # the system-named default must be looked up, never hardcoded
    assert "sys.default_constraints" in executable
    assert "DF__Bill__IsDraft" not in executable, "the auto-generated name must not be hardcoded"

    # all three blockers dropped before the column
    drop_col = executable.index("DROP COLUMN [IsDraft]")
    for blocker in ("DROP INDEX [IX_Bill_Status]",
                    "DROP CONSTRAINT [CK_Bill_Status_IsDraft]",
                    "EXEC sp_executesql @DropDefaultSql"):
        assert executable.index(blocker) < drop_col, f"{blocker} must precede the drop"

    # and the index comes back without the computed column in its INCLUDE
    after = executable[drop_col:]
    assert "INCLUDE ([VendorId], [BillDate])" in after


def test_nothing_anywhere_writes_Bill_IsDraft():
    """Repo-wide guard, and the pattern is deliberately BROAD.

    My own U-446 enumeration searched `INSERT INTO dbo.[Bill]` and
    `SET [IsDraft]` — bracketed forms only — and reported the write surface
    closed. Codex then found three writers it had missed, all spelled
    `INSERT dbo.Bill (...)`: two spent contract-labor repair scripts and the
    Tier-0 verification harness. Each would now fail with SQL Server error 271
    ("cannot be specified in an INSERT list") the moment it ran.

    So this matches Bill INSERTs with or without INTO, with or without brackets,
    and Bill UPDATEs that assign the column — the shape of the mistake, not the
    shape I happened to grep for.
    """
    import re

    from tests.sproc_text import REPO_ROOT

    SKIP_DIRS = {".venv", ".git", "__pycache__", "node_modules", "dist"}
    # Other entities still have a REAL IsDraft column and legitimately write it.
    OTHER = re.compile(r"Bill(Line|Credit|Folder|Payment|Completion)", re.I)

    insert_bill = re.compile(
        r"INSERT\s+(?:INTO\s+)?(?:\[?dbo\]?\.)?\[?Bill\]?\s*\((?P<cols>[^)]*)\)",
        re.I | re.S,
    )
    update_bill = re.compile(
        r"UPDATE\s+(?:TOP\s*\([^)]*\)\s*)?(?:\[?dbo\]?\.)?\[?Bill\]?\b(?P<body>.*?)"
        r"(?:\bWHERE\b|\bOUTPUT\b|\bFROM\b|$)",
        re.I | re.S,
    )
    # An ASSIGNMENT has a distinct shape: it opens a clause, so it sits at the
    # start of a line or immediately after SET. A COMPARISON sits mid-expression
    # (`WHEN b.[IsDraft] = 0`, `OR b.[IsDraft] = @IsDraft`) and is legal —
    # reading a computed column is transparent, and the list filters and the
    # U-445 backfill both still do it. An earlier version of this pattern
    # matched `@IsDraft = 0` inside the compat CASE and flagged three of its
    # own file's correct lines.
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
            continue  # in-memory SQLite fixtures define their own dbo.Bill
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "IsDraft" not in text:
            continue
        executable = "\n".join(l.split("--")[0] for l in text.splitlines())

        for m in insert_bill.finditer(executable):
            if OTHER.search(m.group(0)):
                continue
            if re.search(r"\[?IsDraft\]?", m.group("cols"), re.I):
                offenders.append(f"{rel}: INSERT names IsDraft (SQL error 271)")

        for m in update_bill.finditer(executable):
            if OTHER.search(m.group(0)):
                continue
            for line in m.group("body").splitlines():
                if assigns_isdraft.search(line):
                    offenders.append(f"{rel}: UPDATE assigns IsDraft -> {line.strip()[:60]}")

    assert not offenders, (
        "Bill.IsDraft is a PERSISTED COMPUTED column since U-446 and cannot be "
        f"written: {offenders}"
    )


def test_a_second_apply_does_not_resurrect_the_dropped_constraint():
    """Codex P2. U-446 drops CK_Bill_Status_IsDraft for good — it is tautological
    once IsDraft derives from Status. But U-445's creation block runs EARLIER in
    the same file, and it was guarded only on "the constraint is absent". A
    second apply therefore recreated it: legal on a persisted computed column,
    so it came back silently, and re-validated 20,266 rows every time the file
    was re-run.

    The creation block is now additionally guarded on IsDraft still being a REAL
    column, which is false forever after the swap.
    """
    from tests.sproc_text import REPO_ROOT

    sql = (REPO_ROOT / "entities/bill/sql/dbo.bill.sql").read_text()
    executable = "\n".join(l.split("--")[0] for l in sql.splitlines())

    create_ck = executable.index("ADD CONSTRAINT [CK_Bill_Status_IsDraft]")
    guard = executable[:create_ck]
    guard = guard[guard.rindex("IF OBJECT_ID('dbo.Bill', 'U') IS NOT NULL"):]
    assert "is_computed = 0" in guard, (
        "the CK creation must be gated on IsDraft still being a real column, or "
        "a re-apply brings the constraint back"
    )

    # and the drop still precedes nothing that would recreate it afterwards
    drop_ck = executable.index("DROP CONSTRAINT [CK_Bill_Status_IsDraft]")
    assert create_ck < drop_ck, "creation must come before the drop in file order"
    assert "ADD CONSTRAINT [CK_Bill_Status_IsDraft]" not in executable[drop_ck:]


def test_both_bill_sql_files_demand_an_atomic_apply():
    """Codex P1 — there is NO safe order for these two files on their own.

    `CreateBill` is homed in dbo.bill_create_source_email.sql; dbo.Bill's schema
    lives in dbo.bill.sql. Since U-446 they are coupled:

      schema first -> the still-old CreateBill names [IsDraft] in its INSERT
                      column list, so every POST /create/bill and every
                      bill-folder intake fails with SQL error 271.
      create first -> the new CreateBill writes Status only while IsDraft is
                      still REAL with DEFAULT 1, so a create-as-completed lands
                      Status='completed' beside IsDraft=1 and
                      CK_Bill_Status_IsDraft rejects it.

    scripts/run_sql.py commits per invocation, so this cannot be expressed as a
    file ordering — it has to be a deploy instruction, and it has to be where
    whoever applies these will see it.
    """
    from tests.sproc_text import REPO_ROOT

    for rel in ("entities/bill/sql/dbo.bill.sql",
                "entities/bill/sql/dbo.bill_create_source_email.sql"):
        head = (REPO_ROOT / rel).read_text()[:2000]
        # Whitespace-normalised: the banner wraps, so the phrase spans a line
        # break and comment prefix.
        flat = " ".join(head.replace("--", " ").split())
        assert "ONE** TRANSACTION" in flat, (
            f"{rel} must carry the atomic-apply warning in its first 2000 chars — "
            "a deploy warning buried mid-file is not a warning"
        )
        assert "271" in flat, f"{rel} must name the failure mode"
