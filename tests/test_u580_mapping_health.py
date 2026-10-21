"""U-580 — durable MS reconciliation for missing SharePoint mappings + mapping health."""

import inspect
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import app
from integrations.ms.sharepoint.driveitem.api.schemas import (
    classify_sharepoint_mapping_health_status,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import actor_absent, actor_context  # noqa: E402


MODULE_FOLDER_CASES = [
    (
        "expense",
        "entities.expense.business.service.ExpenseService",
        "_upload_attachments_to_module_folder",
        "Expense",
    ),
    (
        "bill",
        "entities.bill.business.service.BillService",
        "_upload_attachments_to_module_folder",
        "Bill",
    ),
    (
        "bill_credit",
        "entities.bill_credit.business.complete_service.BillCreditCompleteService",
        "_upload_attachments_to_module_folder",
        "BillCredit",
    ),
]

EXCEL_RECORDING_CASES = [
    (
        "bill_excel_single",
        "entities.bill.business.service.BillService",
        "sync_to_excel_workbook",
        "Bill",
        False,
    ),
    (
        "bill_excel_batch",
        "entities.bill.business.service.BillService",
        "sync_bills_batch_to_excel",
        "Bill",
        True,
    ),
    (
        "expense_excel_single",
        "entities.expense.business.service.ExpenseService",
        "sync_to_excel_workbook",
        "Expense",
        False,
    ),
    (
        "expense_excel_batch",
        "entities.expense.business.service.ExpenseService",
        "sync_expenses_batch_to_excel",
        "Expense",
        True,
    ),
    (
        "bill_credit_excel_single",
        "entities.bill_credit.business.complete_service.BillCreditCompleteService",
        "sync_to_excel_workbook",
        "BillCredit",
        False,
    ),
    (
        "bill_credit_excel_batch",
        "entities.bill_credit.business.complete_service.BillCreditCompleteService",
        "sync_bill_credits_batch_to_excel",
        "BillCredit",
        True,
    ),
]

_OK_FLAGS = {
    "has_root": True,
    "has_bills_folder": True,
    "has_expenses_folder": True,
    "has_invoices_folder": True,
    "has_bill_credits_folder": False,
    "has_excel": True,
}

_REQUIRED_OK_FLAG_NAMES = (
    "has_root",
    "has_bills_folder",
    "has_expenses_folder",
    "has_invoices_folder",
    "has_excel",
)


def _load_service(service_path: str):
    module_path, class_name = service_path.rsplit(".", 1)
    service_mod = __import__(module_path, fromlist=[class_name])
    return getattr(service_mod, class_name)()


@pytest.mark.parametrize(
    "entity_key,service_path,method_name,entity_type",
    MODULE_FOLDER_CASES,
    ids=[c[0] for c in MODULE_FOLDER_CASES],
)
def test_missing_module_folder_records_project_mapping_missing(
    entity_key, service_path, method_name, entity_type
):
    service = _load_service(service_path)

    entity = SimpleNamespace(public_id="11111111-1111-1111-1111-111111111111")
    line_items = [SimpleNamespace(public_id="22222222-2222-2222-2222-222222222222")]
    project_id = 56
    if entity_key == "expense":
        entity.id = 1
        service._expense_line_item_service = MagicMock()
        service._expense_line_item_service.read_by_expense_id.return_value = line_items
        service.sub_cost_code_service.is_holding_code = MagicMock(return_value=False)

    service.module_service = MagicMock()
    service.module_service.read_by_name.return_value = SimpleNamespace(id=16)
    mock_connector = MagicMock()
    mock_connector.get_folder_for_module.return_value = None
    if entity_key == "bill_credit":
        service.project_module_connector = mock_connector
    else:
        service._project_module_connector = mock_connector

    with patch(
        "integrations.ms.reconciliation.business.service.MsReconciliationIssueService"
    ) as mock_svc_cls:
        mock_svc_cls.return_value.flag_project_mapping_missing = MagicMock()
        if entity_key == "bill":
            result = getattr(service, method_name)(
                entity, line_items, project_id, bill_line_items_count=1
            )
        else:
            result = getattr(service, method_name)(entity, line_items, project_id)

    assert result["success"] is False
    assert "Module folder not linked" in result["message"]
    mock_svc_cls.return_value.flag_project_mapping_missing.assert_called_once()
    call_kwargs = mock_svc_cls.return_value.flag_project_mapping_missing.call_args.kwargs
    assert call_kwargs["entity_type"] == entity_type
    assert call_kwargs["mapping_kind"] == "module_folder"
    assert call_kwargs["project_id"] == project_id


@pytest.mark.parametrize(
    "case_id,service_path,method_name,entity_type,is_batch",
    EXCEL_RECORDING_CASES,
    ids=[c[0] for c in EXCEL_RECORDING_CASES],
)
def test_missing_excel_mapping_records_project_mapping_missing(
    case_id, service_path, method_name, entity_type, is_batch
):
    service = _load_service(service_path)
    project_id = 99
    entity = SimpleNamespace(
        public_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        vendor_id=1,
    )
    line_item = SimpleNamespace(
        public_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        project_id=project_id,
    )

    mock_excel = MagicMock()
    mock_excel.get_excel_for_project.return_value = None
    if hasattr(service, "_project_excel_connector"):
        service._project_excel_connector = mock_excel
    else:
        service.project_excel_connector = mock_excel

    with patch(
        "integrations.ms.reconciliation.business.service.MsReconciliationIssueService"
    ) as mock_svc_cls:
        mock_svc_cls.return_value.flag_project_mapping_missing = MagicMock()
        if is_batch:
            result = getattr(service, method_name)(
                [(entity, [line_item])], project_id=project_id
            )
        elif case_id == "expense_excel_single":
            result = getattr(service, method_name)(entity, [line_item], project_id=project_id)
        else:
            result = getattr(service, method_name)(entity, [line_item], project_id)

    assert result["success"] is False
    mock_svc_cls.return_value.flag_project_mapping_missing.assert_called_once()
    excel_call = mock_svc_cls.return_value.flag_project_mapping_missing.call_args.kwargs
    assert excel_call["mapping_kind"] == "excel"
    assert excel_call["entity_type"] == entity_type
    assert excel_call["project_id"] == project_id


def test_flag_project_mapping_missing_persists_drift_type():
    from integrations.ms.reconciliation.business.service import MsReconciliationIssueService

    expected_issue = SimpleNamespace(public_id="issue-id")
    with patch(
        "integrations.ms.reconciliation.business.service.MsReconciliationIssueRepository"
    ) as repo_cls:
        repo_cls.return_value.create.return_value = expected_issue
        with patch.object(
            MsReconciliationIssueService,
            "_resolve_tenant_id_for_audit",
            return_value="tenant-1",
        ):
            issue = MsReconciliationIssueService().flag_project_mapping_missing(
                entity_type="Bill",
                entity_public_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                project_id=56,
                mapping_kind="module_folder",
                details="Module folder not linked for project 56",
            )

        repo_cls.return_value.create.assert_called_once()
        create_kwargs = repo_cls.return_value.create.call_args.kwargs
        assert create_kwargs["drift_type"] == "project_mapping_missing"
        assert create_kwargs["severity"] == "high"
        assert create_kwargs["action"] == "flagged"
        assert create_kwargs["tenant_id"] == "tenant-1"
        assert "project_id=56" in create_kwargs["details"]
        assert "mapping_kind=module_folder" in create_kwargs["details"]
        assert issue is expected_issue


def test_flag_project_mapping_missing_uses_unknown_tenant_when_unresolved():
    from integrations.ms.reconciliation.business.service import MsReconciliationIssueService

    with patch(
        "integrations.ms.reconciliation.business.service.MsReconciliationIssueRepository"
    ) as repo_cls:
        repo_cls.return_value.create.return_value = SimpleNamespace(public_id="x")
        with patch.object(
            MsReconciliationIssueService,
            "_resolve_tenant_id_for_audit",
            return_value=None,
        ):
            MsReconciliationIssueService().flag_project_mapping_missing(
                entity_type="Expense",
                entity_public_id="cccccccc-cccc-cccc-cccc-cccccccccccc",
                project_id=1,
                mapping_kind="excel",
                details="Excel not linked",
            )
        assert repo_cls.return_value.create.call_args.kwargs["tenant_id"] == "unknown"


RECORDER_FAILURE_CASES = [
    (
        "bill_module_folder",
        "entities.bill.business.service.BillService",
        "_upload_attachments_to_module_folder",
        lambda svc: svc._upload_attachments_to_module_folder(
            SimpleNamespace(public_id="55555555-5555-5555-5555-555555555555"),
            [SimpleNamespace(public_id="66666666-6666-6666-6666-666666666666")],
            project_id=12,
            bill_line_items_count=1,
        ),
        {
            "success": False,
            "message": "Module folder not linked for project 12",
            "synced_count": 0,
            "skipped_count": 0,
            "errors": [{"error": "Module folder not linked for project 12"}],
        },
        lambda svc: (
            setattr(
                svc,
                "module_service",
                MagicMock(read_by_name=MagicMock(return_value=SimpleNamespace(id=2))),
            ),
            setattr(
                svc,
                "_project_module_connector",
                MagicMock(get_folder_for_module=MagicMock(return_value=None)),
            ),
        ),
    ),
    (
        "expense_excel_batch",
        "entities.expense.business.service.ExpenseService",
        "sync_expenses_batch_to_excel",
        lambda svc: svc.sync_expenses_batch_to_excel(
            [
                (
                    SimpleNamespace(
                        public_id="77777777-7777-7777-7777-777777777777",
                        vendor_id=1,
                    ),
                    [
                        SimpleNamespace(
                            public_id="88888888-8888-8888-8888-888888888888",
                            project_id=44,
                        )
                    ],
                )
            ],
            project_id=44,
        ),
        {
            "success": False,
            "message": "Excel not linked for project 44",
            "synced_count": 0,
            "errors": [{"error": "Excel not linked for project 44"}],
        },
        lambda svc: setattr(
            svc,
            "_project_excel_connector",
            MagicMock(get_excel_for_project=MagicMock(return_value=None)),
        ),
    ),
]


@pytest.mark.parametrize(
    "case_id,service_path,_,invoke,expected,setup",
    RECORDER_FAILURE_CASES,
    ids=[c[0] for c in RECORDER_FAILURE_CASES],
)
def test_recorder_failure_does_not_change_completion_failure_dict(
    case_id, service_path, _, invoke, expected, setup
):
    service = _load_service(service_path)
    setup(service)

    with patch(
        "integrations.ms.reconciliation.business.service.MsReconciliationIssueService"
    ) as mock_svc_cls:
        mock_svc_cls.return_value.flag_project_mapping_missing.side_effect = RuntimeError(
            "boom"
        )
        result = invoke(service)

    mock_svc_cls.return_value.flag_project_mapping_missing.assert_called_once()
    assert result == expected


@pytest.mark.parametrize(
    "flags,expected",
    [
        (_OK_FLAGS, "ok"),
        (
            {
                "has_root": False,
                "has_bills_folder": False,
                "has_expenses_folder": False,
                "has_invoices_folder": False,
                "has_bill_credits_folder": False,
                "has_excel": False,
            },
            "unmapped",
        ),
        (
            {
                "has_root": True,
                "has_bills_folder": False,
                "has_expenses_folder": False,
                "has_invoices_folder": False,
                "has_bill_credits_folder": False,
                "has_excel": False,
            },
            "partial",
        ),
    ],
)
def test_classify_sharepoint_mapping_health_status(flags, expected):
    assert classify_sharepoint_mapping_health_status(**flags) == expected


@pytest.mark.parametrize("false_flag", _REQUIRED_OK_FLAG_NAMES)
def test_classify_each_required_flag_false_yields_partial(false_flag):
    flags = dict(_OK_FLAGS)
    flags[false_flag] = False
    assert classify_sharepoint_mapping_health_status(**flags) == "partial"


_MAPPING_HEALTH_REPO_ROWS = [
    {
        "project_id": 1,
        "project_name": "Low volume",
        "has_root": False,
        "has_bills_folder": False,
        "has_expenses_folder": False,
        "has_invoices_folder": False,
        "has_bill_credits_folder": False,
        "has_excel": False,
        "worksheet_name": None,
        "expense_lines_90d": 1,
        "bill_lines_90d": 0,
    },
    {
        "project_id": 2,
        "project_name": "High volume",
        "has_root": True,
        "has_bills_folder": True,
        "has_expenses_folder": True,
        "has_invoices_folder": True,
        "has_bill_credits_folder": False,
        "has_excel": True,
        "worksheet_name": "DETAILS",
        "expense_lines_90d": 10,
        "bill_lines_90d": 40,
    },
]


@pytest.mark.parametrize(
    "user_id,is_system_admin",
    [
        (42, False),
        (17, True),
        (None, False),
    ],
    ids=["non_admin", "system_admin", "absent_actor"],
)
def test_mapping_health_forwards_callers_actor_to_repo(user_id, is_system_admin):
    """MEASURED RED: hardcode actor_is_system_admin=True in the router and non_admin fails."""
    from integrations.ms.sharepoint.driveitem.api import router as driveitem_router

    handler = driveitem_router.sharepoint_mapping_health_router
    rbac_dependency = inspect.signature(handler).parameters["current_user"].default.dependency
    app.dependency_overrides[rbac_dependency] = lambda: {"sub": "u580-actor-test"}
    client = TestClient(app, raise_server_exceptions=False)

    try:
        with actor_context(user_id=user_id, is_system_admin=is_system_admin):
            with patch(
                "integrations.ms.sharepoint.driveitem.api.router.MsDriveItemRepository"
            ) as mock_repo_cls:
                mock_repo_cls.return_value.read_sharepoint_mapping_health_rows.return_value = []
                client.get("/api/v1/ms/sharepoint/driveitem/connector/mapping-health")
                kwargs = (
                    mock_repo_cls.return_value.read_sharepoint_mapping_health_rows.call_args.kwargs
                )
                assert kwargs["actor_user_id"] == user_id
                assert kwargs["actor_is_system_admin"] is is_system_admin
                if actor_absent(kwargs):
                    assert user_id is None and is_system_admin is False
    finally:
        app.dependency_overrides.clear()


def test_mapping_health_endpoint_orders_by_90d_activity_desc():
    from integrations.ms.sharepoint.driveitem.api import router as driveitem_router

    handler = driveitem_router.sharepoint_mapping_health_router
    rbac_dependency = inspect.signature(handler).parameters["current_user"].default.dependency
    app.dependency_overrides[rbac_dependency] = lambda: {"sub": "u580-test"}
    client = TestClient(app, raise_server_exceptions=False)

    try:
        with actor_context(user_id=17, is_system_admin=True):
            with patch(
                "integrations.ms.sharepoint.driveitem.api.router.MsDriveItemRepository"
            ) as mock_repo_cls:
                mock_repo_cls.return_value.read_sharepoint_mapping_health_rows.return_value = (
                    _MAPPING_HEALTH_REPO_ROWS
                )
                response = client.get(
                    "/api/v1/ms/sharepoint/driveitem/connector/mapping-health"
                )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    items = response.json()["data"]["items"]
    assert [row["project_id"] for row in items] == [2, 1]
    assert items[0]["status"] == "ok"
    assert items[1]["status"] == "unmapped"
