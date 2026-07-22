"""Filename hint tests for business license detection in vendor folder scans."""
import pytest

from entities.vendor_compliance.business.folder_helpers import is_bl_hint


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("BL - Woodworks LLC.pdf", True),
        ("Eco-Lyfe, LLC - BL 05-15-2026.pdf", True),
        ("Business License 2026.pdf", True),
        ("WC - A&A - COI.pdf", False),
        ("A & A Masonry - W-9.pdf", False),
        ("", False),
    ],
)
def test_is_bl_hint(filename, expected):
    assert is_bl_hint(filename) is expected
