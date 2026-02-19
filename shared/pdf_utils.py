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
