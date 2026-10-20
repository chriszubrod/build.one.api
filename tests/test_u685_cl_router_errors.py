"""U-685 — contract-labor router catch-all 500s must not leak exception text (P0)."""

from __future__ import annotations

import inspect
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app import app
from entities.contract_labor.api.router import (
    generate_all_pdfs,
    generate_pdfs_for_bill,
    mark_contract_labor_as_ready,
    preview_pdf_for_vendor,
    update_contract_labor_bill,
)
from shared.access import EntityNotAccessibleError
from shared.api.errors import ErrorCode
from shared.lifecycle.terminal_lock import StatusLockedError

_ODBC_LEAK = "[Microsoft][ODBC Driver 18 for SQL Server] secret-xyz"
_ENTITY_ID = 987654


@pytest.fixture
def api_client():
    return TestClient(app, raise_server_exceptions=False)


def _override_dep(route_fn):
    dep = inspect.signature(route_fn).parameters["current_user"].default.dependency
    app.dependency_overrides[dep] = lambda: {"sub": "test-sub"}


@pytest.fixture(autouse=True)
def _clear_dependency_overrides():
    yield
    app.dependency_overrides.clear()


@pytest.mark.parametrize(
    "route_fn,method,url,json_body,patch_target,patch_attr,fixed_detail",
    [
        (
            update_contract_labor_bill,
            "put",
            "/api/v1/contract-labor/cl-1/bill",
            {"row_version": "AAAA", "line_items": []},
            "entities.contract_labor.api.router.ContractLaborService",
            "read_by_public_id",
            "Failed to update contract labor bill",
        ),
        (
            mark_contract_labor_as_ready,
            "post",
            "/api/v1/contract-labor/cl-1/mark-ready",
            None,
            "entities.contract_labor.api.router.ContractLaborService",
            "mark_as_ready",
            "Failed to mark contract labor as ready",
        ),
        (
            generate_pdfs_for_bill,
            "post",
            "/api/v1/contract-labor/billing/generate-pdfs/bill-1",
            None,
            "entities.contract_labor.business.pdf_service.ContractLaborPDFService",
            "generate_pdfs_for_bill",
            "Failed to generate PDFs for bill",
        ),
        (
            generate_all_pdfs,
            "post",
            "/api/v1/contract-labor/billing/generate-all-pdfs",
            None,
            "entities.contract_labor.business.pdf_service.ContractLaborPDFService",
            "generate_pdfs_for_billed_entries",
            "Failed to generate PDFs",
        ),
        (
            preview_pdf_for_vendor,
            "get",
            "/api/v1/contract-labor/preview-pdf/1",
            None,
            "entities.contract_labor.business.bill_service.ContractLaborBillService",
            "preview_pdf_for_vendor",
            "Failed to preview PDF",
        ),
    ],
)
def test_unexpected_error_returns_500_fixed_detail(
    api_client,
    route_fn,
    method,
    url,
    json_body,
    patch_target,
    patch_attr,
    fixed_detail,
):
    _override_dep(route_fn)
    with patch(patch_target) as svc_cls:
        getattr(svc_cls.return_value, patch_attr).side_effect = RuntimeError(_ODBC_LEAK)
        request = getattr(api_client, method)
        kwargs = {} if json_body is None else {"json": json_body}
        response = request(url, **kwargs)
    assert response.status_code == 500
    assert response.json()["detail"] == fixed_detail
    assert "secret-xyz" not in response.text
    assert "ODBC" not in response.text


@pytest.mark.parametrize(
    "route_fn,method,url,json_body,patch_target,patch_attr,entity_name",
    [
        (
            update_contract_labor_bill,
            "put",
            "/api/v1/contract-labor/cl-1/bill",
            {"row_version": "AAAA", "line_items": []},
            "entities.contract_labor.api.router.ContractLaborService",
            "read_by_public_id",
            "ContractLabor",
        ),
        (
            mark_contract_labor_as_ready,
            "post",
            "/api/v1/contract-labor/cl-1/mark-ready",
            None,
            "entities.contract_labor.api.router.ContractLaborService",
            "mark_as_ready",
            "ContractLabor",
        ),
        (
            preview_pdf_for_vendor,
            "get",
            "/api/v1/contract-labor/preview-pdf/1",
            None,
            "entities.contract_labor.business.bill_service.ContractLaborBillService",
            "preview_pdf_for_vendor",
            "Bill",
        ),
    ],
)
def test_entity_not_accessible_returns_404(
    api_client,
    route_fn,
    method,
    url,
    json_body,
    patch_target,
    patch_attr,
    entity_name,
):
    _override_dep(route_fn)
    with patch(patch_target) as svc_cls:
        getattr(svc_cls.return_value, patch_attr).side_effect = EntityNotAccessibleError(
            entity_name, _ENTITY_ID
        )
        request = getattr(api_client, method)
        kwargs = {} if json_body is None else {"json": json_body}
        response = request(url, **kwargs)
    assert response.status_code == 404
    assert response.json()["error_code"] == ErrorCode.NOT_FOUND
    assert str(_ENTITY_ID) not in response.text


def test_mark_ready_value_error_still_returns_400_with_service_message(api_client):
    _override_dep(mark_contract_labor_as_ready)
    msg = "Vendor is required before marking as ready."
    with patch("entities.contract_labor.api.router.ContractLaborService") as cl_svc_cls:
        cl_svc_cls.return_value.mark_as_ready.side_effect = ValueError(msg)
        response = api_client.post("/api/v1/contract-labor/cl-1/mark-ready")
    assert response.status_code == 400
    assert response.json()["detail"] == msg


def test_generate_pdfs_for_bill_status_locked_still_422(api_client):
    _override_dep(generate_pdfs_for_bill)
    with patch(
        "entities.contract_labor.business.pdf_service.ContractLaborPDFService"
    ) as pdf_svc_cls:
        pdf_svc_cls.return_value.generate_pdfs_for_bill.side_effect = StatusLockedError(
            "time-log PDFs cannot be generated for it"
        )
        response = api_client.post("/api/v1/contract-labor/billing/generate-pdfs/bill-1")
    assert response.status_code == 422
    assert response.json()["error_code"] == ErrorCode.STATUS_LOCKED
