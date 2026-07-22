"""Regression tests for U-089 review fixes (pure-logic, no live DB).

- merge_pdfs must NOT silently return a cover-only packet when source documents
  were requested but every blob download/read failed (Codex P2e).
- VendorInsurancePolicy money limits must serialize as strings, not floats, so
  JSON transport can't lose precision (Codex P2d).
"""
import io
from decimal import Decimal

import pytest
from pypdf import PdfWriter

from shared import pdf_utils
from entities.vendor_insurance_policy.business.model import VendorInsurancePolicy


def _one_page_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_merge_pdfs_allows_leading_only_when_no_blobs_requested():
    # A vendor with zero source docs → a cover-only packet is intended, not an error.
    out = pdf_utils.merge_pdfs([], leading_pdf_bytes=[_one_page_pdf()])
    assert isinstance(out, (bytes, bytearray)) and len(out) > 0


def test_merge_pdfs_raises_when_all_requested_blobs_fail(monkeypatch):
    class _FailingStorage:
        def download_file(self, url):
            raise RuntimeError("blob storage unavailable")

    # merge_pdfs imports AzureBlobStorage from shared.storage at call time.
    monkeypatch.setattr("shared.storage.AzureBlobStorage", _FailingStorage)

    with pytest.raises(ValueError):
        pdf_utils.merge_pdfs(
            ["https://blob.example/license.pdf"],
            leading_pdf_bytes=[_one_page_pdf()],
        )


def test_policy_to_dict_stringifies_money_limits():
    policy = VendorInsurancePolicy(
        id=1,
        public_id="pol-1",
        row_version="AAAAAAAAB9E=",
        created_datetime=None,
        modified_datetime=None,
        certificate_of_insurance_id=1,
        coverage_type="GL",
        carrier="Acme Mutual",
        policy_number="GL-123",
        each_occurrence=Decimal("1000000.01"),
        aggregate=Decimal("2000000.00"),
        effective_date=None,
        expiry_date=None,
        created_by_user_id=None,
    )
    d = policy.to_dict()
    assert d["each_occurrence"] == "1000000.01"
    assert isinstance(d["each_occurrence"], str)
    assert d["aggregate"] == "2000000.00"
    assert isinstance(d["aggregate"], str)
