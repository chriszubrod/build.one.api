"""U-676h: format_short_description table-driven tests."""
from __future__ import annotations

import pytest

from entities.bill.business.description_format import format_short_description


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, None),
        ("", None),
        ("   ", None),
        ("mowing", "Mowing"),
        ("biweekly mowing", "Biweekly Mowing"),
        ("  biweekly   mowing  ", "Biweekly Mowing"),
        ("Biweekly mowing service Oct 1-15 site", "Biweekly Mowing Service Oct 1-15"),
        ("one two three four five six seven eight", "One Two Three Four Five"),
        ('3/4" plywood', '3/4" Plywood'),
        ("PVC pipe", "PVC Pipe"),
        ("2x4 lumber", "2x4 Lumber"),
        ("Acme LLC invoice", "Acme LLC"),
        ("O'Neil roofing", "O'Neil Roofing"),
        ("re-roof main house", "Re-roof Main House"),
        ("café résumé", "Café Résumé"),
        ("parts,", "Parts"),
        ("parts!!!", "Parts"),
        (
            "Biweekly mowing service, Oct 1-15, 5224 Williamsburg",
            "Biweekly Mowing Service, Oct 1-15",
        ),
        ("one two three four five,", "One Two Three Four Five"),
        ("one two three four five;", "One Two Three Four Five"),
        ("one two three four five.", "One Two Three Four Five"),
        ('"plywood" purchase', '"Plywood"'),
        ("(misc) parts", "(Misc) Parts"),
        ("e.g. plywood etc.", "E.g. Plywood Etc."),
        ("Plywood purchase.", "Plywood"),
            ("Acme Inc.", "Acme Inc."),
            ("ÿes ßtraße", "Ÿes ßtraße"),
            ("parts…", "Parts"),
            ("x" * 80, "X" + "x" * 59),
            (
                "Comprehensive waterproofing installation, foundation-perimeter remediation services",
                "Comprehensive Waterproofing Installation",
            ),
    ],
    ids=[
        "none",
        "empty",
        "whitespace",
        "one_word",
        "two_words",
        "collapse_ws",
        "eight_words",
        "five_word_cap",
        "fraction_quote",
        "pvc",
        "2x4",
        "llc",
        "apostrophe",
        "hyphen",
        "unicode",
        "trailing_comma",
        "trailing_bang",
        "contract_long_fallback_words",
        "fifth_word_comma",
        "fifth_word_semicolon",
        "fifth_word_period",
        "quoted_plywood",
        "paren_misc",
        "abbrev_etc",
        "strip_sentence_period",
        "keep_inc_abbrev",
        "german_eszett",
        "unicode_ellipsis",
        "eighty_char_word",
        "long_title_sixty_cap",
    ],
)
def test_format_short_description(raw, expected):
    assert format_short_description(raw) == expected


def test_format_short_description_max_sixty_chars():
    result = format_short_description("word " * 20)
    assert result is not None
    assert len(result) <= 60
