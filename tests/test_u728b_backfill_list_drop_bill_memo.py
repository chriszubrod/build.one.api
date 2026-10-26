"""U-728b: backfill list-drop Bill.Memo — pure mocks, no live DB/LLM/network."""
from __future__ import annotations

import asyncio
import csv
import inspect
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from intelligence.cascade.core import CascadeResult, Rung

import scripts.backfill_list_drop_bill_memo as backfill_mod
from scripts.backfill_list_drop_bill_memo import (
    CandidateRow,
    RowOutcome,
    _candidate_sql,
    _statuses_from_args,
    _validate_review_csv_header,
    apply_from_reviewed_csv,
    apply_memo_update,
    list_candidates,
    main,
    process_candidate,
    run_backfill,
)


def _header_cascade(result: dict | None = None, *, stopped_reason: str | None = None) -> CascadeResult:
    return CascadeResult(
        accepted=result is not None,
        needs_human=False,
        result=result,
        confidence=0.9 if result else None,
        winning_rung=Rung(provider="anthropic", model="claude-haiku-5-5") if result else None,
        attempts=[],
        stopped_reason=stopped_reason,
    )


def _ok_header_result() -> dict:
    return {
        "vendor_name": "Acme",
        "bill_number": "INV-READ-99",
        "bill_date": "2026-01-01",
        "total_amount": "10.00",
        "long_description": "SECRET_LONG_DESCRIPTION_FOR_TEST",
        "reference": "REF-1",
        "confidence": 0.9,
    }


def _candidate(**overrides) -> CandidateRow:
    base = dict(
        bill_id=101,
        bill_public_id="11111111-1111-1111-1111-111111111111",
        status="in_review",
        bill_number="B-CURRENT",
        entered_by_user_id=17,
        prediction_attachment_id=55,
    )
    base.update(overrides)
    return CandidateRow(**base)


@contextmanager
def _attachment_pipeline(
    *,
    att,
    is_taxpayer: bool,
    linked: bool,
    masked_text: str | None = "text",
):
    with ExitStack() as stack:
        stack.enter_context(
            patch.object(
                backfill_mod,
                "AttachmentService",
                return_value=MagicMock(read_by_id=MagicMock(return_value=att)),
            )
        )
        stack.enter_context(
            patch.object(backfill_mod, "_attachment_linked_to_bill", return_value=linked)
        )
        stack.enter_context(
            patch.object(
                backfill_mod,
                "read_attachment_link_state",
                return_value=SimpleNamespace(is_taxpayer=is_taxpayer),
            )
        )
        stack.enter_context(
            patch.object(
                backfill_mod,
                "_cached_masked_text_for_bill_drop",
                return_value=masked_text,
            )
        )
        yield


def test_candidate_sql_one_row_per_bill_cross_apply():
    sql, params = _candidate_sql(statuses=["in_review"], bill_ids=None, limit=50)
    assert "CROSS APPLY" in sql
    assert "TOP 1" in sql
    assert "ORDER BY p.[Id]" in sql
    assert "p.[AttachmentId]" in sql
    assert "COALESCE(p.[CreatedByUserId], b.[CreatedByUserId])" in sql
    assert params[0] == 50
    assert params[1] == "list_drop"
    assert params[2] == "in_review"
    assert "TOP (?)" in sql
    assert "INNER JOIN dbo.[BillIntakePrediction]" not in sql


def test_candidate_sql_includes_draft_when_requested():
    sql, params = _candidate_sql(statuses=["in_review", "draft"], bill_ids=[1, 2], limit=10)
    assert params == (10, "list_drop", "in_review", "draft", 1, 2)
    assert "b.[Id] IN" in sql


def test_statuses_from_args():
    assert _statuses_from_args([]) == ["in_review"]
    assert _statuses_from_args(["draft"]) == ["in_review", "draft"]


def test_script_does_not_import_bill_intake_write_paths():
    source = Path(backfill_mod.__file__).read_text()
    assert "run_bill_read_attachment" not in source
    assert "_schedule_record_read" not in source
    assert "BillIntakePredictionService" not in source
    assert ".extract_for_bill_drop_prefill(" not in source
    assert "_bill_drop_extract(" not in source


