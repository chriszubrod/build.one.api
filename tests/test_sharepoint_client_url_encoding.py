"""
Regression coverage for the `#`-in-filename SharePoint upload bug (2026-09-02).

`#` and `%` are legal SharePoint filenames but are URL-reserved (fragment
delimiter / escape marker). Left raw in Graph's `:/{name}:/` colon-addressing
path syntax, `#` silently truncates the request path before it reaches Graph,
which then rejects the malformed request with a confusing "Entity only allows
writes with a JSON Content-Type header" error (non-retryable -> dead-letter).

These tests assert the *path segment* handed to MsGraphClient is percent-encoded,
while any *JSON body* value (the real display name) stays unencoded.
"""
from unittest.mock import patch

from integrations.ms.sharepoint.external.client import (
    create_folder,
    upload_large_file,
    upload_small_file,
)
from tests.conftest import mock_ms_graph_client_cm

_MODULE = "integrations.ms.sharepoint.external.client"


def test_upload_small_file_encodes_hash_in_filename():
    client = mock_ms_graph_client_cm(return_value={"id": "item-1", "name": "x"})
    with patch(f"{_MODULE}.MsGraphClient", return_value=client):
        upload_small_file(
            "drive-1", "parent-1", "#7 Washed stone.pdf", b"content", "application/pdf"
        )

    path = client.upload.call_args.args[0]
    assert "%237" in path  # '#' -> %23, adjacent digit proves it's not double-decoded
    assert "#7" not in path
    assert path == "drives/drive-1/items/parent-1:/%237%20Washed%20stone.pdf:/content"


def test_upload_small_file_encodes_percent_in_filename():
    client = mock_ms_graph_client_cm(return_value={"id": "item-1", "name": "x"})
    with patch(f"{_MODULE}.MsGraphClient", return_value=client):
        upload_small_file("drive-1", "parent-1", "50% markup.pdf", b"content", "application/pdf")

    path = client.upload.call_args.args[0]
    assert path == "drives/drive-1/items/parent-1:/50%25%20markup.pdf:/content"
    assert "%2525" not in path  # not double-encoded


def test_upload_small_file_plain_filename_unchanged():
    client = mock_ms_graph_client_cm(return_value={"id": "item-1", "name": "x"})
    with patch(f"{_MODULE}.MsGraphClient", return_value=client):
        upload_small_file("drive-1", "parent-1", "plain-name.pdf", b"content", "application/pdf")

    path = client.upload.call_args.args[0]
    assert path == "drives/drive-1/items/parent-1:/plain-name.pdf:/content"


def test_upload_large_file_encodes_hash_in_session_path_but_not_json_name():
    client = mock_ms_graph_client_cm()
    client.post.return_value = {"uploadUrl": None}
    with patch(f"{_MODULE}.MsGraphClient", return_value=client):
        result = upload_large_file(
            "drive-1", "parent-1", "#7 Washed stone.pdf", b"x" * 10, "application/pdf"
        )

    path = client.post.call_args.args[0]
    assert "%237" in path
    assert "#7" not in path

    # The JSON body's "name" field is the real display name shown in SharePoint --
    # it must stay unencoded, only the URL path segment is encoded.
    json_body = client.post.call_args.kwargs["json"]
    assert json_body["item"]["name"] == "#7 Washed stone.pdf"

    assert result["status_code"] == 500  # no uploadUrl returned -> handled gracefully, not a crash


def test_create_folder_conflict_lookup_encodes_hash_in_folder_name():
    client = mock_ms_graph_client_cm()
    conflict_error = _make_conflict_error()
    client.post.side_effect = conflict_error
    client.get.return_value = {"id": "existing-1", "name": "x"}

    with patch(f"{_MODULE}.MsGraphClient", return_value=client):
        result = create_folder("drive-1", "parent-1", "Draws #2")

    path = client.get.call_args.args[0]
    assert "%232" in path
    assert "#2" not in path
    assert result["status_code"] == 200


def _make_conflict_error():
    from integrations.ms.base.errors import MsConflictError

    return MsConflictError(
        "conflict",
        code="nameAlreadyExists",
        detail=None,
        http_status=409,
        request_method="POST",
        request_path="drives/drive-1/items/parent-1/children",
    )
