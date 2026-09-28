"""The next invoice number continues the project's OWN series, not its abbreviation.

Separate from test_invoice_next_number_actor_scoping.py on purpose: that file's
docstring makes a precise MEASURED-RED claim tied to restoring one specific line
(`self.repo.read_paginated(...)`), and folding a second, independent defect's
guards under that claim would make it ambiguous which mutation each test kills.

THE DEFECT: `prefix = (project.abbreviation or "INV").upper()`. Measured on prod
2026-09-28 — `Project.Abbreviation` is NULL on 82 of the 106 projects that have
invoices, so all 82 fell back to "INV", which matches 0 of 1,012 existing invoice
numbers, so max() found nothing and every one of those live client-draw sequences
restarted at "INV-1". Nothing bad was written because this endpoint only
SUGGESTS a number; the exposure was the next person to accept the suggestion.

MEASURED RED: with the exact pre-fix method body restored in-process, 11 of the
15 tests below fail. The two that pass either way are the two padding-width
contract guards, and say so in their own docstring.

Replayed over all 1,012 prod invoice rows, the implementation these tests pin
repairs the NULL-abbreviation projects with 0 in-project collisions, and
leaves the 24 abbreviation-bearing projects on the number they return today. The other 3 (19
MOR-A-Permit, 20 MOR-B-Permit, 54 'MR - Payment #8') have neither a parseable
number nor an abbreviation — a data gap, pinned by the INV fallback test below.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

# tests/ on sys.path for the cross-module import below. Explicit rather than
# relying on another test module having already inserted it — alphabetical
# collection order is not a contract.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import actor_context  # noqa: E402
from test_invoice_next_number_actor_scoping import (  # noqa: E402
    OTHER_PROJECT_ID,
    PROJECT_ID,
    FailClosedInvoiceRepo,
)
from entities.invoice.business.service import InvoiceService  # noqa: E402


def _service(numbers, *, abbreviation=None, other_project_numbers=()):
    """Invoices are supplied OLDEST FIRST and given explicit ascending dates+ids,
    so 'newest' is stated by the fixture rather than inherited from sort
    stability — the series choice depends on it."""
    rows = [
        SimpleNamespace(
            invoice_number=n, project_id=PROJECT_ID,
            invoice_date=f"2024-01-{i + 1:02d}", id=i + 1,
        )
        for i, n in enumerate(numbers)
    ] + [
        SimpleNamespace(
            invoice_number=n, project_id=OTHER_PROJECT_ID,
            invoice_date="2023-01-01", id=900 + i,
        )
        for i, n in enumerate(other_project_numbers)
    ]
    repo = FailClosedInvoiceRepo(rows)
    service = InvoiceService(repo=repo)
    service.project_service = SimpleNamespace(
        read_by_public_id=lambda public_id: SimpleNamespace(
            id=PROJECT_ID, abbreviation=abbreviation
        )
    )
    return service


def _next(numbers, **kw):
    with actor_context(17, False):
        return _service(numbers, **kw).get_next_invoice_number("p")


def test_continues_the_series_when_the_abbreviation_is_null():
    """The regression, minimal form: 82 prod projects look exactly like this."""
    assert _next(["OL2-01"]) == "OL2-02"


def test_continues_the_newest_series_after_a_prefix_change():
    """Project 42 (EVR) verbatim: EVD-01..03 then EVR-04..20."""
    numbers = [f"EVD-{n:02d}" for n in range(1, 4)] + [
        f"EVR-{n:02d}" for n in range(4, 21)
    ]
    assert _next(numbers) == "EVR-21"


def test_opens_the_newest_series_even_when_the_older_one_is_larger():
    """Project 16 (BMB) verbatim, and the true latest-wins discriminator: the
    newest series has ONE member and the abandoned one has 34, so any
    implementation that picks the biggest series rather than the newest fails
    here while passing every other test in this file."""
    numbers = [f"BMB-{n:02d}" for n in range(1, 35)] + ["BMB-POST-01"]
    assert _next(numbers) == "BMB-POST-02"


def test_the_abbreviation_wins_over_a_newer_data_series():
    """Project 48 (HE7). The abbreviation is AUTHORITATIVE when it names a live
    series, so this project keeps the number it returns today.

    An earlier draft of this fix did the opposite — preferred the newest data
    series on the theory that the humans had visibly moved on — and review
    proved that unsafe: see the DRAFT-placeholder test below. A placeholder and
    an intentional new series are the same shape, so the abbreviation is the
    only signal that separates them, and guessing wrong invents a parallel
    series while the real one stalls. Projects HE7/ML/HE12 therefore stay put
    and this fix changes nothing for the 24 abbreviation-bearing projects except
    padding."""
    numbers = [f"HE7-{n:02d}" for n in range(1, 28)] + ["HE7-POST-01", "HE7-POST-02"]
    assert _next(numbers, abbreviation="HE7") == "HE7-28"


def test_a_draft_placeholder_cannot_hijack_the_series():
    """Prod regression, caught in review. Projects 18/13/23/73 hold HP-DRAFT-23,
    HP2-DRAFT-07, OL-DRAFT-10 and TB3-DRAFT-17 (all written 2026-02-27), and
    project 76 holds HE12-Initial-2.

    'HP-DRAFT-23' parses as {HP-DRAFT}-{23} and is its own only sibling, so a
    purely data-derived prefix picks 'HP-DRAFT' unopposed and emits HP-DRAFT-24.
    The human issued HP-23 on 2026-03-31. The rows are still in prod, so the
    next placeholder anyone types re-arms this."""
    numbers = [f"HP-{n:02d}" for n in range(1, 23)] + ["HP-DRAFT-23"]
    assert _next(numbers, abbreviation="HP") == "HP-23"


def test_a_tie_on_series_size_picks_the_shorter_prefix():
    """Project 40 (SD2) shape. Both candidates for the newest number have
    exactly one sibling — 'SD2' (SD2-01) and 'SD2-02' (SD2-02-2 matching
    itself) — and longest-on-tie would open a bogus 'SD2-02-NN' series. 48 prod
    numbers carry a KI-5 dedupe suffix, so this tie is reachable."""
    assert _next(["SD2-01", "SD2-01-2", "SD2-02-2"]) == "SD2-02"


def test_ignores_a_dedupe_suffix_when_picking_the_series():
    """KI-5 dedupe suffix. 'HP2-12-3' must read as HP2 #12's third copy, not as
    series 'HP2-12' — resolved by series size (12 siblings vs 0)."""
    numbers = [f"HP2-{n:02d}" for n in range(1, 13)] + ["HP2-12-3"]
    assert _next(numbers) == "HP2-13"


def test_keeps_a_prefix_that_ends_in_a_digit():
    """The opposite reading of the same ambiguity, also live in prod: 'ONT-1-14'
    IS series ONT-1 number 14. Syntactically indistinguishable from the test
    above, so series size is what separates them (8 siblings vs 0)."""
    numbers = [f"ONT-1-{n:02d}" for n in range(7, 15)]
    assert _next(numbers) == "ONT-1-15"


def test_zero_pads_to_the_series_width():
    """942 of 960 parseable prod numbers are 2-wide; the old code was the only
    thing that ever emitted a bare single digit, which also string-sorts AFTER
    '-10' in the web list and in SharePoint folder names."""
    assert _next([f"ABC-{n:02d}" for n in range(1, 9)], abbreviation="ABC") == "ABC-09"


def test_does_not_pad_past_the_series_width():
    """GREEN both sides — contract guard, not a regression guard."""
    assert _next(["ABC-09"], abbreviation="ABC") == "ABC-10"


def test_widens_past_two_digits_when_the_series_already_has():
    """GREEN both sides apart from padding — guards against a hardcoded zfill(2)
    truncating or re-padding a series that has outgrown two digits. Max prod tail
    is 37 today, so this is forward cover, not a current case."""
    assert _next(["ABC-099"], abbreviation="ABC") == "ABC-100"


def test_falls_back_to_the_abbreviation_when_no_number_parses():
    """Project 19 (MOR-A-Permit): unparseable number, abbreviation present."""
    assert _next(["MOR-A-Permit"], abbreviation="MOR-A") == "MOR-A-01"


def test_falls_back_to_INV_only_with_neither_data_nor_abbreviation():
    """Documents the 3 prod projects the fix CANNOT repair — the remedy there is
    to set Project.Abbreviation, a data change, not more code."""
    assert _next(["MOR-A-Permit"]) == "INV-01"


def test_does_not_borrow_another_projects_series():
    """The derivation must read only the actor-scoped, project-scoped page — a
    foreign project's numbers must not supply the prefix OR the max."""
    assert _next(["WL-01"], other_project_numbers=["ABC-90"]) == "WL-02"


def test_ignores_a_whitespace_only_abbreviation():
    """Latent branch, 0 prod instances: '   ' is truthy, so the old `or "INV"`
    let it through and emitted '  -1'."""
    assert _next([], abbreviation="   ") == "INV-01"
