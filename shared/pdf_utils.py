# Python Standard Library Imports
import io
import logging
import os
from typing import Tuple

logger = logging.getLogger(__name__)

# MIME types for images we can convert to PDF
IMAGE_CONTENT_TYPES = frozenset({
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/gif",
    "image/webp",
})

# File extensions for images
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})


def ensure_pdf(
    content: bytes,
    content_type: str,
    file_name: str,
) -> Tuple[bytes, str, str]:
    """
    Ensure file content is PDF. If the file is an image, convert to PDF.
    PDFs are returned as-is.

    Args:
        content: Raw file content
        content_type: MIME type (e.g. application/pdf, image/png)
        file_name: Original file name for extension detection

    Returns:
        Tuple of (content, content_type, file_extension)
    """
    if not content:
        return content, content_type, _ext_from_filename(file_name)

    ct_lower = (content_type or "").strip().lower()
    ext = _ext_from_filename(file_name)

    # Already PDF
    if ct_lower == "application/pdf" or ext == ".pdf":
        return content, "application/pdf", ".pdf"

    # Image: convert to PDF
    if ct_lower in IMAGE_CONTENT_TYPES or ext in IMAGE_EXTENSIONS:
        try:
            from PIL import Image

            img = Image.open(io.BytesIO(content))
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            pdf_buffer = io.BytesIO()
            img.save(pdf_buffer, "PDF", resolution=100.0)
            result = pdf_buffer.getvalue()
            logger.info(
                "Converted image to PDF: %s (%s) -> %s bytes",
                file_name,
                content_type,
                len(result),
            )
            return result, "application/pdf", ".pdf"
        except Exception as e:
            logger.warning("Image-to-PDF conversion failed for %s: %s. Storing as-is.", file_name, e)
            return content, content_type, ext

    # Other types: return as-is
    return content, content_type, ext


def _ext_from_filename(file_name: str) -> str:
    """Extract lowercase extension including dot."""
    if not file_name:
        return ""
    _, ext = os.path.splitext(file_name)
    return ext.lower() if ext else ""


def compact_pdf(content: bytes) -> bytes:
    """
    Reduce PDF file size losslessly when possible.
    Uses pypdf to compress content streams and remove duplicate/orphan objects.
    Returns the original bytes if compaction fails or would increase size.
    """
    if not content or len(content) < 100:
        return content

    try:
        from pypdf import PdfReader, PdfWriter

        reader = PdfReader(io.BytesIO(content))
        writer = PdfWriter()

        # Append all pages
        writer.append(reader)

        # Lossless: compress content streams (deflate)
        for page in writer.pages:
            page.compress_content_streams(level=9)

        # Merge identical objects and remove orphans (can significantly reduce size)
        writer.compress_identical_objects(remove_identicals=True, remove_orphans=True)

        out = io.BytesIO()
        writer.write(out)
        result = out.getvalue()

        # Only use compacted version if it's actually smaller
        if len(result) < len(content):
            logger.info(
                "PDF compacted: %s -> %s bytes (%.1f%% reduction)",
                len(content),
                len(result),
                100.0 * (1 - len(result) / len(content)),
            )
            return result
        return content

    except Exception as e:
        logger.warning("PDF compaction failed, using original: %s", e)
        return content


# US Letter in points (72dpi): 8.5in x 11in.
LETTER_WIDTH_PT = 612.0
LETTER_HEIGHT_PT = 792.0


