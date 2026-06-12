# Python Standard Library Imports
import re
import unicodedata

# Third-party Imports

# Local Imports


# Box-illegal characters (per Phase-2 contract): { } < > : / " \ | ? *
# `/` and `\` are Box's hard-rejected path separators; the rest are the
# Windows-illegal set Box mirrors plus braces. Each is replaced with `_`.
_ILLEGAL_CHARS = re.compile(r'[{}<>:/"\\|?*]')

# Collapse any run of whitespace (incl. tabs/newlines after control-strip)
# into a single space.
_WHITESPACE_RUN = re.compile(r"\s+")

# A "real" extension: a literal dot followed by 1-16 alphanumerics at the
# very end of the name. Anything else (e.g. `.._.._etc_passwd`) is treated
# as extensionless so the identity suffix lands at the true end of the name.
_EXTENSION = re.compile(r"\.[A-Za-z0-9]{1,16}$")

# Base-name cap BEFORE the identity suffix is appended. Box's hard limit is
# 255 chars for the full name; 180 + "-" + 8 hex + extension stays well under.
_MAX_BASE_LENGTH = 180

_FALLBACK_BASE = "document"


def sanitize_filename(filename: str, entity_public_id: str) -> str:
    """
    Sanitize a filename for Box and embed entity identity for idempotency.

    Pure + deterministic: the same `(filename, entity_public_id)` pair always
    yields the same output, so an outbox retry re-uploads under the SAME name
    and Box's 409 `item_name_in_use` conflict (recovered via the `[box].[File]`
    registry) becomes the dedup mechanism instead of a duplicate file.

    Steps (Phase-2 contract):
      1. NFC-normalize unicode.
      2. Strip control characters.
      3. Replace Box-illegal characters ({}<>:/"\\|?*) with `_`.
      4. Collapse whitespace runs to single spaces and trim.
      5. Reject `.` / `..` (and empty) -> fallback base 'document'.
      6. Cap the base (name sans extension) at 180 chars.
      7. Embed identity: "{base}-{first 8 hex of entity_public_id sans dashes}{ext}".
    """
    raw = unicodedata.normalize("NFC", str(filename or ""))
    raw = "".join(ch for ch in raw if unicodedata.category(ch) != "Cc")
    raw = _ILLEGAL_CHARS.sub("_", raw)
    raw = _WHITESPACE_RUN.sub(" ", raw).strip()

    if raw in ("", ".", ".."):
        raw = _FALLBACK_BASE

    match = _EXTENSION.search(raw)
    if match:
        base = raw[: match.start()]
        extension = match.group(0)
    else:
        base = raw
        extension = ""

    base = base[:_MAX_BASE_LENGTH].strip()
    if base in ("", ".", ".."):
        base = _FALLBACK_BASE

    identity = (entity_public_id or "").replace("-", "").lower()[:8]
    return f"{base}-{identity}{extension}"


if __name__ == "__main__":
    # Focused self-test: `python3 integrations/box/file/business/naming.py`
    _pid = "12345678-90ab-cdef-1234-567890abcdef"

    # Deterministic for idempotent re-runs.
    assert sanitize_filename("invoice.pdf", _pid) == sanitize_filename("invoice.pdf", _pid)
    assert sanitize_filename("invoice.pdf", _pid) == "invoice-12345678.pdf"

    # Identity suffix uses first 8 hex of the public id, dashes stripped.
    assert sanitize_filename("invoice.pdf", "ABCDEF01-2345-6789-abcd-ef0123456789") == "invoice-abcdef01.pdf"

    # Legal punctuation survives untouched ('=1+1.pdf' style names).
    assert sanitize_filename("=1+1.pdf", _pid) == "=1+1-12345678.pdf"

    # Box-illegal characters replaced with underscores.
    assert sanitize_filename('a{b}c<d>e:f/g"h\\i|j?k*.pdf', _pid) == "a_b_c_d_e_f_g_h_i_j_k_-12345678.pdf"

    # Whitespace collapsed + trimmed; trailing base whitespace stripped.
    assert sanitize_filename("  my   report 2024 .pdf  ", _pid) == "my report 2024-12345678.pdf"

    # Control characters stripped (incl. tab/newline — strip runs BEFORE
    # whitespace collapse per the contract's step order).
    assert sanitize_filename("bad\x00\x1fname.pdf", _pid) == "badname-12345678.pdf"
    assert sanitize_filename("a\tb\nc.pdf", _pid) == "abc-12345678.pdf"

    # Traversal rejection: '.' / '..' / empty fall back to 'document'.
    assert sanitize_filename(".", _pid) == "document-12345678"
    assert sanitize_filename("..", _pid) == "document-12345678"
    assert sanitize_filename("", _pid) == "document-12345678"

    # Path separators cannot survive — no traversal possible.
    traversal = sanitize_filename("../../etc/passwd", _pid)
    assert "/" not in traversal and "\\" not in traversal
    assert traversal == ".._.._etc_passwd-12345678"

    # Base cap at 180 chars; extension + identity suffix preserved.
    long_name = ("x" * 300) + ".pdf"
    capped = sanitize_filename(long_name, _pid)
    assert capped == ("x" * 180) + "-12345678.pdf"
    assert len(capped) <= 255

    # Weird "extension" longer than 16 alnum chars is treated as part of the base.
    assert sanitize_filename("archive.tar.gz", _pid) == "archive.tar-12345678.gz"

    print("naming self-test passed")
