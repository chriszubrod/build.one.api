"""U-654b: bill text coding tick (pure, mocked)."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from entities.coding_suggestion.business import bill_text_suggestion_service as svc
from entities.coding_suggestion.business.bill_suggestion_service import BillSuggestion
from entities.coding_suggestion.persistence.bill_text_suggestion_repo import BillTextCodingClaim
from entities.attachment.business.extraction_service import REDACTION_VERSION
from intelligence.cascade.core import CascadeResult, Rung


def _blob(content: str) -> bytes:
    """An extraction blob as the current (TIN-masking) writer stamps it."""
    return json.dumps({"content": content, "redaction_version": REDACTION_VERSION}).encode("utf-8")


def _claim(**kwargs) -> BillTextCodingClaim:
    defaults = dict(
        bill_id=42,
        attachment_id=9,
        extracted_text_blob_url="https://blob/x.json",
        extracted_datetime=datetime(2026, 5, 1, 12, 0, 0),
        vendor_id=7,
        chosen_project_id=100,
        bill_date=None,
        created_datetime=datetime(2026, 5, 1, 10, 0, 0),
    )
    defaults.update(kwargs)
    return BillTextCodingClaim(**defaults)


def _catalog(**kwargs) -> svc.CatalogSnapshot:
    scc = SimpleNamespace(
        scc_id=1, number="03.01", name="Framing", created=datetime(2020, 1, 1)
    )
    defaults = dict(
        scc_by_id={1: scc},
        scc_number_to_id={"03.01": 1},
        ambiguous_scc_numbers=frozenset(),
        proj_abbrev_to_id={"P1": 100},
        ambiguous_project_abbrevs=frozenset(),
        candidate_projects=[{"abbreviation": "P1", "name": "Proj"}],
        vendor_name="Acme",
    )
    defaults.update(kwargs)
    return svc.CatalogSnapshot(**defaults)


def test_claim_next_parses_varchar_millisecond_datetimes():
    """Regression A: sproc-style strings must become datetime for catalog compare."""
    from types import SimpleNamespace
    from entities.coding_suggestion.persistence.bill_text_suggestion_repo import (
        BillTextCodingSuggestionRepository,
    )

    row = SimpleNamespace(
        BillId=1,
        AttachmentId=2,
        ExtractedTextBlobUrl="https://blob/x",
        ExtractedDatetime="2026-05-01 10:00:00.123",
        VendorId=3,
        ChosenProjectId=None,
        BillDate="2026-05-01 09:00:00.456",
        CreatedDatetime="2026-05-01 08:00:00.789",
    )
    repo = BillTextCodingSuggestionRepository()
    with patch(
        "entities.coding_suggestion.persistence.bill_text_suggestion_repo.get_connection"
    ) as gc:
        conn = MagicMock()
        gc.return_value.__enter__.return_value = conn
        conn.cursor.return_value.fetchone.return_value = row
        claim = repo.claim_next()
    assert claim is not None
    assert claim.extracted_datetime.microsecond == 123000
    assert claim.created_datetime.microsecond == 789000
    scc = SimpleNamespace(
        scc_id=1, number="03.01", name="Framing", created=datetime(2020, 1, 1)
    )
    catalog = _catalog(scc_by_id={1: scc})
    payload, _mode = svc.build_runtime_payload(
        claim=claim,
        bill_text="x",
        catalog=catalog,
        history_rows=[],
    )
    assert payload["candidate_sccs"]


def test_empty_queue_tick_idle():
    repo = MagicMock()
    repo.count_calls_today.return_value = 0
    repo.claim_next.return_value = None
    with patch.object(svc.config, "Settings") as mock_settings:
        mock_settings.return_value.bill_coding_text_enabled = True
        mock_settings.return_value.bill_coding_text_daily_call_cap = 200
        mock_settings.return_value.foundry_api_key = "k"
        mock_settings.return_value.foundry_endpoint = "https://x"
        out = asyncio.run(svc.run_bill_text_coding_tick(repo=repo))
    assert out["status"] == "idle"


def test_disabled_flag_no_claim():
    repo = MagicMock()
    with patch.object(svc.config, "Settings") as mock_settings:
        mock_settings.return_value.bill_coding_text_enabled = False
        mock_settings.return_value.bill_coding_text_daily_call_cap = 200
        mock_settings.return_value.foundry_api_key = "k"
        mock_settings.return_value.foundry_endpoint = "https://x"
        out = asyncio.run(svc.run_bill_text_coding_tick(repo=repo))
    assert out["status"] == "disabled"
    repo.claim_next.assert_not_called()


def test_missing_foundry_no_claim():
    repo = MagicMock()
    with patch.object(svc.config, "Settings") as mock_settings:
        mock_settings.return_value.bill_coding_text_enabled = True
        mock_settings.return_value.bill_coding_text_daily_call_cap = 200
        mock_settings.return_value.foundry_api_key = None
        mock_settings.return_value.foundry_endpoint = None
        out = asyncio.run(svc.run_bill_text_coding_tick(repo=repo))
    assert out["status"] == "disabled"
    repo.claim_next.assert_not_called()


def test_cap_reached_no_claim():
    repo = MagicMock()
    repo.count_calls_today.return_value = 200
    with patch.object(svc.config, "Settings") as mock_settings:
        mock_settings.return_value.bill_coding_text_enabled = True
        mock_settings.return_value.bill_coding_text_daily_call_cap = 200
        mock_settings.return_value.foundry_api_key = "k"
        mock_settings.return_value.foundry_endpoint = "https://x"
        out = asyncio.run(svc.run_bill_text_coding_tick(repo=repo))
    assert out["status"] == "cap_reached"
    repo.claim_next.assert_not_called()


def test_strong_history_skipped_no_cascade():
    repo = MagicMock()
    repo.count_calls_today.return_value = 0
    repo.claim_next.return_value = _claim()
    suggester = MagicMock()
    suggester.suggest.return_value = BillSuggestion(
        project_id=100,
        sub_cost_code_id=1,
        sub_cost_code_public_id=None,
        sub_cost_code_number="03.01",
        sub_cost_code_name="Framing",
        source="vendor_project_history",
        reason=None,
        confidence=Decimal("0.70"),
        raw_score=Decimal("0.70"),
        top_count=5,
        second_count=1,
        total_count=5,
    )
    with patch.object(svc.config, "Settings") as mock_settings:
        mock_settings.return_value.bill_coding_text_enabled = True
        mock_settings.return_value.bill_coding_text_daily_call_cap = 200
        mock_settings.return_value.foundry_api_key = "k"
        mock_settings.return_value.foundry_endpoint = "https://x"
        with patch.object(svc, "run_bill_coding_suggestion", new_callable=AsyncMock) as mock_cascade:
            out = asyncio.run(
                svc.run_bill_text_coding_tick(repo=repo, suggester=suggester)
            )
    mock_cascade.assert_not_called()
    assert out["status"] == "skipped"
    assert out["skipped_reason"] == "history_strong"
    repo.complete.assert_called_once()
    assert repo.complete.call_args.kwargs["status"] == "skipped"


def test_cold_start_invokes_cascade_with_history_evidence():
    repo = MagicMock()
    repo.count_calls_today.return_value = 0
    repo.claim_next.return_value = _claim(chosen_project_id=None)
    repo.read_vendor_scc_counts.return_value = [
        SimpleNamespace(Number="03.01", Name="Framing", VoteCount=2),
    ]
    suggester = MagicMock()
    suggester.suggest.return_value = BillSuggestion(
        project_id=None,
        sub_cost_code_id=None,
        sub_cost_code_public_id=None,
        sub_cost_code_number=None,
        sub_cost_code_name=None,
        source="none",
        reason=None,
        confidence=None,
        raw_score=None,
        top_count=None,
        second_count=None,
        total_count=None,
    )
    storage = MagicMock()
    storage.download_file.return_value = (
        _blob("invoice body"),
        {},
    )
    accepted = CascadeResult(
        accepted=True,
        needs_human=False,
        result={
            "scc_number": "03.01",
            "project_abbreviation": "P1",
            "confidence": 0.9,
            "rationale": "ok",
        },
        confidence=0.9,
        winning_rung=Rung("foundry", "gpt-5.4-mini"),
        attempts=[],
    )
    with patch.object(svc.config, "Settings") as mock_settings:
        mock_settings.return_value.bill_coding_text_enabled = True
        mock_settings.return_value.bill_coding_text_daily_call_cap = 200
        mock_settings.return_value.foundry_api_key = "k"
        mock_settings.return_value.foundry_endpoint = "https://x"
        with patch.object(svc, "run_bill_coding_suggestion", new_callable=AsyncMock) as mock_cascade:
            mock_cascade.return_value = accepted
            with patch.object(svc, "_read_vendor_name", return_value="Acme"):
                out = asyncio.run(
                    svc.run_bill_text_coding_tick(
                        repo=repo,
                        storage=storage,
                        suggester=suggester,
                        catalog_loader=lambda _as_of: _catalog(),
                    )
                )
    mock_cascade.assert_called_once()
    payload = mock_cascade.call_args[0][0]
    assert payload["history_evidence"] == [
        {"scc_number": "03.01", "scc_name": "Framing", "count": 2}
    ]
    assert payload["candidate_projects"] is not None
    assert out["status"] == "done"


def test_weak_history_raw_059_calls_cascade():
    repo = MagicMock()
    repo.count_calls_today.return_value = 0
    repo.claim_next.return_value = _claim()
    repo.read_vendor_scc_counts.return_value = []
    suggester = MagicMock()
    suggester.suggest.return_value = BillSuggestion(
        project_id=100,
        sub_cost_code_id=1,
        sub_cost_code_public_id=None,
        sub_cost_code_number="03.01",
        sub_cost_code_name="Framing",
        source="vendor_project_history",
        reason=None,
        confidence=Decimal("0.59"),
        raw_score=Decimal("0.59"),
        top_count=3,
        second_count=2,
        total_count=8,
    )
    storage = MagicMock()
    storage.download_file.return_value = (_blob("x"), {})
    cascade = CascadeResult(
        accepted=True,
        needs_human=False,
        result={"scc_number": "03.01", "project_abbreviation": None, "confidence": 0.91},
        confidence=0.91,
        winning_rung=Rung("foundry", "m"),
        attempts=[],
    )
    with patch.object(svc.config, "Settings") as mock_settings:
        mock_settings.return_value.bill_coding_text_enabled = True
        mock_settings.return_value.bill_coding_text_daily_call_cap = 200
        mock_settings.return_value.foundry_api_key = "k"
        mock_settings.return_value.foundry_endpoint = "https://x"
        with patch.object(svc, "run_bill_coding_suggestion", new_callable=AsyncMock) as mock_cascade:
            mock_cascade.return_value = cascade
            with patch.object(svc, "_read_vendor_name", return_value="V"):
                out = asyncio.run(
                    svc.run_bill_text_coding_tick(
                        repo=repo, storage=storage, suggester=suggester, catalog_loader=lambda _as_of: _catalog()
                    )
                )
    mock_cascade.assert_called_once()
    assert out["status"] == "done"


def test_not_accepted_marks_failed():
    repo = MagicMock()
    repo.count_calls_today.return_value = 0
    repo.claim_next.return_value = _claim(chosen_project_id=None)
    suggester = MagicMock()
    suggester.suggest.return_value = BillSuggestion(
        project_id=None,
        sub_cost_code_id=None,
        sub_cost_code_public_id=None,
        sub_cost_code_number=None,
        sub_cost_code_name=None,
        source="none",
        reason=None,
        confidence=None,
        raw_score=None,
        top_count=None,
        second_count=None,
        total_count=None,
    )
    storage = MagicMock()
    storage.download_file.return_value = (_blob("x"), {})
    cascade = CascadeResult(
        accepted=False,
        needs_human=True,
        result=None,
        confidence=None,
        winning_rung=None,
        attempts=[],
    )
    with patch.object(svc.config, "Settings") as mock_settings:
        mock_settings.return_value.bill_coding_text_enabled = True
        mock_settings.return_value.bill_coding_text_daily_call_cap = 200
        mock_settings.return_value.foundry_api_key = "k"
        mock_settings.return_value.foundry_endpoint = "https://x"
        with patch.object(svc, "run_bill_coding_suggestion", new_callable=AsyncMock) as mock_cascade:
            mock_cascade.return_value = cascade
            with patch.object(svc, "_read_vendor_name", return_value="V"):
                out = asyncio.run(
                    svc.run_bill_text_coding_tick(
                        repo=repo, storage=storage, suggester=suggester, catalog_loader=lambda _as_of: _catalog()
                    )
                )
    assert out["status"] == "failed"
    assert repo.complete.call_args.kwargs["failure_reason"] == "not_accepted"


def test_catalog_as_of_uses_created_datetime_not_bill_date():
    """E1: SCC catalog cutoff follows CreatedDatetime (backtest parity)."""
    claim = _claim(
        bill_date=datetime(2020, 1, 1),
        created_datetime=datetime(2026, 6, 1, 12, 0, 0),
    )
    scc_old = SimpleNamespace(
        scc_id=1, number="03.01", name="Old", created=datetime(2025, 1, 1)
    )
    scc_new = SimpleNamespace(
        scc_id=2, number="04.01", name="New", created=datetime(2026, 7, 1)
    )
    catalog = _catalog(
        scc_by_id={1: scc_old, 2: scc_new},
        scc_number_to_id={"03.01": 1, "04.01": 2},
    )
    payload, _ = svc.build_runtime_payload(
        claim=claim, bill_text="x", catalog=catalog, history_rows=[]
    )
    numbers = {row["number"] for row in payload["candidate_sccs"]}
    assert "03.01" in numbers
    assert "04.01" not in numbers


def test_bill_text_whole_blob_utf8_like_backtest():
    """E3: use full blob utf-8 decode, not JSON content field."""
    raw = '{"content": "ignored-inner"}\nFULL BLOB TEXT'
    assert svc.decode_bill_text_from_blob_bytes(raw.encode("utf-8")) == raw


def test_strong_history_with_no_project_still_runs_cascade():
    """I: no chosen project ⇒ eligible even when history is strong."""
    repo = MagicMock()
    repo.count_calls_today.return_value = 0
    repo.claim_next.return_value = _claim(chosen_project_id=None)
    repo.read_vendor_scc_counts.return_value = []
    suggester = MagicMock()
    suggester.suggest.return_value = BillSuggestion(
        project_id=10,
        sub_cost_code_id=1,
        sub_cost_code_public_id=None,
        sub_cost_code_number="03.01",
        sub_cost_code_name="Framing",
        source="vendor_project_history",
        reason=None,
        confidence=Decimal("0.90"),
        raw_score=Decimal("0.90"),
        top_count=5,
        second_count=1,
        total_count=5,
    )
    storage = MagicMock()
    storage.download_file.return_value = (_blob("invoice plain text"), {})
    cascade = CascadeResult(
        accepted=True,
        needs_human=False,
        result={"scc_number": "03.01", "project_abbreviation": "P1", "confidence": 0.9},
        confidence=0.9,
        winning_rung=Rung("foundry", "gpt-5.4-mini"),
        attempts=[],
    )
    with patch.object(svc.config, "Settings") as mock_settings:
        mock_settings.return_value.bill_coding_text_enabled = True
        mock_settings.return_value.bill_coding_text_daily_call_cap = 200
        mock_settings.return_value.foundry_api_key = "k"
        mock_settings.return_value.foundry_endpoint = "https://x"
        with patch.object(svc, "run_bill_coding_suggestion", new_callable=AsyncMock) as mock_cascade:
            mock_cascade.return_value = cascade
            with patch.object(svc, "_read_vendor_name", return_value="V"):
                out = asyncio.run(
                    svc.run_bill_text_coding_tick(
                        repo=repo,
                        storage=storage,
                        suggester=suggester,
                        catalog_loader=lambda _as_of: _catalog(),
                    )
                )
    mock_cascade.assert_called_once()
    assert out["status"] == "done"
    assert repo.complete.call_args.kwargs.get("project_id") is not None


def test_mode_a_when_project_chosen():
    repo = MagicMock()
    repo.count_calls_today.return_value = 0
    repo.claim_next.return_value = _claim(chosen_project_id=100)
    repo.read_vendor_scc_counts.return_value = []
    suggester = MagicMock()
    suggester.suggest.return_value = BillSuggestion(
        project_id=None,
        sub_cost_code_id=None,
        sub_cost_code_public_id=None,
        sub_cost_code_number=None,
        sub_cost_code_name=None,
        source="none",
        reason=None,
        confidence=None,
        raw_score=None,
        top_count=None,
        second_count=None,
        total_count=None,
    )
    storage = MagicMock()
    storage.download_file.return_value = (_blob("text"), {})
    cascade = CascadeResult(
        accepted=True,
        needs_human=False,
        result={"scc_number": "03.01", "confidence": 0.9},
        confidence=0.9,
        winning_rung=Rung("foundry", "m"),
        attempts=[],
    )
    with patch.object(svc.config, "Settings") as mock_settings:
        mock_settings.return_value.bill_coding_text_enabled = True
        mock_settings.return_value.bill_coding_text_daily_call_cap = 200
        mock_settings.return_value.foundry_api_key = "k"
        mock_settings.return_value.foundry_endpoint = "https://x"
        with patch.object(svc, "run_bill_coding_suggestion", new_callable=AsyncMock) as mock_cascade:
            mock_cascade.return_value = cascade
            with patch.object(svc, "_read_vendor_name", return_value="V"):
                asyncio.run(
                    svc.run_bill_text_coding_tick(
                        repo=repo,
                        storage=storage,
                        suggester=suggester,
                        catalog_loader=lambda _as_of: _catalog(),
                    )
                )
    payload = mock_cascade.call_args[0][0]
    assert payload["candidate_projects"] is None
    assert repo.complete.call_args.kwargs["mode"] == "A"
    assert repo.complete.call_args.kwargs.get("project_id") is None


def test_cold_start_with_chosen_project_mode_a():
    repo = MagicMock()
    repo.count_calls_today.return_value = 0
    repo.claim_next.return_value = _claim(chosen_project_id=100)
    repo.read_vendor_scc_counts.return_value = []
    suggester = MagicMock()
    suggester.suggest.return_value = BillSuggestion(
        project_id=None,
        sub_cost_code_id=None,
        sub_cost_code_public_id=None,
        sub_cost_code_number=None,
        sub_cost_code_name=None,
        source="none",
        reason=None,
        confidence=None,
        raw_score=None,
        top_count=None,
        second_count=None,
        total_count=None,
    )
    storage = MagicMock()
    storage.download_file.return_value = (_blob("body"), {})
    with patch.object(svc.config, "Settings") as mock_settings:
        mock_settings.return_value.bill_coding_text_enabled = True
        mock_settings.return_value.bill_coding_text_daily_call_cap = 200
        mock_settings.return_value.foundry_api_key = "k"
        mock_settings.return_value.foundry_endpoint = "https://x"
        with patch.object(svc, "run_bill_coding_suggestion", new_callable=AsyncMock) as mock_cascade:
            mock_cascade.return_value = CascadeResult(
                accepted=True,
                needs_human=False,
                result={"scc_number": "03.01", "confidence": 0.5},
                confidence=0.5,
                winning_rung=Rung("foundry", "m"),
                attempts=[],
            )
            with patch.object(svc, "_read_vendor_name", return_value="V"):
                out = asyncio.run(
                    svc.run_bill_text_coding_tick(
                        repo=repo,
                        storage=storage,
                        suggester=suggester,
                        catalog_loader=lambda _as_of: _catalog(),
                    )
                )
    assert out["status"] == "done"
    mock_cascade.assert_called_once()


def test_cap_zero_never_claims():
    repo = MagicMock()
    with patch.object(svc.config, "Settings") as mock_settings:
        mock_settings.return_value.bill_coding_text_enabled = True
        mock_settings.return_value.bill_coding_text_daily_call_cap = 0
        mock_settings.return_value.foundry_api_key = "k"
        mock_settings.return_value.foundry_endpoint = "https://x"
        out = asyncio.run(svc.run_bill_text_coding_tick(repo=repo))
    assert out["status"] == "cap_reached"
    repo.claim_next.assert_not_called()


def test_limit_processes_multiple_claims():
    repo = MagicMock()
    repo.count_calls_today.return_value = 0
    claims = [_claim(bill_id=1), _claim(bill_id=2)]
    repo.claim_next.side_effect = claims + [None]
    suggester = MagicMock()
    suggester.suggest.return_value = BillSuggestion(
        project_id=None,
        sub_cost_code_id=None,
        sub_cost_code_public_id=None,
        sub_cost_code_number=None,
        sub_cost_code_name=None,
        source="none",
        reason=None,
        confidence=None,
        raw_score=None,
        top_count=None,
        second_count=None,
        total_count=None,
    )
    storage = MagicMock()
    storage.download_file.return_value = (_blob("x"), {})
    with patch.object(svc.config, "Settings") as mock_settings:
        mock_settings.return_value.bill_coding_text_enabled = True
        mock_settings.return_value.bill_coding_text_daily_call_cap = 200
        mock_settings.return_value.foundry_api_key = "k"
        mock_settings.return_value.foundry_endpoint = "https://x"
        with patch.object(svc, "run_bill_coding_suggestion", new_callable=AsyncMock) as mock_cascade:
            mock_cascade.return_value = CascadeResult(
                accepted=False,
                needs_human=True,
                result=None,
                confidence=None,
                winning_rung=None,
                attempts=[],
            )
            with patch.object(svc, "_read_vendor_name", return_value="V"):
                out = asyncio.run(
                    svc.run_bill_text_coding_tick(
                        limit=2,
                        repo=repo,
                        storage=storage,
                        suggester=suggester,
                        catalog_loader=lambda _as_of: _catalog(),
                    )
                )
    assert repo.claim_next.call_count == 2
    assert out["bill_id"] == 2


def test_exception_marks_failed_no_raise():
    repo = MagicMock()
    repo.count_calls_today.return_value = 0
    repo.claim_next.return_value = _claim()
    suggester = MagicMock()
    suggester.suggest.side_effect = RuntimeError("boom")
    with patch.object(svc.config, "Settings") as mock_settings:
        mock_settings.return_value.bill_coding_text_enabled = True
        mock_settings.return_value.bill_coding_text_daily_call_cap = 200
        mock_settings.return_value.foundry_api_key = "k"
        mock_settings.return_value.foundry_endpoint = "https://x"
        out = asyncio.run(svc.run_bill_text_coding_tick(repo=repo, suggester=suggester))
    assert out["status"] == "failed"
    assert out["error"] == "RuntimeError"
    repo.complete.assert_called_once()
    assert repo.complete.call_args.kwargs["status"] == "failed"
    assert repo.complete.call_args.kwargs["failure_reason"] == "error"


def _cold_suggester() -> MagicMock:
    suggester = MagicMock()
    suggester.suggest.return_value = BillSuggestion(
        project_id=None,
        sub_cost_code_id=None,
        sub_cost_code_public_id=None,
        sub_cost_code_number=None,
        sub_cost_code_name=None,
        source="none",
        reason=None,
        confidence=None,
        raw_score=None,
        top_count=None,
        second_count=None,
        total_count=None,
    )
    return suggester


def _run_tick_with(blob: bytes, cascade_result, **cascade_kwargs):
    repo = MagicMock()
    repo.count_calls_today.return_value = 0
    repo.claim_next.return_value = _claim(chosen_project_id=None)
    repo.read_vendor_scc_counts.return_value = []
    storage = MagicMock()
    storage.download_file.return_value = (blob, {})
    with patch.object(svc.config, "Settings") as mock_settings:
        mock_settings.return_value.bill_coding_text_enabled = True
        mock_settings.return_value.bill_coding_text_daily_call_cap = 200
        mock_settings.return_value.foundry_api_key = "k"
        mock_settings.return_value.foundry_endpoint = "https://x"
        with patch.object(svc, "run_bill_coding_suggestion", new_callable=AsyncMock) as mock_cascade:
            mock_cascade.return_value = cascade_result
            mock_cascade.side_effect = cascade_kwargs.get("side_effect")
            with patch.object(svc, "_read_vendor_name", return_value="V"):
                out = asyncio.run(
                    svc.run_bill_text_coding_tick(
                        repo=repo,
                        storage=storage,
                        suggester=_cold_suggester(),
                        catalog_loader=lambda _as_of: _catalog(),
                    )
                )
    return out, repo, mock_cascade


def _accepted(raw_confidence, coerced_confidence) -> CascadeResult:
    return CascadeResult(
        accepted=True,
        needs_human=False,
        result={
            "scc_number": "03.01",
            "project_abbreviation": "P1",
            "confidence": raw_confidence,
            "rationale": "ok",
        },
        confidence=coerced_confidence,
        winning_rung=Rung("foundry", "gpt-5.4-mini"),
        attempts=[],
    )


def test_r3_blob_has_current_redaction():
    assert svc.blob_has_current_redaction(_blob("x").decode("utf-8"))
    assert not svc.blob_has_current_redaction(json.dumps({"content": "x"}))
    assert not svc.blob_has_current_redaction(
        json.dumps({"content": "x", "redaction_version": "u535g-v1"})
    )
    assert not svc.blob_has_current_redaction("plain text 12-3456789")
    assert not svc.blob_has_current_redaction(json.dumps(["x"]))


def test_r3_stale_redaction_blob_never_reaches_the_model():
    stale_blobs = [
        json.dumps({"content": "EIN 12-3456789"}).encode("utf-8"),
        json.dumps({"content": "EIN 12-3456789", "redaction_version": "u535g-v1"}).encode("utf-8"),
        b"EIN 12-3456789",
    ]
    for blob in stale_blobs:
        out, repo, mock_cascade = _run_tick_with(blob, _accepted(0.9, 0.9))
        mock_cascade.assert_not_called()
        assert out["status"] == "skipped"
        assert out["skipped_reason"] == svc.STALE_REDACTION
        kwargs = repo.complete.call_args.kwargs
        assert kwargs["status"] == "skipped"
        assert kwargs["failure_reason"] == svc.STALE_REDACTION
        assert kwargs["record_call"] is False


def test_r3_confidence_uses_coerced_value_not_raw_model_output():
    for raw in (85, True, "NaN"):
        out, repo, _ = _run_tick_with(_blob("x"), _accepted(raw, 1.0))
        assert out["status"] == "done", raw
        kwargs = repo.complete.call_args.kwargs
        assert kwargs["confidence"] == Decimal("1.0")
        assert kwargs["record_call"] is True


def test_r3_error_after_the_model_call_still_counts_toward_the_cap():
    with patch.object(svc, "resolve_text_prediction", side_effect=RuntimeError("boom")):
        out, repo, mock_cascade = _run_tick_with(_blob("x"), _accepted(0.9, 0.9))
    mock_cascade.assert_called_once()
    assert out["status"] == "failed"
    kwargs = repo.complete.call_args.kwargs
    assert kwargs["failure_reason"] == "error"
    assert kwargs["record_call"] is True


def test_r3_error_before_the_model_call_does_not_count():
    with patch.object(svc, "build_runtime_payload", side_effect=RuntimeError("boom")):
        out, repo, mock_cascade = _run_tick_with(_blob("x"), _accepted(0.9, 0.9))
    mock_cascade.assert_not_called()
    assert out["status"] == "failed"
    assert repo.complete.call_args.kwargs["record_call"] is False
