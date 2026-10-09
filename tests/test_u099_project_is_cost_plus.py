"""U-099: Project.IsCostPlus drives BillableStatus on the expense-coding recode.

Pure-logic, no live DB. SQL contracts are pinned against file content; the
worker rule is pinned by stubbing the handler's collaborators.
"""
import base64
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from entities.project.business.model import Project
from entities.project.persistence.repo import ProjectRepository
from integrations.intuit.qbo.outbox.business.model import QboOutbox
from integrations.intuit.qbo.outbox.business.worker import QboOutboxWorker
from tests.sproc_text import sproc_body, sproc_params, strip_sql_comments

REPO_ROOT = Path(__file__).resolve().parents[1]
_PROJECT_SQL = REPO_ROOT / "entities" / "project" / "sql" / "dbo.project.sql"
_MIGRATION_004 = REPO_ROOT / "entities" / "project" / "sql" / "migrations" / "004_project_is_cost_plus.sql"

_COL_TOKEN = re.compile(r"\[(\w+)\]")
_BY_ID_AND_LIST_PROCS = (
    "ReadProjects",
    "ReadProjectById",
    "ReadProjectByPublicId",
    "ReadProjectByName",
    "ReadProjectByQboIdAndRealmId",
)


def _select_lists(proc_name: str) -> list[str]:
    return re.findall(
        r"SELECT(?:\s+DISTINCT)?(.*?)\bFROM\b",
        sproc_body(_PROJECT_SQL, proc_name),
        re.IGNORECASE | re.DOTALL,
    )