def fit_page_to_letter(src_page):
    """
    Normalize a PDF page onto a letter-sized (612x792pt) canvas, scaled to fit
    while preserving aspect ratio and centered.

    Receipt images get wrapped into PDFs at their native pixel dimensions (a
    phone photo can be 40+ inches per side), so without this they render
    enormous and inconsistent next to letter-sized pages.

    Two correctness details:
      - Geometry is measured from the CropBox, which is the region pypdf's
        `merge_transformed_page` actually clips to; it defaults to the MediaBox
        when no crop is set. Measuring the MediaBox instead would off-center or
        shrink any page whose CropBox is smaller than its MediaBox.
      - Any /Rotate is baked into the page content BEFORE measuring (via
        `transfer_rotation_to_content`), so a landscape-rotated page is measured
        and scaled in its VISUAL orientation and the output is a plain,
        un-rotated portrait letter page. Re-stamping /Rotate onto the canvas
        instead would leave rotated pages landscape and defeat uniform sizing.

    A page already at letter size (CropBox == MediaBox == letter, origin 0,0) is
    returned unchanged so it is not needlessly re-encoded.
    """
    from pypdf import PageObject, Transformation

    # Bake rotation into content so we measure/scale the visual orientation.
    if src_page.get("/Rotate"):
        try:
            src_page.transfer_rotation_to_content()
        except Exception as e:
            logger.warning("Could not bake page rotation, proceeding as-is: %s", e)

    box = src_page.cropbox  # pypdf returns the MediaBox when no CropBox is set
    sw = float(box.width)
    sh = float(box.height)
    if sw <= 0 or sh <= 0:
        return src_page

    mb = src_page.mediabox
    already_letter = (
        abs(sw - LETTER_WIDTH_PT) < 0.5
        and abs(sh - LETTER_HEIGHT_PT) < 0.5
        and abs(float(box.left)) < 0.5
        and abs(float(box.bottom)) < 0.5
        and abs(float(mb.width) - LETTER_WIDTH_PT) < 0.5
        and abs(float(mb.height) - LETTER_HEIGHT_PT) < 0.5
        and abs(float(mb.left)) < 0.5
        and abs(float(mb.bottom)) < 0.5
    )
    if already_letter:
        return src_page

    scale = min(LETTER_WIDTH_PT / sw, LETTER_HEIGHT_PT / sh)
    # Center the scaled content, normalizing any non-zero box origin.
    tx = (LETTER_WIDTH_PT - sw * scale) / 2.0 - float(box.left) * scale
    ty = (LETTER_HEIGHT_PT - sh * scale) / 2.0 - float(box.bottom) * scale

    new_page = PageObject.create_blank_page(width=LETTER_WIDTH_PT, height=LETTER_HEIGHT_PT)
    new_page.merge_transformed_page(src_page, Transformation().scale(scale).translate(tx, ty))
    return new_page


def merge_pdfs(blob_urls, leading_pdf_bytes=None):
    """Merge PDFs into one letter-sized PDF's bytes.

    leading_pdf_bytes: list[bytes] of already-letter-sized pages (cover/TOC) added first, as-is.
    blob_urls: list[str] Azure blob URLs; each downloaded, read, and each page fit to letter.
    Unreadable/empty blobs are skipped. Raises ValueError if zero pages result.
    """
    from pypdf import PdfReader, PdfWriter
    from shared.storage import AzureBlobStorage

    writer = PdfWriter()
    for pdf_bytes in (leading_pdf_bytes or []):
        reader = PdfReader(io.BytesIO(pdf_bytes))
        for page in reader.pages:
            writer.add_page(page)
    storage = AzureBlobStorage()
    merged_blob_pages = 0
    for blob_url in blob_urls:
        if not blob_url:
            continue
        try:
            content, _ = storage.download_file(blob_url)
            reader = PdfReader(io.BytesIO(content))
            for page in reader.pages:
                writer.add_page(fit_page_to_letter(page))
                merged_blob_pages += 1
        except Exception:
            continue
    if len(writer.pages) == 0:
        raise ValueError("No PDF pages could be read for the merged document")
    requested_any_blob = any(bool(u) for u in blob_urls)
    if requested_any_blob and merged_blob_pages == 0:
        raise ValueError("All source documents failed to merge; refusing to produce an incomplete packet")
    merged_buf = io.BytesIO()
    writer.write(merged_buf)
    uncompressed_bytes = merged_buf.getvalue()
    try:
        import pikepdf
        compressed_buf = io.BytesIO()
        with pikepdf.open(io.BytesIO(uncompressed_bytes)) as pdf:
            pdf.save(
                compressed_buf,
                compress_streams=True,
                object_stream_mode=pikepdf.ObjectStreamMode.generate,
                recompress_flate=True,
                normalize_content=True,
            )
        merged_bytes = compressed_buf.getvalue()
    except Exception as e:
        logger.warning("merge_pdfs: pikepdf compression failed, using uncompressed: %s", e)
        merged_bytes = uncompressed_bytes
    return merged_bytes
