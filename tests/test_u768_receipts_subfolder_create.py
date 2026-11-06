"""U-768: the general-receipts upload must use a NEWLY CREATED year/month folder.

``ExpenseService._upload_to_general_receipts_folder`` resolves
``520 - Current Receipts/{yyyy}/{mm}`` and creates the year/month folder when
it is missing. The create result is the client's ``_format_drive_item`` shape,
whose id key is ``item_id`` -- reading ``item["id"]`` raised KeyError, so the
first receipt of every month failed its upload.

The drive-item fake below behaves like the real ``MsDriveItemService``:
``read_or_create_folder`` reuses an existing child folder (200 + the formatted
item) or creates one (201 + the formatted item). Pure unit test -- no DB,
Graph, blob storage or network.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from entities.expense.business.service import ExpenseService
from integrations.ms.base.errors import MsConflictError
from integrations.ms.sharepoint.driveitem.business.service import MsDriveItemService
from integrations.ms.sharepoint.external import client as graph_client
from tests.test_u757b_sharepoint_enqueue_characterization import (
    _R_ATTS,
    _R_LINKS,
    _R_PROJECTS,
    _R_SCCS,
    _R_VENDORS,
    FakeExpenseLineItemService,
    FakeLinkService,
    FakeReadById,
    FakeSccService,
    Recorder,
    _drive,
    _expense,
    _folder,
    _line,
    _make_drive_service_cls,
    _make_outbox_cls,
    _make_storage_cls,
)

_EXPENSE_MOD = "entities.expense.business.service"
_OUTBOX = "integrations.ms.outbox.business.service.MsOutboxService"


class TreeDriveItemService:
    """Folder tree keyed by parent item id; mirrors the real service's shapes."""

    def __init__(self, tree, create_override=None):
        self.tree = {k: list(v) for k, v in tree.items()}
        self.create_override = create_override or {}
        self.create_calls: list = []

    def _children(self, parent_item_id):
        return list(self.tree.get(parent_item_id, []))

    def create_folder(self, drive_public_id, parent_item_id, folder_name):
        self.create_calls.append((parent_item_id, folder_name))
        if folder_name in self.create_override:
            return self.create_override[folder_name]
        item = _folder(folder_name, f"NEW-{parent_item_id}-{folder_name}")
        self.tree.setdefault(parent_item_id, []).append(item)
        return {"status_code": 201, "message": "Folder created successfully", "item": item}

    def read_or_create_folder(self, drive_public_id, parent_item_id, folder_name):
        for child in self._children(parent_item_id):
            if child.get("item_type") == "folder" and child.get("name") == folder_name:
                return {"message": "Folder exists; reusing.", "status_code": 200, "item": child}
        return self.create_folder(
            drive_public_id=drive_public_id, parent_item_id=parent_item_id, folder_name=folder_name
        )


def _run(date, tree, create_override=None, driveitems=None):
    rec = Recorder()
    svc = object.__new__(ExpenseService)
    svc.vendor_service = FakeReadById(rec, "vendor", _R_VENDORS)
    svc.project_service = FakeReadById(rec, "project", _R_PROJECTS)
    svc.sub_cost_code_service = FakeSccService(rec, _R_SCCS)
    svc.attachment_service = FakeReadById(rec, "attachment", _R_ATTS)
    svc._expense_line_item_attachment_service = FakeLinkService(rec, _R_LINKS)
    lines = [_line(1, "li-1")]
    svc._expense_line_item_service = FakeExpenseLineItemService(rec, lines)
    if driveitems is None:
        driveitems = TreeDriveItemService(tree, create_override)
    svc._driveitem_service = driveitems

    def list_children(drive_id, item_id):  # pre-fix code listed children itself
        return {"status_code": 200, "items": driveitems._children(item_id)}

    with patch(_OUTBOX, _make_outbox_cls(rec, {})), patch(
        f"{_EXPENSE_MOD}.AzureBlobStorage", _make_storage_cls(rec)
    ), patch(f"{_EXPENSE_MOD}.MsDriveService", _make_drive_service_cls(rec, [_drive()])), patch(
        f"{_EXPENSE_MOD}.list_drive_item_children", list_children
    ), patch.object(svc, "_navigate_to_folder", return_value="R"):
        result = svc._upload_to_general_receipts_folder(_expense(date=date), lines)
    enqueues = [c for c in rec.calls if c[0] == "outbox.enqueue_sharepoint_upload"]
    parents = [dict(c[2])["parent_item_id"] for c in enqueues]
    return result, driveitems, parents


def test_missing_month_folder_is_created_and_used_as_upload_parent():
    tree = {"R": [_folder("2026", "Y26")], "Y26": [_folder("10", "M10")]}
    result, driveitems, parents = _run("2026-11-02", tree)
    assert result["success"] is True, result
    assert result["synced_count"] == 1
    assert driveitems.create_calls == [("Y26", "11")]
    assert parents == ["NEW-Y26-11"]


def test_missing_year_and_month_folders_are_created_and_chained():
    tree = {"R": [_folder("2026", "Y26")]}
    result, driveitems, parents = _run("2027-01-15", tree)
    assert result["success"] is True, result
    assert driveitems.create_calls == [("R", "2027"), ("NEW-R-2027", "01")]
    assert parents == ["NEW-NEW-R-2027-01"]


