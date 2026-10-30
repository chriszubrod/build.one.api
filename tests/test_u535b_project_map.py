"""U-535b b1: Box folder / filename prefix -> project mapping (synthetic projects only)."""
from __future__ import annotations

from entities.attachment_backfill.business.project_map import (
    ProjectMap,
    ProjectRef,
    folder_token,
    project_abbreviation,
    street_number,
)

PROJECTS = [
    ProjectRef(id=1, name="ABC1 - 1200 Elm Street", abbreviation=None),
    ProjectRef(id=2, name="Something else entirely", abbreviation="xyz9"),
    ProjectRef(id=3, name="DUP - 100 Oak Ave", abbreviation=None),
    ProjectRef(id=4, name="DUP - 2450B Pine Rd", abbreviation=None),
    ProjectRef(id=5, name="ABC1-GUEST - 1200 Elm Street guest house", abbreviation=None),
]


def test_abbreviation_prefers_column_then_name_prefix():
    assert project_abbreviation(PROJECTS[0]) == "ABC1"
    assert project_abbreviation(PROJECTS[1]) == "XYZ9"


def test_folder_token_strips_year_prefix():
    assert folder_token("2023--4--ABC1 - 1200 Elm Street") == "ABC1"
    assert folder_token("XYZ9 Main") == "XYZ9"
    assert folder_token("") == ""


def test_street_number():
    assert street_number("2450B Pine Rd") == "2450B"
    assert street_number("ABC1 no number") is None
    assert street_number("unit 12") is None


def test_unique_abbreviation_maps():
    m = ProjectMap(PROJECTS)
    assert m.map_folder("ABC1 - 1200 Elm Street") == 1
    assert m.map_folder("2022--1--xyz9 whatever") == 2


def test_ambiguous_abbreviation_broken_by_street_number():
    m = ProjectMap(PROJECTS)
    assert m.map_folder("DUP - 2450B Pine") == 4
    assert m.map_folder("DUP 100 Oak") == 3


def test_ambiguous_without_street_number_is_unmapped():
    m = ProjectMap(PROJECTS)
    assert m.map_folder("DUP - Pine") is None
    assert m.map_folder("DUP - 9999 Nowhere") is None


def test_unknown_folder_unmapped():
    assert ProjectMap(PROJECTS).map_folder("NOPE - 1 Road") is None


def test_filename_prefix_resolves_sub_unit_independently_of_folder():
    m = ProjectMap(PROJECTS)
    assert m.map_folder("ABC1 - 1200 Elm Street") == 1
    assert m.resolve_prefix("ABC1-GUEST") == 5
    assert m.resolve_prefix("abc1-guest") == 5


def test_unresolvable_or_ambiguous_prefix_is_none():
    m = ProjectMap(PROJECTS)
    assert m.resolve_prefix("ABC1 GUEST typo") is None
    assert m.resolve_prefix("DUP") is None  # ambiguous prefix never resolves
    assert m.resolve_prefix("NOPE") is None
    assert m.resolve_prefix("") is None and m.resolve_prefix(None) is None


def test_map_folder_is_memoized(monkeypatch):
    m = ProjectMap(PROJECTS)
    calls = []
    real = m._map_folder
    monkeypatch.setattr(m, "_map_folder", lambda name: calls.append(name) or real(name))
    assert m.map_folder("DUP - 2450B Pine") == 4
    assert m.map_folder("DUP - 2450B Pine") == 4
    assert calls == ["DUP - 2450B Pine"]
    assert not hasattr(m, "resolve")
