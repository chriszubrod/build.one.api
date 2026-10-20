"""U-679 — contract-labor router must not leak access denials or exception text (P0)."""

from __future__ import annotations

import inspect
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app import app
from entities.contract_labor.api.router import (
    generate_bills_for_vendor,
    regenerate_pdf_for_entries,
)
from shared.access import EntityNotAccessibleError
from shared.api.errors import ErrorCode


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


def test_regenerate_pdf_entity_not_accessible_returns_404_without_bill_id(api_client):
    _override_dep(regenerate_pdf_for_entries)
    with patch(
        "entities.contract_labor.business.bill_service.ContractLaborBillService"
    ) as bill_svc_cls:
        bill_svc_cls.return_value.regenerate_pdf_for_entries.side_effect = (
            EntityNotAccessibleError("Bill", 888)
        )
        response = api_client.post(
            "/api/v1/contract-labor/billing/regenerate-pdf",
            json={"public_ids": ["cl-entry-1"]},
        )
    assert response.status_code == 404
    body = response.json()
    assert body["error_code"] == ErrorCode.NOT_FOUND
    assert body["detail"] == "Not found"
    assert "888" not in response.text
    assert "Bill" not in response.text


def test_regenerate_pdf_unexpected_error_returns_500_fixed_detail(api_client):
    _override_dep(regenerate_pdf_for_entries)
    secret = "ODBC driver secret-xyz"
    with patch(
        "entities.contract_labor.business.bill_service.ContractLaborBillService"
    ) as bill_svc_cls:
        bill_svc_cls.return_value.regenerate_pdf_for_entries.side_effect = RuntimeError(
            secret
        )
        response = api_client.post(
            "/api/v1/contract-labor/billing/regenerate-pdf",
            json={"public_ids": ["cl-entry-1"]},
        )
    assert response.status_code == 500
    assert response.json()["detail"] == "Failed to regenerate PDF for entries"
    assert secret not in response.text


def test_generate_bills_entity_not_accessible_returns_404(api_client):
    _override_dep(generate_bills_for_vendor)
    with patch(
        "entities.contract_labor.business.bill_service.ContractLaborBillService"
    ) as bill_svc_cls:
        bill_svc_cls.return_value.generate_bills_for_vendor.side_effect = (
            EntityNotAccessibleError("Bill", 999)
        )
        response = api_client.post("/api/v1/contract-labor/generate-bills/1")
    assert response.status_code == 404
    assert response.json()["error_code"] == ErrorCode.NOT_FOUND
    assert "999" not in response.text


def test_generate_bills_unexpected_error_returns_500_fixed_detail(api_client):
    _override_dep(generate_bills_for_vendor)
    secret = "internal schema leak"
    with patch(
        "entities.contract_labor.business.bill_service.ContractLaborBillService"
    ) as bill_svc_cls:
        bill_svc_cls.return_value.generate_bills_for_vendor.side_effect = RuntimeError(
            secret
        )
        response = api_client.post("/api/v1/contract-labor/generate-bills/1")
    assert response.status_code == 500
    assert response.json()["detail"] == "Failed to generate bills for vendor"
    assert secret not in response.text
