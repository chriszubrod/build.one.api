"""U-676r: live bill text-tier narrowed SCC candidates (fakes only)."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from entities.coding_suggestion.business import bill_text_suggestion_service as svc
from entities.coding_suggestion.business.bill_suggestion_service import BillSuggestion
from entities.coding_suggestion.persistence.bill_text_suggestion_repo import BillTextCodingClaim
from entities.attachment.business.extraction_service import REDACTION_VERSION
from intelligence.cascade.claude_budget import _invalid_setting_warnings
from intelligence.cascade.core import CascadeResult, Rung


FULL_CATALOG_NUMBERS = sorted(
    [
        "43.00",
        "43.10",
        "43.20",
        "65.10",
        "65.20",
        "99.01",
        "12.00",
        "12.10",
    ]
)
NARROWING_HISTORY = ["43.00", "65.10", "99.01"]
NARROWED_CATALOG_NUMBERS = sorted(
    [
        "43.00",
        "43.10",
        "43.20",
        "65.10",
        "65.20",
        "99.01",
    ]
)


@pytest.fixture(autouse=True)
def _claude_budget_not_stopped():
    with patch.object(
        svc.claude_budget,
        "is_claude_day_stopped_async",
        new=AsyncMock(return_value=False),
    ):
        yield


def _blob(content: str) -> bytes:
    return json.dumps({"content": content, "redaction_version": REDACTION_VERSION}).encode(
        "utf-8"
    )


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


def _multi_scc_catalog(**kwargs) -> svc.CatalogSnapshot:
    sccs = [
        SimpleNamespace(
            scc_id=i + 1,
            number=num,
            name=f"N{num}",
            created=datetime(2020, 1, 1),
        )
        for i, num in enumerate(FULL_CATALOG_NUMBERS)
    ]
    scc_by_id = {s.scc_id: s for s in sccs}
    defaults = dict(
        scc_by_id=scc_by_id,
        scc_number_to_id={s.number: s.scc_id for s in sccs},
        ambiguous_scc_numbers=frozenset(),
        proj_abbrev_to_id={"P1": 100},
        ambiguous_project_abbrevs=frozenset(),
        candidate_projects=[{"abbreviation": "P1", "name": "Proj"}],
        vendor_name="Acme",
    )
    defaults.update(kwargs)
    return svc.CatalogSnapshot(**defaults)


def _settings_mock(mock_settings, *, candidate_sccs: str = "narrowed"):
    mock_settings.return_value.bill_coding_text_enabled = True
    mock_settings.return_value.bill_coding_text_daily_call_cap = 200
    mock_settings.return_value.bill_coding_candidate_sccs = candidate_sccs
    mock_settings.return_value.foundry_api_key = "k"
    mock_settings.return_value.foundry_endpoint = "https://x"


def _accepted_cascade(**result_kwargs) -> CascadeResult:
    result = {
        "scc_number": "43.00",
        "project_abbreviation": None,
        "confidence": 0.9,
        "rationale": "ok",
    }
    result.update(result_kwargs)
    return CascadeResult(
        accepted=True,
        needs_human=False,
        result=result,
        confidence=0.9,
        winning_rung=Rung("foundry", "gpt-5.4-mini"),
        attempts=[],
    )


def _history_eligible_suggester() -> MagicMock:
    suggester = MagicMock()
    suggester.suggest.return_value = BillSuggestion(
        project_id=100,
        sub_cost_code_id=1,
        sub_cost_code_public_id=None,
        sub_cost_code_number="43.00",
        sub_cost_code_name="A",
        source="vendor_project_history",
        reason=None,
        confidence=Decimal("0.59"),
        raw_score=Decimal("0.59"),
        top_count=3,
        second_count=2,
        total_count=8,
    )
    return suggester


def _history_none_suggester() -> MagicMock:
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


def _vendor_wide_rows():
    return [
        SimpleNamespace(Number=n, Name="x", VoteCount=1)
        for n in NARROWING_HISTORY
    ]


def _run_tick(
    repo: MagicMock,
    *,
    candidate_sccs: str = "narrowed",
    suggester: MagicMock | None = None,
    chosen_project_id: int | None = 100,
):
    suggester = suggester or _history_eligible_suggester()
    storage = MagicMock()
    storage.download_file.return_value = (_blob("body"), {})
    with patch.object(svc.config, "Settings") as mock_settings:
        _settings_mock(mock_settings, candidate_sccs=candidate_sccs)
        with patch.object(svc, "run_bill_coding_suggestion", new_callable=AsyncMock) as mock_cascade:
            mock_cascade.return_value = _accepted_cascade()
            with patch.object(svc, "_read_vendor_name", return_value="Acme"):
                out = asyncio.run(
                    svc.run_bill_text_coding_tick(
                        repo=repo,
                        storage=storage,
                        suggester=suggester,
                        catalog_loader=lambda _as_of: _multi_scc_catalog(),
                    )
                )
    return mock_cascade, out


def _run_predict(
    repo: MagicMock,
    *,
    candidate_sccs: str = "narrowed",
    chosen_project_id: int | None = 100,
    track_to_thread: bool = False,
):
    catalog = _multi_scc_catalog()
    calls: list[tuple] = []

    async def _to_thread(fn, *args, **kwargs):
        if track_to_thread:
            calls.append((fn, args, kwargs))
        return fn(*args, **kwargs)

    with patch.object(svc.config, "Settings") as mock_settings:
        _settings_mock(mock_settings, candidate_sccs=candidate_sccs)
        mock_settings.return_value.bill_coding_text_daily_call_cap = 200
        with patch.object(svc, "run_bill_coding_suggestion", new_callable=AsyncMock) as mock_cascade:
            mock_cascade.return_value = _accepted_cascade()
            with patch.object(svc.asyncio, "to_thread", side_effect=_to_thread):
                asyncio.run(
                    svc.predict_text_coding(
                        bill_text="t",
                        vendor_id=7,
                        chosen_project_id=chosen_project_id,
                        as_of=datetime(2026, 5, 1),
                        exclude_bill_id=42,
                        text_repo=repo,
                        catalog=catalog,
                    )
                )
    return mock_cascade, catalog, calls


def test_build_runtime_payload_narrows_with_three_in_catalog_history():
    catalog = _multi_scc_catalog()
    built = svc.build_runtime_payload(
        bill_text="x",
        catalog=catalog,
        history_rows=[],
        chosen_project_id=100,
        as_of=datetime(2026, 5, 1),
        narrowing_history_scc_numbers=NARROWING_HISTORY,
    )
    numbers = [r["number"] for r in built.payload["candidate_sccs"]]
    assert numbers == NARROWED_CATALOG_NUMBERS
    assert len(numbers) < len(FULL_CATALOG_NUMBERS)
    assert built.candidate_scc_narrowed is True


def test_build_runtime_payload_full_when_narrowing_numbers_none():
    catalog = _multi_scc_catalog()
    built = svc.build_runtime_payload(
        bill_text="x",
        catalog=catalog,
        history_rows=[],
        chosen_project_id=100,
        as_of=datetime(2026, 5, 1),
        narrowing_history_scc_numbers=None,
    )
    numbers = [r["number"] for r in built.payload["candidate_sccs"]]
    assert numbers == FULL_CATALOG_NUMBERS
    assert built.candidate_scc_narrowed is False


def test_build_runtime_payload_full_when_fewer_than_three_history_codes():
    catalog = _multi_scc_catalog()
    built = svc.build_runtime_payload(
        bill_text="x",
        catalog=catalog,
        history_rows=[],
        chosen_project_id=100,
        as_of=datetime(2026, 5, 1),
        narrowing_history_scc_numbers=["43.00", "65.10"],
    )
    assert [r["number"] for r in built.payload["candidate_sccs"]] == FULL_CATALOG_NUMBERS
    assert built.candidate_scc_narrowed is False


def test_tick_narrowed_second_vendor_wide_read_and_narrowed_payload():
    project_history = [
        SimpleNamespace(Number="43.00", Name="A", VoteCount=5),
    ]
    repo = MagicMock()
    repo.count_calls_today.return_value = 0
    repo.claim_next.return_value = _claim()
    repo.read_vendor_scc_counts.side_effect = [project_history, _vendor_wide_rows()]
    mock_cascade, _out = _run_tick(repo, candidate_sccs="narrowed")
    assert repo.read_vendor_scc_counts.call_count == 2
    first = repo.read_vendor_scc_counts.call_args_list[0]
    second = repo.read_vendor_scc_counts.call_args_list[1]
    assert first.kwargs["project_id"] == 100
    assert first.kwargs.get("top", 5) == 5
    assert second.kwargs["project_id"] is None
    assert second.kwargs["top"] == svc.VENDOR_WIDE_SCC_HISTORY_TOP
    assert first.kwargs["as_of"] == second.kwargs["as_of"]
    assert first.kwargs["exclude_bill_id"] == second.kwargs["exclude_bill_id"] == 42
    payload = mock_cascade.call_args[0][0]
    assert payload["history_evidence"] == [
        {"scc_number": "43.00", "scc_name": "A", "count": 5}
    ]
    numbers = [r["number"] for r in payload["candidate_sccs"]]
    assert numbers == NARROWED_CATALOG_NUMBERS
    assert len(numbers) < len(FULL_CATALOG_NUMBERS)


@pytest.fixture
def _skip_post_cascade_db():
    with patch(
        "entities.coding_suggestion.business.bill_suggestion_service.bill_suggestion_from_text_cascade",
        return_value=None,
    ):
        yield


def test_predict_text_coding_narrowed_second_read_off_event_loop(_skip_post_cascade_db):
    project_history = [
        SimpleNamespace(Number="43.00", Name="A", VoteCount=2),
    ]
    repo = MagicMock()
    repo.count_calls_today.return_value = 0
    repo.read_vendor_scc_counts.side_effect = [project_history, _vendor_wide_rows()]
    mock_cascade, _catalog, calls = _run_predict(
        repo,
        candidate_sccs="narrowed",
        track_to_thread=True,
    )
    assert repo.read_vendor_scc_counts.call_count == 2
    assert any(fn is svc.load_narrowing_history_scc_numbers for fn, _a, _k in calls)
    wide = next(c for c in repo.read_vendor_scc_counts.call_args_list if c.kwargs.get("project_id") is None)
    assert wide.kwargs["top"] == svc.VENDOR_WIDE_SCC_HISTORY_TOP
    payload = mock_cascade.call_args[0][0]
    assert payload["history_evidence"] == [
        {"scc_number": "43.00", "scc_name": "A", "count": 2}
    ]
    numbers = [r["number"] for r in payload["candidate_sccs"]]
    assert numbers == NARROWED_CATALOG_NUMBERS
    assert len(numbers) < len(FULL_CATALOG_NUMBERS)


def test_tick_full_mode_single_read_full_candidates():
    repo = MagicMock()
    repo.count_calls_today.return_value = 0
    repo.claim_next.return_value = _claim(chosen_project_id=None)
    repo.read_vendor_scc_counts.return_value = []
    suggester = _history_none_suggester()
    storage = MagicMock()
    storage.download_file.return_value = (_blob("body"), {})
    with patch.object(svc.config, "Settings") as mock_settings:
        _settings_mock(mock_settings, candidate_sccs="full")
        with patch.object(svc, "run_bill_coding_suggestion", new_callable=AsyncMock) as mock_cascade:
            mock_cascade.return_value = _accepted_cascade(
                project_abbreviation="P1", scc_number="43.00"
            )
            with patch.object(svc, "_read_vendor_name", return_value="Acme"):
                asyncio.run(
                    svc.run_bill_text_coding_tick(
                        repo=repo,
                        storage=storage,
                        suggester=suggester,
                        catalog_loader=lambda _as_of: _multi_scc_catalog(),
                    )
                )
    repo.read_vendor_scc_counts.assert_called_once()
    numbers = [r["number"] for r in mock_cascade.call_args[0][0]["candidate_sccs"]]
    assert numbers == FULL_CATALOG_NUMBERS


def test_predict_text_coding_full_mode_single_read(_skip_post_cascade_db):
    repo = MagicMock()
    repo.count_calls_today.return_value = 0
    repo.read_vendor_scc_counts.return_value = []
    mock_cascade, _catalog, _calls = _run_predict(repo, candidate_sccs="full")
    repo.read_vendor_scc_counts.assert_called_once()
    numbers = [r["number"] for r in mock_cascade.call_args[0][0]["candidate_sccs"]]
    assert numbers == FULL_CATALOG_NUMBERS


def test_unknown_setting_uses_full_catalog_tick(caplog):
    _invalid_setting_warnings.discard(("bill_coding_candidate_sccs", "'bogus'"))
    caplog.set_level(logging.WARNING)
    repo = MagicMock()
    repo.count_calls_today.return_value = 0
    repo.claim_next.side_effect = [
        _claim(chosen_project_id=None),
        _claim(bill_id=43, attachment_id=10, chosen_project_id=None),
    ]
    repo.read_vendor_scc_counts.return_value = []
    repo.complete.return_value = SimpleNamespace()
    suggester = _history_none_suggester()
    storage = MagicMock()
    storage.download_file.return_value = (_blob("body"), {})
    with patch.object(svc.config, "Settings") as mock_settings:
        _settings_mock(mock_settings, candidate_sccs="bogus")
        with patch.object(svc, "run_bill_coding_suggestion", new_callable=AsyncMock) as mock_cascade:
            mock_cascade.return_value = _accepted_cascade(scc_number="43.00")
            with patch.object(svc, "_read_vendor_name", return_value="Acme"):
                asyncio.run(
                    svc.run_bill_text_coding_tick(
                        limit=2,
                        repo=repo,
                        storage=storage,
                        suggester=suggester,
                        catalog_loader=lambda _as_of: _multi_scc_catalog(),
                    )
                )
    assert repo.read_vendor_scc_counts.call_count == 2
    numbers = [r["number"] for r in mock_cascade.call_args[0][0]["candidate_sccs"]]
    assert numbers == FULL_CATALOG_NUMBERS
    setting_warnings = [
        r
        for r in caplog.records
        if r.name == "intelligence.cascade.claude_budget"
        and "bill_coding_candidate_sccs" in r.getMessage()
    ]
    assert len(setting_warnings) == 1


def test_vendor_wide_read_failure_tick_still_runs_cascade_full_list():
    repo = MagicMock()
    repo.count_calls_today.return_value = 0
    repo.claim_next.return_value = _claim(chosen_project_id=None)
    repo.read_vendor_scc_counts.side_effect = [
        [],
        RuntimeError("db down"),
    ]
    mock_cascade, out = _run_tick(
        repo,
        candidate_sccs="narrowed",
        suggester=_history_none_suggester(),
        chosen_project_id=None,
    )
    assert out["status"] == "done"
    mock_cascade.assert_called_once()
    numbers = [r["number"] for r in mock_cascade.call_args[0][0]["candidate_sccs"]]
    assert numbers == FULL_CATALOG_NUMBERS


def test_narrowing_extraction_failure_tick_uses_full_catalog():
    project_history: list = []
    vendor_wide = _vendor_wide_rows()
    repo = MagicMock()
    repo.count_calls_today.return_value = 0
    repo.claim_next.return_value = _claim(chosen_project_id=None)
    repo.read_vendor_scc_counts.side_effect = [project_history, vendor_wide]
    with patch.object(
        svc,
        "vendor_history_scc_numbers_from_scc_counts",
        side_effect=ValueError("bad row"),
    ):
        mock_cascade, out = _run_tick(
            repo,
            candidate_sccs="narrowed",
            suggester=_history_none_suggester(),
            chosen_project_id=None,
        )
    assert out["status"] == "done"
    mock_cascade.assert_called_once()
    numbers = [r["number"] for r in mock_cascade.call_args[0][0]["candidate_sccs"]]
    assert numbers == FULL_CATALOG_NUMBERS


def test_vendor_wide_read_failure_predict_still_runs_cascade(_skip_post_cascade_db):
    repo = MagicMock()
    repo.count_calls_today.return_value = 0
    repo.read_vendor_scc_counts.side_effect = [[], OSError("x")]
    mock_cascade, _, _calls = _run_predict(
        repo,
        candidate_sccs="narrowed",
        chosen_project_id=None,
    )
    mock_cascade.assert_called_once()
    numbers = [r["number"] for r in mock_cascade.call_args[0][0]["candidate_sccs"]]
    assert numbers == FULL_CATALOG_NUMBERS


class _ProjectReadBoom(Exception):
    pass


def test_predict_narrowed_project_read_failure_still_propagates(_skip_post_cascade_db):
    """U-676r P3: running the reads together must not swallow a failure of the
    project-scoped history read (only the narrowing read falls back to full)."""
    repo = MagicMock()
    repo.count_calls_today.return_value = 0

    def _read(vendor_id, *, project_id=None, **_kw):
        if project_id is not None:
            raise _ProjectReadBoom("project-scoped read failed")
        return _vendor_wide_rows()

    repo.read_vendor_scc_counts.side_effect = _read
    with pytest.raises(_ProjectReadBoom):
        _run_predict(repo, candidate_sccs="narrowed", chosen_project_id=100)
