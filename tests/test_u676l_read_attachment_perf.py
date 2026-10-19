"""U-676l: read-attachment latency (timing, concurrency, dedupe, catalog cache)."""
from __future__ import annotations

import asyncio
import gc
import json
import logging
import threading
from datetime import datetime
import time
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from entities.attachment.persistence.repo import AttachmentLinkState
from entities.bill.business import read_attachment_service as ras
from entities.bill.business.read_attachment_service import AttachmentNotReadable
from entities.coding_suggestion.business.bill_suggestion_service import BillSuggestion
from entities.coding_suggestion.business.bill_text_suggestion_service import (
    CatalogSnapshot,
    configure_catalog_cache_for_tests,
    load_catalog_from_db,
)
from entities.vendor.business.vendor_name_resolver import VendorResolveMatch
from intelligence.cascade.core import CascadeResult, Rung

ATT_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
PROJ_UUID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
VENDOR_UUID = "cccccccc-cccc-cccc-cccc-cccccccccccc"




GOLDEN_STRONG_HISTORY = {
    "coding": {
        "budget_check": None,
        "budget_remaining": None,
        "confidence": "0.85",
        "model_version": "u653a-history-v1",
        "project_name": "Proj",
        "project_public_id": PROJ_UUID,
        "reason": "history",
        "source": "vendor_history",
        "sub_cost_code_name": "Framing",
        "sub_cost_code_number": "05.100",
        "sub_cost_code_public_id": "11111111-1111-1111-1111-000000000501",
        "text_status": "ready",
    },
    "fields": {
        "bill_date": {"confidence": "0.9000", "value": "2026-05-01"},
        "bill_number": {"confidence": "0.9000", "value": "INV-1"},
        "description": {"confidence": "0.9000", "value": "Materials"},
        "long_description": {"confidence": "0.9000", "value": "Materials"},
        "reference": {"confidence": None, "value": None},
        "total_amount": {"confidence": "0.9000", "value": "100.00"},
        "vendor": {
            "confidence": "0.8000",
            "extracted_name": "Acme Supply",
            "value": {"name": "Acme", "public_id": VENDOR_UUID},
        },
    },
    "message": None,
    "model": "claude-haiku-5-5",
    "status": "ok",
}

GOLDEN_WEAK_HISTORY = {
    "coding": {
        "budget_check": None,
        "budget_remaining": None,
        "confidence": "0.88",
        "model_version": "u653a-history-v1",
        "project_name": "Proj",
        "project_public_id": PROJ_UUID,
        "reason": "Suggested from the bill's document text",
        "source": "llm",
        "sub_cost_code_name": "From LLM",
        "sub_cost_code_number": "06.200",
        "sub_cost_code_public_id": "22222222-2222-2222-2222-000000000502",
        "text_status": "ready",
    },
    "fields": GOLDEN_STRONG_HISTORY["fields"],
    "message": None,
    "model": "claude-haiku-5-5",
    "status": "ok",
}

GOLDEN_CHOSEN_PROJECT = {
    "coding": {
        "budget_check": None,
        "budget_remaining": None,
        "confidence": "0.85",
        "model_version": "u653a-history-v1",
        "project_name": "Proj",
        "project_public_id": PROJ_UUID,
        "reason": "history",
        "source": "vendor_project_history",
        "sub_cost_code_name": "Framing",
        "sub_cost_code_number": "05.100",
        "sub_cost_code_public_id": "11111111-1111-1111-1111-000000000501",
        "text_status": "ready",
    },
    "fields": GOLDEN_STRONG_HISTORY["fields"],
    "message": None,
    "model": "claude-haiku-5-5",
    "status": "ok",
}


def _ok_header_cascade():
    return CascadeResult(
        accepted=True,
        needs_human=False,
        result={
            "vendor_name": "Acme Supply",
            "bill_number": "INV-1",
            "bill_date": "2026-05-01",
            "total_amount": "100.00",
            "description": "Materials",
            "confidence": 0.9,
        },
        confidence=0.9,
        winning_rung=Rung(provider="anthropic", model="claude-haiku-5-5"),
        attempts=[],
    )


