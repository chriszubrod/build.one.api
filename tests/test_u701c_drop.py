"""U-701c: PDF drop falls back to Foundry when Claude's daily cap is hit (flag default OFF)."""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from entities.attachment.persistence.repo import AttachmentLinkState
from entities.bill.business import read_attachment_service as ras
from entities.coding_suggestion.business.bill_coding_decision import RungAnswer
from intelligence.cascade import bill_header_extraction as bhe
from intelligence.transport.base import Done, TextDelta, TransportError, Usage
from shared.authz import current_company_id, current_user_id
from tests.test_u701b4_drop_ladder import (
    ATT_UUID,
    LADDER,
    PROJ_A_UUID,
    PROJ_B_UUID,
    SCC_PUB,
    VENDOR_UUID,
    _decision,
    _suggester,
)

CAP = "claude_unavailable:daily_cap"
DEEPSEEK = "DeepSeek-V4-Flash"
HEADER = {
    "vendor_name": "Acme Supply", "bill_number": "INV-1", "bill_date": "2026-05-01",
    "total_amount": "100.00", "short_description": "Lumber", "long_description": None,
    "reference": None,
}


class _Transport:
    """anthropic -> daily cap; foundry -> per-model scripted reply (default: error)."""

    def __init__(self, provider, log, foundry):
        self.provider, self.log, self.foundry = provider, log, foundry

    async def stream(self, messages, *, model, **_kw):
        self.log.append(model)
        if self.provider == "anthropic":
            yield TransportError(message="cap", code=CAP)
            return
        conf = self.foundry.get(model)
        if conf is None:
            yield TransportError(message="boom", code="http_500")
            return
        yield TextDelta(text=json.dumps({**HEADER, "confidence": conf}))
        yield Done(usage=Usage(input_tokens=3, output_tokens=2))


def _foundry_decision():
    return _decision(
        source="fallback",
        sub_cost_code_id=502,
        confidence=Decimal("0.60"),
        model=DEEPSEEK,
        primary=RungAnswer("claude-haiku-5-5", None, None, None, CAP),
        fallback=RungAnswer(DEEPSEEK, 502, None, Decimal("0.60")),
        escalation="claude_unavailable",
    )


def _settings(flag):
    s = MagicMock()
    s.bill_drop_prefill_enabled = True
    s.bill_drop_prefill_timeout_seconds = 25
    s.bill_coding_ladder_v2_enabled = True
    if flag is not None:
        s.bill_coding_foundry_on_claude_cap_enabled = flag
    return s


def _run(
    *, flag=True, foundry_configured=True, foundry=None, decision=None, capped=True, header_ladder=None,
):
    """Claude is capped. Returns (out, ladder mock, extraction mock, record mock, model log)."""
    from entities.vendor.business.vendor_name_resolver import VendorResolveMatch

    log: list[str] = []
    foundry = {DEEPSEEK: 0.9} if foundry is None else foundry
    att = SimpleNamespace(id=1, public_id=ATT_UUID)
    with patch.object(ras.config, "Settings", return_value=_settings(flag)), patch.object(
        ras.claude_budget, "is_claude_day_stopped_async", new=AsyncMock(return_value=capped)
    ), patch.object(ras, "foundry_is_configured", return_value=foundry_configured), patch.object(
        bhe, "resolve_ladder", lambda _n, default: header_ladder or default
    ), patch.object(bhe, "resolve_threshold", lambda _n, default: default), patch.object(
        ras, "AttachmentService"
    ) as AS, patch.object(ras, "AttachmentExtractionService") as ES, patch.object(
        ras, "read_attachment_link_state", return_value=AttachmentLinkState(False, False, True)
    ), patch.object(
        ras, "resolve_vendor_match_from_name",
        return_value=VendorResolveMatch(public_id=VENDOR_UUID, name="Acme", confidence=0.8, vendor_id=10),
    ) as resolve_vendor, patch.object(
        ras, "_resolve_chosen_project_id", new=AsyncMock(return_value=100)
    ), patch.object(
        ras, "resolve_accessible_text_project_id", side_effect=lambda pid, **_: pid
    ), patch.object(
        ras, "resolve_accessible_project_ids", return_value=frozenset({100, 300})
    ), patch.object(ras, "load_catalog_from_db", return_value=MagicMock()), patch.object(
        ras, "_schedule_record_read"
    ) as record, patch(
        "entities.coding_suggestion.business.presenters.ProjectService"
    ) as PS, patch(
        LADDER, new=AsyncMock(return_value=decision or _foundry_decision()), create=True
    ) as ladder:
        AS.return_value.read_by_public_id.return_value = att
        extract = ES.return_value.extract_for_bill_drop_prefill
        extract.return_value = {"status": "completed", "masked_text": "invoice body"}
        PS.return_value.read_by_id.side_effect = lambda pid: SimpleNamespace(
            id=pid, public_id={100: PROJ_A_UUID, 300: PROJ_B_UUID}.get(pid), name=f"P{pid}"
        )
        uid_tok = current_user_id.set(42)
        cid_tok = current_company_id.set(7)
        try:
            out = asyncio.run(ras.run_bill_read_attachment(
                attachment_public_id=ATT_UUID,
                project_public_id=PROJ_A_UUID,
                suggester=_suggester(),
                transport_for=lambda provider: _Transport(provider, log, foundry),
            ))
        finally:
            current_user_id.reset(uid_tok)
            current_company_id.reset(cid_tok)
    return SimpleNamespace(
        out=out, ladder=ladder, extract=extract, record=record, log=log,
        resolve_vendor=resolve_vendor,
    )