def test_process_skipped_no_attachment_when_prediction_attachment_missing():
    row = asyncio.run(process_candidate(_candidate(prediction_attachment_id=None)))
    assert row.outcome == "skipped_no_attachment"


def test_process_skipped_attachment_unlinked():
    att = SimpleNamespace(id=55, blob_url="http://x")
    with patch.object(
        backfill_mod,
        "AttachmentService",
        return_value=MagicMock(read_by_id=MagicMock(return_value=att)),
    ), patch.object(backfill_mod, "_attachment_linked_to_bill", return_value=False):
        row = asyncio.run(process_candidate(_candidate(prediction_attachment_id=55)))
    assert row.outcome == "skipped_attachment_unlinked"


def test_process_reads_prediction_attachment_id():
    att = SimpleNamespace(id=55, blob_url="http://x")
    read_by_id = MagicMock(return_value=att)
    with patch.object(
        backfill_mod,
        "AttachmentService",
        return_value=MagicMock(read_by_id=read_by_id),
    ), patch.object(backfill_mod, "_attachment_linked_to_bill", return_value=True), patch.object(
        backfill_mod,
        "_cached_masked_text_for_bill_drop",
        return_value=None,
    ):
        row = asyncio.run(process_candidate(_candidate(prediction_attachment_id=55)))
    read_by_id.assert_called_once_with(id=55)
    assert row.outcome == "skipped_no_cached_extraction"


def test_reuse_only_uses_aggressive_masking_when_taxpayer():
    att = SimpleNamespace(id=5, blob_url="http://blob")
    prior = {"content": "masked body", "method": "text_layer"}
    ext_instance = MagicMock()
    ext_instance._bill_drop_reusable_payload.return_value = prior
    ext_instance.repo.read_by_id.return_value = None
    ext_instance._completed_outcome_from_payload.return_value = {
        "status": "completed",
        "masked_text": "masked body",
    }

    with patch.object(backfill_mod, "AttachmentExtractionService", return_value=ext_instance):
        text = backfill_mod._cached_masked_text_for_bill_drop(
            att, is_taxpayer_document=True
        )
    assert text == "masked body"
    ext_instance._bill_drop_reusable_payload.assert_called()
    assert ext_instance._bill_drop_reusable_payload.call_args.kwargs["aggressive"] is True


def test_process_passes_taxpayer_flag_to_cached_extraction():
    att = SimpleNamespace(id=55, blob_url="http://x")
    cascade = _header_cascade(_ok_header_result())
    with patch.object(
        backfill_mod,
        "AttachmentService",
        return_value=MagicMock(read_by_id=MagicMock(return_value=att)),
    ), patch.object(backfill_mod, "_attachment_linked_to_bill", return_value=True), patch.object(
        backfill_mod, "read_attachment_link_state", return_value=SimpleNamespace(is_taxpayer=True)
    ), patch.object(
        backfill_mod,
        "_cached_masked_text_for_bill_drop",
        return_value="masked",
    ) as mock_cached, patch.object(
        backfill_mod, "run_bill_header_extraction", new=AsyncMock(return_value=cascade)
    ), patch.object(
        backfill_mod, "_resolve_entered_by_for_memo", new=AsyncMock(return_value="Chris Z")
    ), patch(
        "entities.bill.business.read_attachment_service.resolve_vendor_match_from_name",
        return_value=None,
    ):
        asyncio.run(process_candidate(_candidate()))
    mock_cached.assert_called_once()
    assert mock_cached.call_args.kwargs["is_taxpayer_document"] is True


def test_process_skipped_no_read_when_header_not_ok():
    att = SimpleNamespace(id=55, blob_url="http://x")
    empty_cascade = _header_cascade(None)
    with _attachment_pipeline(
        att=att, is_taxpayer=False, linked=True, masked_text="text"
    ), patch.object(
        backfill_mod, "run_bill_header_extraction", new=AsyncMock(return_value=empty_cascade)
    ), patch.object(
        backfill_mod, "_header_from_cascade", return_value=({}, False, None, None)
    ):
        row = asyncio.run(process_candidate(_candidate()))
    assert row.outcome == "skipped_no_read"
    assert row.proposed_memo is None


