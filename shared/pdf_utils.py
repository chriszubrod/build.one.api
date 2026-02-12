# Python Standard Library Imports
import io
import logging

logger = logging.getLogger(__name__)


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