@pytest.mark.parametrize("flag", [None, False])
def test_flag_off_capped_is_unchanged_ai_unavailable(flag):
    run = _run(flag=flag)
    assert run.out["status"] == "ai_unavailable"
    assert run.out["message"] == ras.MSG_AI_PAUSED
    run.extract.assert_not_called()
    run.ladder.assert_not_awaited()
    assert run.log == []


def test_flag_on_capped_foundry_reads_header_and_codes():
    run = _run()
    assert run.out["status"] == "ok"
    assert run.out["model"] == DEEPSEEK
    assert run.out["fields"]["vendor"]["value"]["public_id"] == VENDOR_UUID
    assert run.log[-1] == DEEPSEEK
    kw = run.ladder.await_args.kwargs
    assert kw["stage"] == "drop" and kw["fallback_on_daily_cap"] is True
    assert run.out["coding"]["sub_cost_code_public_id"] == SCC_PUB[502]
    assert run.out["coding"]["decision_source"] == "fallback"
    rec = run.record.call_args.kwargs
    assert rec["coding_decision"].escalation == "claude_unavailable"
    assert rec["header_model"] == DEEPSEEK


def test_flag_on_without_foundry_configured_stays_ai_unavailable():
    run = _run(foundry_configured=False)
    assert run.out["status"] == "ai_unavailable"
    run.extract.assert_not_called()
    assert run.log == []


def test_flag_on_every_foundry_rung_fails_is_ai_unavailable():
    run = _run(foundry={})
    assert run.out["status"] == "ai_unavailable"
    assert "gpt-5.4-mini" in run.log
    run.ladder.assert_not_awaited()


def test_flag_on_foundry_header_below_bar_is_still_used():
    run = _run(foundry={DEEPSEEK: 0.3})
    assert run.out["status"] == "ok"
    assert run.out["fields"]["bill_number"]["value"] == "INV-1"
    assert run.resolve_vendor.call_args.args[0] == "Acme Supply"
    run.ladder.assert_awaited_once()


# A CASCADE_LADDER_BILL_HEADER_EXTRACTION override with a Claude rung AFTER a Foundry one:
# DeepSeek validates below the bar, then Haiku hits the cap mid-request.
FOUNDRY_FIRST = (
    bhe.Rung("foundry", DEEPSEEK, fallback_only=True),
    bhe.Rung("anthropic", "claude-haiku-5-5"),
)


@pytest.mark.parametrize("flag", [None, False, True])
def test_header_exit_is_today_with_a_foundry_first_override_unless_capped_at_entry(flag):
    """Not capped when the drop started => no fallback opt-in (foundry_on_cap False),
    whatever the flag: the cap hit mid-request exits exactly as today."""
    run = _run(flag=flag, capped=False, header_ladder=FOUNDRY_FIRST, foundry={DEEPSEEK: 0.3})
    assert run.log == [DEEPSEEK, "claude-haiku-5-5"]
    assert run.out["status"] == "ai_unavailable"
    run.ladder.assert_not_awaited()


def test_flag_on_capped_at_entry_uses_the_validated_foundry_answer_with_that_override():
    run = _run(header_ladder=FOUNDRY_FIRST, foundry={DEEPSEEK: 0.3})
    assert run.out["status"] == "ok"
    assert run.out["fields"]["bill_number"]["value"] == "INV-1"
