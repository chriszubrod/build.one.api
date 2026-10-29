"""U-745 - live serving of the U-698 M0 decision model (no LLM, pure Python).

M0 runs beside the FILTERED similar-bills vote on every ladder V2 decision. It reuses the
backtest's own feature code (``build_context`` + ``m0_features`` + ``LogisticModel`` +
``rank_candidates``); this module only adapts live inputs to it:

- priors: the vendor's labelled bills strictly before the query (``ReadVendorPriorBillsForDecisionModel``
  rows as :class:`PriorBill`), plus a company-wide per-label count for ``base_rate``;
- neighbours: the SAME scoped, catalog-filtered list ``filtered_vote`` votes over, mapped to
  :class:`NeighbourEvidence`;
- ``choosable``: the same catalog rules ``apply_similar_evidence`` applies to neighbours.

Owner rules (Gate 1, 2026-10-29): M0 abstains whenever the filtered vote has no neighbours
(never prefill from weak history alone); no confidence bar; it never sets a project; any
failure -> no pick (the caller logs the exception type). The stored confidence is the
CALIBRATED probability (isotonic ``m0_calibration``); candidates rank on the RAW probability.

No DB I/O here; the only file read is the shipped model JSON (loaded once, cached).
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from entities.coding_suggestion.business.decision_model.features import (
    M0_FEATURES,
    NO_LLM,
    AsOfIndex,
    BillContext,
    NeighbourEvidence,
    PriorBill,
    QueryBill,
    build_context,
    m0_features,
)
from entities.coding_suggestion.business.decision_model.learn import (
    Decision,
    IsotonicMap,
    LogisticModel,
    score_candidates,
)
from entities.coding_suggestion.business.similar_bills import scc_drop_reason

logger = logging.getLogger(__name__)

MODEL_PATH = Path(__file__).with_name("m0_model.json")
CONFIDENCE_QUANTUM = Decimal("0.0001")  # DECIMAL(5,4), as the Similar*Confidence columns
LABEL_COUNTS_TTL_SECONDS = 3600.0
# THE M0 read bound: the read timeout, the tick's wait for the reads and the label-count
# follower wait all derive from it.
M0_READ_BOUND_SECONDS = 5.0
LABEL_COUNTS_FOLLOWER_WAIT_SECONDS = M0_READ_BOUND_SECONDS


class M0ModelError(ValueError):
    """The shipped model file is unusable (missing keys, feature list mismatch)."""


@dataclass(frozen=True)
class M0Model:
    model: LogisticModel
    calibration: IsotonicMap
    model_version: str
    trained_through: str
    data_hash: str


def load_m0_model(
    path: Path = MODEL_PATH,
    *,
    expected_features: Sequence[str] = M0_FEATURES,
) -> M0Model:
    """Parse the model JSON. The stored feature names must equal ``expected_features``
    exactly (names AND order), else :class:`M0ModelError` - fail closed."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    m0 = data["m0"]
    names = tuple(data["features"])
    if names != tuple(expected_features) or tuple(m0["features"]) != tuple(expected_features):
        raise M0ModelError("m0 feature list does not match M0_FEATURES")
    weights = [float(w) for w in m0["weights"]]
    scales = [float(s) for s in m0["scales"]]
    if len(weights) != len(names) or len(scales) != len(names):
        raise M0ModelError("m0 weights/scales length mismatch")
    cal = data["m0_calibration"]
    if cal.get("kind") != "isotonic":
        raise M0ModelError("m0 calibration is not isotonic")
    upper = [float(u) for u in cal["upper"]]
    values = [float(v) for v in cal["values"]]
    if len(upper) != len(values):
        raise M0ModelError("m0 calibration length mismatch")
    return M0Model(
        model=LogisticModel(
            names=names,
            weights=weights,
            bias=float(m0["bias"]),
            scales=scales,
            l2=float(m0.get("l2", 0.0)),
            converged=bool(m0.get("converged", False)),
        ),
        calibration=IsotonicMap(upper=upper, values=values),
        model_version=str(data["model_version"]),
        trained_through=str(data["trained_through"]),
        data_hash=str(data["data_hash"]),
    )


