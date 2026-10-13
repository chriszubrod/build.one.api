"""U-656 — SharePoint bill/expense folder intake retired (U-478 dead routes stay gone).

Manual web entry is the only intake path. dbo.BillFolderRun* tables remain for
history/backtest; Python processors, admin ticks, and API folder routes must not
come back.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

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

RETIRED_FOLDER_ROUTE_FRAGMENTS = (
    "bill-folder",
    "expense-folder",
)

RETIRED_MODULES = (
    "entities.bill.business.folder_processor",
    "entities.expense.business.folder_processor",
    "entities.bill.persistence.folder_run_repo",
    "entities.expense.persistence.folder_run_repo",
    "integrations.ms.sharepoint.driveitem.connector.bill_folder.api",
    "integrations.ms.sharepoint.driveitem.connector.bill_folder.business",
    "integrations.ms.sharepoint.driveitem.connector.bill_folder.persistence",
    "integrations.ms.sharepoint.driveitem.connector.expense_folder.api",
    "integrations.ms.sharepoint.driveitem.connector.expense_folder.business",
    "integrations.ms.sharepoint.driveitem.connector.expense_folder.persistence",
)


def _router_src() -> str:
    return ROUTER_PATH.read_text()


def test_u478_deleted_paths_and_handlers_absent_from_bill_router_source():
    src = _router_src()
    for path in DELETED_PATHS:
        assert path not in src, f"dead path still in router.py: {path}"
    for name in DELETED_HANDLERS:
        assert name not in src, f"dead handler still in router.py: {name}"


def test_folder_processing_results_absent_from_bill_router_source():
    src = _router_src()
    assert src.count("_folder_processing_results") == 0


def test_no_bill_or_expense_folder_routes_registered():
    from app import app

    paths = {getattr(route, "path", "") for route in app.routes}
    hits = sorted(
        p for p in paths if any(frag in p for frag in RETIRED_FOLDER_ROUTE_FRAGMENTS)
    )
    assert hits == [], f"retired folder routes still registered: {hits}"


@pytest.mark.parametrize("module_name", RETIRED_MODULES)
def test_retired_folder_intake_modules_not_importable(module_name: str):
    spec = importlib.util.find_spec(module_name)
    assert spec is None, f"retired module still on disk: {module_name}"
