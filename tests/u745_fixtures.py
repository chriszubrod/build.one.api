"""Shared U-745 test helpers: cache reset, a live neighbour with a created time, one fake
priors repository (configurable: rows, counts, errors, a blocking read)."""
from __future__ import annotations

import threading
from datetime import datetime, timedelta
from typing import Optional

import pytest

from entities.coding_suggestion.business.decision_model import serve
from tests import test_u701b3_bill_coding_decision as b3


@pytest.fixture(autouse=True)
def _fresh_caches():
    serve.reset_m0_model_cache_for_tests()
    serve.LABEL_COUNTS.clear()
    yield
    serve.reset_m0_model_cache_for_tests()
    serve.LABEL_COUNTS.clear()


def nb(bill_id: int, scc_id: int, combined: float = 0.9, created: Optional[datetime] = None):
    """A live similar_bills.Neighbour whose prior was created before b3.AS_OF (or ``created``)."""
    n = b3._neighbour(bill_id, scc_id, combined)
    n.prior.enriched.bill.created = created or (b3.AS_OF - timedelta(days=3))
    return n


class FakePriorsRepo:
    """DecisionModelPriorsRepository stand-in. ``hold`` > 0 blocks the prior read until
    ``release`` is set (or ``hold`` seconds pass); ``finished`` is set when it returns."""

    def __init__(
        self,
        *,
        priors=(),
        counts=None,
        prior_error: Optional[BaseException] = None,
        count_error: Optional[BaseException] = None,
        hold: float = 0.0,
        release: Optional[threading.Event] = None,
    ):
        self.priors = list(priors)
        self.counts = counts if counts is not None else {1: 3}
        self.prior_error, self.count_error = prior_error, count_error
        self.hold = hold
        self.release = release or threading.Event()
        self.finished = threading.Event()
        self.calls: list[dict] = []
        self.count_calls = 0

    def read_vendor_prior_bills(self, **kw):
        self.calls.append(kw)
        try:
            if self.hold:
                self.release.wait(self.hold)
            if self.prior_error:
                raise self.prior_error
            return list(self.priors)
        finally:
            self.finished.set()

    def read_label_counts(self, _company_id):
        self.count_calls += 1
        if self.count_error:
            raise self.count_error
        return dict(self.counts)