_model_lock = threading.Lock()
_model_loaded = False
_model_cached: Optional[M0Model] = None


def get_m0_model() -> Optional[M0Model]:
    """The shipped model, loaded once per process. A load failure disables M0 for the
    process (logged once, exception type only)."""
    global _model_loaded, _model_cached
    if _model_loaded:
        return _model_cached
    with _model_lock:
        if not _model_loaded:
            try:
                _model_cached = load_m0_model()
            except Exception as error:
                logger.warning("decision_model.m0_disabled err=%s", type(error).__name__)
                _model_cached = None
            _model_loaded = True
    return _model_cached


def reset_m0_model_cache_for_tests() -> None:
    global _model_loaded, _model_cached
    with _model_lock:
        _model_loaded = False
        _model_cached = None


# ---------------------------------------------------------------------------------------------
# Live inputs
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class M0Priors:
    """The two DB reads M0 needs: the vendor's strictly-earlier labelled bills and the
    company-wide per-label bill counts (the base-rate numerators)."""

    vendor_priors: tuple[PriorBill, ...]
    label_counts: Mapping[int, int]


class _LiveIndex(AsOfIndex):
    """``AsOfIndex`` over the vendor's priors; ``base_rate`` from the cached company-wide
    label counts (training: share of ALL strictly-earlier labelled bills). The vendor
    lookups keep AsOfIndex's own strictly-earlier / self-exclusion guard."""

    def __init__(self, priors: M0Priors) -> None:
        super().__init__(priors.vendor_priors)
        self._label_counts = {int(k): int(v) for k, v in priors.label_counts.items()}
        self._label_total = sum(self._label_counts.values())

    def base_rate(self, scc_id: int, *, as_of: datetime) -> float:
        if self._label_total <= 0:
            return 0.0
        return self._label_counts.get(int(scc_id), 0) / self._label_total


def neighbour_evidence(neighbours: Sequence[Any]) -> list[NeighbourEvidence]:
    """Live similar_bills.Neighbour -> NeighbourEvidence (order kept)."""
    out = []
    for n in neighbours:
        prior = n.prior.enriched.bill
        out.append(
            NeighbourEvidence(
                combined=float(n.combined),
                match=float(n.match),
                scc_id=int(n.scc_id),
                same_vendor=bool(n.same_vendor),
                prior_bill_id=int(prior.bill_id),
                prior_created=prior.created,
            )
        )
    return out


def catalog_choosable(
    *,
    choosable_nums: frozenset[str],
    scc_by_id: Mapping[int, Any],
    ambiguous_numbers: frozenset[str],
) -> Callable[[int], bool]:
    """M0's ``choosable``: exactly the rule ``apply_similar_evidence`` filters neighbours
    with (``similar_bills.scc_drop_reason``), over ``choosable_scc_numbers(as_of_catalog)``
    (the as-of catalog already excludes holding codes)."""

    def ok(scc_id: int) -> bool:
        return (
            scc_drop_reason(
                scc_id,
                choosable_nums=choosable_nums,
                scc_by_id=scc_by_id,
                ambiguous_numbers=ambiguous_numbers,
            )
            is None
        )

    return ok


@dataclass(frozen=True)
class M0Pick:
    scc_id: int
    confidence: Decimal  # calibrated, quantized to DECIMAL(5,4)


def m0_context(
    priors: M0Priors,
    query: QueryBill,
    *,
    mode: str,
    neighbours: Sequence[NeighbourEvidence],
    choosable: Callable[[int], bool],
) -> BillContext:
    """The backtest's ``build_context`` over the live inputs (M0 = no LLM). Raises
    LeakageError on a neighbour that is not strictly earlier than the query."""
    return build_context(
        _LiveIndex(priors),
        query,
        mode=mode,
        neighbours=neighbours,
        choosable=choosable,
        llm=NO_LLM,
    )


