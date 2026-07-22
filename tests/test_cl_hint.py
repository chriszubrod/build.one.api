"""Filename hint tests for contractors license detection in vendor folder scans."""
import pytest

from entities.vendor_compliance.business.folder_helpers import is_cl_hint


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("CL - State - Scales Electric LLC.pdf", True),
        ("CL - Metro - x.pdf", True),
        ("Contractor License 2027.pdf", True),
        ("BL - Woodworks.pdf", False),
        ("A & A Masonry - W-9.pdf", False),
        ("", False),
    ],
)
def test_is_cl_hint(filename, expected):
    assert is_cl_hint(filename) is expected
