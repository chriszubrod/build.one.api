"""Shared fakes and PDF builders for the attachment text-extraction tests."""

import io
import types

import pytest


class _FakeRepo:
    """Claims one row per call, like ClaimAttachmentsForExtraction with @Limit = 1.

    lose_claim makes the guarded terminal write match no row (the lease was reclaimed).
    fail_terminal makes every terminal write raise.
    """

    def __init__(self, rows=None, fail_terminal=False, lose_claim=False):
        self.rows = list(rows or [])
        self.fail_terminal = fail_terminal
        self.lose_claim = lose_claim
        self.claim_calls = []
        self.updates = []

    def claim_for_extraction(self, *, groups, limit, lease_minutes):
        self.claim_calls.append({"groups": groups, "limit": limit, "lease_minutes": lease_minutes})
        if limit < 1 or not self.rows:
            return []
        return [self.rows.pop(0)]

    def update_extraction(self, **kwargs):
        if self.fail_terminal and kwargs.get("extraction_status") in ("completed", "failed"):
            raise RuntimeError("terminal write failed")
        self.updates.append(kwargs)
        return None if self.lose_claim else types.SimpleNamespace(id=kwargs["id"])


class _FakeStorage:
    def __init__(self, downloads=None, upload_error=None, delete_error=None):
        self.downloads = dict(downloads or {})
        self.upload_error = upload_error
        self.delete_error = delete_error
        self.uploads = []
        self.deleted = []

    def download_file(self, blob_url):
        value = self.downloads[blob_url]
        if isinstance(value, BaseException):
            raise value
        return value, {}

    def upload_file(self, *, blob_name, file_content, content_type):
        if self.upload_error is not None:
            raise self.upload_error
        self.uploads.append(
            {"blob_name": blob_name, "file_content": file_content, "content_type": content_type}
        )
        return f"https://blob.example/{blob_name}"

    def delete_file(self, blob_url):
        if self.delete_error is not None:
            raise self.delete_error
        self.deleted.append(blob_url)


class _FakeDI:
    def __init__(self, text_result=None, forbid=False):
        self.text_result = text_result
        self.forbid = forbid
        self.text_calls = []
        self.invoice_calls = []

    def extract_text(self, content, content_type):
        # Record first: the service swallows exceptions per row, so a forbidden call
        # must still be visible to the test through text_calls.
        self.text_calls.append(content_type)
        if self.forbid:
            raise AssertionError("DI must NOT be called on the text-layer path")
        if isinstance(self.text_result, BaseException):
            raise self.text_result
        return self.text_result

    def extract_invoice(self, content, content_type):
        self.invoice_calls.append(content_type)
        raise AssertionError("attachment extraction must use extract_text, not extract_invoice")


def _text_pdf_bytes(lines):
    reportlab = pytest.importorskip("reportlab")  # noqa: F841
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    y = 720
    for line in lines:
        c.drawString(72, y, line)
        y -= 20
    c.showPage()
    c.save()
    return buf.getvalue()


def _image_only_pdf_bytes():
    """A one-page PDF that embeds a real raster image and carries no text layer."""
    from PIL import Image
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    png = io.BytesIO()
    Image.new("RGB", (64, 64), (200, 30, 30)).save(png, format="PNG")
    png.seek(0)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.drawImage(ImageReader(png), 72, 500, width=200, height=200)
    c.showPage()
    c.save()
    return buf.getvalue()
