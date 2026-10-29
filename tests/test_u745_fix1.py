"""U-745: M0 reads off the critical path (drop never waits; tick bounded)."""
from __future__ import annotations

import asyncio
import dataclasses
import time
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


from entities.coding_suggestion.business import bill_coding_decision as bcd
from entities.coding_suggestion.business import bill_text_suggestion_service as bts
from entities.coding_suggestion.business.decision_model.features import PriorBill
from intelligence.cascade.core import CascadeResult
from tests import test_u701b3_bill_coding_decision as b3
from tests.u745_fixtures import FakePriorsRepo, _fresh_caches  # noqa: F401 (autouse fixture)
from tests.u745_fixtures import nb as _nb


NEIGHBOURS = [_nb(10, 2, 0.8), _nb(11, 1, 0.4)]
PRIORS = [
    PriorBill(900 + i, 5, b3.AS_OF - timedelta(days=30 - i), 1, 100, 120.0, has_pdf=True)
    for i in range(6)
]


def _gated(*, hold: float, fail: bool = False) -> FakePriorsRepo:
    """M0 reads that block until the repo's ``release`` is set (or ``hold`` seconds pass)."""
    return FakePriorsRepo(priors=PRIORS, counts={1: 6, 2: 3}, hold=hold,
                          prior_error=RuntimeError("no") if fail else None)


def _run(stage: str, repo, cascade, *, chosen_project_id=None, history=None, timing=None):
    settings = SimpleNamespace(bill_coding_candidate_sccs="full", bill_text_token_hash_key="k" * 40)
    repository = MagicMock()
    repository.read_vendor_scc_counts.return_value = []

    async def go():
        started = time.monotonic()
        d = await bts.run_bill_coding_decision(
            stage=stage, bill_text="invoice body text", vendor_id=5, vendor_name="Vendor",
            chosen_project_id=chosen_project_id, amount=Decimal("120.00"), as_of=b3.AS_OF,
            exclude_bill_id=77, company_id=1, actor_user_id=42, accessible_project_ids=None,
            settings=settings, repository=repository,
            suggester=MagicMock(suggest=MagicMock(return_value=history)),
            catalog=b3._snapshot(), similar_service=b3.FakeSimilar(NEIGHBOURS),
            m0_repository=repo,
        )
        if timing is not None:
            timing["elapsed"] = time.monotonic() - started
        repo.release.set()  # let a still-blocked read finish so the loop closes fast
        return d

    with patch.object(bcd, "_default_run_cascade", cascade):
        return asyncio.run(go())


def _empty_cascade(seen=None, release=None, repo=None):
    async def cascade(_payload, _ladder, _transport):
        if seen is not None:
            seen["m0_read_done_at_cascade_start"] = repo.finished.is_set()
        if release is not None:
            release.set()
        return CascadeResult(False, True, None, None, None, [])

    return cascade


def test_slow_m0_read_does_not_delay_the_cascade_start():
    repo = _gated(hold=2.0)
    release = repo.release
    seen: dict = {}
    d = _run("tick_full", repo, _empty_cascade(seen, release, repo))
    # The cascade started while the M0 read was still blocked; it then released it, and
    # the tick (5 s bound) still got its M0 pick.
    assert seen["m0_read_done_at_cascade_start"] is False
    assert d.m0_scc_id is not None


def _sleepy_cascade(seconds: float):
    async def cascade(_payload, _ladder, _transport):
        await asyncio.sleep(seconds)
        return CascadeResult(False, True, None, None, None, [])

    return cascade


def test_drop_never_waits_for_m0_and_equals_the_vote_only_decision():
    vote_only = _run("drop", _gated(hold=0, fail=True),
                     _empty_cascade())
    timing: dict = {}
    slow = _run("drop", _gated(hold=3.0),
                _sleepy_cascade(0.05), timing=timing)
    assert (slow.m0_scc_id, slow.m0_confidence) == (None, None)
    assert dataclasses.replace(slow, cascades=()) == dataclasses.replace(vote_only, cascades=())
    assert slow.source == "similar_vote" and slow.similar_vote_scc_id == 2
    # Zero added wait: the whole decision is the fake cascade's 0.05 s plus overhead.
    assert timing["elapsed"] < 0.05 + 0.1


def test_drop_uses_m0_when_its_reads_already_finished():
    d = _run("drop", _gated(hold=0), _sleepy_cascade(0.3))
    assert d.m0_scc_id is not None and isinstance(d.m0_confidence, Decimal)


def test_drop_history_short_circuit_never_waits_for_m0():
    timing: dict = {}
    d = _run("drop", _gated(hold=3.0), _empty_cascade(),
             chosen_project_id=100, history=b3._hist(1), timing=timing)
    assert d.source == "history" and d.m0_scc_id is None
    assert timing["elapsed"] < 0.1