def m0_score(model: M0Model, ctx: BillContext) -> Decision:
    """The decision (ranked on RAW probability, ``prob`` calibrated) via the one scorer
    the backtest's ``score_row`` also uses."""
    decision, _probs = score_candidates(
        model.model, ctx.candidates, lambda c: m0_features(ctx, c), calibrate=model.calibration
    )
    return decision


def m0_pick(
    model: M0Model,
    priors: M0Priors,
    query: QueryBill,
    *,
    mode: str,
    neighbours: Sequence[NeighbourEvidence],
    choosable: Callable[[int], bool],
) -> Optional[M0Pick]:
    """M0's pick, or None. ``neighbours`` must be the filtered vote's list: none -> abstain
    (owner rule: never prefill from weak history alone). The caller treats any exception
    (LeakageError included) as no pick."""
    if not neighbours:
        return None
    ctx = m0_context(priors, query, mode=mode, neighbours=neighbours, choosable=choosable)
    if not ctx.neighbours or not ctx.candidates:
        return None
    decision = m0_score(model, ctx)
    if decision.scc_id is None:
        return None
    confidence = Decimal(str(decision.prob)).quantize(CONFIDENCE_QUANTUM)
    return M0Pick(int(decision.scc_id), confidence)


# ---------------------------------------------------------------------------------------------
# Company-wide label counts: in-process TTL cache (best-effort, thread-safe)
# ---------------------------------------------------------------------------------------------


class _Flight:
    __slots__ = ("done", "counts", "error")

    def __init__(self) -> None:
        self.done = threading.Event()
        self.counts: Optional[dict[int, int]] = None
        self.error: Optional[BaseException] = None


class LabelCountCache:
    """Per-company counts with a TTL and a SINGLE-FLIGHT cold load: one loader call per
    company at a time; concurrent callers wait (at most ``follower_wait_seconds``) for that
    load's result instead of starting their own company-wide scan."""

    def __init__(
        self,
        *,
        ttl_seconds: float = LABEL_COUNTS_TTL_SECONDS,
        now: Callable[[], float] = time.monotonic,
        follower_wait_seconds: float = LABEL_COUNTS_FOLLOWER_WAIT_SECONDS,
    ) -> None:
        self._ttl = ttl_seconds
        self._now = now
        self._follower_wait = follower_wait_seconds
        self._lock = threading.Lock()
        self._entries: dict[int, tuple[float, dict[int, int]]] = {}
        self._inflight: dict[int, _Flight] = {}

    def get(self, company_id: int, loader: Callable[[int], Mapping[int, int]]) -> dict[int, int]:
        """Cached counts for ``company_id``. A miss/expiry makes the first caller the
        loader (outside the lock); concurrent callers wait for it. A loader error
        propagates to the leader and its followers (the caller treats it as an M0
        failure); a follower that waits too long gets TimeoutError. An expired entry is
        never served after a failed reload."""
        key = int(company_id)
        with self._lock:
            hit = self._entries.get(key)
            if hit is not None and self._now() - hit[0] < self._ttl:
                return hit[1]
            flight = self._inflight.get(key)
            leader = flight is None
            if leader:
                flight = _Flight()
                self._inflight[key] = flight
        if not leader:
            if not flight.done.wait(self._follower_wait):
                raise TimeoutError("label counts load still in flight")
            if flight.error is not None:
                raise flight.error
            return dict(flight.counts or {})
        try:
            counts = {int(k): int(v) for k, v in loader(key).items()}
            flight.counts = counts
            with self._lock:
                self._entries[key] = (self._now(), counts)
            return counts
        except BaseException as error:
            flight.error = error
            raise
        finally:
            with self._lock:
                self._inflight.pop(key, None)
            flight.done.set()

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


LABEL_COUNTS = LabelCountCache()
