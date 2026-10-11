"""U-646: human Billable decision on expense lines; QBO pull guard; recode seed."""
import base64
import re
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from entities.expense.intelligence.tools import UpdateExpenseLineItemArgs
from entities.expense_line_item.api.schemas import ExpenseLineItemUpdate
from entities.expense_line_item.business.model import ExpenseLineItem
from entities.expense_line_item.business.service import ExpenseLineItemService
from entities.expense_line_item.persistence.repo import ExpenseLineItemRepository
from integrations.intuit.qbo.outbox.business.worker import QboOutboxWorker
from tests.sproc_text import sproc_body, sproc_params, strip_sql_comments

REPO_ROOT = Path(__file__).resolve().parents[1]
_ELI_SQL = REPO_ROOT / "entities" / "expense_line_item" / "sql" / "dbo.expense_line_item.sql"
_MIGRATION_001 = (
    REPO_ROOT
    / "entities"
    / "expense_line_item"
    / "sql"
    / "migrations"
    / "001_billable_decision.sql"
)

_COL_TOKEN = re.compile(r"\[(\w+)\]")
_FULL_ROW_READ_PROCS = (
    "ReadExpenseLineItems",
    "ReadExpenseLineItemById",
    "ReadExpenseLineItemByPublicId",
    "ReadExpenseLineItemsByExpenseId",
    "ReadExpenseLineItemByExpenseIdAndQboId",
)


def _select_lists(proc_name: str) -> list[str]:
    return re.findall(
        r"SELECT(?:\s+DISTINCT)?(.*?)\bFROM\b",
        sproc_body(_ELI_SQL, proc_name),
        re.IGNORECASE | re.DOTALL,
    )