def _mock_row(**kwargs):
    defaults = {
        "Id": 202,
        "PublicId": "00000000-0000-0000-0000-000000000202",
        "RowVersion": b"\x00\x00\x00\x00\x00\x00\x00\x01",
        "CreatedDatetime": "2026-01-01 00:00:00",
        "ModifiedDatetime": "2026-01-02 00:00:00",
        "Name": "CRS - 425 Craighead St",
        "Description": "Renovation",
        "Status": "active",
        "CustomerId": 9,
        "Abbreviation": "CRS",
        "Notes": None,
        "QboId": "1479",
        "RealmId": "realm-1",
        "CustomerName": "Acme Builders",
        "IsCostPlus": True,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


@pytest.mark.parametrize("proc", _BY_ID_AND_LIST_PROCS)
def test_read_proc_selects_is_cost_plus(proc):
    select_list = _select_lists(proc)[0]
    assert "IsCostPlus" in set(_COL_TOKEN.findall(select_list)), proc


def test_read_projects_by_user_id_both_branches_select_is_cost_plus():
    blocks = _select_lists("ReadProjectsByUserId")
    assert len(blocks) == 2, f"expected admin + scoped SELECT variants, got {len(blocks)}"
    for segment in blocks:
        assert "IsCostPlus" in set(_COL_TOKEN.findall(segment))


def test_create_project_takes_is_cost_plus_param_defaulting_true():
    params = strip_sql_comments(sproc_params(_PROJECT_SQL, "CreateProject"))
    assert re.search(r"@IsCostPlus\s+BIT\s*=\s*1\b", params)


def test_create_project_inserts_and_returns_is_cost_plus():
    body = sproc_body(_PROJECT_SQL, "CreateProject")
    insert_columns = re.search(r"INSERT INTO dbo\.\[Project\](.*?)OUTPUT", body, re.DOTALL).group(1)
    values = re.search(r"VALUES\s*\((.*?)\);", body, re.DOTALL).group(1)
    output = re.search(r"OUTPUT(.*?)VALUES", body, re.DOTALL).group(1)
    assert "[IsCostPlus]" in insert_columns
    assert "@IsCostPlus" in values
    assert "INSERTED.[IsCostPlus]" in output


def test_update_project_takes_nullable_is_cost_plus_param():
    params = strip_sql_comments(sproc_params(_PROJECT_SQL, "UpdateProjectById"))
    assert re.search(r"@IsCostPlus\s+BIT\s*=\s*NULL\b", params)


def test_update_project_keeps_is_cost_plus_when_param_omitted():
    set_clause = re.search(r"SET(.*?)OUTPUT", sproc_body(_PROJECT_SQL, "UpdateProjectById"), re.DOTALL).group(1)
    assert "[IsCostPlus] = CASE WHEN @IsCostPlus IS NULL THEN [IsCostPlus] ELSE @IsCostPlus END" in set_clause


def test_update_project_outputs_is_cost_plus():
    output = re.search(r"OUTPUT(.*?)WHERE", sproc_body(_PROJECT_SQL, "UpdateProjectById"), re.DOTALL).group(1)
    assert "INSERTED.[IsCostPlus]" in output


def test_base_table_declares_is_cost_plus_column_with_default_one():
    text = _PROJECT_SQL.read_text()
    assert re.search(r"\[IsCostPlus\]\s+BIT\s+NOT\s+NULL\s+DEFAULT\s+1\b", text)


def test_migration_004_adds_column_guarded_and_defines_no_procedure():
    text = _MIGRATION_004.read_text()
    assert re.search(r"COL_LENGTH\(\s*'dbo\.Project'\s*,\s*'IsCostPlus'\s*\)\s+IS\s+NULL", text)
    assert re.search(r"ALTER\s+TABLE\s+dbo\.\[?Project\]?\s+ADD\s+\[?IsCostPlus\]?\s+BIT\s+NOT\s+NULL", text, re.IGNORECASE)
    assert not re.search(r"CREATE\s+(OR\s+ALTER\s+)?PROCEDURE", text, re.IGNORECASE)


def test_project_model_default_is_unknown_not_true():
    """None means "the row did not carry the column" - a stored 0 must never
    read as cost-plus through a not-yet-reapplied sproc (Codex P1)."""
    project = Project(
        id=None, public_id=None, row_version=None, created_datetime=None,
        modified_datetime=None, name="P", description=None, status="active",
        customer_id=None, abbreviation=None,
    )
    assert project.is_cost_plus is None
    assert project.to_dict()["is_cost_plus"] is None


@pytest.mark.parametrize("raw,expected", [(0, False), (1, True), (False, False), (True, True)])
def test_from_db_maps_bit_values(raw, expected):
    assert ProjectRepository()._from_db(_mock_row(IsCostPlus=raw)).is_cost_plus is expected


def test_from_db_maps_absent_column_to_unknown():
    row = _mock_row()
    del row.IsCostPlus
    assert ProjectRepository()._from_db(row).is_cost_plus is None


@patch("entities.project.persistence.repo.call_procedure")
@patch("entities.project.persistence.repo.get_connection")
def test_repo_create_passes_is_cost_plus_to_call_procedure(mock_get_connection, mock_call_procedure):
    cursor = MagicMock()
    cursor.fetchone.return_value = _mock_row(IsCostPlus=False)
    mock_get_connection.return_value.__enter__.return_value.cursor.return_value = cursor

    project = ProjectRepository().create(
        name="Overhead", description="d", status="active", is_cost_plus=False,
    )

    params = mock_call_procedure.call_args.kwargs["params"]
    assert params["IsCostPlus"] is False
    assert project.is_cost_plus is False


@patch("entities.project.persistence.repo.call_procedure")
@patch("entities.project.persistence.repo.get_connection")
def test_repo_update_passes_is_cost_plus_to_call_procedure(mock_get_connection, mock_call_procedure):
    cursor = MagicMock()
    cursor.fetchone.return_value = _mock_row(IsCostPlus=False)
    mock_get_connection.return_value.__enter__.return_value.cursor.return_value = cursor

    project = Project(
        id=202, public_id=None,
        row_version=base64.b64encode(b"\x00\x00\x00\x00\x00\x00\x00\x01").decode("ascii"),
        created_datetime=None, modified_datetime=None, name="CRS", description="d",
        status="active", customer_id=None, abbreviation="CRS", is_cost_plus=False,
    )
    ProjectRepository().update_by_id(project)

    params = mock_call_procedure.call_args.kwargs["params"]
    assert params["IsCostPlus"] is False


@patch("entities.project.persistence.repo.call_procedure")
@patch("entities.project.persistence.repo.get_connection")
def test_repo_create_omits_the_param_when_not_given(mock_get_connection, mock_call_procedure):
    """An old CreateProject (column applied, procs not yet) rejects an unknown
    @IsCostPlus; omitting it lets the DB default (1) apply (Codex P1, deploy order)."""
    cursor = MagicMock()
    cursor.fetchone.return_value = _mock_row()
    mock_get_connection.return_value.__enter__.return_value.cursor.return_value = cursor

    ProjectRepository().create(name="P", description="d", status="active")

    assert "IsCostPlus" not in mock_call_procedure.call_args.kwargs["params"]


@patch("entities.project.persistence.repo.call_procedure")
@patch("entities.project.persistence.repo.get_connection")
def test_repo_update_omits_the_param_when_unknown(mock_get_connection, mock_call_procedure):
    """A project read through an old proc carries None; the update must not
    turn that into an authoritative value (and must not send a param an old
    UpdateProjectById rejects)."""
    cursor = MagicMock()
    cursor.fetchone.return_value = _mock_row()
    mock_get_connection.return_value.__enter__.return_value.cursor.return_value = cursor

    project = Project(
        id=202, public_id=None,
        row_version=base64.b64encode(b"\x00\x00\x00\x00\x00\x00\x00\x01").decode("ascii"),
        created_datetime=None, modified_datetime=None, name="CRS", description="d",
        status="active", customer_id=None, abbreviation="CRS",
    )
    ProjectRepository().update_by_id(project)

    assert "IsCostPlus" not in mock_call_procedure.call_args.kwargs["params"]


def _make_item(*, confirmed_project_id):
    return SimpleNamespace(
        public_id="11111111-1111-1111-1111-111111111111",
        status="enqueued",
        qbo_purchase_qbo_id="purchase-123",
        qbo_line_id="1",
        confirmed_sub_cost_code_id=101,
        confirmed_project_id=confirmed_project_id,
        confirmed_description="recode desc",
        sync_token_at_suggest="5",
        qbo_purchase_id=999,
        realm_id="realm-test",
    )


def _make_outbox_row():
    return QboOutbox(
        id=1,
        public_id="outbox-1",
        row_version="abc",
        kind="recode_purchase_line",
        entity_type="ExpenseCodingItem",
        entity_public_id="11111111-1111-1111-1111-111111111111",
        realm_id="realm-test",
        request_id="req-1",
        status="processing",
        attempts=0,
    )


def _local_line(is_billable):
    return (SimpleNamespace(quantity=1, rate=2, amount=3, is_billable=is_billable), None)


def _run_recode(*, item, local_line, project_service):
    """Run the handler with stubbed item / local economics / project read; return recode kwargs."""
    with patch(
        "integrations.intuit.qbo.purchase.connector.expense.business.service.PurchaseExpenseConnector"
    ) as mock_connector_cls, patch(
        "entities.expense_coding_item.business.service.ExpenseCodingItemService"
    ) as mock_svc_cls, patch.object(
        QboOutboxWorker, "_resolve_recode_line_economics", return_value=local_line,
    ), patch(
        "entities.project.business.service.ProjectService", project_service,
    ):
        svc = MagicMock()
        mock_svc_cls.return_value = svc
        svc.read_by_public_id.return_value = item
        connector = MagicMock()
        mock_connector_cls.return_value = connector
        connector.recode_purchase_line.return_value = {"status": "written", "sync_token": "6"}

        QboOutboxWorker()._handle_recode_purchase_line(_make_outbox_row())

    connector.recode_purchase_line.assert_called_once()
    return connector.recode_purchase_line.call_args.kwargs


def _project_service_returning(project):
    service_cls = MagicMock()
    service_cls.return_value.read_by_id.return_value = project
    return service_cls


def _failing_project_service(exc):
    service_cls = MagicMock()
    service_cls.return_value.read_by_id.side_effect = exc
    return service_cls


@pytest.mark.parametrize(
    "project_id,local_billable,project_service,expected",
    [
        pytest.param(202, True, _project_service_returning(SimpleNamespace(is_cost_plus=False)), False, id="not_cost_plus"),
        pytest.param(202, False, _project_service_returning(SimpleNamespace(is_cost_plus=True)), True, id="cost_plus"),
        pytest.param(None, True, _project_service_returning(SimpleNamespace(is_cost_plus=True)), False, id="no_project"),
        pytest.param(202, True, _failing_project_service(RuntimeError("db down")), False, id="read_fails"),
        pytest.param(202, True, _project_service_returning(None), False, id="not_found"),
        pytest.param(202, True, _project_service_returning(SimpleNamespace(is_cost_plus=None)), False, id="flag_unknown_old_proc"),
    ],
)
def test_recode_billable_follows_the_confirmed_project_only(project_id, local_billable, project_service, expected):
    """The local line's IsBillable (a mirror of Ramp's NotBillable) is never the
    signal: only a readable, confirmed, cost-plus project stamps Billable. Every
    other case - no project, unreadable, not found - stamps NotBillable."""
    kwargs = _run_recode(
        item=_make_item(confirmed_project_id=project_id),
        local_line=_local_line(local_billable),
        project_service=project_service,
    )
    assert kwargs["is_billable"] is expected
    if project_id is None:
        project_service.return_value.read_by_id.assert_not_called()


# ---------------------------------------------------------------------------
# Connector: Billable only against the CONFIRMED project's own customer.
# ---------------------------------------------------------------------------

from tests.test_expense_recode import (  # noqa: E402
    _build_connector,
    _make_categorize_line_dict,
    _make_raw_purchase,
    _patch_raw_client,
)
from tests.test_u488_recode_carries_qty_rate_billable import _call_recode  # noqa: E402

_FOREIGN_CUSTOMER = {"value": "999", "name": "Somebody Else"}


def _recode_with_foreign_customer_on_the_placeholder(*, customer_ref):
    fresh = _make_raw_purchase(lines=[_make_categorize_line_dict(extra_detail={"CustomerRef": _FOREIGN_CUSTOMER})])
    connector = _build_connector(customer_ref=customer_ref)
    client_patch, mock_client = _patch_raw_client(fresh=fresh)
    with client_patch:
        _call_recode(connector, is_billable=True)
    return mock_client.update_purchase_raw.call_args[0][0]["Line"][0]["ItemBasedExpenseLineDetail"]


def test_cost_plus_project_without_qbo_identity_does_not_bill_a_carried_foreign_customer():
    """RED on U-488's guard, which checked for ANY CustomerRef on the new detail -
    including the one carried forward from the placeholder line, which may be
    another customer's. The confirmed project has no QBO identity here, so the
    foreign ref is carried (unchanged behaviour) but the line must NOT be Billable."""
    detail = _recode_with_foreign_customer_on_the_placeholder(customer_ref=None)
    assert detail["CustomerRef"] == _FOREIGN_CUSTOMER
    assert detail["BillableStatus"] == "NotBillable"


def test_cost_plus_project_with_its_own_customer_is_billable():
    from tests.test_expense_recode import FAKE_CUSTOMER_REF
    detail = _recode_with_foreign_customer_on_the_placeholder(customer_ref=FAKE_CUSTOMER_REF)
    assert detail["CustomerRef"]["value"] == FAKE_CUSTOMER_REF.value
    assert detail["BillableStatus"] == "Billable"
