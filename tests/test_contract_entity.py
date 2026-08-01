"""U-188 pure tests for the (minimal) Contract entity: model Decimal round-trip,
schema validation, service money coercion, per-project access scoping, stale-
RowVersion → 409, and the instant-workflow dispatch contract.

MINIMAL BY DESIGN: BuildersFeeRate is the only business field — the full contract
model is deferred to a formal design conversation. These tests track that shape.

Pure-logic only (no live DB). The CRUD sprocs are DB-integration and are covered
by the SQL guard tests (nocount shape, repo/sproc param contract) that auto-scan
the base file — this file exercises the Python layers with a stub repo and a
mocked access check (`shared.access.assert_can_access_project`).
"""

import inspect
from decimal import Decimal
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from core.workflow.business.definitions.instant import (
    SYNCHRONOUS_TASKS,
    is_instant_workflow_type,
    parse_instant_workflow_type,
)
from core.workflow.business.instant import (
    METHOD_MAPPING,
    PROCESS_REGISTRY,
    _build_service_kwargs,
)
from shared.access import EntityNotAccessibleError
from shared.api.responses import raise_workflow_error
from entities.contract.api.schemas import ContractCreate, ContractUpdate
from entities.contract.business.model import Contract
from entities.contract.business.service import ContractService, _coerce_decimal

_ACCESS = "entities.contract.business.service.assert_can_access_project"


# --------------------------------------------------------------------------- #
# Model — Decimal round-trip                                                   #
# --------------------------------------------------------------------------- #

def _make_contract(**overrides) -> Contract:
    base = dict(
        id=1,
        public_id="11111111-1111-1111-1111-111111111111",
        row_version=None,
        created_datetime="2026-08-01 00:00:00",
        modified_datetime=None,
        created_by_user_id=17,
        project_id=128,
        builders_fee_rate=Decimal(str("0.100000")),
    )
    base.update(overrides)
    return Contract(**base)


def test_model_fee_rate_is_decimal_not_float():
    c = _make_contract()
    assert isinstance(c.builders_fee_rate, Decimal)
    assert not isinstance(c.builders_fee_rate, float)


def test_model_to_dict_serializes_fee_rate_as_string():
    d = _make_contract().to_dict()
    assert d["builders_fee_rate"] == "0.100000"
    assert d["project_id"] == 128


def test_model_to_dict_leaves_none_fee_rate_as_none():
    d = _make_contract(builders_fee_rate=None).to_dict()
    assert d["builders_fee_rate"] is None


def test_model_has_only_the_minimal_fields():
    """Guard the minimal shape — no deferred business columns leaked back in."""
    fields = set(Contract.__dataclass_fields__)
    assert fields == {
        "id",
        "public_id",
        "row_version",
        "created_datetime",
        "modified_datetime",
        "created_by_user_id",
        "project_id",
        "builders_fee_rate",
    }


def test_fee_rate_not_built_from_float_literal():
    """0.1 is not exactly float-representable. The fraction must equal
    Decimal('0.1') and Decimal(str(0.1)) (the required idiom) but NOT the raw
    Decimal(0.1) — proving the value did not pass through a float."""
    rate = _coerce_decimal("0.1")
    assert rate == Decimal("0.1")
    assert rate == Decimal(str(0.1))
    assert rate != Decimal(0.1)


# --------------------------------------------------------------------------- #
# Schema validation                                                           #
# --------------------------------------------------------------------------- #

def test_create_schema_coerces_fee_rate_to_decimal():
    m = ContractCreate(project_id=128, builders_fee_rate="0.1")
    assert isinstance(m.builders_fee_rate, Decimal)
    assert m.builders_fee_rate == Decimal("0.1")


def test_create_schema_requires_project_id():
    with pytest.raises(ValidationError):
        ContractCreate(builders_fee_rate="0.1")


def test_create_schema_fee_rate_optional():
    m = ContractCreate(project_id=5)
    assert m.builders_fee_rate is None


def test_update_schema_requires_row_version():
    with pytest.raises(ValidationError):
        ContractUpdate(builders_fee_rate="0.14")


def test_update_schema_accepts_fee_rate():
    m = ContractUpdate(row_version="AAAAAAAAB9E=", builders_fee_rate="0.14")
    assert m.builders_fee_rate == Decimal("0.14")


# --------------------------------------------------------------------------- #
# Service — money coercion + per-project access scoping (mocked access check)  #
# --------------------------------------------------------------------------- #

class _StubRepo:
    def __init__(self):
        self.create_kwargs = None
        self.updated = None
        self._row = None
        self._project_rows = []
        self.update_returns_none = False

    def create(self, **kwargs):
        self.create_kwargs = kwargs
        return "created"

    def read_by_public_id(self, public_id):
        return self._row

    def read_by_project_id(self, project_id):
        return list(self._project_rows)

    def update_by_public_id(self, contract):
        self.updated = contract
        return None if self.update_returns_none else contract


@patch(_ACCESS)
def test_service_create_asserts_project_access_then_coerces(mock_access):
    repo = _StubRepo()
    ContractService(repo=repo).create(project_id=128, builders_fee_rate="0.1")
    # Access to the TARGET project is checked before insert.
    mock_access.assert_called_once_with(128)
    kw = repo.create_kwargs
    assert isinstance(kw["builders_fee_rate"], Decimal)
    assert not isinstance(kw["builders_fee_rate"], float)
    assert kw["builders_fee_rate"] == Decimal("0.1")


