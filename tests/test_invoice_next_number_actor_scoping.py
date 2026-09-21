"""get_next_invoice_number must thread the caller's actor to the scoped list sproc.

Bug (found 2026-09-21 running the EVR-20 draw): `InvoiceService.get_next_invoice_
number` called `self.repo.read_paginated(...)` directly and passed neither
`actor_user_id` nor `actor_is_system_admin`. Those forward to `ReadInvoicesPaginated`,
whose WHERE clause filters on
`dbo.UserCanAccessProject(@ActorUserId, @ActorIsSystemAdmin, i.[ProjectId]) = 1`
— and that UDF FAILS CLOSED on NULL/NULL.

Verified against prod, project 42 ('EVR - 6315 E Valley Rd', 20 invoices):
    SELECT dbo.UserCanAccessProject(NULL, NULL, 42)                        -> False
    EXEC ReadInvoicesPaginated @ProjectId=42,@ActorUserId=17,@ActorIsSystemAdmin=1 -> 20 rows
    EXEC ReadInvoicesPaginated @ProjectId=42  (no actor)                   -> 0 rows

So the `max_num` loop iterated an empty list for EVERY project and the method
handed back `{PREFIX}-1` — a number that collides with an existing invoice. It
returned "EVR-1" for a project whose latest draw was EVR-19.

MEASURED RED (restore the pre-fix `self.repo.read_paginated(...)` body to re-check):
5 of the 8 tests below fail — `continues_the_sequence`, `ignores_other_prefixes`,
and `runs_under_system_authz` fail with the literal bug symptom (`'ABC-1'` where the
next number was expected); `passes_actor_to_the_scoped_read` and
`excludes_other_projects` fail on the missing kwarg. The other 3 are deliberately
NOT regression guards and stay green either way: `starts_at_one` asserts documented
empty-project behaviour (its expected value IS "ABC-1", indistinguishable from the
bug), `truncation` and `raises_when_project_missing` guard adjacent contracts.

`test_list_sproc_scoping.py` guards the other half — that the sproc keeps its actor
params and UDF filter in the base file. The completeness of this read additionally
rests on `ReadProjectByPublicId`'s predicate being a strict subset of
`UserCanAccessProject`'s; see the comment at the call site.
"""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from shared.authz import current_is_system_admin, current_user_id, system_authz
from entities.invoice.business.service import InvoiceService

PROJECT_ID = 42
OTHER_PROJECT_ID = 99


@contextmanager
def _actor(user_id, is_system_admin):
    """Set the auth ContextVars for the block, restoring them afterwards."""
    t1 = current_user_id.set(user_id)
    t2 = current_is_system_admin.set(is_system_admin)
    try:
        yield
    finally:
        current_user_id.reset(t1)
        current_is_system_admin.reset(t2)


