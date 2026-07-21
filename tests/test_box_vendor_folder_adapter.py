"""Mock-based tests for Box vendor folder listing adapter (U-106 Stage 6).

Exercises ``_box_list_children`` pagination and entry shaping with a fake
``BoxHttpClient.get`` — no DB or network.
"""
from unittest.mock import Mock

from integrations.box.folder.business.vendor_service import _box_list_children


def _make_paginating_client():
    """Fake client: page 1 has two entries, page 2 has one (total_count=3)."""
    pages = [
        {
            "entries": [
                {"id": 1, "name": "a.pdf", "type": "file", "size": 10},
                {"id": 2, "name": "sub", "type": "folder"},
            ],
            "total_count": 3,
        },
        {
            "entries": [{"id": 3, "name": "b.pdf", "type": "file", "size": 20}],
            "total_count": 3,
        },
    ]
    call_index = {"n": 0}

    def get(path, params=None, operation_name=None):
        assert path == "folders/0/items"
        assert operation_name == "box.vendor_folder.list_children"
        idx = call_index["n"]
        call_index["n"] += 1
        page = pages[idx]
        if idx == 0:
            assert params["offset"] == 0
        else:
            assert params["offset"] == 2
        return page

    client = Mock()
    client.get.side_effect = get
    return client


def test_box_list_children_paginates_and_maps_entries():
    client = _make_paginating_client()
    result = _box_list_children(client, "0")
    items = result["items"]
    assert len(items) == 3
    assert client.get.call_count == 2

    by_id = {item["item_id"]: item for item in items}
    assert by_id["1"] == {
        "item_type": "file",
        "item_id": "1",
        "name": "a.pdf",
        "size": 10,
    }
    assert by_id["2"] == {
        "item_type": "folder",
        "item_id": "2",
        "name": "sub",
        "size": None,
    }
    assert by_id["3"] == {
        "item_type": "file",
        "item_id": "3",
        "name": "b.pdf",
        "size": 20,
    }


def test_box_list_children_empty_folder():
    client = Mock()
    client.get.return_value = {"entries": [], "total_count": 0}

    assert _box_list_children(client, "0") == {"items": []}
    client.get.assert_called_once()
