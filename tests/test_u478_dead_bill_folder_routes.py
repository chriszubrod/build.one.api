"""U-478 — delete four dead Bill folder-intake routes and one dead helper.

The four routes (`/process/bill-folder-pending`, `-single`, `-prepare`, `-move`)
and `_run_single_file_processing` have zero callers. Two are booked defects:

- `POST /process/bill-folder-single` writes `_folder_processing_results`, a
  name defined nowhere in the repo since run state moved to `dbo.BillFolderRun`.
  It NameError's on 100% of calls, inside the handler, before the background
  task is even queued. Repairing it is a second processing path in parallel
  with the live queue (bare uuid4() is never inserted into BillFolderRun, so a
  client would poll a status route that 404s — and there is no status route).
- `POST /process/bill-folder-move` is gated on default `can_read`, and its
  name-conflict branch reaches `sp_client.delete_item`, so a view-only AP clerk
  could permanently delete a PDF from the SharePoint processed folder. Deleting
  the route removes the exposure outright.

Live single-file processing is already
`enqueue_bill_folder_run` → `POST /admin/bill-folder/tick` →
`BillFolderProcessor.process_single_item`. The three routes with live callers
in BillList.tsx (`POST /process/bill-folder`, `GET /process/bill-folder/{run_id}`,
`GET /get/bill-folder-summary`) stay.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ROUTER_PATH = REPO_ROOT / "entities/bill/api/router.py"

DELETED_PATHS = (
    "/process/bill-folder-pending",
    "/process/bill-folder-single",
    "/process/bill-folder-prepare",
    "/process/bill-folder-move",
)
DELETED_HANDLERS = (
    "list_pending_files_router",
    "process_single_file_router",
    "prepare_file_for_create_router",
    "move_file_to_processed_router",
    "_run_single_file_processing",
)
SURVIVING_PATHS = (
    "/process/bill-folder",
    "/process/bill-folder/{run_id}",
    "/get/bill-folder-summary",
)
SURVIVING_HANDLERS = (
    "process_bill_folder_router",
    "get_bill_folder_status_router",
    "get_bill_folder_summary_router",
)


def _router_src() -> str:
    return ROUTER_PATH.read_text()


def _folder_route_paths(router) -> set[str]:
    return {route.path for route in router.routes if "folder" in getattr(route, "path", "")}


def test_deleted_paths_and_handlers_absent_from_source():
    src = _router_src()
    for path in DELETED_PATHS:
        assert path not in src, f"dead path still in router.py: {path}"
    for name in DELETED_HANDLERS:
        assert name not in src, f"dead handler still in router.py: {name}"


def test_folder_processing_results_absent_from_source():
    """The NameError is the regression that matters: the name must be gone.

    An unreferenced leftover would still blow up if a future handler wrote it.
    """
    src = _router_src()
    count = src.count("_folder_processing_results")
    assert count == 0, (
        f"_folder_processing_results still appears {count} time(s) in router.py"
    )


def test_surviving_routes_present_in_source():
    src = _router_src()
    for path in SURVIVING_PATHS:
        assert path in src, f"live path missing from router.py: {path}"
    for name in SURVIVING_HANDLERS:
        assert name in src, f"live handler missing from router.py: {name}"


def test_router_exposes_surviving_folder_paths_only():
    """Walk the registered FastAPI routes, not just source text.

    Catches a decorator delete that left the function, or the reverse.
    """
    import entities.bill.api.router as bill_router

    prefix = bill_router.router.prefix.rstrip("/")
    expected = {f"{prefix}{path}" for path in SURVIVING_PATHS}
    deleted = {f"{prefix}{path}" for path in DELETED_PATHS}
    actual = _folder_route_paths(bill_router.router)
    assert actual == expected
    assert actual.isdisjoint(deleted)


def test_live_single_file_path_is_process_single_item():
    """Tripwire: the queue path this unit relies on as the replacement."""
    from entities.bill.business.folder_processor import BillFolderProcessor

    assert callable(getattr(BillFolderProcessor, "process_single_item", None)), (
        "BillFolderProcessor.process_single_item is the live single-file path; "
        "do not delete it without replacing the intake drain"
    )