def _mock_eli_row(**kwargs):
    defaults = {
        "Id": 1,
        "PublicId": "00000000-0000-0000-0000-000000000001",
        "RowVersion": b"\x00\x00\x00\x00\x00\x00\x00\x01",
        "CreatedDatetime": "2026-01-01 00:00:00",
        "ModifiedDatetime": "2026-01-02 00:00:00",
        "ExpenseId": 10,
        "SubCostCodeId": None,
        "ProjectId": 5,
        "Description": "Materials",
        "Quantity": Decimal("1"),
        "Rate": Decimal("10"),
        "Amount": Decimal("10"),
        "IsBillable": 0,
        "IsBilled": 0,
        "Markup": None,
        "Price": None,
        "IsDraft": 1,
        "QboId": "line-1",
        "RealmId": "realm-1",
        "BillableDecidedByUserId": None,
        "BillableDecidedAt": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _line_item(**kwargs) -> ExpenseLineItem:
    base = dict(
        id=1,
        public_id="eli-pub",
        row_version=base64.b64encode(b"\x00\x00\x00\x00\x00\x00\x00\x01").decode("ascii"),
        created_datetime="2026-01-01 00:00:00",
        modified_datetime="2026-01-02 00:00:00",
        expense_id=10,
        sub_cost_code_id=None,
        project_id=5,
        description="x",
        quantity=Decimal("1"),
        rate=Decimal("1"),
        amount=Decimal("1"),
        is_billable=False,
        is_billed=False,
        markup=None,
        price=None,
        is_draft=True,
        qbo_id="l1",
        realm_id="r1",
        billable_decided_by_user_id=None,
        billable_decided_at=None,
    )
    base.update(kwargs)
    return ExpenseLineItem(**base)


# ---------------------------------------------------------------------------
# SQL contract
# ---------------------------------------------------------------------------


def test_base_table_declares_billable_decision_columns_after_is_billed():
    text = _ELI_SQL.read_text()
    assert re.search(
        r"\[IsBilled\]\s+BIT\s+NULL,\s*\n\s*\[BillableDecidedByUserId\]\s+BIGINT\s+NULL,\s*\n\s*\[BillableDecidedAt\]\s+DATETIME2\(3\)\s+NULL,",
        text,
    )


@pytest.mark.parametrize("proc", _FULL_ROW_READ_PROCS)
def test_read_proc_selects_billable_decision_columns_after_is_billed(proc):
    select_list = _select_lists(proc)[0]
    cols = _COL_TOKEN.findall(select_list)
    billed_idx = cols.index("IsBilled")
    assert cols[billed_idx + 1 : billed_idx + 3] == [
        "BillableDecidedByUserId",
        "BillableDecidedAt",
    ], proc


def test_create_expense_line_item_takes_billable_decision_params_default_null():
    params = strip_sql_comments(sproc_params(_ELI_SQL, "CreateExpenseLineItem"))
    assert re.search(r"@BillableDecidedByUserId\s+BIGINT\s*=\s*NULL\b", params)
    assert re.search(r"@BillableDecidedAt\s+DATETIME2\(3\)\s*=\s*NULL\b", params)


def test_update_expense_line_item_takes_billable_decision_params_default_null():
    params = strip_sql_comments(sproc_params(_ELI_SQL, "UpdateExpenseLineItemById"))
    assert re.search(r"@BillableDecidedByUserId\s+BIGINT\s*=\s*NULL\b", params)
    assert re.search(r"@BillableDecidedAt\s+DATETIME2\(3\)\s*=\s*NULL\b", params)


def test_update_expense_line_item_keeps_billable_decision_when_params_omitted():
    set_clause = re.search(
        r"SET(.*?)OUTPUT",
        sproc_body(_ELI_SQL, "UpdateExpenseLineItemById"),
        re.DOTALL,
    ).group(1)
    assert (
        "[BillableDecidedByUserId] = CASE WHEN @BillableDecidedByUserId IS NULL "
        "THEN [BillableDecidedByUserId] ELSE @BillableDecidedByUserId END"
    ) in set_clause
    assert (
        "[BillableDecidedAt] = CASE WHEN @BillableDecidedAt IS NULL "
        "THEN [BillableDecidedAt] ELSE @BillableDecidedAt END"
    ) in set_clause


def test_create_expense_line_item_outputs_billable_decision_columns():
    body = sproc_body(_ELI_SQL, "CreateExpenseLineItem")
    insert_columns = re.search(
        r"INSERT INTO dbo\.\[ExpenseLineItem\](.*?)OUTPUT", body, re.DOTALL
    ).group(1)
    output = re.search(r"OUTPUT(.*?)VALUES", body, re.DOTALL).group(1)
    assert "[BillableDecidedByUserId]" in insert_columns
    assert "[BillableDecidedAt]" in insert_columns
    assert "INSERTED.[BillableDecidedByUserId]" in output
    assert "INSERTED.[BillableDecidedAt]" in output


def test_update_expense_line_item_outputs_billable_decision_columns():
    output = re.search(
        r"OUTPUT(.*?)WHERE",
        sproc_body(_ELI_SQL, "UpdateExpenseLineItemById"),
        re.DOTALL,
    ).group(1)
    assert "INSERTED.[BillableDecidedByUserId]" in output
    assert "INSERTED.[BillableDecidedAt]" in output


def test_migration_001_adds_columns_guarded_and_defines_no_procedure():
    text = _MIGRATION_001.read_text()
    assert re.search(
        r"COL_LENGTH\(\s*'dbo\.ExpenseLineItem'\s*,\s*'BillableDecidedByUserId'\s*\)\s+IS\s+NULL",
        text,
    )
    assert re.search(
        r"COL_LENGTH\(\s*'dbo\.ExpenseLineItem'\s*,\s*'BillableDecidedAt'\s*\)\s+IS\s+NULL",
        text,
    )
    assert not re.search(r"CREATE\s+(OR\s+ALTER\s+)?PROCEDURE", text, re.IGNORECASE)


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


def test_from_db_maps_billable_decision_columns_when_present():
    mapped = ExpenseLineItemRepository()._from_db(
        _mock_eli_row(BillableDecidedByUserId=17, BillableDecidedAt="2026-10-11 12:00:00")
    )
    assert mapped.billable_decided_by_user_id == 17
    assert mapped.billable_decided_at == "2026-10-11 12:00:00"


def test_from_db_maps_absent_billable_decision_columns_to_none():
    row = _mock_eli_row()
    del row.BillableDecidedByUserId
    del row.BillableDecidedAt
    mapped = ExpenseLineItemRepository()._from_db(row)
    assert mapped.billable_decided_by_user_id is None
    assert mapped.billable_decided_at is None


@patch("entities.expense_line_item.persistence.repo.call_procedure")
@patch("entities.expense_line_item.persistence.repo.get_connection")
def test_repo_create_omits_billable_decision_params_when_none(
    mock_get_connection, mock_call_procedure
):
    cursor = MagicMock()
    cursor.fetchone.return_value = _mock_eli_row()
    mock_get_connection.return_value.__enter__.return_value.cursor.return_value = cursor

    ExpenseLineItemRepository().create(expense_id=10, is_draft=True)

    params = mock_call_procedure.call_args.kwargs["params"]
    assert "BillableDecidedByUserId" not in params
    assert "BillableDecidedAt" not in params


@patch("entities.expense_line_item.persistence.repo.call_procedure")
@patch("entities.expense_line_item.persistence.repo.get_connection")
def test_repo_create_passes_billable_decision_params_when_set(
    mock_get_connection, mock_call_procedure
):
    cursor = MagicMock()
    cursor.fetchone.return_value = _mock_eli_row(
        BillableDecidedByUserId=42, BillableDecidedAt="2026-10-11 12:00:00"
    )
    mock_get_connection.return_value.__enter__.return_value.cursor.return_value = cursor

    ExpenseLineItemRepository().create(
        expense_id=10,
        is_draft=True,
        billable_decided_by_user_id=42,
        billable_decided_at="2026-10-11 12:00:00",
    )

    params = mock_call_procedure.call_args.kwargs["params"]
    assert params["BillableDecidedByUserId"] == 42
    assert params["BillableDecidedAt"] == "2026-10-11 12:00:00"


@patch("entities.expense_line_item.persistence.repo.call_procedure")
@patch("entities.expense_line_item.persistence.repo.get_connection")
def test_repo_update_omits_billable_decision_params_when_none(
    mock_get_connection, mock_call_procedure
):
    cursor = MagicMock()
    cursor.fetchone.return_value = _mock_eli_row()
    mock_get_connection.return_value.__enter__.return_value.cursor.return_value = cursor

    ExpenseLineItemRepository().update_by_id(_line_item())

    params = mock_call_procedure.call_args.kwargs["params"]
    assert "BillableDecidedByUserId" not in params
    assert "BillableDecidedAt" not in params


@patch("entities.expense_line_item.persistence.repo.call_procedure")
@patch("entities.expense_line_item.persistence.repo.get_connection")
def test_repo_update_passes_billable_decision_params_when_set(
    mock_get_connection, mock_call_procedure
):
    cursor = MagicMock()
    cursor.fetchone.return_value = _mock_eli_row()
    mock_get_connection.return_value.__enter__.return_value.cursor.return_value = cursor

    item = _line_item(
        billable_decided_by_user_id=99,
        billable_decided_at="2026-10-11 12:00:00",
    )
    ExpenseLineItemRepository().update_by_id(item)

    params = mock_call_procedure.call_args.kwargs["params"]
    assert params["BillableDecidedByUserId"] == 99
    assert params["BillableDecidedAt"] == "2026-10-11 12:00:00"


# ---------------------------------------------------------------------------
# Service — pull guard and human stamp
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "decided_at,mirrored,expected",
    [
        pytest.param("2026-10-01 00:00:00", False, None, id="decided_line_keeps_its_value"),
        pytest.param(None, False, False, id="undecided_line_takes_the_mirror"),
        pytest.param(None, True, True, id="undecided_line_takes_a_billable_mirror"),
    ],
)
def test_the_pull_does_not_overwrite_a_decided_line(decided_at, mirrored, expected):
    """The guard lives where the pull decides to mirror QuickBooks, not on the
    service's lock-exemption flag (`_via_internal_pipeline` also covers expense
    completion, a human action)."""
    from integrations.intuit.qbo.purchase.connector.expense_line_item.business.service import pull_is_billable
    assert pull_is_billable(SimpleNamespace(billable_decided_at=decided_at), mirrored) is expected


