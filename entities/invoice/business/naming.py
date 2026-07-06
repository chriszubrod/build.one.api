# Python Standard Library Imports
import re
from typing import Optional

# Third-party Imports

# Local Imports


# Character class stripped from SharePoint + Box filenames. SharePoint's URL
# path length + reserved-character constraints drive the list; Box is more
# permissive but we sanitize identically so the human-readable portion of the
# name matches across both integrations (humans cross-reference by name).
# NOT byte-identical: the Box outbox additionally appends a deterministic
# `-{8hex}` identity suffix (its 409-recovery idempotency key) and collapses
# whitespace runs — a Box name equals its SP counterpart plus that suffix.
_FILENAME_SANITIZE_RE = re.compile(r'[<>:"/\\|?*]')

# Content-type → extension fallback for attachments whose stored
# FileExtension + OriginalFilename are both empty. Mirrors the mapping at
# entities/invoice/business/service.py in _upload_to_sharepoint.
_CONTENT_TYPE_EXTENSIONS = {
    "application/pdf": "pdf",
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
}


def sanitize_for_filename(text: str) -> str:
    """Strip characters SharePoint refuses; replace with underscore."""
    return _FILENAME_SANITIZE_RE.sub("_", text)


def build_line_pdf_filename(
    *,
    invoice_number: str,
    vendor_name: Optional[str],
    parent_number: Optional[str],
    description: Optional[str],
    scc_number: Optional[str],
    price,
    source_date: Optional[str],
    file_extension: Optional[str],
    original_filename: Optional[str],
    content_type: Optional[str],
) -> str:
    """
    Build the display filename for a per-line-item PDF attached to an invoice
    packet's supporting-docs folder.

    Shape: `<invoice#> - <vendor> - <parent#> - <description> - <scc> -
    $<price> - <source date>.<ext>`. Empty parts are omitted (no double
    " - " separators). Reserved characters are replaced with `_`.

    Shared between:
      - entities/invoice/business/service.py::_upload_to_sharepoint
      - entities/invoice/business/service.py::_enqueue_box_line_pdfs
    Both surfaces MUST produce byte-identical names — humans use them to
    cross-reference between the SharePoint UI and the Box UI. That contract
    is why this helper lives in a shared module instead of being inlined
    twice.
    """
    price_str = ""
    if price is not None:
        try:
            price_str = f"${float(price):,.2f}"
        except (TypeError, ValueError):
            price_str = ""

    filename_parts = [
        invoice_number or "",
        vendor_name or "",
        parent_number or "",
        description or "",
        scc_number or "",
        price_str,
        source_date or "",
    ]
    base_filename = sanitize_for_filename(
        " - ".join(part for part in filename_parts if part)
    )

    ext = (file_extension or "").strip()
    if not ext and original_filename and "." in original_filename:
        ext = original_filename.rsplit(".", 1)[-1]
    if not ext and content_type:
        ext = _CONTENT_TYPE_EXTENSIONS.get(content_type, "")
    if ext and not ext.startswith("."):
        ext = "." + ext

    return base_filename + ext


def build_packet_filename(invoice_number: str) -> str:
    """`<invoice#> - Packet.pdf` with reserved characters replaced."""
    return sanitize_for_filename(invoice_number) + " - Packet.pdf"
