"""U-702: isolated Claude spend ledger for offline harnesses."""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from entities.attachment.business.extraction_service import REDACTION_VERSION

from config import Settings
from intelligence.cascade import claude_budget
from intelligence.transport.base import Usage
from tests.conftest import REPO_ROOT


class _ExplodingRepo:
    def read_daily_spend(self, *a, **k):
        raise AssertionError("LlmSpendRepository must not be used in isolated mode")

    def add_daily_spend(self, *a, **k):
        raise AssertionError("LlmSpendRepository must not be used in isolated mode")


class _TrackingRepo:
    def __init__(self) -> None:
        self.reads: list[tuple] = []
        self.writes: list[tuple] = []

    def read_daily_spend(self, spend_date, provider: str) -> Decimal:
        self.reads.append((spend_date, provider))
        return Decimal("0")

    def add_daily_spend(self, spend_date, provider: str, cost_usd: Decimal) -> Decimal:
        self.writes.append((spend_date, provider, cost_usd))
        return cost_usd


@pytest.fixture(autouse=True)
def _reset_budget():
    claude_budget.configure_for_tests()
    yield
    claude_budget.configure_for_tests()


def test_isolated_mode_never_hits_prod_repo(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "shared.llm_spend_repo",
        MagicMock(LlmSpendRepository=_ExplodingRepo),
    )
    prod = _TrackingRepo()
    fixed = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
    claude_budget.configure_for_tests(now_fn=lambda: fixed, repo_reader=prod, repo_writer=prod)
    claude_budget.use_isolated_spend_ledger(Decimal("1.00"))
    assert claude_budget.is_isolated_spend_ledger()
    assert asyncio.run(claude_budget.pre_call_gate(has_api_key=True)) is None
    claude_budget.record_anthropic_spend(Decimal("0.50"))
    assert prod.reads == []
    assert prod.writes == []
    assert claude_budget.get_isolated_spend_total() == Decimal("0.50")
    claude_budget.record_anthropic_spend(Decimal("0.60"))
    assert prod.reads == []
    assert prod.writes == []
    assert asyncio.run(claude_budget.pre_call_gate(has_api_key=True)) == "daily_cap"


def test_isolated_cap_not_settings_cap(monkeypatch):
    monkeypatch.setenv("CLAUDE_DAILY_SPEND_CAP_USD", "100")
    fixed = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
    claude_budget.configure_for_tests(now_fn=lambda: fixed)
    claude_budget.use_isolated_spend_ledger(Decimal("0.10"))
    claude_budget.record_anthropic_spend(Decimal("0.10"))
    assert asyncio.run(claude_budget.pre_call_gate(has_api_key=True)) == "daily_cap"


def test_default_path_uses_injected_repo_and_default_cap(monkeypatch):
    monkeypatch.setenv("CLAUDE_DAILY_SPEND_CAP_USD", "6.45")
    repo = _TrackingRepo()
    fixed = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
    claude_budget.configure_for_tests(
        now_fn=lambda: fixed, repo_reader=repo, repo_writer=repo,
    )
    assert claude_budget._daily_cap_usd(Settings()) == claude_budget._DEFAULT_CAP_USD
    assert asyncio.run(claude_budget.pre_call_gate(has_api_key=True)) is None
    claude_budget.record_anthropic_spend(Decimal("0.01"))
    assert repo.reads and repo.reads[0][1] == claude_budget.ANTHROPIC_PROVIDER
    assert repo.writes and repo.writes[0][1] == claude_budget.ANTHROPIC_PROVIDER
    assert not claude_budget.is_isolated_spend_ledger()
    assert claude_budget.get_isolated_spend_total() is None


def test_clear_isolated_resets_default_path_spend_buffers():
    repo = _TrackingRepo()
    t0 = datetime(2026, 10, 21, 23, 0, tzinfo=timezone.utc)
    claude_budget.configure_for_tests(now_fn=lambda: t0, repo_reader=repo, repo_writer=repo)
    claude_budget.use_isolated_spend_ledger(Decimal("10"))
    claude_budget.record_anthropic_spend(Decimal("7"))
    assert claude_budget.get_isolated_spend_total() == Decimal("7")
    claude_budget.clear_isolated_spend_ledger()
    assert asyncio.run(claude_budget.pre_call_gate(has_api_key=True)) is None
    assert claude_budget.get_today_anthropic_spend() == Decimal("0")


def test_enable_isolated_clears_pending_default_buffers(monkeypatch):
    monkeypatch.setenv("CLAUDE_DAILY_SPEND_CAP_USD", "5")
    repo = _TrackingRepo()
    fixed = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
    claude_budget.configure_for_tests(now_fn=lambda: fixed, repo_reader=repo, repo_writer=repo)
    claude_budget._unsaved_by_date[fixed.date()] = Decimal("5")
    claude_budget._sync_unsaved_mirror()
    assert asyncio.run(claude_budget.pre_call_gate(has_api_key=True)) == "daily_cap"
    claude_budget.use_isolated_spend_ledger(Decimal("5"))
    repo.reads.clear()
    repo.writes.clear()
    assert claude_budget.get_isolated_spend_total() == Decimal("0")
    assert asyncio.run(claude_budget.pre_call_gate(has_api_key=True)) is None
    claude_budget.record_anthropic_spend(Decimal("1"))
    assert claude_budget.get_isolated_spend_total() == Decimal("1")
    assert repo.reads == []
    assert repo.writes == []


