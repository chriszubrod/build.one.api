"""U-797a: shadow service with fakes (no DB, no LLM, no network)."""
from __future__ import annotations

import ast
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Optional

from entities.contract_labor.business import coding_shadow_service as svc
from entities.contract_labor.business.coding_shadow import CatalogEntry, ShadowInputs
from entities.contract_labor.business.coding_shadow import PrecedentDay


def _settings(enabled: bool = True, cap: int = 60) -> SimpleNamespace:
    return SimpleNamespace(cl_coding_shadow_enabled=enabled, cl_coding_shadow_daily_call_cap=cap)


_CORPUS_ROWS = [
    (100, datetime(2026, 9, 1, 8, 0), "framing wall"),
    (101, datetime(2026, 9, 2, 8, 0), "drywall hanging"),
    (200, datetime(2026, 9, 3, 8, 0), "pavers crates hauling"),
    (201, datetime(2026, 9, 4, 8, 0), "pavers crates loading"),
]


@dataclass
class FakeRepo:
    enqueue_result: int = 2
    enqueue_error: Optional[Exception] = None
    claims: list = field(default_factory=list)
    calls_today: int = 0
    inputs: Optional[ShadowInputs] = None
    inputs_error: Optional[Exception] = None
    idf_rows: list = field(default_factory=lambda: list(_CORPUS_ROWS))
    idf_reads: int = 0
    completed: list = field(default_factory=list)
    released: list = field(default_factory=list)
    release_attempts: list = field(default_factory=list)
    enqueued: list = field(default_factory=list)

    def enqueue_for_contract_labor(self, contract_labor_id: int) -> int:
        self.enqueued.append(contract_labor_id)
        if self.enqueue_error is not None:
            raise self.enqueue_error
        return self.enqueue_result

    def claim_next(self):
        return self.claims.pop(0) if self.claims else None

    def count_calls_today(self) -> int:
        return self.calls_today

    def read_inputs(self, contract_labor_id: int, project_id: int) -> ShadowInputs:
        if self.inputs_error is not None:
            raise self.inputs_error
        return self.inputs

    def read_idf_corpus(self):
        self.idf_reads += 1
        return list(self.idf_rows)

    def release(self, suggestion_id: int, count_attempt: bool = False) -> None:
        self.released.append(suggestion_id)
        self.release_attempts.append((suggestion_id, count_attempt))

    def complete(self, suggestion_id: int, **fields: Any) -> None:
        self.completed.append((suggestion_id, fields))


@dataclass
class FakeLineRepo:
    project_counts: dict = field(default_factory=dict)
    vendor_counts: dict = field(default_factory=dict)

    def read_sub_cost_code_counts(self, **_kwargs):
        return self.project_counts, self.vendor_counts


@dataclass
class PerProjectLines:
    """Project 4 has a strong history (4 of 4 on code 5); every other project has none."""

    def read_sub_cost_code_counts(self, *, project_id, **_kwargs):
        if project_id == 4:
            return {5: 4}, {5: 4}
        return {}, {}


