"""Filename hint tests for certificate of insurance detection in vendor folder scans."""
import pytest

from entities.vendor_compliance.business.folder_helpers import is_coi_hint


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("ACORD 25 Cert.pdf", True),
        ("Certificate of Insurance.pdf", True),
        ("x - COI.pdf", True),
        ("coi-2027.pdf", True),
        ("coil spring.pdf", False),
        ("recoil.pdf", False),
        ("BL - Woodworks.pdf", False),
        ("A & A Masonry - W-9.pdf", False),
        ("", False),
    ],
)
def test_is_coi_hint(filename, expected):
    assert is_coi_hint(filename) is expected