def test_existing_year_and_month_folders_are_reused_without_create():
    tree = {"R": [_folder("2026", "Y26")], "Y26": [_folder("10", "M10")]}
    result, driveitems, parents = _run("2026-10-04", tree)
    assert result["success"] is True, result
    assert driveitems.create_calls == []
    assert parents == ["M10"]


def test_month_create_failure_returns_existing_failure_result():
    tree = {"R": [_folder("2026", "Y26")], "Y26": []}
    result, driveitems, parents = _run(
        "2026-11-02", tree, {"11": {"status_code": 500, "message": "graph-down", "item": None}}
    )
    assert result == {
        "success": False,
        "message": "Could not get/create month folder '11'",
        "synced_count": 0,
        "skipped_count": 0,
        "errors": [],
    }
    assert parents == []


def test_year_create_with_no_item_returns_existing_failure_result():
    tree = {"R": []}
    result, driveitems, parents = _run(
        "2027-01-15", tree, {"2027": {"status_code": 201, "message": "odd", "item": None}}
    )
    assert result == {
        "success": False,
        "message": "Could not get/create year folder '2027'",
        "synced_count": 0,
        "skipped_count": 0,
        "errors": [],
    }
    assert driveitems.create_calls == [("R", "2027")]
    assert parents == []


# ---------------------------------------------------------------- REAL drive-item path
# The REAL MsDriveItemService.read_or_create_folder + the REAL client
# create_folder / _format_drive_item / 409 recovery run here; only the Graph
# transport (MsGraphClient, the children listing) and the drive repo are faked.


class FakeGraph:
    """Raw Graph DriveItems keyed by parent id; a folder has a `folder` facet."""

    def __init__(self, tree, list_fail=()):
        self.tree = {k: list(v) for k, v in tree.items()}
        self.list_fail = set(list_fail)
        self.posts: list = []
        self.gets: list = []

    def list_children(self, drive_id, item_id):
        if item_id in self.list_fail:
            return {"status_code": 503, "message": "list-down", "items": []}
        return {
            "status_code": 200,
            "items": [graph_client._format_drive_item(i) for i in self.tree.get(item_id, [])],
        }

    def client_cls(self):
        graph = self

        class FakeMsGraphClient:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def post(self, path, json=None, operation_name=None):
                graph.posts.append(path)
                parent = path.split("/items/")[1].split("/")[0]
                if any(i["name"] == json["name"] for i in graph.tree.get(parent, [])):
                    raise MsConflictError("nameAlreadyExists", http_status=409)
                item = {"id": f"G-{parent}-{json['name']}", "name": json["name"], "folder": {"childCount": 0}}
                graph.tree.setdefault(parent, []).append(item)
                return item

            def get(self, path, operation_name=None):
                graph.gets.append(path)
                parent, name = path.split("/items/")[1].split(":/")
                return next(i for i in graph.tree.get(parent, []) if i["name"] == name)

        return FakeMsGraphClient


def _gfolder(name, gid):
    return {"id": gid, "name": name, "folder": {"childCount": 1}}


def _gfile(name, gid):
    return {"id": gid, "name": name, "file": {"mimeType": "application/octet-stream"}, "size": 3}


def _run_real(date, tree, list_fail=()):
    graph = FakeGraph(tree, list_fail)
    drive_repo = MagicMock()
    drive_repo.read_by_public_id.return_value = SimpleNamespace(drive_id="DRIVE-SD")
    driveitems = MsDriveItemService(repo=MagicMock(), drive_repo=drive_repo)
    with patch.object(graph_client, "MsGraphClient", graph.client_cls()), patch.object(
        graph_client, "list_drive_item_children", graph.list_children
    ):
        result, _, parents = _run(date, tree={}, driveitems=driveitems)
    return result, graph, parents


def test_real_path_created_folder_item_id_is_upload_parent():
    tree = {"R": [_gfolder("2026", "Y26")], "Y26": [_gfolder("10", "M10")]}
    result, graph, parents = _run_real("2026-11-02", tree)
    assert result["success"] is True, result
    assert graph.posts == ["drives/DRIVE-SD/items/Y26/children"]
    assert parents == ["G-Y26-11"]


def test_real_path_409_recovery_of_existing_folder_is_used():
    tree = {"R": [_gfolder("2026", "Y26")], "Y26": [_gfolder("10", "M10")]}
    result, graph, parents = _run_real("2026-10-04", tree, list_fail=("Y26",))
    assert result["success"] is True, result
    assert graph.posts == ["drives/DRIVE-SD/items/Y26/children"]
    assert graph.gets == ["drives/DRIVE-SD/items/Y26:/10"]
    assert parents == ["M10"]


def test_real_path_409_recovery_of_a_file_is_rejected():
    tree = {"R": [_gfolder("2026", "Y26")], "Y26": [_gfile("11", "FILE-11")]}
    result, graph, parents = _run_real("2026-11-02", tree)
    assert result == {
        "success": False,
        "message": "Could not get/create month folder '11'",
        "synced_count": 0,
        "skipped_count": 0,
        "errors": [],
    }
    assert graph.gets == ["drives/DRIVE-SD/items/Y26:/11"]
    assert parents == []


def test_year_name_taken_by_a_file_is_rejected():
    tree = {"R": [_gfile("2026", "FILE-2026")]}
    result, graph, parents = _run_real("2026-10-04", tree)
    assert result["message"] == "Could not get/create year folder '2026'"
    assert result["success"] is False
    assert parents == []