def test_the_pull_update_call_routes_is_billable_through_the_guard():
    """RED if _apply_line_fields goes back to passing the raw mirror."""
    import inspect
    from integrations.intuit.qbo.purchase.connector.expense_line_item.business import service as pull
    src = inspect.getsource(pull.PurchaseLineExpenseLineItemConnector.sync_from_qbo_purchase_line) if hasattr(pull, "PurchaseLineExpenseLineItemConnector") else inspect.getsource(pull)
    assert "is_billable=pull_is_billable(direct, is_billable)" in src


@patch.object(ExpenseLineItemService, "_assert_parent_editable")
@patch("entities.expense_line_item.business.service.assert_can_access_expense")
@patch.object(ExpenseLineItemService, "read_by_public_id")
@patch.object(ExpenseLineItemRepository, "update_by_id")
@patch("entities.expense_line_item.business.service._is_human_user", return_value=True)
def test_human_update_with_billable_decided_stamps_and_applies_value(
    _mock_human, mock_update, mock_read, _mock_access, _mock_parent,
):
    mock_read.return_value = _line_item(is_billable=False, billable_decided_at=None)
    mock_update.side_effect = lambda item, **kw: item
    import datetime as _dt
    fixed = _dt.datetime(2026, 10, 11, 18, 0, 0, 123000, tzinfo=_dt.timezone.utc)

    with patch("entities.expense_line_item.business.service.datetime") as fake_dt:
        fake_dt.now.return_value = fixed
        ExpenseLineItemService().update_by_public_id(
            "eli-pub",
            row_version=mock_read.return_value.row_version,
            is_billable=True,
            billable_decided=True,
            actor_user_id=17,
        )

    passed = mock_update.call_args.args[0]
    assert passed.is_billable is True
    assert passed.billable_decided_by_user_id == 17
    assert passed.billable_decided_at == fixed.replace(tzinfo=None)  # a datetime, ms kept for DATETIME2(3)