def _claim(claim_id: int = 1, **over):
    base = dict(
        id=claim_id,
        contract_labor_id=100 + claim_id,
        project_id=3,
        work_date=date(2026, 11, 10),
        vendor_id=9,
        attempt_count=0,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _inputs(own_status: Optional[str] = None) -> ShadowInputs:
    return ShadowInputs(
        total_hours=Decimal("8"),
        day_note_text="pavers crates hauling",
        precedent_days=(
            PrecedentDay(1, date(2026, 11, 7), ((5, Decimal("8")),), "framing wall"),
        ),
        catalog=(CatalogEntry(5, "05.00", "Framing"), CatalogEntry(6, "06.00", "Misc")),
        own_status=own_status,
    )


async def _never_called(*_a, **_k):
    raise AssertionError("model must not be called")


async def _not_stopped():
    return False


async def _has_headroom():
    return True


def _run(**kwargs):
    kwargs.setdefault("budget_stopped", _not_stopped)
    kwargs.setdefault("spend_headroom", _has_headroom)
    kwargs.setdefault("line_item_repo", FakeLineRepo())
    return asyncio.run(svc.run_cl_coding_tick(**kwargs))


# --- enqueue --------------------------------------------------------------


def test_enqueue_is_a_noop_when_flag_off():
    repo = FakeRepo()
    count = svc.ContractLaborCodingShadowService(settings=_settings(enabled=False), repo=repo).enqueue_for_contract_labor(7)
    assert count == 0
    assert repo.enqueued == []


def test_enqueue_calls_repo_when_flag_on():
    repo = FakeRepo(enqueue_result=2)
    count = svc.ContractLaborCodingShadowService(settings=_settings(), repo=repo).enqueue_for_contract_labor(7)
    assert count == 2
    assert repo.enqueued == [7]


def test_enqueue_never_raises_and_logs_class_name_only(caplog):
    repo = FakeRepo(enqueue_error=RuntimeError("secret note text"))
    with caplog.at_level(logging.WARNING):
        count = svc.ContractLaborCodingShadowService(settings=_settings(), repo=repo).enqueue_for_contract_labor(7)
    assert count == 0
    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "cl_coding_shadow.enqueue_failed" in messages
    assert "RuntimeError" in messages
    assert "secret note text" not in messages


def test_enqueue_never_raises_when_settings_cannot_load():
    class Broken:
        @property
        def cl_coding_shadow_enabled(self):
            raise OSError("no env")

    count = svc.ContractLaborCodingShadowService(settings=Broken(), repo=FakeRepo()).enqueue_for_contract_labor(7)
    assert count == 0


# --- tick -----------------------------------------------------------------


def test_tick_disabled_returns_skipped_and_touches_nothing():
    repo = FakeRepo(claims=[_claim()])
    result = _run(limit=3, settings=_settings(enabled=False), repo=repo, budget_stopped=_never_called)
    assert result == {"skipped": "disabled"}
    assert repo.completed == [] and repo.released == []


def test_claude_day_stopped_still_completes_history_rows_without_a_model_call():
    async def stopped():
        return True

    repo = FakeRepo(claims=[_claim(1, project_id=4)], inputs=_inputs())
    result = _run(
        limit=1,
        settings=_settings(),
        repo=repo,
        line_item_repo=PerProjectLines(),
        budget_stopped=stopped,
        model_call=_never_called,
    )
    assert result["completed"] == 1
    (_, fields), = repo.completed
    assert fields["status"] == "done" and fields["decision_source"] == "history"


def test_history_strong_completes_without_a_model_call():
    repo = FakeRepo(claims=[_claim()], inputs=_inputs())
    lines = FakeLineRepo(project_counts={5: 4}, vendor_counts={5: 4})
    result = _run(
        limit=1,
        settings=_settings(),
        repo=repo,
        line_item_repo=lines,
        model_call=_never_called,
    )
    assert result["completed"] == 1
    (suggestion_id, fields), = repo.completed
    assert fields["status"] == "done"
    assert fields["decision_source"] == "history"
    assert fields["sub_cost_code_id"] == 5
    assert "model" not in fields or fields.get("model") is None


def test_cap_reached_defers_model_rows_and_the_tick_continues_to_history_rows():
    # Row 1 needs the model (no history) and the cap is full: deferred. Row 2 is strong history:
    # it must still complete, so the deferred row does not head-of-line block it.
    repo = FakeRepo(claims=[_claim(1), _claim(2, project_id=4)], inputs=_inputs(), calls_today=60)
    result = _run(
        limit=2,
        settings=_settings(cap=60),
        repo=repo,
        line_item_repo=PerProjectLines(),
        model_call=_never_called,
    )
    assert result["deferred"] == 1
    assert repo.released == [1]
    assert [(sid, f["decision_source"]) for sid, f in repo.completed] == [(2, "history")]
    assert repo.claims == []


def test_model_rung_single_completes_with_model_name():
    async def model(system, user):
        return '{"proposal":"single","lines":[{"sub_cost_code_id":6,"sub_cost_code_number":"06.00","hours":8}]}'

    repo = FakeRepo(claims=[_claim()], inputs=_inputs(), calls_today=0)
    # Code 6 is a vendor top code, so it is in the candidate catalog; no history rung.
    lines = FakeLineRepo(project_counts={}, vendor_counts={6: 2})
    _run(
        limit=1,
        settings=_settings(),
        repo=repo,
        line_item_repo=lines,
        model_call=model,
    )
    (_, fields), = repo.completed
    assert fields["status"] == "done"
    assert fields["decision_source"] == "model"
    assert fields["sub_cost_code_id"] == 6
    assert fields["model"]


def test_model_unavailable_releases_the_claim_and_stops_the_tick():
    async def unavailable(system, user):
        return None

    repo = FakeRepo(claims=[_claim(1), _claim(2)], inputs=_inputs())
    result = _run(
        limit=5,
        settings=_settings(),
        repo=repo,
        line_item_repo=FakeLineRepo(),
        model_call=unavailable,
    )
    assert repo.released == [1]
    assert repo.completed == []
    assert len(repo.claims) == 1  # the tick stopped; row 2 was not claimed
    assert result["unavailable"] == 1


def test_stopped_is_checked_before_each_rows_model_call():
    values = [False, True]

    async def stopped():
        return values.pop(0) if len(values) > 1 else values[0]

    calls = {"n": 0}

    async def model(system, user):
        calls["n"] += 1
        return '{"proposal":"single","lines":[{"sub_cost_code_id":6,"hours":8}]}'

    repo = FakeRepo(claims=[_claim(1), _claim(2)], inputs=_inputs())
    _run(
        limit=2,
        settings=_settings(),
        repo=repo,
        line_item_repo=FakeLineRepo(),
        budget_stopped=stopped,
        model_call=model,
    )
    assert calls["n"] == 1
    assert repo.released == [2]


def test_spend_headroom_defers_model_rows_but_not_history_rows():
    async def no_headroom():
        return False

    repo = FakeRepo(claims=[_claim(1, project_id=4), _claim(2)], inputs=_inputs())
    _run(
        limit=2,
        settings=_settings(),
        repo=repo,
        line_item_repo=PerProjectLines(),
        spend_headroom=no_headroom,
        model_call=_never_called,
    )
    assert [sid for sid, _ in repo.completed] == [1]
    assert repo.released == [2]
    # A headroom block is a policy deferral, not a failed attempt (mutation survivor, U-797a).
    assert repo.release_attempts == [(2, False)]


def test_spend_headroom_is_seventy_percent_of_the_cap_and_no_cap_means_headroom():
    from decimal import Decimal as D

    assert svc.SPEND_HEADROOM_FRACTION == D("0.70")
    assert svc.spend_has_headroom(D("4.50"), D("6.45")) is True
    assert svc.spend_has_headroom(D("4.52"), D("6.45")) is False
    assert svc.spend_has_headroom(D("100"), D("0")) is True
    # Exactly at 70 % of the cap the gate is closed (mutation survivor, U-797a).
    assert svc.spend_has_headroom(D("7.00"), D("10")) is False


def test_own_cl_already_labelled_is_skipped_without_a_model_call():
    repo = FakeRepo(claims=[_claim()], inputs=_inputs(own_status="approved"))
    _run(
        limit=1,
        settings=_settings(),
        repo=repo,
        line_item_repo=FakeLineRepo(),
        model_call=_never_called,
    )
    (_, fields), = repo.completed
    assert fields["status"] == "skipped"
    assert fields["failure_reason"] == "own_cl_coded"


def test_idf_corpus_is_read_once_per_utc_day_and_cached():
    svc.reset_idf_cache()

    async def model(system, user):
        return '{"proposal":"single","lines":[{"sub_cost_code_id":6,"hours":8}]}'

    repo = FakeRepo(inputs=_inputs())
    for _ in range(2):
        repo.claims = [_claim(1)]
        _run(limit=1, settings=_settings(), repo=repo, line_item_repo=FakeLineRepo(), model_call=model)
    assert repo.idf_reads == 1


def test_same_row_claimed_twice_in_one_tick_is_processed_once():
    repo = FakeRepo(claims=[_claim(1, project_id=4), _claim(1, project_id=4)], inputs=_inputs())
    _run(limit=2, settings=_settings(), repo=repo, line_item_repo=PerProjectLines(), model_call=_never_called)
    assert len(repo.completed) == 1


def test_row_exception_releases_with_an_attempt_and_stops_the_tick_not_completed_as_failed():
    # Fix round 2: an exception while processing a row is an attempt failure, not a verdict. The row
    # goes back to pending with AttemptCount +1 and the tick stops (infrastructure or row fault).
    repo = FakeRepo(claims=[_claim(1), _claim(2)], inputs_error=ValueError("bad row contains note text"))
    result = _run(limit=5, settings=_settings(), repo=repo, line_item_repo=FakeLineRepo(), model_call=_never_called)
    assert repo.completed == []
    assert repo.release_attempts == [(1, True)]
    assert len(repo.claims) == 1  # the tick stopped: row 2 was not claimed
    assert result["failed"] == 1


def test_row_exception_on_the_third_strike_completes_failed_with_the_class_name():
    repo = FakeRepo(claims=[_claim(1, attempt_count=2)], inputs_error=RuntimeError("note body"))
    _run(limit=1, settings=_settings(), repo=repo, line_item_repo=FakeLineRepo(), model_call=_never_called)
    assert repo.release_attempts == []
    (_, fields), = repo.completed
    assert fields["status"] == "failed"
    assert fields["failure_reason"] == "RuntimeError"
    assert "note body" not in repr(fields)


def test_failure_reason_is_class_name_not_message():
    repo = FakeRepo(claims=[_claim(1, attempt_count=2)], inputs_error=RuntimeError("note body"))
    _run(
        limit=1,
        settings=_settings(),
        repo=repo,
        line_item_repo=FakeLineRepo(),
        model_call=_never_called,
    )
    (_, fields), = repo.completed
    assert fields["failure_reason"] == "RuntimeError"
    assert "note body" not in repr(fields)


def test_model_unavailable_counts_an_attempt_when_released():
    async def unavailable(system, user):
        return None

    repo = FakeRepo(claims=[_claim(1)], inputs=_inputs())
    _run(limit=1, settings=_settings(), repo=repo, line_item_repo=FakeLineRepo(), model_call=unavailable)
    assert repo.release_attempts == [(1, True)]
    assert repo.completed == []


def test_model_unavailable_on_the_third_strike_completes_failed_with_model_unavailable():
    async def unavailable(system, user):
        return None

    repo = FakeRepo(claims=[_claim(1, attempt_count=2)], inputs=_inputs())
    _run(limit=1, settings=_settings(), repo=repo, line_item_repo=FakeLineRepo(), model_call=unavailable)
    assert repo.release_attempts == []
    (_, fields), = repo.completed
    assert fields["status"] == "failed"
    assert fields["failure_reason"] == "model_unavailable"


def test_policy_deferral_releases_without_counting_an_attempt():
    repo = FakeRepo(claims=[_claim(1)], inputs=_inputs(), calls_today=60)
    _run(limit=1, settings=_settings(cap=60), repo=repo, line_item_repo=FakeLineRepo(), model_call=_never_called)
    assert repo.release_attempts == [(1, False)]
    assert repo.completed == []


def test_sync_wrapper_returns_the_same_shape():
    result = svc.run_cl_coding_tick_sync(limit=1, settings=_settings(enabled=False), repo=FakeRepo())
    assert result == {"skipped": "disabled"}


# --- hook placement -------------------------------------------------------


def test_review_hook_runs_after_enqueue_drafts_and_is_failure_isolated():
    from tests.sproc_text import REPO_ROOT

    text = (REPO_ROOT / "entities" / "review" / "business" / "service.py").read_text(encoding="utf-8")
    drafts = text.index("ContractLaborReviewNotificationService().enqueue_drafts(")
    shadow = text.index("ContractLaborCodingShadowService().enqueue_for_contract_labor(")
    assert drafts < shadow
    head = text[:shadow].rsplit("try:", 1)[1]
    assert "enqueue_for_contract_labor" not in head
    tail = text[shadow:].split("except Exception", 1)[1][:300]
    assert "raise" not in tail.split("\n\n", 1)[0]


def test_shadow_hook_sits_outside_the_drafts_try_block():
    from tests.sproc_text import REPO_ROOT

    tree = ast.parse((REPO_ROOT / "entities" / "review" / "business" / "service.py").read_text(encoding="utf-8"))

    def calls_in(nodes, attr):
        return [
            n
            for stmt in nodes
            for n in ast.walk(stmt)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == attr
        ]

    drafts_tries = [
        n for n in ast.walk(tree) if isinstance(n, ast.Try) and calls_in(n.body, "enqueue_drafts")
    ]
    assert len(drafts_tries) == 1
    drafts_try = drafts_tries[0]
    shadow_calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "enqueue_for_contract_labor"
    ]
    assert len(shadow_calls) == 1
    shadow_line = shadow_calls[0].lineno
    assert shadow_line > drafts_try.end_lineno, "shadow enqueue must run after the drafts try block, not inside it"