def test_process_daily_cap_stops_flag():
    att = SimpleNamespace(id=55, blob_url="http://x")
    cap_cascade = _header_cascade(stopped_reason="daily_cap")
    with _attachment_pipeline(
        att=att, is_taxpayer=False, linked=True, masked_text="text"
    ), patch.object(
        backfill_mod, "run_bill_header_extraction", new=AsyncMock(return_value=cap_cascade)
    ):
        row = asyncio.run(process_candidate(_candidate()))
    assert row.stop_daily_cap is True


def test_process_composes_would_write():
    att = SimpleNamespace(id=55, blob_url="http://x")
    cascade = _header_cascade(_ok_header_result())
    resolve = AsyncMock(return_value="Chris Z")
    with _attachment_pipeline(
        att=att, is_taxpayer=True, linked=True, masked_text="masked"
    ), patch.object(
        backfill_mod, "run_bill_header_extraction", new=AsyncMock(return_value=cascade)
    ), patch.object(
        backfill_mod, "_resolve_entered_by_for_memo", new=resolve
    ), patch(
        "entities.bill.business.read_attachment_service.resolve_vendor_match_from_name",
        return_value=None,
    ):
        row = asyncio.run(process_candidate(_candidate(entered_by_user_id=42)))
    resolve.assert_awaited_once_with(42)
    assert row.outcome == "would_write"
    assert "PDF drop" in (row.proposed_memo or "")
    assert "SECRET_LONG_DESCRIPTION_FOR_TEST" in (row.proposed_memo or "")
    assert "DOC#: INV-READ-99" in (row.proposed_memo or "")


def test_error_on_one_row_does_not_raise():
    with patch.object(
        backfill_mod,
        "AttachmentService",
        side_effect=RuntimeError("boom"),
    ):
        row = asyncio.run(process_candidate(_candidate()))
    assert row.outcome == "error:RuntimeError"


def test_run_bill_read_attachment_never_called_during_backfill():
    att = SimpleNamespace(id=55, blob_url="http://x")
    cascade = _header_cascade(_ok_header_result())
    with patch(
        "entities.bill.business.read_attachment_service.run_bill_read_attachment",
        new=AsyncMock(),
    ) as mock_read, patch(
        "entities.bill.business.read_attachment_service._schedule_record_read",
    ) as mock_schedule, _attachment_pipeline(
        att=att, is_taxpayer=False, linked=True, masked_text="x"
    ), patch.object(
        backfill_mod, "run_bill_header_extraction", new=AsyncMock(return_value=cascade)
    ), patch.object(
        backfill_mod, "_resolve_entered_by_for_memo", new=AsyncMock(return_value="U")
    ), patch(
        "entities.bill.business.read_attachment_service.resolve_vendor_match_from_name",
        return_value=None,
    ):
        asyncio.run(process_candidate(_candidate()))
    mock_read.assert_not_called()
    mock_schedule.assert_not_called()