@patch.object(ExpenseLineItemService, "_assert_parent_editable")
@patch("entities.expense_line_item.business.service.assert_can_access_expense")
@patch.object(ExpenseLineItemService, "read_by_public_id")
@patch.object(ExpenseLineItemRepository, "update_by_id")
def test_billable_decided_with_internal_pipeline_does_not_stamp(
    mock_update, mock_read, _mock_access, _mock_parent
):
    mock_read.return_value = _line_item(is_billable=False, billable_decided_at=None)
    mock_update.side_effect = lambda item, **kw: item

    ExpenseLineItemService().update_by_public_id(
        "eli-pub",
        row_version=mock_read.return_value.row_version,
        is_billable=True,
        billable_decided=True,
        actor_user_id=17,
        _via_internal_pipeline=True,
    )

    passed = mock_update.call_args.args[0]
    assert passed.billable_decided_by_user_id is None
    assert passed.billable_decided_at is None


@patch.object(ExpenseLineItemService, "_assert_parent_editable")
@patch("entities.expense_line_item.business.service.assert_can_access_expense")
@patch.object(ExpenseLineItemService, "read_by_public_id")
@patch.object(ExpenseLineItemRepository, "update_by_id")
def test_billable_decided_false_never_stamps(
    mock_update, mock_read, _mock_access, _mock_parent
):
    mock_read.return_value = _line_item(is_billable=False, billable_decided_at=None)
    mock_update.side_effect = lambda item, **kw: item

    ExpenseLineItemService().update_by_public_id(
        "eli-pub",
        row_version=mock_read.return_value.row_version,
        is_billable=True,
        billable_decided=False,
        actor_user_id=17,
    )

    passed = mock_update.call_args.args[0]
    assert passed.billable_decided_by_user_id is None
    assert passed.billable_decided_at is None


# ---------------------------------------------------------------------------
# Schema / agent contract
# ---------------------------------------------------------------------------


def test_expense_line_item_update_schema_accepts_billable_decided():
    model = ExpenseLineItemUpdate(
        row_version="AAAA",
        expense_public_id="00000000-0000-0000-0000-000000000099",
        billable_decided=True,
    )
    assert model.billable_decided is True


def test_agent_update_expense_line_item_args_has_no_billable_decided_field():
    assert "billable_decided" not in UpdateExpenseLineItemArgs.model_fields


# ---------------------------------------------------------------------------
# Worker — recode Billable seed at drain time
# ---------------------------------------------------------------------------


def _coding_item(*, confirmed_project_id=100):
    return SimpleNamespace(confirmed_project_id=confirmed_project_id)


