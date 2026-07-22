"""Filename heuristics for W-9 vs other vendor compliance documents."""
import pytest

from entities.vendor_compliance.business.folder_helpers import is_w9_hint


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("A & A Masonry - W-9.pdf", True),
        ("w9 form.pdf", True),
        ("WC - COI.pdf", False),
        ("", False),
    ],
)
def test_is_w9_hint(filename, expected):
    assert is_w9_hint(filename) is expected