class FailClosedInvoiceRepo:
    """Stands in for ReadInvoicesPaginated.

    Models the two behaviours of the real sproc that this test cares about:

    1. FAIL-CLOSED ON A MISSING ACTOR. `dbo.UserCanAccessProject` returns 0 unless
       `@ActorIsSystemAdmin = 1` OR `Project.CreatedByUserId = @ActorUserId` OR a
       `UserProject(@ActorUserId, @ProjectId)` row exists; with NULL/NULL it is 0
       and the sproc yields no rows.

       NB this fake treats *any* user id as sufficient, which the real UDF does
       NOT — membership is unmodelled here. That is deliberate and safe only
       because `get_next_invoice_number` resolves the project through the
       actor-scoped `ReadProjectByPublicId` first and raises before reaching this
       read, so a member-less caller never gets this far. Do not add a test to
       this file that depends on the membership dimension; it would be vacuous.

    2. PROJECT SCOPING. The sproc filters `@ProjectId IS NULL OR i.ProjectId = @ProjectId`,
       so a caller that forgets `project_id` would see other projects' numbers.
    """

    def __init__(self, invoices):
        self._invoices = invoices
        self.calls = []

    def read_paginated(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("actor_user_id") is None and not kwargs.get("actor_is_system_admin"):
            return []
        project_id = kwargs.get("project_id")
        rows = [
            i for i in self._invoices
            if project_id is None or i.project_id == project_id
        ]
        return rows[: kwargs.get("page_size") or len(rows)]


def _service(invoice_numbers, *, abbreviation="ABC", other_project_numbers=()):
    rows = [
        SimpleNamespace(invoice_number=n, project_id=PROJECT_ID)
        for n in invoice_numbers
    ] + [
        SimpleNamespace(invoice_number=n, project_id=OTHER_PROJECT_ID)
        for n in other_project_numbers
    ]
    repo = FailClosedInvoiceRepo(rows)
    service = InvoiceService(repo=repo)
    service.project_service = SimpleNamespace(
        read_by_public_id=lambda public_id: SimpleNamespace(
            id=PROJECT_ID, abbreviation=abbreviation
        )
    )
    return service, repo


def test_next_number_continues_the_sequence():
    """The regression: ABC-01..ABC-19 present -> ABC-20, never ABC-1."""
    service, _ = _service([f"ABC-{n:02d}" for n in range(1, 20)])

    with _actor(17, False):
        assert service.get_next_invoice_number("proj-public-id") == "ABC-20"


def test_next_number_passes_actor_to_the_scoped_read():
    """Direct guard on the omission itself, independent of the returned string."""
    service, repo = _service(["ABC-01"])

    with _actor(17, False):
        service.get_next_invoice_number("proj-public-id")

    assert len(repo.calls) == 1
    call = repo.calls[0]
    assert call["actor_user_id"] == 17
    assert call["actor_is_system_admin"] is False
    assert call["project_id"] == PROJECT_ID


def test_next_number_excludes_other_projects():
    """The sequence is per-project: another project's higher numbers must not count."""
    service, _ = _service(["ABC-01", "ABC-02"], other_project_numbers=["ABC-90"])

    with _actor(17, False):
        assert service.get_next_invoice_number("proj-public-id") == "ABC-3"


def test_next_number_reads_one_unfiltered_page_big_enough_to_hold_the_max():
    """`page_size` and `is_draft` are load-bearing, not incidental.

    The sproc's default page is 50 rows sorted `InvoiceDate DESC`, so falling back
    to the default would compute max() over a partial set and could understate it
    — the same colliding-number failure by a different route. A draft filter would
    do the same by hiding rows that already hold a number.
    """
    service, repo = _service(["ABC-01"])

    with _actor(17, False):
        service.get_next_invoice_number("proj-public-id")

    call = repo.calls[0]
    assert call["page_size"] == 10000
    assert call.get("is_draft") is None
    assert call.get("search_term") is None


def test_next_number_starts_at_one_when_project_has_no_invoices():
    """Documented behaviour for a genuinely empty project (not a regression guard)."""
    service, _ = _service([])

    with _actor(17, False):
        assert service.get_next_invoice_number("proj-public-id") == "ABC-1"


def test_next_number_ignores_other_prefixes_and_suffixed_duplicates():
    """Regex stays strict: only exact `{PREFIX}-{digits}` rows advance the max.

    Guards KI-5 (suffixed duplicate invoice numbers like ABC-07-2) and the
    legacy-prefix rows real projects carry — EVR's own history opens with
    EVD-01..EVD-03 before switching to EVR-04. `abc-04` covers the re.IGNORECASE
    branch, which a "tighten the regex" cleanup would otherwise drop silently.
    """
    service, _ = _service(["ABC-01", "ABC-07-2", "XYZ-99", "abc-04", "ABC-4x"])

    with _actor(17, False):
        assert service.get_next_invoice_number("proj-public-id") == "ABC-5"


def test_next_number_runs_under_system_authz():
    """Scheduler / CLI paths carry no user id but do carry the system-admin bit."""
    service, repo = _service([f"ABC-{n:02d}" for n in range(1, 20)])

    with system_authz():
        assert service.get_next_invoice_number("proj-public-id") == "ABC-20"

    assert repo.calls[0]["actor_is_system_admin"] is True


def test_next_number_raises_when_the_project_does_not_resolve():
    """The project gate fires before the invoice read — a member-less caller gets
    a ValueError (HTTP 400), never a silently colliding number."""
    service, repo = _service(["ABC-01"])
    service.project_service = SimpleNamespace(read_by_public_id=lambda public_id: None)

    with _actor(17, False):
        with pytest.raises(ValueError, match="not found"):
            service.get_next_invoice_number("proj-public-id")

    assert repo.calls == []
