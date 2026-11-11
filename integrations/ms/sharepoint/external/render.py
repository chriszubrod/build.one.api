"""Render an .xlsx to PDF through Microsoft Graph (U-786).

WHY: the draw-packet PDFs (Invoice and Trend tabs) are printed from the tracker
workbook. There is no desktop Excel on the server. Excel-for-web's converter
prints the sheet with its page setup, header/footer and drawings, which is what
the human export does today. So the server uploads a one-sheet workbook to the
integration account's OneDrive, asks Graph for the PDF, and deletes the temp copy.

WHAT: one function. It uploads `content` to `BuildOneTemp/` in the integration
account's OneDrive, requests `format=pdf`, deletes the temp item in `finally`,
and returns the PDF bytes. The temp item is removed even when the render fails.
A failed delete is logged (`ms.render.cleanup_failed`) and never masks the
render error. The write gate (ALLOW_MS_WRITES) is enforced by MsGraphClient.

Residual: if the upload succeeds but its response is lost (timeout, dropped
connection), there is no item id, so nothing is deleted and the orphaned temp
xlsx stays in `BuildOneTemp/`. An orphan is not a one-sheet copy: it carries the
tracker's whole shared-strings table (all ledger text) and docProps, not just one
sheet. Deleted items also sit in the OneDrive recycle bin. The folder is private
to the integration account. Sweep it by hand if it grows. (Pruning the shared
strings is booked separately.)
"""

import logging
import re
import uuid

from integrations.ms.base.client import MsGraphClient

logger = logging.getLogger(__name__)

XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
TEMP_FOLDER = "BuildOneTemp"
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_stem(name_hint: str) -> str:
    stem = _UNSAFE.sub("_", name_hint or "").strip("._-")[:80]
    return stem or "workbook"


def render_xlsx_to_pdf(content: bytes, *, name_hint: str) -> bytes:
    temp_name = f"{_safe_stem(name_hint)}-{uuid.uuid4().hex[:8]}.xlsx"
    upload_path = f"me/drive/root:/{TEMP_FOLDER}/{temp_name}:/content"

    with MsGraphClient() as client:
        item = client.upload(
            upload_path,
            content=content,
            content_type=XLSX_CONTENT_TYPE,
            method="PUT",
            operation_name="render.upload",
        )
        item_id = (item or {}).get("id")
        if not item_id:
            raise RuntimeError("render upload returned no item id")
        item_path = f"me/drive/items/{item_id}"
        try:
            pdf = client.get_bytes(
                f"{item_path}/content",
                params={"format": "pdf"},
                extra_headers={"Accept": "*/*"},
                timeout_tier="C",
                operation_name="render.pdf",
            )
        finally:
            try:
                client.delete(item_path, operation_name="render.cleanup")
            except Exception as cleanup_error:
                logger.warning(
                    "ms.render.cleanup_failed",
                    extra={
                        "event_name": "ms.render.cleanup_failed",
                        "item_id": item_id,
                        "error_class": type(cleanup_error).__name__,
                    },
                )

    if not pdf or not pdf.startswith(b"%PDF"):
        raise RuntimeError("render returned bytes that are not a PDF")
    return pdf