def test_isolated_run_total_survives_utc_midnight():
    t0 = datetime(2026, 10, 21, 23, 30, tzinfo=timezone.utc)
    t1 = t0 + timedelta(hours=1)
    state = {"now": t0}

    def now_fn():
        return state["now"]

    claude_budget.configure_for_tests(now_fn=now_fn)
    claude_budget.use_isolated_spend_ledger(Decimal("6"))
    claude_budget.record_anthropic_spend(Decimal("4"))
    state["now"] = t1
    claude_budget.record_anthropic_spend(Decimal("3"))
    assert claude_budget.get_isolated_spend_total() == Decimal("7")
    assert asyncio.run(claude_budget.pre_call_gate(has_api_key=True)) == "daily_cap"


def test_app_tree_never_references_isolated_spend_ledger():
    allowed = {
        REPO_ROOT / "intelligence" / "cascade" / "claude_budget.py",
        REPO_ROOT / "scripts" / "u654a_text_backtest.py",
        REPO_ROOT / "scripts" / "u676a_bill_field_bakeoff.py",
    }
    skip_dirs = frozenset(
        {".venv", "tests", "tests_sqlserver", "scripts", "__pycache__", ".git"}
    )
    hits: list[str] = []
    for path in REPO_ROOT.rglob("*.py"):
        if any(part in skip_dirs for part in path.parts):
            continue
        if path in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        if "isolated_spend_ledger" in text:
            hits.append(str(path.relative_to(REPO_ROOT)))
    assert hits == []


def test_main_run_finally_clears_isolation(tmp_path: Path):
    from scripts import u654a_text_backtest as u654a

    fake_bills = [
        u654a.TextBillRow(
            bill_id=1,
            vendor_id=1,
            created=datetime(2026, 5, 1),
            label_scc_id=1,
            label_project_id=10,
            total_abs_amount=Decimal("100"),
            mixed_code=False,
            extracted_text_blob_url="https://x",
        )
    ]
    patches = [
        patch.object(u654a, "load_bills_from_db", return_value=fake_bills),
        patch.object(u654a, "load_blob_urls_from_db", return_value={1: "https://x"}),
        patch.object(u654a, "load_reference_data", return_value=u654a.ReferenceData(
            scc_by_id={1: u654a.SccRef(1, "03.01", "n", datetime(2026, 1, 1))},
            scc_number_to_id={"03.01": 1},
            proj_by_id={10: u654a.ProjectRef(10, "p", "P")},
            proj_abbrev_to_id={"p": 10},
            vendor_names={1: "V"},
            ambiguous_scc_numbers=frozenset(),
            ambiguous_project_abbrevs=frozenset(),
            duplicate_scc_number_count=0,
            duplicate_project_abbrev_count=0,
        )),
        patch.object(u654a, "stratified_sample", return_value=[(fake_bills[0], "cold_start")]),
        patch.object(u654a, "execute_run", return_value="complete"),
        patch.object(u654a, "assert_cli_system_admin"),
    ]
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        u654a.main(["--out-dir", str(tmp_path), "--run", "--sample-size", "1"])
    assert not claude_budget.is_isolated_spend_ledger()


def test_u676a_main_run_finally_clears_isolation(tmp_path: Path):
    from scripts import u676a_bill_field_bakeoff as u676a

    fake = u676a.HeaderBillRow(
        bill_id=1,
        vendor_id=10,
        vendor_public_id="11111111-1111-1111-1111-111111111111",
        bill_number="INV-100",
        bill_date=date(2026, 5, 1),
        total_amount=Decimal("100.00"),
        created=datetime(2026, 5, 2, 10, 0, 0),
        intake_source="manual",
        extracted_text_blob_url="https://example/blob",
        first_line_description="Framing labor",
    )
    payload_json = json.dumps(
        {
            "content": "t",
            "key_value_pairs": [],
            "redaction_version": REDACTION_VERSION,
            "attachment_public_id": "00000000-0000-0000-0000-000000000001",
            "source_file_hash": "a",
            "method": "text_layer",
            "pages_count": 1,
        }
    )

    def run_raises(build, _llm):
        build()
        raise RuntimeError("simulated run failure")

    with patch.object(u676a, "load_header_bills_from_db", return_value=[fake]):
        with patch.object(u676a, "read_text_blob_with_retry", return_value=payload_json):
            with patch.object(u676a, "execute_run", side_effect=run_raises):
                with patch.object(u676a, "assert_cli_system_admin"):
                    with pytest.raises(RuntimeError, match="simulated run failure"):
                        u676a.main(
                            [
                                "--out-dir",
                                str(tmp_path),
                                "--run",
                                "--sample-size",
                                "1",
                                "--arms",
                                "A",
                            ]
                        )
    assert not claude_budget.is_isolated_spend_ledger()
