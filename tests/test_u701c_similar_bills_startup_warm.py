"""U-701c: the similar-bills corpus is built at app startup, not on the first bill drop.

Live drop 2026-10-29 (BillIntakePrediction 158): the first drop on a freshly restarted
worker found a cold snapshot and coded with zero similar-bills neighbours.
"""
import ast
import pathlib

from entities.coding_suggestion.business import similar_bills_service as sbs
from entities.coding_suggestion.business.similar_bills_service import SimilarBillsService


def test_warm_at_startup_schedules_a_refresh_for_company_one(monkeypatch):
    SimilarBillsService._reset_shared_for_tests()
    calls: list[int] = []
    monkeypatch.setattr(
        SimilarBillsService,
        "_schedule_refresh_if_needed",
        lambda self, force=False: calls.append(self._company_id),
    )
    try:
        SimilarBillsService.warm_at_startup()
        assert calls == [1]
        warmed = SimilarBillsService._instances[1]
        # The drop path passes its own settings; an existing instance ignores them, so
        # the drop reuses the instance that is already building.
        assert SimilarBillsService.for_company(1, settings=object()) is warmed
    finally:
        SimilarBillsService._reset_shared_for_tests()


def test_warm_at_startup_never_raises(monkeypatch, caplog):
    SimilarBillsService._reset_shared_for_tests()

    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(SimilarBillsService, "for_company", classmethod(lambda cls, cid, **k: boom()))
    try:
        SimilarBillsService.warm_at_startup(company_ids=(1, 2))
    finally:
        SimilarBillsService._reset_shared_for_tests()
    assert "similar_bills.startup_warm_failed err=RuntimeError" in caplog.text


def test_warm_at_startup_does_not_block_on_the_build(monkeypatch):
    """It only kicks the background thread; it never runs the refresh inline."""
    SimilarBillsService._reset_shared_for_tests()
    inline: list[bool] = []
    kicked: list[bool] = []
    monkeypatch.setattr(sbs, "feature_enabled", lambda settings=None: True)
    monkeypatch.setattr(
        SimilarBillsService, "_refresh_incremental_sync", lambda self: inline.append(True)
    )
    monkeypatch.setattr(
        SimilarBillsService, "_kick_background_refresh", lambda self: kicked.append(True)
    )
    try:
        SimilarBillsService.warm_at_startup()
    finally:
        SimilarBillsService._reset_shared_for_tests()
    assert kicked == [True]
    assert inline == []


def test_app_startup_calls_warm_at_startup():
    src = pathlib.Path(__file__).resolve().parents[1].joinpath("app.py").read_text()
    tree = ast.parse(src)
    startup = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "startup_event"
    )
    called = {
        (c.func.value.id, c.func.attr)
        for c in ast.walk(startup)
        if isinstance(c, ast.Call)
        and isinstance(c.func, ast.Attribute)
        and isinstance(c.func.value, ast.Name)
    }
    assert ("SimilarBillsService", "warm_at_startup") in called


def test_warm_at_startup_starts_nothing_when_the_feature_is_disabled(monkeypatch):
    SimilarBillsService._reset_shared_for_tests()
    kicked: list[bool] = []
    monkeypatch.setattr(sbs, "feature_enabled", lambda settings=None: False)
    monkeypatch.setattr(
        SimilarBillsService, "_kick_background_refresh", lambda self: kicked.append(True)
    )
    try:
        SimilarBillsService.warm_at_startup()
    finally:
        SimilarBillsService._reset_shared_for_tests()
    assert kicked == []
