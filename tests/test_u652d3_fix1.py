"""U-652d3 fix rounds 1-2 (EM review).

F1 an empty strict set probes the label-only lines; a landed write or a human recode is decided by the
   connector's live fence, not an error.
F2 a confirm with zero uncategorized lines is refused as `nothing_to_recode` before any write.
F3 a review_submit line is written with its OWN ELI coding at drain time (the permanent rule).
F4 an account-based line clears its sticky ItemRefValue in the staging upsert.
P3-2 a transient ELI read failure on a review_submit single line is retryable, not sticky.
P3-c nothing_to_recode is an info-level skip.
"""

import json
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from entities.coding_suggestion.business.service import CodingSuggestionService
from entities.expense.business.service import ExpenseService
from integrations.intuit.qbo.base.errors import QboServerError
from integrations.intuit.qbo.outbox.business.worker import QboOutboxWorker
from integrations.intuit.qbo.purchase.connector.expense.business.errors import PurchaseChangedInQboError
from tests.sproc_text import REPO_ROOT, sproc_body, strip_sql_comments

QBO_PURCHASE_SQL = REPO_ROOT / "integrations/intuit/qbo/purchase/sql/qbo.purchase.sql"
CONNECTOR_PATH = "integrations.intuit.qbo.purchase.connector.expense.business.service.PurchaseExpenseConnector"
SUGGESTION_SVC_PATH = "entities.coding_suggestion.business.service.CodingSuggestionService"


# --- shared helpers -------------------------------------------------------------------------------


def _row():
    return SimpleNamespace(entity_public_id="pub-1", realm_id="R1", entity_type="ExpenseCodingItem")