@pytest.mark.parametrize(
    "case,local_line,item,project,is_billable_expected",
    [
        (
            "decided_no_on_cost_plus",
            SimpleNamespace(is_billable=False, billable_decided_at="2026-10-01"),
            _coding_item(),
            SimpleNamespace(is_cost_plus=True),
            False,
        ),
        (
            "decided_yes_on_flat_rate",
            SimpleNamespace(is_billable=True, billable_decided_at="2026-10-01"),
            _coding_item(),
            SimpleNamespace(is_cost_plus=False),
            True,
        ),
        (
            "undecided_cost_plus",
            SimpleNamespace(is_billable=False, billable_decided_at=None),
            _coding_item(),
            SimpleNamespace(is_cost_plus=True),
            True,
        ),
        (
            "undecided_flat_rate",
            SimpleNamespace(is_billable=True, billable_decided_at=None),
            _coding_item(),
            SimpleNamespace(is_cost_plus=False),
            False,
        ),
        (
            "undecided_no_confirmed_project",
            SimpleNamespace(is_billable=True, billable_decided_at=None),
            _coding_item(confirmed_project_id=None),
            None,
            True,
        ),
        (
            "undecided_project_unknown_flag",
            SimpleNamespace(is_billable=False, billable_decided_at=None),
            _coding_item(),
            SimpleNamespace(is_cost_plus=None),
            False,
        ),
    ],
)
def test_recode_is_billable_rule(case, local_line, item, project, is_billable_expected):
    with patch(
        "entities.project.business.service.ProjectService"
    ) as mock_project_svc:
        if project is not None and item.confirmed_project_id is not None:
            mock_project_svc.return_value.read_by_id.return_value = project
        result = QboOutboxWorker._recode_is_billable(item, local_line)
    assert result is is_billable_expected, case


def test_recode_is_billable_project_read_failure_falls_back_to_line_value(caplog):
    item = _coding_item()
    local_line = SimpleNamespace(is_billable=True, billable_decided_at=None)
    with patch(
        "entities.project.business.service.ProjectService"
    ) as mock_project_svc:
        mock_project_svc.return_value.read_by_id.side_effect = RuntimeError("db down")
        with caplog.at_level("WARNING"):
            result = QboOutboxWorker._recode_is_billable(item, local_line)
    assert result is True


def test_recode_is_billable_local_line_none_returns_none():
    assert QboOutboxWorker._recode_is_billable(_coding_item(), None) is None



# ---------------------------------------------------------------------------
# Integration pins (Codex P1, round 1): the unit tests above called the service
# and the helper directly, so a router that never supplied the actor, or a handler
# that never called the helper, still passed them.
# ---------------------------------------------------------------------------


def test_router_forwards_the_authenticated_user_id_as_the_actor():
    """RED on the build: the router read current_user["id"], which the JWT payload
    does not carry (it is "user_id", shared/rbac.py), so actor_user_id reached the
    service as None and no human decision was ever stamped. The instant workflow
    passes this payload to ExpenseLineItemService.update_by_public_id as kwargs
    (core/workflow/business/instant.py), so the payload IS the service call."""
    from entities.expense_line_item.api.router import update_expense_line_item_by_public_id_router
    from entities.expense_line_item.api.schemas import ExpenseLineItemUpdate

    body = ExpenseLineItemUpdate(
        row_version="AAAAAAAAAAE=", expense_public_id="00000000-0000-0000-0000-000000000001",
        is_billable=False, billable_decided=True,
    )
    with patch("entities.expense_line_item.api.router.ProcessEngine") as engine_cls:
        engine_cls.return_value.execute_synchronous.return_value = {"success": True, "data": {}}
        update_expense_line_item_by_public_id_router(
            "00000000-0000-0000-0000-0000000000aa", body,
            current_user={"user_id": 17, "sub": "u17", "tenant_id": 1},
        )
        payload = engine_cls.return_value.execute_synchronous.call_args[0][0].payload
    assert payload["billable_decided"] is True
    assert payload["actor_user_id"] == 17


def test_update_service_signature_accepts_the_router_payload_keys():
    """The instant handler calls update_by_public_id(**payload); a renamed kwarg
    would TypeError into a 400 instead of stamping."""
    import inspect
    from entities.expense_line_item.business.service import ExpenseLineItemService
    params = inspect.signature(ExpenseLineItemService.update_by_public_id).parameters
    assert "billable_decided" in params and "actor_user_id" in params


