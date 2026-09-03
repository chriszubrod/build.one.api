# Python Standard Library Imports
import urllib.parse


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
