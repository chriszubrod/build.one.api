"""U-218b — Bill push surface: one sanctioned outbox path."""
import importlib.util
import inspect
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from integrations.intuit.qbo.base.errors import (
    QboBudgetExceededError,
    QboDuplicateError,
    QboValidationError,
    QboWriteRefusedError,
)
from integrations.intuit.qbo.outbox.business.model import QboOutbox
from integrations.intuit.qbo.outbox.business.worker import (
    QboOutboxWorker,
    WRITE_REFUSED_PARK_INTERVAL,
    WRITE_REFUSED_PARK_PREFIX,
)
from scripts.reconcile_project import report_db_to_qbo_bills

REALM_ID = "realm-test"


def _make_outbox_row(attempts=0, last_error=None):
    return QboOutbox(
        id=1,
        public_id="outbox-1",
        row_version="abc",
        kind="sync_bill_to_qbo",
        entity_type="Bill",
        entity_public_id="22222222-2222-2222-2222-222222222222",
        realm_id=REALM_ID,
        request_id="req-1",
        status="in_progress",
        attempts=attempts,
        last_error=last_error,
    )


def test_sync_bill_to_qbo_router_returns_202_and_enqueues():
    from integrations.intuit.qbo.bill.api import router as router_mod
    from integrations.intuit.qbo.bill.api.schemas import QboBillPush

    bill = MagicMock()
    bill.public_id = "11111111-1111-1111-1111-111111111111"
    bill.is_draft = False

    outbox_row = MagicMock(public_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

    with patch.object(router_mod, "BillService") as bill_svc_cls, patch.object(
        router_mod, "QboOutboxService"
    ) as outbox_svc_cls:
        bill_svc_cls.return_value.read_by_public_id.return_value = bill
        outbox_svc_cls.return_value.enqueue.return_value = outbox_row

        response = router_mod.sync_bill_to_qbo_router(
            bill_public_id=str(bill.public_id),
            body=QboBillPush(realm_id=REALM_ID),
            current_user={"sub": "user"},
        )

        outbox_svc_cls.return_value.enqueue.assert_called_once_with(
            kind="sync_bill_to_qbo",
            entity_type="Bill",
            entity_public_id=str(bill.public_id),
            realm_id=REALM_ID,
        )
        assert response.status_code == 202
        import json
        assert json.loads(response.body) == {
            "outbox_public_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        }


def test_sync_bill_to_qbo_router_refuses_with_409_when_dead_letter_exists():
    from fastapi import HTTPException
    from integrations.intuit.qbo.bill.api import router as router_mod
    from integrations.intuit.qbo.bill.api.schemas import QboBillPush
    from integrations.intuit.qbo.outbox.business.service import QboOutboxDeadLetterExistsError

    bill = MagicMock()
    bill.public_id = "11111111-1111-1111-1111-111111111111"
    bill.is_draft = False

    dead_letter_error = QboOutboxDeadLetterExistsError(
        entity_type="Bill",
        entity_public_id=str(bill.public_id),
        kind="sync_bill_to_qbo",
        dead_letter_public_id="dead-letter-outbox-1",
    )

    with patch.object(router_mod, "BillService") as bill_svc_cls, patch.object(
        router_mod, "QboOutboxService"
    ) as outbox_svc_cls:
        bill_svc_cls.return_value.read_by_public_id.return_value = bill
        outbox_svc_cls.return_value.enqueue.side_effect = dead_letter_error

        with pytest.raises(HTTPException) as exc_info:
            router_mod.sync_bill_to_qbo_router(
                bill_public_id=str(bill.public_id),
                body=QboBillPush(realm_id=REALM_ID),
                current_user={"sub": "user"},
            )

    assert exc_info.value.status_code == 409
    assert "retry_qbo_outbox_dead_letters.py" in exc_info.value.detail
    assert "dead-letter-outbox-1" in exc_info.value.detail


def test_qbo_bill_push_schema_has_no_sync_attachments():
    from integrations.intuit.qbo.bill.api.schemas import QboBillPush

    assert "sync_attachments" not in QboBillPush.model_fields


def test_report_db_to_qbo_bills_reports_unmapped_without_qbo_write():
    bill = MagicMock(id=42, bill_number="B-100")

    with patch(
        "integrations.intuit.qbo.bill.connector.bill.business.service.BillBillConnector.sync_to_qbo_bill"
    ) as sync_mock:
        count = report_db_to_qbo_bills({42: bill}, mapped_bill_ids=set())

    assert count == 1
    sync_mock.assert_not_called()


def test_recover_duplicate_qbo_bill_records_issue_and_reraises_typed_error():
    from integrations.intuit.qbo.bill.connector.bill.business.service import BillBillConnector

    connector = BillBillConnector.__new__(BillBillConnector)
    bill = MagicMock()
    bill.id = 7
    bill.public_id = "33333333-3333-3333-3333-333333333333"
    bill.bill_number = "DOC-7"
    bill.vendor_id = 99

    vendor = MagicMock()
    vendor.name = "Acme Supply"
    connector.vendor_service = MagicMock()
    connector.vendor_service.read_by_id.return_value = vendor
    connector.reconciliation_repo = MagicMock()
    connector.create_mapping = MagicMock()
    connector.qbo_bill_repo = MagicMock()

    dup_error = QboDuplicateError("Duplicate DocNumber", code="6140", http_status=400)

    with patch(
        "integrations.intuit.qbo.base.reconciliation_recorder.record_mapping_issue"
    ) as record_issue:
        with pytest.raises(QboDuplicateError) as exc_info:
            connector._recover_duplicate_qbo_bill(
                bill=bill,
                bill_id=7,
                realm_id=REALM_ID,
                error=dup_error,
            )

    assert exc_info.value is dup_error
    record_issue.assert_called_once()
    kwargs = record_issue.call_args.kwargs
    assert kwargs["drift_type"] == "duplicate_qbo_bill_docnumber"
    assert record_issue.call_args.args[0] is connector.reconciliation_repo
    assert len(kwargs["drift_type"]) <= 32
    assert "Acme Supply" in kwargs["details"]
    assert "DOC-7" in kwargs["details"]
    connector.create_mapping.assert_not_called()
    connector.qbo_bill_repo.create.assert_not_called()


def test_sync_to_qbo_bill_duplicate_records_issue_and_propagates_typed_error():
    from integrations.intuit.qbo.bill.connector.bill.business.service import BillBillConnector
    from integrations.intuit.qbo.bill.external.schemas import QboBillLine, QboReferenceType

    connector = BillBillConnector.__new__(BillBillConnector)
    bill = MagicMock()
    bill.id = 7
    bill.public_id = "33333333-3333-3333-3333-333333333333"
    bill.bill_number = "DOC-7"
    bill.bill_date = "2026-01-15"
    bill.due_date = None
    bill.memo = None
    bill.vendor_id = 99
    bill.payment_term_id = None

    line_item = MagicMock()
    line_item.id = 101
    line_item.description = "Materials"
    line_item.amount = Decimal("100.00")
    line_item.sub_cost_code_id = 1
    line_item.project_id = 2
    line_item.is_billable = True
    line_item.is_billed = False

    connector.mapping_repo = MagicMock()
    connector.mapping_repo.read_by_bill_id.return_value = None
    connector.bill_line_item_service = MagicMock()
    connector.bill_line_item_service.read_by_bill_id.return_value = [line_item]
    connector.vendor_service = MagicMock()
    connector.reconciliation_repo = MagicMock()
    connector.qbo_bill_repo = MagicMock()

    qbo_line = QboBillLine(line_num=1, description="Materials", amount=Decimal("100.00"))
    vendor_ref = QboReferenceType(value="v1", name="Acme")

    dup_error = QboDuplicateError("Duplicate DocNumber", code="6140", http_status=400)

    with patch.object(connector, "_get_qbo_vendor_ref", return_value=vendor_ref), patch.object(
        connector, "_build_qbo_line", return_value=qbo_line
    ), patch.object(
        connector, "_get_ap_account_ref", return_value=QboReferenceType(value="ap1")
    ), patch.object(connector, "_get_qbo_sales_term_ref", return_value=None), patch(
        "integrations.intuit.qbo.bill.connector.bill.business.service.QboBillClient"
    ) as client_cls, patch(
        "integrations.intuit.qbo.base.reconciliation_recorder.record_mapping_issue"
    ) as record_issue:
        client_cls.return_value.__enter__.return_value.create_bill.side_effect = dup_error

        with pytest.raises(QboDuplicateError) as exc_info:
            connector.sync_to_qbo_bill(bill=bill, realm_id=REALM_ID)

    assert exc_info.value is dup_error
    record_issue.assert_called_once()
    assert record_issue.call_args.kwargs["drift_type"] == "duplicate_qbo_bill_docnumber"


# NOTE: the pre-claim write-gate tests live in tests/test_qbo_api_budget.py, beside the
# U-211 budget pre-claim guard they mirror. A version of that test lived here first and was
# VACUOUS: `api_budget=MagicMock(blocked=False)` sets `.blocked` on the mock itself, but
# drain_once reads `self._api_budget.status().blocked` — on a MagicMock that is truthy, so the
# BUDGET guard short-circuited and the write guard was never reached. It passed with the guard
# deleted. Mutation testing caught it (removing the guard left the whole suite green); the
# replacement builds the budget mock through `_make_status(blocked=False)` and pins both
# directions.


def test_write_refused_error_parks_with_prefix_and_scheduled_retry():
    repo = MagicMock()
    worker = QboOutboxWorker(repo=repo, api_budget=MagicMock())
    error = QboWriteRefusedError("ALLOW_QBO_WRITES is not true")
    before = datetime.now(timezone.utc)

    worker._handle_qbo_error(_make_outbox_row(attempts=0), error)

    repo.mark_failed.assert_called_once()
    repo.mark_dead_letter.assert_not_called()
    kwargs = repo.mark_failed.call_args.kwargs
    assert kwargs["last_error"].startswith(WRITE_REFUSED_PARK_PREFIX)
    next_retry_at = kwargs["next_retry_at"]
    assert before + WRITE_REFUSED_PARK_INTERVAL <= next_retry_at <= before + WRITE_REFUSED_PARK_INTERVAL + timedelta(seconds=5)


def test_budget_error_still_parks_until_month_reset():
    from integrations.intuit.qbo.base.budget import reset_at_for_month

    repo = MagicMock()
    worker = QboOutboxWorker(repo=repo, api_budget=MagicMock())
    error = QboBudgetExceededError(
        "budget exhausted",
        month_key="2026-08",
        call_count=475_001,
        budget=500_000,
    )

    worker._handle_qbo_error(_make_outbox_row(attempts=0), error)

    repo.mark_dead_letter.assert_not_called()
    repo.mark_failed.assert_called_once()
    kwargs = repo.mark_failed.call_args.kwargs
    assert kwargs["last_error"].startswith("Parked: monthly QBO API budget exhausted")
    assert kwargs["next_retry_at"] == reset_at_for_month("2026-08")


def test_permanent_validation_error_still_dead_letters():
    repo = MagicMock()
    worker = QboOutboxWorker(repo=repo, api_budget=MagicMock())
    error = QboValidationError("bad payload")

    worker._handle_qbo_error(_make_outbox_row(attempts=0), error)

    repo.mark_dead_letter.assert_called_once()
    repo.mark_failed.assert_not_called()


def _load_retry_script_module():
    path = Path(__file__).resolve().parents[1] / "scripts/retry_qbo_outbox_dead_letters.py"
    spec = importlib.util.spec_from_file_location("retry_qbo_outbox_dead_letters", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["retry_qbo_outbox_dead_letters"] = module
    spec.loader.exec_module(module)
    return module


def _run_retry_main(argv, *, rows, rowcount=None):
    retry = _load_retry_script_module()
    if rowcount is None:
        rowcount = len(rows)

    cursor = MagicMock()
    cursor.fetchall.return_value = rows
    cursor.rowcount = rowcount

    conn = MagicMock()
    conn.cursor.return_value = cursor

    @contextmanager
    def fake_get_connection():
        yield conn

    with patch.object(retry, "get_connection", fake_get_connection), patch(
        "sys.argv", argv
    ):
        exit_code = retry.main()

    return retry, cursor, conn, exit_code


def test_retry_qbo_dead_letters_dry_run_mutates_nothing():
    rows = [
        (1, "pid-1", "sync_bill_to_qbo", "Bill", "ent-1", 5, "2026-08-01", "err"),
    ]
    _, cursor, conn, exit_code = _run_retry_main(
        ["retry_qbo_outbox_dead_letters.py"],
        rows=rows,
    )
    assert exit_code == 0
    conn.commit.assert_not_called()
    update_calls = [
        c for c in cursor.execute.call_args_list if "UPDATE qbo.Outbox" in c.args[0]
    ]
    assert update_calls == []


def test_retry_qbo_dead_letters_apply_reasserts_dead_letter_status_and_preserves_request_id():
    rows = [
        (10, "pid-10", "sync_bill_to_qbo", "Bill", "ent-10", 5, "2026-08-01", "err"),
    ]
    _, cursor, conn, exit_code = _run_retry_main(
        ["retry_qbo_outbox_dead_letters.py", "--kind", "sync_bill_to_qbo", "--apply"],
        rows=rows,
    )
    assert exit_code == 0
    conn.commit.assert_called_once()
    select_call = cursor.execute.call_args_list[0]
    assert "Kind IN (?)" in select_call.args[0]
    assert select_call.args[1] == "sync_bill_to_qbo"
    update_call = next(
        c for c in cursor.execute.call_args_list if "UPDATE qbo.Outbox" in c.args[0]
    )
    sql = update_call.args[0]
    assert "AND Status = 'dead_letter'" in sql
    assert "RequestId" not in sql
    assert "LastError" not in sql
    assert "DeadLetteredAt" not in sql
    assert update_call.args[1:] == (
        update_call.args[1],
        update_call.args[2],
        10,
    )


def test_retry_qbo_dead_letters_reports_actual_rowcount_not_scan_count():
    rows = [
        (10, "pid-10", "sync_bill_to_qbo", "Bill", "ent-10", 5, "2026-08-01", "err"),
        (11, "pid-11", "sync_bill_to_qbo", "Bill", "ent-11", 5, "2026-08-01", "err"),
    ]
    retry = _load_retry_script_module()
    cursor = MagicMock()
    cursor.fetchall.return_value = rows
    cursor.rowcount = 1
    conn = MagicMock()
    conn.cursor.return_value = cursor

    @contextmanager
    def fake_get_connection():
        yield conn

    printed = []

    def capture_print(*args, **kwargs):
        printed.append(" ".join(str(a) for a in args))

    with patch.object(retry, "get_connection", fake_get_connection), patch(
        "sys.argv",
        ["retry_qbo_outbox_dead_letters.py", "--kind", "sync_bill_to_qbo", "--apply"],
    ), patch("builtins.print", capture_print):
        assert retry.main() == 0

    assert any("Reset 1 row(s)" in line for line in printed)
    assert any("matched 2 row(s) but updated 1" in line for line in printed)


def test_retry_qbo_dead_letters_rejects_unknown_kind():
    retry = _load_retry_script_module()
    with patch("sys.argv", ["retry_qbo_outbox_dead_letters.py", "--kind", "typo_kind"]):
        assert retry.main() == 2


def test_retry_qbo_dead_letters_rejects_limit_above_sql_cap():
    retry = _load_retry_script_module()
    with patch("sys.argv", ["retry_qbo_outbox_dead_letters.py", "--limit", "3000"]):
        assert retry.main() == 2


def test_sync_qbo_bill_has_no_push_flag_or_push_path():
    import scripts.sync_qbo_bill as bill_script

    with patch("sys.argv", ["sync_qbo_bill.py"]):
        bill_script.parse_args()

    with patch("sys.argv", ["sync_qbo_bill.py", "--push"]):
        with pytest.raises(SystemExit):
            bill_script.parse_args()

    source = inspect.getsource(bill_script.sync_qbo_bill)
    assert "sync_local_to_qbo" not in source
    assert "--push" not in inspect.getsource(bill_script.parse_args)