def _item(**overrides):
    base = dict(
        status="enqueued",
        confirmation_seq=3,
        confirmation_source=None,
        qbo_purchase_qbo_id="P9",
        realm_id="R1",
        sync_token_at_suggest="5",
        written_sync_token=None,
        written_lines=None,
        confirmed_project_id=99,
        confirmed_sub_cost_code_id=999,
        confirmed_description="confirmed desc",
        suggested_project_id=None,
        suggested_sub_cost_code_id=None,
        is_billable=True,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _local_line(sub=11, project=1):
    return SimpleNamespace(
        sub_cost_code_id=sub,
        project_id=project,
        description="local desc",
        quantity=2,
        rate=10,
        amount=20,
    )


def _resolve(item, *, uncategorized, economics, label_only=None):
    """Targets for a drain, with the staging reads, ELI economics and billable lookup patched.

    `economics` is passed straight to patch.object for _resolve_recode_line_economics, e.g.
    {"return_value": (line, None)} or {"side_effect": RuntimeError("boom")}.
    """
    worker = QboOutboxWorker.__new__(QboOutboxWorker)
    with patch.object(QboOutboxWorker, "_read_uncategorized_staging_line_ids", return_value=uncategorized), patch.object(
        QboOutboxWorker, "_read_label_staging_line_ids", return_value=label_only or []
    ), patch.object(QboOutboxWorker, "_resolve_recode_line_economics", **economics), patch.object(
        QboOutboxWorker, "_record_recode_unresolved_line_economics"
    ), patch.object(
        QboOutboxWorker, "_recode_is_billable", return_value=True
    ), patch.object(
        QboOutboxWorker,
        "_recode_target_from_local_line",
        side_effect=lambda item, qbo_line_id, local_line, **kw: {"target_qbo_line_id": qbo_line_id, **kw},
    ):
        return worker._resolve_recode_purchase_line_targets(_row(), item)


def _drain(item, *, uncategorized, label_only, connector_result=None, connector_error=None):
    """Run _drain_recode_purchase_line_locked with the QBO and DB edges mocked."""
    svc = MagicMock()
    svc.read_by_public_id.return_value = item
    svc.mark_written.return_value = object()
    svc.mark_changed_in_qbo.return_value = object()
    svc.mark_error.return_value = object()
    connector = MagicMock()
    if connector_error is not None:
        connector.recode_purchase_lines.side_effect = connector_error
    else:
        connector.recode_purchase_lines.return_value = connector_result
    worker = QboOutboxWorker.__new__(QboOutboxWorker)
    with patch.object(QboOutboxWorker, "_read_uncategorized_staging_line_ids", return_value=uncategorized), patch.object(
        QboOutboxWorker, "_read_label_staging_line_ids", return_value=label_only
    ), patch.object(
        QboOutboxWorker, "_resolve_recode_line_economics", return_value=(_local_line(), None)
    ), patch.object(
        QboOutboxWorker, "_recode_is_billable", return_value=True
    ), patch.object(
        QboOutboxWorker,
        "_recode_target_from_local_line",
        side_effect=lambda item, qbo_line_id, local_line, **kw: {"target_qbo_line_id": qbo_line_id, **kw},
    ), patch(SUGGESTION_SVC_PATH, return_value=svc), patch(CONNECTOR_PATH, return_value=connector):
        worker._drain_recode_purchase_line_locked(_row())
    return svc, connector


# --- F1: empty strict set probes the label-only lines --------------------------------------------


def test_f1_retry_after_our_write_and_pull_adopts_the_record_as_written():
    """Our write landed; a pull kept ItemRef set so the strict set is empty. The label-only probe reaches
    the connector, whose live-token adoption marks the row written with the verified lines."""
    verified = [{"target_qbo_line_id": "1", "sub_cost_code_id": 11, "project_id": 1}]
    svc, connector = _drain(
        _item(),
        uncategorized=[],
        label_only=["1"],
        connector_result={"status": "already_recoded", "sync_token": "6", "verified_lines": verified},
    )
    connector.recode_purchase_lines.assert_called_once()
    assert [line["target_qbo_line_id"] for line in connector.recode_purchase_lines.call_args.kwargs["lines"]] == ["1"]
    svc.mark_error.assert_not_called()
    svc.mark_written.assert_called_once()
    kwargs = svc.mark_written.call_args.kwargs
    assert kwargs["sync_token"] == "6"
    assert json.loads(kwargs["written_lines_json"]) == verified


def test_f1_human_recode_then_pull_goes_changed_in_qbo_not_error():
    """A human recoded the line in QBO and a pull ran: the connector's changed-branch raises, and the row
    goes changed_in_qbo (sweeper-resolvable), never error."""
    svc, connector = _drain(
        _item(),
        uncategorized=[],
        label_only=["1"],
        connector_error=PurchaseChangedInQboError(
            qbo_purchase_qbo_id="P9", expected_sync_token="5", actual_sync_token="6"
        ),
    )
    connector.recode_purchase_lines.assert_called_once()
    svc.mark_changed_in_qbo.assert_called_once()
    svc.mark_error.assert_not_called()


def test_f1_neither_strict_nor_label_lines_keeps_the_existing_error():
    svc, connector = _drain(_item(), uncategorized=[], label_only=[])
    connector.recode_purchase_lines.assert_not_called()
    svc.mark_error.assert_called_once()
    assert svc.mark_error.call_args.kwargs["write_error"] == "no_target_lines"


def test_f1_repoint_never_uses_the_label_probe():
    """A repoint keeps its own verify-token branch; the label fallback is first-write only."""
    source = (REPO_ROOT / "integrations/intuit/qbo/outbox/business/worker.py").read_text()
    resolver = source[source.index("def _resolve_recode_purchase_line_targets"):]
    resolver = resolver[: resolver.index("def _recode_target_from_local_line")]
    assert "_read_label_staging_line_ids" in resolver
    fallback_guard = resolver.index("_read_label_staging_line_ids(")
    assert resolver.index("REPOINT_CONFIRMATION_SOURCE") < fallback_guard


# --- F2: zero uncategorized lines refused as nothing_to_recode -----------------------------------


def test_f2_zero_uncategorized_lines_is_refused_as_nothing_to_recode():
    assert CodingSuggestionService._uncategorized_count_refusal(
        uncategorized_count=0, allow_multi_line=False
    ) == {"status": "nothing_to_recode"}


def test_f2_zero_uncategorized_lines_refused_even_when_multi_line_allowed():
    assert CodingSuggestionService._uncategorized_count_refusal(
        uncategorized_count=0, allow_multi_line=True
    ) == {"status": "nothing_to_recode"}


def test_f2_one_or_more_lines_proceed_and_many_without_multi_still_refused():
    assert CodingSuggestionService._uncategorized_count_refusal(uncategorized_count=1, allow_multi_line=False) is None
    assert CodingSuggestionService._uncategorized_count_refusal(uncategorized_count=3, allow_multi_line=True) is None
    assert CodingSuggestionService._uncategorized_count_refusal(uncategorized_count=2, allow_multi_line=False) == {
        "status": "multi_line_unsupported",
        "reason": "multi_line_purchase_confirm_unsupported",
    }


def test_f2_refusal_runs_before_any_confirmation_write():
    import inspect

    source = inspect.getsource(CodingSuggestionService._confirm_under_lock)
    assert source.index("_uncategorized_count_refusal(") < source.index("record_confirmation"), (
        "zero-count refusal must precede every write"
    )


# --- F3: a review_submit line is written with its OWN ELI coding (the permanent rule) ------------


def test_f3_review_submit_single_line_uses_the_local_eli_coding_not_confirmed():
    """Deploy straddle: a submit recorded under the OLD loose dominant (99/999, a recoded sibling's coding)
    drains under the strict single-line set. The write is the genuine line's ELI coding (1/11)."""
    targets, error = _resolve(
        _item(confirmation_source="review_submit"),
        uncategorized=["1"],
        economics={"return_value": (_local_line(sub=11, project=1), None)},
    )
    assert error is None
    assert targets[0]["sub_cost_code_id"] == 11
    assert targets[0]["project_id"] == 1


def test_f3_review_submit_single_line_without_local_eli_is_an_error_not_the_confirmed_coding():
    targets, error = _resolve(
        _item(confirmation_source="review_submit"),
        uncategorized=["1"],
        economics={"return_value": (None, "missing")},
    )
    assert targets is None
    assert error == QboOutboxWorker._MULTI_LINE_UNMAPPED_ERROR


def test_f3_non_review_single_line_keeps_the_confirmed_coding():
    targets, error = _resolve(
        _item(confirmation_source="expense_coding"),
        uncategorized=["1"],
        economics={"return_value": (None, None)},
    )
    assert error is None
    assert targets[0]["sub_cost_code_id"] == 999
    assert targets[0]["project_id"] == 99


# --- F4: an account-based line clears the sticky ItemRef ----------------------------------------


def test_f4_update_upsert_clears_item_ref_when_the_line_is_account_based():
    body = strip_sql_comments(sproc_body(QBO_PURCHASE_SQL, "UpdateQboPurchaseLineById"))
    for column in ("ItemRefValue", "ItemRefName"):
        expected = (
            f"[{column}] = CASE WHEN @DetailType = N'AccountBasedExpenseLineDetail' THEN NULL "
            f"WHEN @{column} IS NULL THEN [{column}] ELSE @{column} END"
        )
        assert expected in body, f"{column} must clear on an account-based line, keep otherwise"


def test_f4_item_based_lines_keep_todays_sticky_behaviour():
    body = strip_sql_comments(sproc_body(QBO_PURCHASE_SQL, "UpdateQboPurchaseLineById"))
    assert "WHEN @ItemRefValue IS NULL THEN [ItemRefValue] ELSE @ItemRefValue END" in body


# --- P3-2: a transient ELI read failure is retryable, not sticky ---------------------------------


def test_p3_2_review_submit_single_line_read_failure_is_retryable_not_sticky():
    with pytest.raises(QboServerError) as raised:
        _resolve(
            _item(confirmation_source="review_submit"),
            uncategorized=["1"],
            economics={"side_effect": RuntimeError("db blip")},
        )
    assert raised.value.is_retryable is True


def test_p3_2_review_submit_single_line_genuinely_missing_eli_stays_a_sticky_error():
    targets, error = _resolve(
        _item(confirmation_source="review_submit"),
        uncategorized=["1"],
        economics={"return_value": (None, "no_local_line")},
    )
    assert targets is None
    assert error == QboOutboxWorker._MULTI_LINE_UNMAPPED_ERROR


def test_p3_2_multi_line_read_failure_keeps_the_existing_sticky_error_asymmetry():
    """The multi-line path does not distinguish a read failure from a missing ELI today (reported as an
    asymmetry, not changed): both stay the sticky unmapped error."""
    targets, error = _resolve(
        _item(confirmation_source="review_submit"),
        uncategorized=["1", "2"],
        economics={"side_effect": RuntimeError("db blip")},
    )
    assert targets is None
    assert error == QboOutboxWorker._MULTI_LINE_UNMAPPED_ERROR


# --- P3-c: nothing_to_recode is an info-level skip -----------------------------------------------


def test_p3_c_nothing_to_recode_is_an_info_skip():
    assert "nothing_to_recode" in ExpenseService._RECODE_ENQUEUE_NOTHING_TO_DO_REASONS
    assert ExpenseService._recode_enqueue_skip_log_level("nothing_to_recode") == logging.INFO


def test_p3_c_nothing_to_recode_surfaces_as_the_skip_reason():
    summary = {"enqueued": 0, "items": [{"reason": "nothing_to_recode"}]}
    assert ExpenseService._recode_enqueue_skip_reason(summary) == "nothing_to_recode"