@patch(_ACCESS, side_effect=EntityNotAccessibleError("Project", 5))
def test_service_create_blocked_for_inaccessible_project(mock_access):
    repo = _StubRepo()
    with pytest.raises(EntityNotAccessibleError):
        ContractService(repo=repo).create(project_id=5, builders_fee_rate="0.1")
    # Nothing inserted when access is denied.
    assert repo.create_kwargs is None


@patch(_ACCESS)
def test_service_read_by_public_id_asserts_fetched_project(mock_access):
    repo = _StubRepo()
    repo._row = _make_contract(project_id=7)
    result = ContractService(repo=repo).read_by_public_id("pub")
    mock_access.assert_called_once_with(7)
    assert result is repo._row


@patch(_ACCESS, side_effect=EntityNotAccessibleError("Project", 7))
def test_service_read_by_public_id_denied_raises_not_accessible(mock_access):
    repo = _StubRepo()
    repo._row = _make_contract(project_id=7)
    with pytest.raises(EntityNotAccessibleError):
        ContractService(repo=repo).read_by_public_id("pub")


@patch(_ACCESS)
def test_service_read_by_public_id_missing_returns_none_without_check(mock_access):
    repo = _StubRepo()  # _row is None → genuinely missing
    assert ContractService(repo=repo).read_by_public_id("missing") is None
    mock_access.assert_not_called()


@patch(_ACCESS)
def test_service_read_by_project_id_asserts_requested_project(mock_access):
    repo = _StubRepo()
    repo._project_rows = [_make_contract(project_id=9)]
    rows = ContractService(repo=repo).read_by_project_id(9)
    mock_access.assert_called_once_with(9)
    assert len(rows) == 1


@patch(_ACCESS, side_effect=EntityNotAccessibleError("Project", 9))
def test_service_read_by_project_id_denied_raises_not_accessible(mock_access):
    repo = _StubRepo()
    with pytest.raises(EntityNotAccessibleError):
        ContractService(repo=repo).read_by_project_id(9)


@patch(_ACCESS)
def test_service_update_sets_fee_rate_and_row_version(mock_access):
    repo = _StubRepo()
    repo._row = _make_contract(builders_fee_rate=Decimal("0.100000"))
    ContractService(repo=repo).update_by_public_id(
        "11111111-1111-1111-1111-111111111111",
        row_version="AAAAAAAAB9E=",
        builders_fee_rate="0.14",
    )
    assert repo.updated.builders_fee_rate == Decimal("0.14")
    assert repo.updated.row_version == "AAAAAAAAB9E="
    # Update enforces access via the read_by_public_id prefetch.
    mock_access.assert_called_once_with(128)


@patch(_ACCESS)
def test_service_update_missing_row_returns_none(mock_access):
    repo = _StubRepo()  # read_by_public_id returns None
    result = ContractService(repo=repo).update_by_public_id(
        "missing", row_version="x", builders_fee_rate="0.1"
    )
    assert result is None


@patch(_ACCESS)
def test_service_update_stale_rowversion_raises_concurrency_conflict(mock_access):
    """A stale RowVersion matches no row → repo returns None → the service raises a
    concurrency conflict (surfaced as 409), NOT a silent None (which routes to 404)."""
    repo = _StubRepo()
    repo._row = _make_contract()
    repo.update_returns_none = True  # sproc matched no row (stale token)
    with pytest.raises(ValueError) as exc:
        ContractService(repo=repo).update_by_public_id(
            "pub", row_version="stale-token", builders_fee_rate="0.14"
        )
    message = str(exc.value).lower()
    assert "concurrency" in message or "conflict" in message


def test_concurrency_message_maps_to_409():
    """The router's raise_workflow_error turns the service's concurrency message
    into HTTP 409 (not 400/404) — the contract this fix depends on."""
    with pytest.raises(HTTPException) as exc:
        raise_workflow_error(
            "Concurrency conflict: Contract has been modified by another user.",
            "Failed to update contract",
        )
    assert exc.value.status_code == 409


# --------------------------------------------------------------------------- #
# Instant-workflow dispatch contract                                          #
# --------------------------------------------------------------------------- #

def test_contract_registered_in_synchronous_tasks_and_process_registry():
    assert "contract" in SYNCHRONOUS_TASKS
    assert PROCESS_REGISTRY["contract"] == (
        "entities.contract.business.service.ContractService"
    )


@pytest.mark.parametrize("operation", ["create", "update"])
def test_contract_workflow_types_parse_to_entity_and_operation(operation):
    workflow_type = f"contract_{operation}"
    assert is_instant_workflow_type(workflow_type)
    entity, parsed_op = parse_instant_workflow_type(workflow_type)
    assert entity == "contract"
    assert parsed_op == operation


def test_service_exposes_dispatched_method_mapping_targets():
    """The lean surface implements the create + update dispatch targets. Delete is
    intentionally not exposed (deferred), so it is not asserted here."""
    assert hasattr(ContractService, METHOD_MAPPING["create"])
    assert hasattr(ContractService, METHOD_MAPPING["update"])


def test_dispatch_injects_tenant_id_into_create():
    """The instant dispatcher injects tenant_id; ContractService.create accepts it
    (explicit param), so the injected kwarg must not raise (mirrors
    test_instant_dispatch_tenant_injection)."""
    built = _build_service_kwargs(
        ContractService(repo=_StubRepo()).create, 1, {"project_id": 5}
    )
    assert built["tenant_id"] == 1
    assert "tenant_id" in inspect.signature(ContractService.create).parameters
