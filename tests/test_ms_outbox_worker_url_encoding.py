"""
Regression coverage for the `#`-in-filename bug (see test_sharepoint_client_url_encoding.py
for the full incident narrative) in the live outbox large-file resume path
(`MsOutboxWorker._upload_large_file_with_resume`), which builds its own
`createUploadSession` path independently of `upload_large_file` in
sharepoint/external/client.py -- large (>4MB) outbox uploads bypass that
module entirely, so fixing only it left this path still vulnerable.
"""
from unittest.mock import MagicMock, patch

import pytest

from integrations.ms.outbox.business.worker import MsOutboxWorker
from tests.conftest import mock_ms_graph_client_cm

# MsGraphClient is imported lazily inside the method under test (not at module
# scope), so it must be patched at its defining module, not at worker's.
_MS_GRAPH_CLIENT_TARGET = "integrations.ms.base.client.MsGraphClient"


def test_upload_large_file_with_resume_encodes_hash_in_session_path():
    worker = MsOutboxWorker(repo=MagicMock())

    client = mock_ms_graph_client_cm()
    client.post.return_value = {"uploadUrl": None}  # short-circuits after the POST we're checking

    payload = {
        "drive_id": "drive-1",
        "parent_item_id": "parent-1",
        "filename": "#7 Washed stone.pdf",
    }

    with patch(_MS_GRAPH_CLIENT_TARGET, return_value=client):
        with pytest.raises(ValueError, match="upload session did not return an uploadUrl"):
            worker._upload_large_file_with_resume(
                row=MagicMock(id=1, row_version="rv-1"),
                payload=payload,
                content=b"x" * (5 * 1024 * 1024 + 1),
                total_size=5 * 1024 * 1024 + 1,
                content_type="application/pdf",
            )

    path = client.post.call_args.args[0]
    assert "%237" in path
    assert "#7" not in path

    # JSON body's "name" is the real display name shown in SharePoint -- unencoded.
    json_body = client.post.call_args.kwargs["json"]
    assert json_body["item"]["name"] == "#7 Washed stone.pdf"
