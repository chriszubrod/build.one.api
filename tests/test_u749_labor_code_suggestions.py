"""U-749 — suggested SubCostCodes for the Contract Labor edit page.

Pure ranking (U-673 S0 vendor x project), the service seam, the GET route, and a
static check of the ReadContractLaborSubCostCodeCounts sproc text. No DB.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import app
from entities.contract_labor.api.router import read_contract_labor_code_suggestions
from entities.contract_labor.business.code_suggestions import (
    NO_SUGGESTION,
    CodeSuggestion,
    rank_sub_cost_codes,
)
from entities.contract_labor.business.service import ContractLaborService
from shared.access import EntityNotAccessibleError

REPO_ROOT = Path(__file__).resolve().parents[1]
BASE_SQL = REPO_ROOT / "entities/contract_labor/sql/dbo.contract_labor.sql"
URL = "/api/v1/contract-labor/cl-pub-1/code-suggestions"
LI_REPO = "entities.contract_labor.persistence.line_item_repo.ContractLaborLineItemRepository"


# ---------------------------------------------------------------------------
# Pure ranking
# ---------------------------------------------------------------------------


def test_project_scope_wins_when_it_has_three_priors():
    s = rank_sub_cost_codes({10: 2, 11: 1}, {99: 50, 10: 2, 11: 1})
    assert s == CodeSuggestion((10, 11), "vendor_project", 3)


def test_project_below_three_falls_back_to_vendor():
    s = rank_sub_cost_codes({10: 2}, {99: 5, 10: 2, 7: 1})
    assert s == CodeSuggestion((99, 10, 7), "vendor", 8)


def test_vendor_below_three_gives_none():
    assert rank_sub_cost_codes({10: 1}, {10: 1, 11: 1}) == NO_SUGGESTION
    assert rank_sub_cost_codes({}, {}) == CodeSuggestion((), "none", 0)


def test_exact_three_boundary_both_scopes():
    assert rank_sub_cost_codes({5: 3}, {}).source == "vendor_project"
    assert rank_sub_cost_codes({5: 2}, {5: 3}).source == "vendor"
    assert rank_sub_cost_codes({5: 2}, {5: 2}).source == "none"


def test_tie_break_is_sub_cost_code_id_ascending():
    s = rank_sub_cost_codes({30: 2, 10: 2, 20: 2}, {})
    assert s.sub_cost_code_ids == (10, 20, 30)


def test_count_desc_before_id():
    s = rank_sub_cost_codes({1: 1, 2: 5, 3: 3}, {})
    assert s.sub_cost_code_ids == (2, 3, 1)


def test_top_n_cap_but_prior_count_is_scope_total():
    s = rank_sub_cost_codes({1: 4, 2: 3, 3: 2, 4: 1}, {})
    assert s.sub_cost_code_ids == (1, 2, 3)
    assert s.prior_count == 10
    assert rank_sub_cost_codes({1: 4, 2: 3}, {}, top_n=1).sub_cost_code_ids == (1,)


def test_min_priors_is_configurable():
    assert rank_sub_cost_codes({1: 1}, {}, min_priors=1).source == "vendor_project"


def test_matches_u673_dominant_on_same_counts():
    """Same ranking key + threshold as scripts/u673 _dominant (imported only here, in the test)."""
    from collections import Counter
    from scripts.u673_labor_coding_backtest import _dominant

    labels = [7, 7, 3, 3, 9, 1, 1, 1]
    priors = [SimpleNamespace(label_scc_id=x) for x in labels]
    expected = _dominant(priors)
    got = rank_sub_cost_codes(dict(Counter(labels)), {})
    assert got.sub_cost_code_ids == expected.ranked


# ---------------------------------------------------------------------------
# Service seam
# ---------------------------------------------------------------------------


def _entry(**kw):
    base = dict(id=42, vendor_id=5, bill_vendor_id=None, work_date="2026-10-20", project_id=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_service_passes_vendor_asof_and_exclusion():
    repo = MagicMock()
    repo.read_sub_cost_code_counts.return_value = ({10: 3}, {10: 3})
    s = ContractLaborService(repo=MagicMock()).suggest_sub_cost_codes(
        _entry(), project_id=77, line_item_repo=repo
    )
    repo.read_sub_cost_code_counts.assert_called_once_with(
        vendor_id=5, project_id=77, as_of_date="2026-10-20", exclude_contract_labor_id=42
    )
    assert s == CodeSuggestion((10,), "vendor_project", 3)


def test_service_falls_back_to_bill_vendor():
    repo = MagicMock()
    repo.read_sub_cost_code_counts.return_value = ({}, {})
    ContractLaborService(repo=MagicMock()).suggest_sub_cost_codes(
        _entry(vendor_id=None, bill_vendor_id=8), project_id=1, line_item_repo=repo
    )
    assert repo.read_sub_cost_code_counts.call_args.kwargs["vendor_id"] == 8


@pytest.mark.parametrize("kw", [dict(vendor_id=None, bill_vendor_id=None), dict(work_date=None)])
def test_service_no_vendor_or_date_is_empty_without_db(kw):
    repo = MagicMock()
    s = ContractLaborService(repo=MagicMock()).suggest_sub_cost_codes(
        _entry(**kw), project_id=1, line_item_repo=repo
    )
    assert s == NO_SUGGESTION
    repo.read_sub_cost_code_counts.assert_not_called()


# ---------------------------------------------------------------------------
# Repo row bucketing (cursor mocked; no connection)
# ---------------------------------------------------------------------------


def test_repo_buckets_rows_by_scope():
    from entities.contract_labor.persistence.line_item_repo import ContractLaborLineItemRepository

    rows = [
        SimpleNamespace(Scope="project", SubCostCodeId=10, LineCount=2),
        SimpleNamespace(Scope="vendor", SubCostCodeId=10, LineCount=4),
        SimpleNamespace(Scope="vendor", SubCostCodeId=11, LineCount=1),
    ]
    cursor = MagicMock()
    cursor.fetchall.return_value = rows
    conn = MagicMock()
    conn.cursor.return_value = cursor
    cm = MagicMock()
    cm.__enter__.return_value = conn
    with patch("entities.contract_labor.persistence.line_item_repo.get_connection", return_value=cm), \
         patch("entities.contract_labor.persistence.line_item_repo.call_procedure") as cp:
        out = ContractLaborLineItemRepository().read_sub_cost_code_counts(
            vendor_id=5, project_id=77, as_of_date="2026-10-20", exclude_contract_labor_id=42
        )
    assert out == ({10: 2}, {10: 4, 11: 1})
    assert cp.call_args.kwargs["name"] == "ReadContractLaborSubCostCodeCounts"
    assert cp.call_args.kwargs["params"] == {
        "VendorId": 5, "ProjectId": 77, "AsOfDate": "2026-10-20", "ExcludeContractLaborId": 42,
    }


def test_repo_db_error_is_mapped_and_raised():
    from entities.contract_labor.persistence.line_item_repo import ContractLaborLineItemRepository
    from shared.database import DatabaseError

    with patch(
        "entities.contract_labor.persistence.line_item_repo.get_connection",
        side_effect=RuntimeError("boom"),
    ):
        with pytest.raises(DatabaseError):
            ContractLaborLineItemRepository().read_sub_cost_code_counts(
                vendor_id=5, project_id=1, as_of_date="2026-10-20", exclude_contract_labor_id=1
            )


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    dep = inspect.signature(read_contract_labor_code_suggestions).parameters["current_user"].default.dependency
    app.dependency_overrides[dep] = lambda: {"sub": "test-sub"}
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def test_route_404_when_entry_missing(client):
    with patch.object(ContractLaborService, "read_by_public_id", return_value=None), \
         patch(LI_REPO) as li:
        r = client.get(URL, params={"project_id": 77})
    assert r.status_code == 404
    li.assert_not_called()


def test_route_404_when_entry_inaccessible(client):
    with patch.object(
        ContractLaborService, "read_by_public_id",
        side_effect=EntityNotAccessibleError("Project", 1),
    ), patch(LI_REPO) as li:
        r = client.get(URL, params={"project_id": 77})
    assert r.status_code == 404
    li.assert_not_called()


def test_route_404_when_project_inaccessible(client):
    with patch.object(ContractLaborService, "read_by_public_id", return_value=_entry()), \
         patch(
             "entities.contract_labor.api.router.assert_can_access_project",
             side_effect=EntityNotAccessibleError("Project", 77),
         ) as acc, patch(LI_REPO) as li:
        r = client.get(URL, params={"project_id": 77})
    assert r.status_code == 404
    acc.assert_called_once_with(77)
    li.return_value.read_sub_cost_code_counts.assert_not_called()


def test_route_422_without_project_id(client):
    with patch.object(ContractLaborService, "read_by_public_id", return_value=_entry()):
        r = client.get(URL)
    assert r.status_code == 422


@pytest.mark.parametrize("bad", [0, -1, 2**63])
def test_route_422_on_out_of_range_project_id(client, bad):
    with patch.object(ContractLaborService, "read_by_public_id", return_value=_entry()) as rd, \
         patch(LI_REPO) as li:
        r = client.get(URL, params={"project_id": bad})
    assert r.status_code == 422
    rd.assert_not_called()
    li.assert_not_called()


def test_route_accepts_bigint_max_project_id(client):
    with patch.object(ContractLaborService, "read_by_public_id", return_value=_entry()), \
         patch("entities.contract_labor.api.router.assert_can_access_project"), \
         patch(LI_REPO) as li:
        li.return_value.read_sub_cost_code_counts.return_value = ({}, {})
        r = client.get(URL, params={"project_id": 2**63 - 1})
    assert r.status_code == 200
    assert r.json()["data"]["project_id"] == 2**63 - 1


def test_route_empty_when_no_vendor(client):
    with patch.object(
        ContractLaborService, "read_by_public_id",
        return_value=_entry(vendor_id=None, bill_vendor_id=None),
    ), patch("entities.contract_labor.api.router.assert_can_access_project"), \
         patch(LI_REPO) as li:
        r = client.get(URL, params={"project_id": 77})
    assert r.status_code == 200
    assert r.json()["data"] == {
        "project_id": 77, "sub_cost_code_ids": [], "source": "none",
    }
    li.return_value.read_sub_cost_code_counts.assert_not_called()


def test_route_happy_path_shape_and_args(client):
    with patch.object(ContractLaborService, "read_by_public_id", return_value=_entry()) as rd, \
         patch("entities.contract_labor.api.router.assert_can_access_project") as acc, \
         patch(LI_REPO) as li:
        li.return_value.read_sub_cost_code_counts.return_value = ({10: 1}, {20: 4, 10: 1, 30: 4})
        r = client.get(URL, params={"project_id": 77})
    assert r.status_code == 200
    assert r.json()["data"] == {
        "project_id": 77, "sub_cost_code_ids": [20, 30, 10], "source": "vendor",
    }
    assert set(r.json()["data"]) == {"project_id", "sub_cost_code_ids", "source"}
    rd.assert_called_once_with(public_id="cl-pub-1")
    acc.assert_called_once_with(77)
    li.return_value.read_sub_cost_code_counts.assert_called_once_with(
        vendor_id=5, project_id=77, as_of_date="2026-10-20", exclude_contract_labor_id=42
    )


# ---------------------------------------------------------------------------
# Static sproc text
# ---------------------------------------------------------------------------


def _sproc_batch() -> str:
    text = BASE_SQL.read_text(encoding="utf-8")
    m = re.search(
        r"CREATE OR ALTER PROCEDURE dbo\.ReadContractLaborSubCostCodeCounts\b.*?^GO\s*$",
        text, re.DOTALL | re.MULTILINE,
    )
    assert m, "ReadContractLaborSubCostCodeCounts batch missing or not GO-terminated"
    return m.group(0)


def test_sproc_defined_once_and_go_terminated():
    text = BASE_SQL.read_text(encoding="utf-8")
    assert len(re.findall(r"PROCEDURE\s+(?:dbo\.)?ReadContractLaborSubCostCodeCounts\b", text)) == 1
    _sproc_batch()


def test_sproc_filters_match_u673():
    body = re.sub(r"\s+", " ", _sproc_batch())
    assert "SET NOCOUNT ON" in body
    assert "TRANSACTION" not in body.upper()
    assert "cl.[Status] IN (N'approved', N'completed', N'ready', N'billed')" in body
    assert "li.[SubCostCodeId] IS NOT NULL" in body
    assert "ISNULL(scc.[IsHoldingCode], 0) = 0" in body
    assert "DATEADD(day, -2, @AsOfDate)" in body
    assert "COALESCE(li.[LineDate], cl.[WorkDate]) <= @VisibleThrough" in body
    assert "cl.[Id] <> @ExcludeContractLaborId" in body
    # COALESCE(cl.VendorId, cl.BillVendorId) = @VendorId, written sargably.
    assert "(cl.[VendorId] = @VendorId OR (cl.[VendorId] IS NULL AND cl.[BillVendorId] = @VendorId))" in body
    assert "li.[ProjectId] = @ProjectId" in body
    for scope in ("N'vendor'", "N'project'"):
        assert scope in body
    for mutating in ("INSERT ", "UPDATE ", "DELETE ", "MERGE "):
        assert mutating not in body.upper()