def test_dry_run_never_calls_guarded_update(tmp_path):
    csv_path = tmp_path / "plan.csv"
    candidates = [_candidate()]
    conn_ctx = MagicMock()
    conn_ctx.__enter__.return_value = MagicMock()
    conn_ctx.__exit__.return_value = False

    with patch.object(backfill_mod, "get_connection", return_value=conn_ctx), patch.object(
        backfill_mod, "list_candidates", return_value=candidates
    ), patch.object(
        backfill_mod,
        "process_candidate",
        new=AsyncMock(
            return_value=RowOutcome(
                bill_id=101,
                bill_public_id=candidates[0].bill_public_id,
                status="in_review",
                current_bill_number="B-CURRENT",
                proposed_memo="SECRET_MEMO_TEXT",
                outcome="would_write",
            )
        ),
    ), patch.object(backfill_mod, "apply_memo_update") as mock_apply, patch.object(
        backfill_mod, "assert_cli_system_admin"
    ), patch(
        "sys.argv",
        ["backfill_list_drop_bill_memo.py", "--out", str(csv_path)],
    ):
        assert main() == 0

    mock_apply.assert_not_called()
    with open(csv_path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 1
    assert rows[0]["outcome"] == "would_write"
    assert rows[0]["proposed_memo"] == "SECRET_MEMO_TEXT"


def test_dry_run_stdout_has_no_memo_text(capsys, tmp_path):
    csv_path = tmp_path / "plan.csv"
    with patch.object(backfill_mod, "assert_cli_system_admin"), patch.object(
        backfill_mod, "get_connection"
    ) as mock_gc, patch.object(
        backfill_mod, "list_candidates", return_value=[_candidate()]
    ), patch.object(
        backfill_mod,
        "process_candidate",
        new=AsyncMock(
            return_value=RowOutcome(
                bill_id=101,
                bill_public_id="11111111-1111-1111-1111-111111111111",
                status="in_review",
                current_bill_number="B-CURRENT",
                proposed_memo="ULTRA_SECRET_MEMO_BODY",
                outcome="would_write",
            )
        ),
    ), patch(
        "sys.argv",
        ["prog", "--out", str(csv_path)],
    ):
        mock_gc.return_value.__enter__.return_value = MagicMock()
        main()
    out = capsys.readouterr().out
    assert "ULTRA_SECRET_MEMO_BODY" not in out
    assert "candidates=1" in out
    assert "would_write=1" in out


def test_apply_from_writes_guarded_update(tmp_path):
    reviewed = tmp_path / "reviewed.csv"
    out_csv = tmp_path / "result.csv"
    memo = "APPLY_MEMO_VALUE"
    candidate = _candidate()
    with open(reviewed, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(backfill_mod.CSV_COLUMNS)
        w.writerow(
            [
                candidate.bill_id,
                candidate.bill_public_id,
                candidate.status,
                candidate.bill_number,
                memo,
                "would_write",
            ]
        )

    conn = MagicMock()
    cursor = MagicMock()
    cursor.rowcount = 1
    conn.cursor.return_value = cursor
    conn_ctx = MagicMock()
    conn_ctx.__enter__.return_value = conn
    conn_ctx.__exit__.return_value = False

    with patch.object(backfill_mod, "assert_cli_system_admin"), patch.object(
        backfill_mod, "get_connection", return_value=conn_ctx
    ), patch.object(backfill_mod, "process_candidate", new=AsyncMock()) as mock_proc, patch.object(
        backfill_mod, "run_bill_header_extraction", new=AsyncMock()
    ) as mock_cascade, patch.object(
        backfill_mod, "_cached_masked_text_for_bill_drop"
    ) as mock_cache, patch(
        "sys.argv",
        ["prog", "--out", str(out_csv), "--apply-from", str(reviewed)],
    ):
        assert main() == 0

    mock_proc.assert_not_called()
    mock_cascade.assert_not_called()
    mock_cache.assert_not_called()
    update_calls = [
        c for c in cursor.execute.call_args_list if "UPDATE" in c.args[0].upper()
    ]
    assert len(update_calls) == 1
    assert update_calls[0].args[1] == (
        memo, candidate.bill_id, candidate.bill_public_id, candidate.status
    )
    conn.commit.assert_called()
    with open(out_csv, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["outcome"] == "written"
    assert rows[0]["proposed_memo"] == memo


def test_apply_from_skipped_changed_when_row_changed(tmp_path):
    reviewed = tmp_path / "reviewed.csv"
    out_csv = tmp_path / "result.csv"
    with open(reviewed, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(backfill_mod.CSV_COLUMNS)
        w.writerow([101, "11111111-1111-1111-1111-111111111111", "in_review", "N", "MEMO", "would_write"])

    conn = MagicMock()
    cur = MagicMock()
    cur.rowcount = 0
    conn.cursor.return_value = cur
    conn_ctx = MagicMock()
    conn_ctx.__enter__.return_value = conn
    conn_ctx.__exit__.return_value = False

    with patch.object(backfill_mod, "get_connection", return_value=conn_ctx):
        outcomes, code = apply_from_reviewed_csv(
            reviewed_path=str(reviewed), out_path=str(out_csv)
        )
    assert code == 0
    assert outcomes[0].outcome == "skipped_changed"


def test_apply_from_rejects_missing_csv_columns(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("bill_id,status\n1,in_review\n", encoding="utf-8")
    out_csv = tmp_path / "out.csv"
    _, code = apply_from_reviewed_csv(reviewed_path=str(bad), out_path=str(out_csv))
    assert code == 2
    assert _validate_review_csv_header(["bill_id", "status"]).startswith("missing columns")


def test_apply_memo_update_helper():
    conn = MagicMock()
    cur = MagicMock()
    cur.rowcount = 0
    conn.cursor.return_value = cur
    n = apply_memo_update(conn, bill_id=9, bill_public_id="pub-9", status="in_review", memo="m")
    assert n == 0
    cur.execute.assert_called_once()


def test_daily_cap_stops_run_exit_nonzero(tmp_path):
    csv_path = tmp_path / "cap.csv"
    c1, c2 = _candidate(bill_id=1), _candidate(bill_id=2)

    async def proc(c):
        if c.bill_id == 1:
            return RowOutcome(
                bill_id=1,
                bill_public_id=c.bill_public_id,
                status=c.status,
                current_bill_number=c.bill_number,
                proposed_memo=None,
                outcome="stopped_daily_cap",
                stop_daily_cap=True,
            )
        return RowOutcome(
            bill_id=2,
            bill_public_id=c.bill_public_id,
            status=c.status,
            current_bill_number=c.bill_number,
            proposed_memo="never",
            outcome="would_write",
        )

    with patch.object(backfill_mod, "process_candidate", side_effect=proc):
        outcomes, hit = asyncio.run(run_backfill([c1, c2], out_path=str(csv_path)))
    assert hit is True
    assert len(outcomes) == 1
    with open(csv_path, newline="", encoding="utf-8") as fh:
        assert len(list(csv.DictReader(fh))) == 1


def test_run_backfill_flushes_after_each_row(tmp_path):
    csv_path = tmp_path / "stream.csv"
    c1, c2 = _candidate(bill_id=1), _candidate(bill_id=2)
    row = RowOutcome(
        bill_id=1,
        bill_public_id=c1.bill_public_id,
        status=c1.status,
        current_bill_number=c1.bill_number,
        proposed_memo="m",
        outcome="would_write",
    )

    mock_fh = MagicMock()
    mock_fh.__enter__ = MagicMock(return_value=mock_fh)
    mock_fh.__exit__ = MagicMock(return_value=False)

    with patch.object(backfill_mod, "process_candidate", new=AsyncMock(return_value=row)), patch(
        "builtins.open", return_value=mock_fh
    ):
        asyncio.run(run_backfill([c1, c2], out_path=str(csv_path)))

    assert mock_fh.flush.call_count >= 2


def test_list_candidates_maps_rows():
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur
    cur.fetchall.return_value = [
        SimpleNamespace(
            BillId=7,
            BillPublicId="22222222-2222-2222-2222-222222222222",
            BillStatus="in_review",
            BillNumber="N-1",
            EnteredByUserId=17,
            PredictionAttachmentId=900,
        )
    ]
    rows = list_candidates(conn, statuses=["in_review"], limit=5)
    assert len(rows) == 1
    assert rows[0].bill_id == 7
    assert rows[0].prediction_attachment_id == 900
    sql, params = _candidate_sql(statuses=["in_review"], bill_ids=None, limit=5)
    cur.execute.assert_called_once_with(sql, params)


def test_run_bill_header_extraction_called_with_bill_text_only():
    """process_candidate must mirror read_attachment_service header kwargs shape."""
    sig = inspect.signature(backfill_mod.run_bill_header_extraction)
    assert "transport_for" in sig.parameters

    att = SimpleNamespace(id=55, blob_url="http://x")
    cascade = _header_cascade(_ok_header_result())

    async def capture_header(payload, **kwargs):
        assert payload == {"bill_text": "masked-in"}
        assert kwargs == {}
        return cascade

    with _attachment_pipeline(
        att=att, is_taxpayer=False, linked=True, masked_text="masked-in"
    ), patch.object(
        backfill_mod, "run_bill_header_extraction", side_effect=capture_header
    ), patch.object(
        backfill_mod, "_resolve_entered_by_for_memo", new=AsyncMock(return_value="")
    ), patch(
        "entities.bill.business.read_attachment_service.resolve_vendor_match_from_name",
        return_value=None,
    ):
        asyncio.run(process_candidate(_candidate()))
