# Python Standard Library Imports
import logging
import re
import urllib.parse

# Control chars (C0 + DEL). Removed, not replaced -- see sharepoint_safe_name.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")
_RESERVED_CHARS_RE = re.compile(r'[<>:"/\\|?*]')


def encode_path_segment(name: str) -> str:
    """
    Percent-encode a single item/file/folder name for Graph's `:/name:/`
    colon-addressing syntax. MsGraphClient never encodes `path` itself (it
    also carries literal `/`-separated segments and OData literals), so any
    caller-supplied name interpolated into a colon segment must be encoded
    here first -- see test_sharepoint_client_url_encoding.py for the incident
    this fixes.
    """
    return urllib.parse.quote(name, safe="")


def sharepoint_safe_name(text: str) -> str:
    """
    Make `text` safe to use as a SharePoint file/folder name (U-779):
      1. remove every control char (\\x00-\\x1f, \\x7f) -- Graph rejects them (HTTP 400);
      2. replace each of `<>:"/\\|?*` with `_` (the historic rule).

    Removed, not replaced: Box's own sanitizer removes them too, so Box names
    derived from this output equal the legacy Box names ("B\\n1" -> "B1").
    entities/invoice/business/naming.py maps them to `_` instead; do not merge
    the two -- either change renames existing files.

    Text with no control char comes back exactly as the historic rule made it
    (no strip, no whitespace or unicode changes), so existing names -- and the
    outbox duplicate guard that compares them -- are untouched.
    """
    return _RESERVED_CHARS_RE.sub("_", _CONTROL_CHARS_RE.sub("", text))


def graph_safe_name(name: str, *, operation: str, log: logging.Logger | logging.LoggerAdapter) -> str:
    """
    Chokepoint backstop: return the sanitized name for a Graph write, logging
    a WARNING (lengths only, never the raw name) when it had to change.
    """
    safe = sharepoint_safe_name(name)
    if safe != name:
        log.warning(
            "ms.sharepoint.name_sanitized_at_chokepoint",
            extra={
                "event_name": "ms.sharepoint.name_sanitized_at_chokepoint",
                "operation": operation,
                "raw_len": len(name),
                "safe_len": len(safe),
            },
        )
    return safe
