"""U-535b b1: tier-A matcher (synthetic inventory and bills only)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from entities.attachment_backfill.business.matcher import (
    BillRow,
    InventoryFile,
    match_tier_a,
)
from entities.attachment_backfill.business.project_map import ProjectMap, ProjectRef

PROJECTS = ProjectMap(
    [
        ProjectRef(id=10, name="ABC1 - 1200 Elm Street"),
        ProjectRef(id=11, name="ABC1-GUEST - 1200 Elm Street guest"),
        ProjectRef(id=20, name="XYZ9 - 5 Oak"),
    ]
)
FOLDER = "ABC1 - 1200 Elm Street"


def f(file_id, name, folder=FOLDER, size=None):
    return InventoryFile(file_id=file_id, name=name, folder_id="f1", project_folder_name=folder, size=size)


def bill(bill_id, number, amount, projects=(10,)):
    return BillRow(id=bill_id, bill_number=number, total_amount=Decimal(amount), project_ids=frozenset(projects))


def name(inv, amount, prefix="ABC1"):
    return f"{prefix} - Acme - {inv} - misc - {amount} - 1-2-2024.pdf"


def test_exact_match_is_candidate():
    r = match_tier_a([f("a", name("INV-0042", "$1,000.00"))], [bill(1, "inv-0042", "1000.00")], PROJECTS)
    assert [(c.bill_id, c.file_id, c.tier) for c in r.candidates] == [(1, "a", "A")]
    assert r.counts()["candidates"] == 1


def test_amount_must_be_exact():
    r = match_tier_a([f("a", name("42", "$1,000.01"))], [bill(1, "42", "1000.00")], PROJECTS)
    assert not r.candidates and r.excluded["no_match"] == 1


def test_invoice_normalization_leading_zeros_and_punctuation():
    r = match_tier_a([f("a", name("000-42", "$5.00"))], [bill(1, "42", "5.00")], PROJECTS)
    assert len(r.candidates) == 1


def test_project_must_be_on_the_bill():
    r = match_tier_a([f("a", name("42", "$5.00"))], [bill(1, "42", "5.00", projects=(20,))], PROJECTS)
    assert not r.candidates and r.excluded["no_match"] == 1


@pytest.mark.parametrize("amount", ["($250.00)", "-$250.00"])
def test_credit_files_are_excluded_not_matched(amount):
    r = match_tier_a([f("a", name("CM1", amount))], [bill(1, "CM1", "250.00")], PROJECTS)
    assert not r.candidates and r.excluded["credit"] == 1


def test_sign_must_agree_with_the_bill():
    r = match_tier_a([f("a", name("CM1", "($250.00)"))], [bill(1, "CM1", "-250.00")], PROJECTS)
    assert not r.candidates
    r = match_tier_a([f("a", name("CM1", "$250.00"))], [bill(1, "CM1", "-250.00")], PROJECTS)
    assert not r.candidates


def test_pushed_files_excluded():
    pushed = "ABC1 - Acme - 42 - misc - $5.00 - 1-2-2024-0123abcd.pdf"
    r = match_tier_a([f("a", pushed)], [bill(1, "42", "5.00")], PROJECTS)
    assert not r.candidates and r.excluded["pushed"] == 1


def test_unparsed_and_no_project_counted():
    r = match_tier_a(
        [f("a", "scan001.pdf"), f("b", name("42", "$5.00", prefix="ZZZ"), folder="NOPE - 1 Rd")],
        [bill(1, "42", "5.00")],
        PROJECTS,
    )
    assert r.excluded["unparsed"] == 1 and r.excluded["no_project"] == 1 and not r.candidates


def test_filename_subunit_prefix_project_matches():
    r = match_tier_a([f("a", name("42", "$5.00", prefix="ABC1-GUEST"))], [bill(2, "42", "5.00", projects=(11,))], PROJECTS)
    assert [c.bill_id for c in r.candidates] == [2]


def test_prefix_and_folder_project_both_count_so_two_bills_are_ambiguous():
    # Fix round 1 (item 8): either project matches, so a parent bill and a sub-unit bill with the
    # same invoice + amount make the file ambiguous rather than silently preferring one.
    bills = [bill(1, "42", "5.00", projects=(10,)), bill(2, "42", "5.00", projects=(11,))]
    r = match_tier_a([f("a", name("42", "$5.00", prefix="ABC1-GUEST"))], bills, PROJECTS)
    assert not r.candidates and r.excluded["ambiguous_file"] == 1


def test_ambiguous_file_two_bills_dropped():
    bills = [bill(1, "42", "5.00"), bill(2, "0042", "5.00")]
    r = match_tier_a([f("a", name("42", "$5.00"))], bills, PROJECTS)
    assert not r.candidates and r.excluded["ambiguous_file"] == 1


def test_contested_bill_two_files_both_dropped():
    files = [f("a", name("42", "$5.00")), f("b", name("42", "$5.00").replace("misc", "copy"))]
    r = match_tier_a(files, [bill(1, "42", "5.00")], PROJECTS)
    assert not r.candidates and r.excluded["contested_bill"] == 2


def test_bill_also_hit_by_an_ambiguous_file_is_contested():
    # File b matches bills 1 AND 2 (ambiguous). Bill 1 is therefore claimed by two files at
    # tier A, so file a's otherwise-unique match is dropped too (mutual uniqueness).
    bills = [bill(1, "42", "5.00", projects=(10, 11)), bill(2, "42", "5.00", projects=(11,))]
    files = [f("a", name("42", "$5.00")), f("b", name("42", "$5.00", prefix="ABC1-GUEST"))]
    r = match_tier_a(files, bills, PROJECTS)
    assert not r.candidates
    assert r.excluded["ambiguous_file"] == 1 and r.excluded["contested_bill"] == 1


def test_duplicate_listing_of_same_file_id_counted_once():
    files = [f("a", name("42", "$5.00")), f("a", name("42", "$5.00"))]
    r = match_tier_a(files, [bill(1, "42", "5.00")], PROJECTS)
    assert len(r.candidates) == 1 and r.excluded["duplicate_listing"] == 1


def test_bills_without_number_or_amount_never_match():
    bills = [BillRow(id=1, bill_number=None, total_amount=Decimal("5.00"), project_ids=frozenset({10})),
             BillRow(id=2, bill_number="42", total_amount=None, project_ids=frozenset({10}))]
    r = match_tier_a([f("a", name("42", "$5.00"))], bills, PROJECTS)
    assert not r.candidates


def test_counts_cover_every_file():
    files = [
        f("a", name("42", "$5.00")),
        f("b", "junk.pdf"),
        f("c", name("99", "$1.00")),
    ]
    c = match_tier_a(files, [bill(1, "42", "5.00")], PROJECTS).counts()
    excluded = sum(v for k, v in c.items() if k not in ("files_seen", "candidates"))
    assert c["files_seen"] == 3 and c["candidates"] + excluded == 3


def test_same_size_copies_collapse_to_lowest_box_id():
    n = "ABC1 - Acme - 42 - misc - $5.00 - 1-2-2024.pdf"
    files = [f("900", n, size=111), f("12", n.replace(".pdf", " (1).pdf"), size=111)]
    r = match_tier_a(files, [bill(1, "42", "5.00")], PROJECTS)
    assert [c.file_id for c in r.candidates] == ["12"]
    assert r.excluded["duplicate_copy"] == 1 and r.excluded["contested_bill"] == 0


def test_different_size_copies_stay_contested():
    n = "ABC1 - Acme - 42 - misc - $5.00 - 1-2-2024.pdf"
    r = match_tier_a([f("900", n, size=111), f("12", n, size=222)], [bill(1, "42", "5.00")], PROJECTS)
    assert not r.candidates and r.excluded["contested_bill"] == 2


def test_three_head_segments_match_with_plain_tier_a():
    r = match_tier_a([f("a", "ABC1 - Acme - 42 - $5.00 - 1-2-2024.pdf")], [bill(1, "42", "5.00")], PROJECTS)
    assert [(c.bill_id, c.tier) for c in r.candidates] == [(1, "A")]


def test_bill_on_folder_project_matches_when_prefix_resolves_elsewhere():
    r = match_tier_a([f("a", name("42", "$5.00", prefix="ABC1-GUEST"))], [bill(1, "42", "5.00", projects=(10,))], PROJECTS)
    assert [c.bill_id for c in r.candidates] == [1]


def test_numeric_description_segment_never_yields_a_candidate():
    n = "ABC1 - Smith - Jones LLC - 42 - misc - $5.00 - 1-2-2024.pdf"
    r = match_tier_a([f("a", n)], [bill(1, "42", "5.00")], PROJECTS)
    assert not r.candidates and r.excluded["no_match"] == 1
    r = match_tier_a([f("a", n)], [bill(1, "42", "5.00"), bill(2, "042", "5.00")], PROJECTS)
    assert not r.candidates


def test_alpha_prefix_alternate_key_removed():
    import entities.attachment_backfill.business.filename as filename_mod

    r = match_tier_a([f("a", "ABC1 - Acme - INV0012 - x - $5.00 - 1-2-2024.pdf")], [bill(1, "0012", "5.00")], PROJECTS)
    assert not r.candidates
    assert not hasattr(filename_mod, "alt_norm_invoice")
