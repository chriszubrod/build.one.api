"""get_next_invoice_number must thread the caller's actor to the scoped list sproc.

`ReadInvoicesPaginated` filters on `dbo.UserCanAccessProject(@ActorUserId,
@ActorIsSystemAdmin, i.[ProjectId])`, which FAILS CLOSED on NULL/NULL. The pre-fix
body called `self.repo.read_paginated(...)` with no actor, so every project read
back empty and the method restarted a live sequence at `{PREFIX}-1`. Measured on
prod project 42 ('EVR - 6315 E Valley Rd', 20 invoices), 2026-09-21:

    EXEC ReadInvoicesPaginated @ProjectId=42,@ActorUserId=17,@ActorIsSystemAdmin=1 -> 20 rows
    EXEC ReadInvoicesPaginated @ProjectId=42  (no actor)                           -> 0 rows

MEASURED RED: restore the pre-fix `self.repo.read_paginated(...)` body and 5 of
these 7 go red — 4 with the literal `'ABC-1'` symptom, and the kwarg guard on the
missing `actor_user_id`. The two that stay green either way say so in their own
docstring: they are contract guards, not regression guards.

`test_list_sproc_scoping.py` guards the other half — that the sproc keeps its
actor params and UDF filter in its base file. This read's COMPLETENESS rests on a
separate subset relation, documented at the call site.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# tests/ must be on sys.path for `from conftest import ...` — this module sorts
# alphabetically before the test_qbo_* files that would otherwise have inserted it.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import actor_absent, actor_context  # noqa: E402
from shared.authz import system_authz
from entities.invoice.business.service import InvoiceService

PROJECT_ID = 42
OTHER_PROJECT_ID = 99


class FailClosedInvoiceRepo:
    """Stands in for ReadInvoicesPaginated, modelling its two relevant behaviours:
    it yields nothing when the actor is absent (the UDF's NULL/NULL path), and it
    honours `@ProjectId IS NULL OR i.ProjectId = @ProjectId`.

    NB this fake treats *any* user id as sufficient, which the real UDF does NOT —
    it also requires system-admin, project authorship, or a UserProject row, and
    membership is unmodelled here. That is deliberate and safe only because
    `get_next_invoice_number` resolves the project through the actor-scoped
    `ReadProjectByPublicId` first and raises before reaching this read, so a
    member-less caller never gets this far. Do not add a test to this file that
    depends on the membership dimension; it would be vacuous.
    """

    def __init__(self, invoices):
        self._invoices = invoices
        self.calls = []

    def read_paginated(self, **kwargs):
        self.calls.append(kwargs)
        if actor_absent(kwargs):
            return []
        project_id = kwargs.get("project_id")
        return [
            i for i in self._invoices
            if project_id is None or i.project_id == project_id
        ]


def _service(invoice_numbers, *, other_project_numbers=()):
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
            id=PROJECT_ID, abbreviation="ABC"
        )
    )
    return service, repo


def test_next_number_continues_the_sequence():
    """The regression: ABC-01..ABC-19 present -> ABC-20, never ABC-1."""
    service, _ = _service([f"ABC-{n:02d}" for n in range(1, 20)])

    with actor_context(17, False):
        assert service.get_next_invoice_number("proj-public-id") == "ABC-20"


def test_next_number_reads_one_project_scoped_page_with_the_callers_actor():
    """Actor threaded, project scoped, one page big enough to hold the max, and no
    draft/search filter hiding a number that is already taken.

    `page_size` is load-bearing, not incidental: the sproc's default page is 50
    rows sorted `InvoiceDate DESC`, so falling back to it would compute max() over
    a partial set — the same colliding-number failure by a different route.
    """
    service, repo = _service(["ABC-01"])

    with actor_context(17, False):
        service.get_next_invoice_number("proj-public-id")

    assert len(repo.calls) == 1
    call = repo.calls[0]
    assert call["actor_user_id"] == 17
    assert call["actor_is_system_admin"] is False
    assert call["project_id"] == PROJECT_ID
    assert call["page_size"] == 10000
    assert call.get("is_draft") is None
    assert call.get("search_term") is None


def test_next_number_excludes_other_projects():
    """The sequence is per-project: another project's higher numbers must not count."""
    service, _ = _service(["ABC-01", "ABC-02"], other_project_numbers=["ABC-90"])

    with actor_context(17, False):
        assert service.get_next_invoice_number("proj-public-id") == "ABC-3"


def test_next_number_ignores_other_prefixes_and_suffixed_duplicates():
    """Regex stays strict: only exact `{PREFIX}-{digits}` rows advance the max.

    Guards KI-5 (suffixed duplicate invoice numbers like ABC-07-2) and the
    legacy-prefix rows real projects carry — EVR's own history opens with
    EVD-01..EVD-03 before switching to EVR-04. `abc-04` covers the re.IGNORECASE
    branch, which a "tighten the regex" cleanup would otherwise drop silently.
    """
    service, _ = _service(["ABC-01", "ABC-07-2", "XYZ-99", "abc-04", "ABC-4x"])

    with actor_context(17, False):
        assert service.get_next_invoice_number("proj-public-id") == "ABC-5"


def test_next_number_runs_under_system_authz():
    """Scheduler / CLI paths carry no user id but do carry the system-admin bit."""
    service, repo = _service([f"ABC-{n:02d}" for n in range(1, 20)])

    with system_authz():
        assert service.get_next_invoice_number("proj-public-id") == "ABC-20"

    assert repo.calls[0]["actor_is_system_admin"] is True


def test_next_number_starts_at_one_when_project_has_no_invoices():
    """Contract guard, not a regression guard — documented empty-project behaviour,
    whose expected value IS "ABC-1" and so cannot distinguish the bug."""
    service, _ = _service([])

    with actor_context(17, False):
        assert service.get_next_invoice_number("proj-public-id") == "ABC-1"


def test_next_number_raises_when_the_project_does_not_resolve():
    """Contract guard, not a regression guard — the project gate fires before the
    invoice read, so a member-less caller gets a ValueError (HTTP 400) rather than
    a silently colliding number."""
    service, repo = _service(["ABC-01"])
    service.project_service = SimpleNamespace(read_by_public_id=lambda public_id: None)

    with actor_context(17, False):
        with pytest.raises(ValueError, match="not found"):
            service.get_next_invoice_number("proj-public-id")

    assert repo.calls == []
