"""U-652c: terminal marks on CodingSuggestion are guarded by ConfirmationSeq."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from integrations.intuit.qbo.base.errors import QboSyncTokenMismatchError
from integrations.intuit.qbo.outbox.business.worker import QboOutboxWorker
from integrations.intuit.qbo.purchase.connector.expense.business.errors import (
    PurchaseChangedInQboError,
    PurchaseRecodeMappingError,
)
from tests.test_u652_fix_round2 import PUBLIC_ID, _make_item, _make_outbox

_SVC = "entities.coding_suggestion.business.service.CodingSuggestionService"
_CONN = "integrations.intuit.qbo.purchase.connector.expense.business.service.PurchaseExpenseConnector"

_SEQ = 3
_LOG_PREFIX = "coding_suggestion terminal mark skipped"


def _default_targets():
    return [
        {
            "target_qbo_line_id": "1",
            "sub_cost_code_id": 101,
            "project_id": 202,
            "description": "d",
            "quantity": None,
            "rate": None,
            "amount": None,
            "is_billable": None,
        }
    ]


def _item_with_seq(**kwargs):
    item = _make_item(**kwargs)
    item.confirmation_seq = _SEQ
    return item


def _run_handler(
    *,
    item,
    resolve=(None, "confirmation_scope_changed"),
    recode_result=None,
    recode_exc=None,
    mark_side_effects=None,
):
    worker = QboOutboxWorker()
    with patch(_SVC) as svc_cls, patch(_CONN) as conn_cls, patch.object(
        worker, "_resolve_recode_purchase_line_targets", return_value=resolve
    ):
        svc = svc_cls.return_value
        svc.read_by_public_id.return_value = item
        if mark_side_effects:
            for name, effects in mark_side_effects.items():
                getattr(svc, name).side_effect = effects
        conn = conn_cls.return_value
        if recode_exc is not None:
            conn.recode_purchase_lines.side_effect = recode_exc
        elif recode_result is not None:
            conn.recode_purchase_lines.return_value = recode_result
        worker._handle_recode_purchase_line(_make_outbox())
    return svc, conn


def _assert_seq_kw(mock_call):
    assert mock_call.kwargs.get("expected_confirmation_seq") == _SEQ
    assert "expected_row_version" not in mock_call.kwargs


def test_written_passes_confirmation_seq():
    item = _item_with_seq()
    svc, conn = _run_handler(
        item=item,
        resolve=(_default_targets(), None),
        recode_result={"status": "written", "sync_token": "6"},
    )
    conn.recode_purchase_lines.assert_called_once()
    svc.mark_written.assert_called_once()
    _assert_seq_kw(svc.mark_written.call_args)


def test_already_recoded_passes_confirmation_seq():
    item = _item_with_seq()
    svc, _ = _run_handler(
        item=item,
        resolve=(_default_targets(), None),
        recode_result={"status": "already_recoded", "sync_token": "6"},
    )
    svc.mark_written.assert_called_once()
    _assert_seq_kw(svc.mark_written.call_args)


def test_purchase_changed_passes_confirmation_seq():
    item = _item_with_seq()
    svc, _ = _run_handler(
        item=item,
        resolve=(_default_targets(), None),
        recode_exc=PurchaseChangedInQboError(
            qbo_purchase_qbo_id="x", expected_sync_token="1", actual_sync_token="2"
        ),
    )
    svc.mark_changed_in_qbo.assert_called_once()
    _assert_seq_kw(svc.mark_changed_in_qbo.call_args)


def test_sync_token_mismatch_passes_confirmation_seq():
    item = _item_with_seq()
    svc, _ = _run_handler(
        item=item,
        resolve=(_default_targets(), None),
        recode_exc=QboSyncTokenMismatchError("stale"),
    )
    svc.mark_changed_in_qbo.assert_called_once()
    _assert_seq_kw(svc.mark_changed_in_qbo.call_args)


def test_mapping_error_passes_confirmation_seq():
    item = _item_with_seq()
    svc, _ = _run_handler(
        item=item,
        resolve=(_default_targets(), None),
        recode_exc=PurchaseRecodeMappingError(sub_cost_code_id=101),
    )
    svc.mark_error.assert_called_once()
    _assert_seq_kw(svc.mark_error.call_args)


def test_no_target_lines_passes_confirmation_seq():
    item = _item_with_seq()
    svc, conn = _run_handler(item=item, resolve=([], None))
    conn.recode_purchase_lines.assert_not_called()
    svc.mark_error.assert_called_once()
    _assert_seq_kw(svc.mark_error.call_args)
    assert svc.mark_error.call_args.kwargs.get("write_error") == "no_target_lines"


def test_resolve_unmapped_passes_confirmation_seq():
    item = _item_with_seq()
    svc, conn = _run_handler(
        item=item,
        resolve=(None, "multi_line_unmapped"),
    )
    conn.recode_purchase_lines.assert_not_called()
    svc.mark_error.assert_called_once()
    _assert_seq_kw(svc.mark_error.call_args)


def test_scope_change_passes_confirmation_seq():
    item = _item_with_seq()
    item.confirmation_source = "confirm_endpoint"
    svc, conn = _run_handler(item=item)
    conn.recode_purchase_lines.assert_not_called()
    svc.mark_changed_in_qbo.assert_called_once()
    _assert_seq_kw(svc.mark_changed_in_qbo.call_args)


def test_line_not_found_passes_confirmation_seq():
    item = _item_with_seq()
    svc, _ = _run_handler(
        item=item,
        resolve=(_default_targets(), None),
        recode_result={"status": "line_not_found"},
    )
    svc.mark_changed_in_qbo.assert_called_once()
    _assert_seq_kw(svc.mark_changed_in_qbo.call_args)


def test_unexpected_recode_status_passes_confirmation_seq():
    item = _item_with_seq()
    svc, _ = _run_handler(
        item=item,
        resolve=(_default_targets(), None),
        recode_result={"status": "weird"},
    )
    svc.mark_error.assert_called_once()
    _assert_seq_kw(svc.mark_error.call_args)


@pytest.mark.parametrize(
    "mark_method,resolve,recode_result,recode_exc",
    [
        ("mark_written", (_default_targets(), None), {"status": "written", "sync_token": "6"}, None),
        ("mark_changed_in_qbo", None, None, None),
        ("mark_error", ([], None), None, None),
    ],
)
def test_guard_miss_logs_and_returns_without_retry(
    caplog, mark_method, resolve, recode_result, recode_exc
):
    item = _item_with_seq()
    if resolve is None:
        resolve = (None, "confirmation_scope_changed")
    with caplog.at_level(logging.INFO):
        svc, _ = _run_handler(
            item=item,
            resolve=resolve,
            recode_result=recode_result,
            recode_exc=recode_exc,
            mark_side_effects={mark_method: [None]},
        )
    assert getattr(svc, mark_method).call_count == 1
    svc.read_by_public_id.assert_called_once()
    assert any(_LOG_PREFIX in r.message and PUBLIC_ID in r.message for r in caplog.records)


_SEQ_GUARD_STATUS_PATTERN = re.compile(
    r"@ExpectedConfirmationSeq IS NULL\s+OR\s+\(\s*"
    r"cs\.\[ConfirmationSeq\] = @ExpectedConfirmationSeq\s+"
    r"AND cs\.\[Status\] IN \(N'confirmed', N'enqueued'\)\s*\)",
    re.MULTILINE,
)


def test_terminal_marks_require_confirmed_or_enqueued_when_seq_guard_set():
    sql = (
        Path(__file__).resolve().parents[1]
        / "entities/coding_suggestion/sql/dbo.coding_suggestion.sql"
    ).read_text(encoding="utf-8")
    for proc in (
        "MarkCodingSuggestionWritten",
        "MarkCodingSuggestionError",
        "MarkCodingSuggestionChangedInQbo",
    ):
        start = sql.index(f"CREATE OR ALTER PROCEDURE {proc}")
        end = sql.index("CREATE OR ALTER PROCEDURE", start + 1)
        body = sql[start:end]
        assert _SEQ_GUARD_STATUS_PATTERN.search(body), (
            f"{proc} must gate seq-guarded updates on confirmed/enqueued status"
        )


def test_worker_never_passes_expected_row_version_on_any_path():
    item = _item_with_seq()
    item.confirmation_source = "confirm_endpoint"
    paths = [
        dict(resolve=(None, "confirmation_scope_changed")),
        dict(resolve=([], None)),
        dict(
            resolve=(_default_targets(), None),
            recode_result={"status": "written", "sync_token": "6"},
        ),
        dict(
            resolve=(_default_targets(), None),
            recode_exc=PurchaseChangedInQboError(
                qbo_purchase_qbo_id="p", expected_sync_token="1", actual_sync_token="2"
            ),
        ),
    ]
    for kwargs in paths:
        svc, _ = _run_handler(item=_item_with_seq(), **kwargs)
        for name in ("mark_written", "mark_changed_in_qbo", "mark_error"):
            mock = getattr(svc, name)
            if mock.called:
                for c in mock.call_args_list:
                    assert "expected_row_version" not in c.kwargs