def _attachment(**kw):
    base = dict(
        id=42,
        public_id=ATT_UUID,
        blob_url="https://blob/in.pdf",
        file_hash="abc",
        content_type="application/pdf",
        extraction_status="pending",
        extracted_text_blob_url=None,
        parse_invoice_number=False,
        is_taxpayer_document=False,
        claim_modified_datetime=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _strong_history(**kw):
    base = dict(
        project_id=200,
        sub_cost_code_id=501,
        sub_cost_code_public_id="11111111-1111-1111-1111-000000000501",
        sub_cost_code_number="05.100",
        sub_cost_code_name="Framing",
        source="vendor_history",
        reason="history",
        confidence=Decimal("0.85"),
        raw_score=Decimal("0.85"),
        top_count=5,
        second_count=1,
        total_count=6,
    )
    base.update(kw)
    return BillSuggestion(**base)


def _text_llm(**kw):
    return BillSuggestion(
        project_id=kw.get("project_id", 100),
        sub_cost_code_id=502,
        sub_cost_code_public_id="22222222-2222-2222-2222-000000000502",
        sub_cost_code_number="06.200",
        sub_cost_code_name="From LLM",
        source="llm",
        reason="Suggested from the bill's document text",
        confidence=Decimal("0.88"),
        raw_score=None,
        top_count=None,
        second_count=None,
        total_count=None,
        text_used=True,
        text_mode="B",
    )


def _settings_mock():
    s = MagicMock()
    s.bill_drop_prefill_enabled = True
    s.bill_drop_prefill_timeout_seconds = 25
    s.bill_coding_text_daily_call_cap = 100
    return s


@pytest.fixture(autouse=True)
def _claude_ok():
    with patch.object(
        ras.claude_budget,
        "is_claude_day_stopped_async",
        new=AsyncMock(return_value=False),
    ):
        yield


@pytest.fixture(autouse=True)
def _reset_catalog_cache():
    configure_catalog_cache_for_tests()
    yield
    configure_catalog_cache_for_tests()


@pytest.fixture(autouse=True)
def _prefetch_db_fakes():
    """Background catalog prefetch must never hit real DB in these tests."""
    with patch.object(
        ras,
        "load_catalog_from_db",
        return_value=CatalogSnapshot(
            scc_by_id={},
            scc_number_to_id={},
            ambiguous_scc_numbers=frozenset(),
            proj_abbrev_to_id={},
            ambiguous_project_abbrevs=frozenset(),
            candidate_projects=[],
            vendor_name="",
        ),
    ):
        yield


def _run_on_persistent_loop(coro):
    """Run coroutine on a fresh loop that outlives the call (task leak detection)."""
    never_retrieved: list[dict] = []
    loop = asyncio.new_event_loop()

    def _handler(_loop, context):
        if context.get("message", "").startswith("Task exception was never retrieved"):
            never_retrieved.append(context)

    loop.set_exception_handler(_handler)
    try:
        return loop.run_until_complete(coro), never_retrieved
    finally:
        loop.run_until_complete(asyncio.sleep(0))
        gc.collect()
        loop.run_until_complete(asyncio.sleep(0))
        loop.close()


def test_timing_log_emitted_without_bill_text(caplog):
    att = _attachment()
    history = _strong_history()
    suggester = MagicMock()
    suggester.suggest.return_value = history
    suggester.project_service = MagicMock()
    suggester.sub_cost_code_service = MagicMock()
    caplog.set_level(logging.INFO, logger=ras.logger.name)

    with patch.object(ras.config, "Settings") as ms:
        ms.return_value = _settings_mock()
        with patch.object(ras, "AttachmentService") as AS, patch.object(
            ras, "AttachmentExtractionService"
        ) as ES, patch.object(
            ras, "read_attachment_link_state", return_value=AttachmentLinkState(False, False, True)
        ), patch(
            "entities.bill.business.read_attachment_service.run_bill_header_extraction",
            new=AsyncMock(return_value=_ok_header_cascade()),
        ), patch.object(ras, "resolve_vendor_match_from_name") as rvm, patch.object(
            ras, "predict_text_coding", new=AsyncMock(return_value=(_text_llm(), None, None))
        ), patch(
            "entities.coding_suggestion.business.presenters.ProjectService"
        ) as PSapi, patch.object(
            ras, "resolve_accessible_text_project_id", side_effect=lambda pid, **_: pid
        ):
            AS.return_value.read_by_public_id.return_value = att
            ES.return_value.extract_for_bill_drop_prefill.return_value = {
                "status": "completed",
                "masked_text": "secret invoice body text must not appear",
            }
            rvm.return_value = VendorResolveMatch(
                public_id=VENDOR_UUID, name="Acme", confidence=0.8, vendor_id=10
            )
            PSapi.return_value.read_by_id.return_value = SimpleNamespace(
                id=100, public_id=PROJ_UUID, name="Proj"
            )
            asyncio.run(
                ras.run_bill_read_attachment(
                    attachment_public_id=ATT_UUID,
                    suggester=suggester,
                )
            )

    timing_lines = [
        r.message
        for r in caplog.records
        if r.message.startswith("bill_read_attachment.timing")
    ]
    assert len(timing_lines) == 1
    line = timing_lines[0]
    assert "attachment_id=42" in line
    assert "status=ok" in line
    assert "secret invoice" not in line
    assert "Acme" not in line
    for key in (
        "attachment=",
        "link_state=",
        "extract=",
        "header_llm=",
        "vendor_match=",
        "history=",
        "catalog=",
        "coding_llm=",
        "payload=",
        "total=",
    ):
        assert key in line


def test_inaccessible_project_404_before_any_model_call():
    att = _attachment()
    with patch.object(ras.config, "Settings") as ms:
        ms.return_value = _settings_mock()
        with patch.object(ras, "AttachmentService") as AS, patch.object(
            ras, "AttachmentExtractionService"
        ) as ES, patch.object(
            ras, "read_attachment_link_state", return_value=AttachmentLinkState(False, False, True)
        ), patch(
            "entities.bill.business.read_attachment_service.run_bill_header_extraction",
            new=AsyncMock(),
        ) as header_mock, patch.object(
            ras, "predict_text_coding", new=AsyncMock()
        ) as coding_mock, patch.object(ras, "ProjectService") as PS:
            AS.return_value.read_by_public_id.return_value = att
            ES.return_value.extract_for_bill_drop_prefill.return_value = {
                "status": "completed",
                "masked_text": "invoice body",
            }
            PS.return_value.read_by_public_id.return_value = SimpleNamespace(
                id=100, public_id=PROJ_UUID, name="P"
            )
            with patch.object(
                ras,
                "assert_can_access_project",
                side_effect=ras.EntityNotAccessibleError("Project", 100),
            ):
                with pytest.raises(AttachmentNotReadable):
                    asyncio.run(
                        ras.run_bill_read_attachment(
                            attachment_public_id=ATT_UUID,
                            project_public_id=UUID(PROJ_UUID),
                        )
                    )
    header_mock.assert_not_awaited()
    coding_mock.assert_not_awaited()


def test_catalog_load_starts_before_header_finishes():
    import threading
    import time

    att = _attachment()
    events: list[str] = []
    lock = threading.Lock()
    header_running = threading.Event()

    def _slow_catalog(_as_of):
        with lock:
            events.append("catalog_start")
        time.sleep(0.05)
        return CatalogSnapshot(
            scc_by_id={},
            scc_number_to_id={},
            ambiguous_scc_numbers=frozenset(),
            proj_abbrev_to_id={},
            ambiguous_project_abbrevs=frozenset(),
            candidate_projects=[],
            vendor_name="",
        )

    async def _slow_header(*_args, **_kwargs):
        header_running.set()
        with lock:
            events.append("header_start")
        await asyncio.sleep(0.2)
        with lock:
            events.append("header_end")
        return _ok_header_cascade()

    history = _strong_history()
    suggester = MagicMock()
    suggester.suggest.return_value = history
    suggester.project_service = MagicMock()
    suggester.sub_cost_code_service = MagicMock()

    with patch.object(ras.config, "Settings") as ms:
        ms.return_value = _settings_mock()
        with patch.object(ras, "AttachmentService") as AS, patch.object(
            ras, "AttachmentExtractionService"
        ) as ES, patch.object(
            ras, "read_attachment_link_state", return_value=AttachmentLinkState(False, False, True)
        ), patch(
            "entities.bill.business.read_attachment_service.run_bill_header_extraction",
            new=AsyncMock(side_effect=_slow_header),
        ), patch.object(ras, "load_catalog_from_db", side_effect=_slow_catalog), patch.object(
            ras, "resolve_vendor_match_from_name"
        ) as rvm, patch.object(
            ras, "predict_text_coding", new=AsyncMock(return_value=(_text_llm(), None, None))
        ), patch(
            "entities.coding_suggestion.business.presenters.ProjectService"
        ) as PSapi, patch.object(
            ras, "resolve_accessible_text_project_id", side_effect=lambda pid, **_: pid
        ):
            AS.return_value.read_by_public_id.return_value = att
            ES.return_value.extract_for_bill_drop_prefill.return_value = {
                "status": "completed",
                "masked_text": "invoice body",
            }
            rvm.return_value = VendorResolveMatch(
                public_id=VENDOR_UUID, name="Acme", confidence=0.8, vendor_id=10
            )
            PSapi.return_value.read_by_id.return_value = SimpleNamespace(
                id=100, public_id=PROJ_UUID, name="Proj"
            )
            asyncio.run(
                ras.run_bill_read_attachment(
                    attachment_public_id=ATT_UUID,
                    suggester=suggester,
                )
            )

    assert "catalog_start" in events
    assert "header_end" in events
    assert events.index("catalog_start") < events.index("header_end")


def test_prefetch_tasks_drained_on_outer_timeout_with_failing_prefetch():
    att = _attachment()

    def _catalog_raises(_as_of):
        time.sleep(0.05)
        raise RuntimeError("catalog boom")

    settings = _settings_mock()
    settings.bill_drop_prefill_timeout_seconds = 1

    def _slow_extract_long(*_a, **_k):
        time.sleep(1.5)
        return {"status": "completed", "masked_text": "body"}

    async def _run():
        with patch.object(ras.config, "Settings") as ms:
            ms.return_value = settings
            with patch.object(ras, "AttachmentService") as AS, patch.object(
                ras, "AttachmentExtractionService"
            ) as ES, patch.object(
                ras, "read_attachment_link_state", return_value=AttachmentLinkState(False, False, True)
            ), patch.object(
                ras, "load_catalog_from_db", side_effect=_catalog_raises
            ), patch(
                "entities.bill.business.read_attachment_service.run_bill_header_extraction",
                new=AsyncMock(),
            ):
                AS.return_value.read_by_public_id.return_value = att
                ES.return_value.extract_for_bill_drop_prefill.side_effect = _slow_extract_long
                return await ras.run_bill_read_attachment_with_timeout(
                    attachment_public_id=ATT_UUID,
                    settings=settings,
                )

    out, leaks = _run_on_persistent_loop(_run())
    assert out["status"] == "timeout"
    assert leaks == []


def test_prefetch_tasks_drained_when_header_raises_with_failing_catalog():
    att = _attachment()

    def _catalog_raises(_as_of):
        time.sleep(0.05)
        raise RuntimeError("catalog boom")

    async def _run():
        with patch.object(ras.config, "Settings") as ms:
            ms.return_value = _settings_mock()
            with patch.object(ras, "AttachmentService") as AS, patch.object(
                ras, "AttachmentExtractionService"
            ) as ES, patch.object(
                ras, "read_attachment_link_state", return_value=AttachmentLinkState(False, False, True)
            ), patch.object(
                ras, "load_catalog_from_db", side_effect=_catalog_raises
            ), patch(
                "entities.bill.business.read_attachment_service.run_bill_header_extraction",
                new=AsyncMock(side_effect=RuntimeError("header boom")),
            ):
                AS.return_value.read_by_public_id.return_value = att
                ES.return_value.extract_for_bill_drop_prefill.return_value = {
                    "status": "completed",
                    "masked_text": "body",
                }
                return await ras.run_bill_read_attachment(attachment_public_id=ATT_UUID)

    out, leaks = _run_on_persistent_loop(_run())
    assert out["status"] == "unreadable"
    assert leaks == []


def test_await_or_cancel_tasks_reraises_the_requests_own_cancellation():
    """If the request itself is cancelled (outer wait_for) while cleanup waits on a child
    that is slow to cancel, the cancellation must propagate — not be swallowed into a
    normal return after the deadline."""

    async def resistant_child():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            await asyncio.sleep(0.3)
            raise

    async def request_body():
        child = asyncio.create_task(resistant_child())
        await asyncio.sleep(0)
        await ras._await_or_cancel_tasks(child)
        return "returned normally"

    async def main():
        outer = asyncio.create_task(request_body())
        await asyncio.sleep(0.05)  # the request is now waiting inside the cleanup gather
        outer.cancel()
        try:
            return await outer
        except asyncio.CancelledError:
            return "cancelled"

    result, unhandled = _run_on_persistent_loop(main())
    assert result == "cancelled"
    assert unhandled == []


def test_predict_text_coding_db_reads_run_off_the_event_loop():
    """The real predict_text_coding: the daily-cap count runs exactly once, and it and the
    other DB reads run on worker threads, never on the event-loop thread."""
    from entities.coding_suggestion.business import bill_text_suggestion_service as bts

    seen: dict[str, int] = {}
    repo = MagicMock()

    def _cap():
        seen["cap"] = threading.get_ident()
        return 0

    def _counts(*_a, **_k):
        seen["counts"] = threading.get_ident()
        return []

    def _catalog(_as_of):
        seen["catalog"] = threading.get_ident()
        return CatalogSnapshot(
            scc_by_id={},
            scc_number_to_id={},
            ambiguous_scc_numbers=frozenset(),
            proj_abbrev_to_id={},
            ambiguous_project_abbrevs=frozenset(),
            candidate_projects=[],
            vendor_name="",
        )

    async def _cascade(_payload, **_kw):
        seen["loop"] = threading.get_ident()
        return SimpleNamespace(stopped_reason=None, accepted=False, result=None)

    repo.count_calls_today.side_effect = _cap
    repo.read_vendor_scc_counts.side_effect = _counts
    with patch.object(bts, "load_catalog_from_db", side_effect=_catalog), patch.object(
        bts, "run_bill_coding_suggestion", new=_cascade
    ), patch.object(bts.config, "Settings") as settings_cls:
        settings_cls.return_value = MagicMock(bill_coding_text_daily_call_cap=200)
        asyncio.run(
            bts.predict_text_coding(
                bill_text="invoice body",
                vendor_id=10,
                chosen_project_id=None,
                as_of=datetime(2026, 10, 19, 12, 0, 0),
                text_repo=repo,
                vendor_name="Acme",
            )
        )
    assert repo.count_calls_today.call_count == 1
    assert {"cap", "counts", "catalog", "loop"} <= set(seen)
    for key in ("cap", "counts", "catalog"):
        assert seen[key] != seen["loop"], f"{key} ran on the event-loop thread"


def test_chosen_project_strong_history_cancels_catalog_prefetch(caplog):
    att = _attachment()
    hist = _strong_history(source="vendor_project_history")
    suggester = MagicMock()
    suggester.suggest.return_value = hist
    suggester.project_service = MagicMock()
    suggester.sub_cost_code_service = MagicMock()
    caplog.set_level(logging.INFO, logger=ras.logger.name)

    with patch.object(ras.config, "Settings") as ms:
        ms.return_value = _settings_mock()
        with patch.object(ras, "AttachmentService") as AS, patch.object(
            ras, "AttachmentExtractionService"
        ) as ES, patch.object(
            ras, "read_attachment_link_state", return_value=AttachmentLinkState(False, False, True)
        ), patch(
            "entities.bill.business.read_attachment_service.run_bill_header_extraction",
            new=AsyncMock(return_value=_ok_header_cascade()),
        ), patch.object(ras, "resolve_vendor_match_from_name") as rvm, patch.object(
            ras, "predict_text_coding", new=AsyncMock()
        ) as predict_mock, patch.object(ras, "ProjectService") as PS, patch.object(
            ras, "assert_can_access_project"
        ), patch(
            "entities.coding_suggestion.business.presenters.ProjectService"
        ) as PSapi, patch.object(
            ras, "resolve_accessible_text_project_id", side_effect=lambda pid, **_: pid
        ):
            AS.return_value.read_by_public_id.return_value = att
            ES.return_value.extract_for_bill_drop_prefill.return_value = {
                "status": "completed",
                "masked_text": "invoice body",
            }
            rvm.return_value = VendorResolveMatch(
                public_id=VENDOR_UUID, name="Acme", confidence=0.8, vendor_id=10
            )
            PS.return_value.read_by_public_id.return_value = SimpleNamespace(
                id=100, public_id=PROJ_UUID, name="P"
            )
            PSapi.return_value.read_by_id.return_value = SimpleNamespace(
                id=100, public_id=PROJ_UUID, name="Proj"
            )
            out = asyncio.run(
                ras.run_bill_read_attachment(
                    attachment_public_id=ATT_UUID,
                    project_public_id=UUID(PROJ_UUID),
                    suggester=suggester,
                )
            )

    assert out["status"] == "ok"
    predict_mock.assert_not_awaited()
    timing_lines = [
        r.message
        for r in caplog.records
        if r.message.startswith("bill_read_attachment.timing")
    ]
    assert len(timing_lines) == 1
    assert "catalog=0" in timing_lines[0]


def test_dedupe_suggest_once_no_vendor_reread_single_catalog_load():
    att = _attachment()
    history = _strong_history()
    suggester = MagicMock()
    suggester.suggest.return_value = history
    suggester.project_service = MagicMock()
    suggester.sub_cost_code_service = MagicMock()
    catalog_calls = {"n": 0}

    def _count_catalog(_as_of):
        catalog_calls["n"] += 1
        return CatalogSnapshot(
            scc_by_id={},
            scc_number_to_id={},
            ambiguous_scc_numbers=frozenset(),
            proj_abbrev_to_id={},
            ambiguous_project_abbrevs=frozenset(),
            candidate_projects=[],
            vendor_name="",
        )

    with patch.object(ras.config, "Settings") as ms:
        ms.return_value = _settings_mock()
        with patch.object(ras, "AttachmentService") as AS, patch.object(
            ras, "AttachmentExtractionService"
        ) as ES, patch.object(
            ras, "read_attachment_link_state", return_value=AttachmentLinkState(False, False, True)
        ), patch(
            "entities.bill.business.read_attachment_service.run_bill_header_extraction",
            new=AsyncMock(return_value=_ok_header_cascade()),
        ), patch.object(ras, "resolve_vendor_match_from_name") as rvm, patch.object(
            ras, "load_catalog_from_db", side_effect=_count_catalog
        ) as _lcd, patch.object(ras, "VendorService") as VS, patch(
            "entities.coding_suggestion.business.bill_text_suggestion_service._read_vendor_name"
        ) as rvn, patch.object(
            ras,
            "predict_text_coding",
            new=AsyncMock(return_value=(_text_llm(), None, None)),
        ) as predict_mock, patch(
            "entities.coding_suggestion.business.presenters.ProjectService"
        ) as PSapi, patch.object(
            ras, "resolve_accessible_text_project_id", side_effect=lambda pid, **_: pid
        ):
            AS.return_value.read_by_public_id.return_value = att
            ES.return_value.extract_for_bill_drop_prefill.return_value = {
                "status": "completed",
                "masked_text": "invoice body",
            }
            rvm.return_value = VendorResolveMatch(
                public_id=VENDOR_UUID, name="Acme", confidence=0.8, vendor_id=10
            )
            PSapi.return_value.read_by_id.return_value = SimpleNamespace(
                id=100, public_id=PROJ_UUID, name="Proj"
            )
            asyncio.run(
                ras.run_bill_read_attachment(
                    attachment_public_id=ATT_UUID,
                    suggester=suggester,
                )
            )
    assert suggester.suggest.call_count == 1
    VS.return_value.read_by_public_id.assert_not_called()
    rvn.assert_not_called()
    assert catalog_calls["n"] == 1
    predict_mock.assert_awaited_once()
    _, kwargs = predict_mock.await_args
    assert kwargs.get("vendor_name") == "Acme"
    assert kwargs.get("catalog") is not None


def test_catalog_cache_hit_and_expiry():
    from datetime import datetime, timezone

    from entities.coding_suggestion.business import bill_text_suggestion_service as bts

    calls = {"n": 0}
    t = {"now": 1000.0}

    def loader(as_of):
        calls["n"] += 1
        return CatalogSnapshot(
            scc_by_id={},
            scc_number_to_id={},
            ambiguous_scc_numbers=frozenset(),
            proj_abbrev_to_id={},
            ambiguous_project_abbrevs=frozenset(),
            candidate_projects=[],
            vendor_name="x",
        )

    configure_catalog_cache_for_tests(now_fn=lambda: t["now"], loader=loader)
    as_of = datetime(2026, 5, 1, 12, 34, 56, tzinfo=timezone.utc).replace(tzinfo=None)
    load_catalog_from_db(as_of)
    load_catalog_from_db(as_of.replace(second=30))
    assert calls["n"] == 1
    t["now"] += 61.0
    load_catalog_from_db(as_of)
    assert calls["n"] == 2
    configure_catalog_cache_for_tests()


def test_catalog_cache_keeps_at_most_one_entry():
    from datetime import datetime, timezone

    from entities.coding_suggestion.business import bill_text_suggestion_service as bts

    t = {"now": 1000.0}

    def loader(as_of):
        return CatalogSnapshot(
            scc_by_id={},
            scc_number_to_id={},
            ambiguous_scc_numbers=frozenset(),
            proj_abbrev_to_id={},
            ambiguous_project_abbrevs=frozenset(),
            candidate_projects=[],
            vendor_name=str(as_of.minute),
        )

    configure_catalog_cache_for_tests(now_fn=lambda: t["now"], loader=loader)
    a = datetime(2026, 5, 1, 12, 0, 0).replace(tzinfo=None)
    b = datetime(2026, 5, 1, 12, 1, 0).replace(tzinfo=None)
    load_catalog_from_db(a)
    load_catalog_from_db(b)
    with bts._catalog_lock:
        assert len(bts._catalog_cache) == 1
    configure_catalog_cache_for_tests()


@pytest.mark.parametrize(
    "history,text,project_uuid,expected",
    [
        ("strong", "text", None, GOLDEN_STRONG_HISTORY),
        ("weak", "text", None, GOLDEN_WEAK_HISTORY),
        ("chosen", None, PROJ_UUID, GOLDEN_CHOSEN_PROJECT),
    ],
)
def test_golden_equivalence(history, text, project_uuid, expected):
    if history == "strong":
        hist = _strong_history()
        txt = _text_llm()
    elif history == "weak":
        hist = BillSuggestion(
            project_id=None,
            sub_cost_code_id=None,
            sub_cost_code_public_id=None,
            sub_cost_code_number=None,
            sub_cost_code_name=None,
            source="none",
            reason=None,
            confidence=None,
            raw_score=Decimal("0.40"),
            top_count=None,
            second_count=None,
            total_count=None,
        )
        txt = _text_llm()
    else:
        hist = _strong_history(source="vendor_project_history")
        txt = None

    suggester = MagicMock()
    suggester.suggest.return_value = hist
    suggester.project_service = MagicMock()
    suggester.sub_cost_code_service = MagicMock()

    predict = AsyncMock(return_value=(txt, None, None)) if txt is not None else AsyncMock()

    with patch.object(ras.config, "Settings") as ms:
        ms.return_value = _settings_mock()
        with patch.object(ras, "AttachmentService") as AS, patch.object(
            ras, "AttachmentExtractionService"
        ) as ES, patch.object(
            ras, "read_attachment_link_state", return_value=AttachmentLinkState(False, False, True)
        ), patch(
            "entities.bill.business.read_attachment_service.run_bill_header_extraction",
            new=AsyncMock(return_value=_ok_header_cascade()),
        ), patch.object(ras, "resolve_vendor_match_from_name") as rvm, patch.object(
            ras, "predict_text_coding", new=predict
        ), patch(
            "entities.coding_suggestion.business.presenters.ProjectService"
        ) as PSapi, patch.object(
            ras, "resolve_accessible_text_project_id", side_effect=lambda pid, **_: pid
        ):
            AS.return_value.read_by_public_id.return_value = _attachment()
            ES.return_value.extract_for_bill_drop_prefill.return_value = {
                "status": "completed",
                "masked_text": "invoice body",
            }
            rvm.return_value = VendorResolveMatch(
                public_id=VENDOR_UUID, name="Acme", confidence=0.8, vendor_id=10
            )
            PSapi.return_value.read_by_id.return_value = SimpleNamespace(
                id=100, public_id=PROJ_UUID, name="Proj"
            )
            kwargs = {"attachment_public_id": ATT_UUID, "suggester": suggester}
            if project_uuid:
                with patch.object(ras, "ProjectService") as PS, patch.object(
                    ras, "assert_can_access_project"
                ):
                    PS.return_value.read_by_public_id.return_value = SimpleNamespace(
                        id=100, public_id=project_uuid, name="P"
                    )
                    kwargs["project_public_id"] = UUID(project_uuid)
                    out = asyncio.run(ras.run_bill_read_attachment(**kwargs))
            else:
                out = asyncio.run(ras.run_bill_read_attachment(**kwargs))

    assert json.loads(json.dumps(out, default=str)) == expected