def _run_drain(*, line, project):
    from tests.test_u492_recode_dbo_native_line_resolve import _make_coding_item, _make_outbox_row
    from integrations.intuit.qbo.outbox.business.worker import QboOutboxWorker
    with patch("entities.expense_coding_item.business.service.ExpenseCodingItemService") as svc_cls, \
         patch("integrations.intuit.qbo.purchase.connector.expense.business.service.PurchaseExpenseConnector") as conn_cls, \
         patch.object(QboOutboxWorker, "_resolve_recode_line_economics", return_value=(line, None)), \
         patch("entities.project.business.service.ProjectService") as proj_cls:
        svc_cls.return_value.read_by_public_id.return_value = _make_coding_item()
        proj_cls.return_value.read_by_id.return_value = project
        connector = conn_cls.return_value
        connector.recode_purchase_line.return_value = {"status": "written", "sync_token": "6"}
        QboOutboxWorker()._handle_recode_purchase_line(_make_outbox_row())
        return connector.recode_purchase_line.call_args.kwargs["is_billable"]


def _line(is_billable, decided):
    return SimpleNamespace(quantity=1, rate=Decimal("1"), amount=Decimal("1"), is_billable=is_billable,
                           realm_id=None, billable_decided_at="2026-10-11 12:00:00" if decided else None)


@pytest.mark.parametrize(
    "line,is_cost_plus,expected",
    [
        pytest.param(_line(False, True), True, False, id="decided_no_on_cost_plus_stays_no"),
        pytest.param(_line(False, False), True, True, id="undecided_on_cost_plus_seeds_yes"),
        pytest.param(_line(True, False), False, False, id="undecided_on_flat_rate_seeds_no"),
    ],
)
def test_the_drain_handler_sends_the_rule_not_the_raw_line(line, is_cost_plus, expected):
    """RED if the handler stops calling the helper: restoring the raw
    local_line.is_billable makes the undecided cases send the Ramp mirror."""
    assert _run_drain(line=line, project=SimpleNamespace(is_cost_plus=is_cost_plus)) is expected



@pytest.mark.parametrize(
    "user,raises,expected",
    [
        pytest.param(SimpleNamespace(is_agent=False), False, True, id="human"),
        pytest.param(SimpleNamespace(is_agent=True), False, False, id="agent_account"),
        pytest.param(SimpleNamespace(is_agent=None), False, False, id="agent_flag_unknown"),
        pytest.param(None, False, False, id="user_not_found"),
        pytest.param(None, True, False, id="user_read_fails"),
    ],
)
def test_only_a_readable_human_user_can_be_recorded_as_the_decider(user, raises, expected):
    """Codex P2 (round 2): an agent account has update rights and can send
    billable_decided straight to the API; the stamp would then lock out the pull
    and the drain seed. Fails closed on anything but a known human."""
    from entities.expense_line_item.business.service import _is_human_user
    with patch("entities.user.business.service.UserService") as user_cls:
        if raises:
            user_cls.return_value.read_by_id.side_effect = RuntimeError("db down")
        else:
            user_cls.return_value.read_by_id.return_value = user
        assert _is_human_user(17) is expected



@patch.object(ExpenseLineItemService, "_assert_parent_editable")
@patch("entities.expense_line_item.business.service.assert_can_access_expense")
@patch.object(ExpenseLineItemService, "read_by_public_id")
@patch.object(ExpenseLineItemRepository, "update_by_id")
@patch("entities.expense_line_item.business.service._is_human_user", return_value=False)
def test_an_agent_account_sending_billable_decided_is_not_stamped(
    _mock_human, mock_update, mock_read, _mock_access, _mock_parent,
):
    """The service must consult the human check, not just define it."""
    mock_read.return_value = _line_item(is_billable=False, billable_decided_at=None)
    mock_update.side_effect = lambda item, **kw: item
    ExpenseLineItemService().update_by_public_id(
        "eli-pub", row_version=mock_read.return_value.row_version,
        is_billable=True, billable_decided=True, actor_user_id=99,
    )
    passed = mock_update.call_args.args[0]
    assert passed.is_billable is True          # the value still applies
    assert passed.billable_decided_at is None  # but no authoritative stamp
    assert passed.billable_decided_by_user_id is None
