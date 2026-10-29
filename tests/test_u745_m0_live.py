"""U-745: M0 model file, abstain rule, choose_final switch, failure isolation, storage, scorecard."""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import threading
import time
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import config
from entities.bill_intake_prediction.business import coding_scorecard as sc
from entities.bill_intake_prediction.business.service import (
    BillIntakePredictionService,
    _intake_decision_params,
)
from entities.coding_suggestion.business import bill_text_suggestion_service as bts
from entities.coding_suggestion.business.bill_coding_decision import (
    RungAnswer,
    choose_final,
    decide_bill_coding,
    m0_shadow_pick,
    neighbour_lookup_possible,
)
from entities.coding_suggestion.business.bill_suggestion_service import (
    SIMILAR_BILLS_SUGGESTION_REASON,
    SUGGESTION_SOURCE_SIMILAR_BILLS,
    _apply_ladder_v2_presentation,
)
from entities.coding_suggestion.business.decision_model import serve
from entities.coding_suggestion.business.decision_model.features import M0_FEATURES, PriorBill
from tests import test_u701b3_bill_coding_decision as b3
from tests import test_u745_m0_parity as par
from tests.u745_fixtures import FakePriorsRepo, _fresh_caches  # noqa: F401 (autouse fixture)
from tests.u745_fixtures import nb as _nb

MODEL_JSON = serve.MODEL_PATH




# ---------------------------------------------------------------- 2: model file


def test_shipped_model_loads_and_matches_the_trained_weights():
    from pathlib import Path

    m = serve.load_m0_model()
    assert m.model.names == M0_FEATURES
    src = json.loads(
        (Path(__file__).resolve().parents[1] / "docs/design/u698/u698_model_20261029T185458Z.json")
        .read_text(encoding="utf-8")
    )
    assert m.model.weights == src["m0"]["weights"]
    assert m.model.scales == src["m0"]["scales"]
    assert m.model.bias == src["m0"]["bias"]
    assert (m.calibration.upper, m.calibration.values) == (
        src["m0_calibration"]["upper"], src["m0_calibration"]["values"]
    )
    assert (m.model_version, m.trained_through, m.data_hash) == (
        src["model_version"], src["trained_through"], src["data_hash"]
    )
    assert serve.get_m0_model() is not None


def test_known_feature_vector_gives_the_pinned_probability():
    m = serve.load_m0_model()
    x = [1, 0, 0, 0.5, 1.6094379124341003, 0.3, 0.6, 0, 1.2, 0.7, 0.9, 0.8, 0.2, 1.0, 1, 0.5,
         0.9, 1, 2, 0, 0, 4.7, -0.1, 1, 1, 3.9, 0.4, 0, 0]
    assert m.model.prob(x) == pytest.approx(0.96724814031175, abs=1e-12)
    assert m.calibration(m.model.prob(x)) == 0.968504
    assert m.model.prob([0.0] * len(M0_FEATURES)) == pytest.approx(0.004527412511094305, abs=1e-12)


def _write_model(tmp_path, mutate):
    data = json.loads(MODEL_JSON.read_text(encoding="utf-8"))
    mutate(data)
    path = tmp_path / "m0_model.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _swap_two(data):
    f = list(data["features"])
    f[0], f[1] = f[1], f[0]
    data["features"] = f


def test_mismatched_feature_list_disables_m0(tmp_path, caplog):
    path = _write_model(tmp_path, _swap_two)
    with pytest.raises(serve.M0ModelError):
        serve.load_m0_model(path)
    with pytest.raises(serve.M0ModelError):
        serve.load_m0_model(MODEL_JSON, expected_features=M0_FEATURES[:-1])
    with patch.object(serve, "MODEL_PATH", path), patch.object(
        serve.load_m0_model, "__defaults__", (path,)
    ), caplog.at_level(logging.WARNING):
        assert serve.get_m0_model() is None
        assert serve.get_m0_model() is None  # cached: logged once
    msgs = [r.getMessage() for r in caplog.records if "m0_disabled" in r.getMessage()]
    assert msgs == ["decision_model.m0_disabled err=M0ModelError"]


# ---------------------------------------------------------------- shared live fixtures


def _priors(n: int = 5, scc: int = 1, *, start: datetime = b3.AS_OF - timedelta(days=30)) -> serve.M0Priors:
    bills = tuple(
        PriorBill(900 + i, 5, start + timedelta(days=i), scc, 100, 120.0, has_pdf=True)
        for i in range(n)
    )
    return serve.M0Priors(vendor_priors=bills, label_counts={scc: n, 2: 3})


def _decide(inputs, deps):
    return asyncio.run(decide_bill_coding(inputs, deps))


def _strip_m0(decision):
    return dataclasses.replace(decision, m0_scc_id=None, m0_confidence=None)


# ---------------------------------------------------------------- 3: abstain


def test_no_neighbours_means_no_m0_pick_even_with_history():
    m = serve.load_m0_model()
    q = par.F.QueryBill(77, 5, b3.AS_OF, 120.0, 100, True, 30)
    assert serve.m0_pick(m, _priors(8), q, mode="A", neighbours=[], choosable=lambda _c: True) is None

    transport = b3.FakeTransport({b3.HAIKU: b3._err("http_500")})
    deps = b3._deps(transport, similar_service=b3.FakeSimilar([]), m0_priors=_priors(8), m0_model=m)
    d = _decide(b3._inputs(), deps)
    assert (d.m0_scc_id, d.m0_confidence, d.similar_vote_scc_id) == (None, None, None)


def test_neighbours_all_filtered_means_no_m0_pick():
    m = serve.load_m0_model()
    transport = b3.FakeTransport({b3.HAIKU: b3._err("http_500")})
    # 9 = Suspense: the vote filter drops it, so the vote is silent and so is M0.
    deps = b3._deps(transport, similar_service=b3.FakeSimilar([_nb(10, 9)]), m0_priors=_priors(8),
                    m0_model=m)
    d = _decide(b3._inputs(), deps)
    assert d.similar_vote_scc_id is None and d.m0_scc_id is None


def test_m0_answers_where_the_vote_answers():
    m = serve.load_m0_model()
    transport = b3.FakeTransport({b3.HAIKU: b3._err("http_500")})
    deps = b3._deps(transport, similar_service=b3.FakeSimilar([_nb(10, 2, 0.8), _nb(11, 1, 0.4)]),
                    m0_priors=_priors(8), m0_model=m)
    d = _decide(b3._inputs(), deps)
    assert d.similar_vote_scc_id == 2
    assert d.m0_scc_id in (1, 2) and isinstance(d.m0_confidence, Decimal)
    assert d.m0_confidence == d.m0_confidence.quantize(Decimal("0.0001"))
    assert d.source == "similar_vote" and d.project_id is None  # switch off: shadow only


# ---------------------------------------------------------------- 4: choose_final


def _ra(scc, conf="0.80", *, project=None, error=None):
    return RungAnswer("m", scc, project, Decimal(conf), error)


LADDER_CASES = [
    # mode, history, primary, escalate, fallback, vote
    ("A", b3._hist(1), _ra(2), None, None, 3),
    ("A", None, _ra(2, "0.50"), _ra(3, "0.90"), None, None),
    ("A", None, _ra(2, "0.40"), None, None, 3),
    ("A", None, _ra(None, error="http_500"), None, _ra(3, "0.55"), 7),
    ("A", None, _ra(None, error="http_500"), None, _ra(3, "0.45"), 7),
    ("A", None, None, None, None, None),
    ("A", b3._hist(1, strong=False), None, None, None, 7),
    ("B", b3._hist(1, project_id=900), _ra(2, "0.9", project=501), None, None, 7),
    ("B", b3._hist(1, project_id=900), _ra(None, error="http_500"), None, None, 7),
    ("B", b3._hist(1, strong=False), _ra(None, error="http_500"), None, None, 7),
    ("B", None, _ra(2, "0.9"), None, None, 7),
    ("B", None, None, None, None, None),
]


def _cf(case, **m0):
    mode, history, primary, escalate, fallback, vote = case
    return choose_final(
        mode=mode, history=history, primary=primary, escalate=escalate, fallback=fallback,
        similar_vote_scc_id=vote, similar_vote_confidence=Decimal("0.6") if vote else None,
        history_for_project=lambda _p: b3._hist(3, strong=False, project_id=501), **m0,
    )


@pytest.mark.parametrize("case", LADDER_CASES)
def test_choose_final_flag_off_is_todays_ladder(case):
    today = _cf(case)
    assert _cf(case, m0_scc_id=7, m0_confidence=Decimal("0.9")) == today
    assert _cf(case, m0_scc_id=7, m0_confidence=Decimal("0.9"), m0_rung_enabled=False) == today


@pytest.mark.parametrize("case", LADDER_CASES)
def test_choose_final_flag_on_replaces_only_the_vote_rung(case):
    today = _cf(case)
    on = _cf(case, m0_scc_id=3, m0_confidence=Decimal("0.4321"), m0_rung_enabled=True)
    if today[0] in ("similar_vote", "none"):
        assert on == ("m0", 3, None, Decimal("0.4321"), None)  # never a project
    else:
        assert on == today  # history / primary / escalate / fallback untouched
    # M0 silent with the flag on -> the vote (fallthrough kept for safety).
    assert _cf(case, m0_scc_id=None, m0_confidence=None, m0_rung_enabled=True) == today


def test_decide_with_switch_on_uses_m0_at_the_last_rung():
    m = serve.load_m0_model()
    transport = b3.FakeTransport({b3.HAIKU: b3._err("http_500")})
    nbs = [_nb(10, 2, 0.8), _nb(11, 1, 0.4)]
    off = _decide(b3._inputs(chosen_project_id=None),
                  b3._deps(transport, similar_service=b3.FakeSimilar(nbs), m0_priors=_priors(8),
                           m0_model=m))
    on = _decide(b3._inputs(chosen_project_id=None),
                 b3._deps(b3.FakeTransport({b3.HAIKU: b3._err("http_500")}),
                          similar_service=b3.FakeSimilar(nbs), m0_priors=_priors(8), m0_model=m,
                          m0_rung_enabled=True))
    assert off.source == "similar_vote"
    assert (on.source, on.sub_cost_code_id, on.project_id, on.confidence) == (
        "m0", off.m0_scc_id, None, off.m0_confidence
    )


def test_settings_flag_defaults_off_and_reads_env(monkeypatch):
    field = config.Settings.model_fields["bill_coding_m0_rung_enabled"]
    assert field.default is False
    assert bts.m0_rung_enabled(SimpleNamespace()) is False
    assert bts.m0_rung_enabled(SimpleNamespace(bill_coding_m0_rung_enabled="true")) is False
    assert bts.m0_rung_enabled(SimpleNamespace(bill_coding_m0_rung_enabled=True)) is True
    monkeypatch.setenv("BILL_CODING_M0_RUNG_ENABLED", "true")
    assert config.Settings().bill_coding_m0_rung_enabled is True


# ---------------------------------------------------------------- 5: failure isolation


def _warnings(caplog, needle):
    return [r.getMessage() for r in caplog.records if needle in r.getMessage()]


def _baseline_and_with(deps_kw, caplog, inputs_kw=None):
    nbs = [_nb(10, 2, 0.8), _nb(11, 1, 0.4)]
    inputs = b3._inputs(**(inputs_kw or {}))
    today = _decide(inputs, b3._deps(b3.FakeTransport({b3.HAIKU: b3._ok("44.20", 0.5)}),
                                     similar_service=b3.FakeSimilar(nbs)))
    with caplog.at_level(logging.WARNING):
        got = _decide(inputs, b3._deps(b3.FakeTransport({b3.HAIKU: b3._ok("44.20", 0.5)}),
                                       similar_service=b3.FakeSimilar(nbs), **deps_kw))
    return today, got


def _strip_cascades(d):
    return dataclasses.replace(d, cascades=())


def test_leakage_error_means_no_pick_and_todays_decision(caplog):
    later = b3.AS_OF + timedelta(seconds=1)
    nbs = [_nb(10, 2, 0.8, created=later)]
    inputs = b3._inputs()
    today = _decide(inputs, b3._deps(b3.FakeTransport({b3.HAIKU: b3._ok("44.20", 0.5)}),
                                     similar_service=b3.FakeSimilar(nbs)))
    with caplog.at_level(logging.WARNING):
        got = _decide(inputs, b3._deps(b3.FakeTransport({b3.HAIKU: b3._ok("44.20", 0.5)}),
                                       similar_service=b3.FakeSimilar(nbs), m0_priors=_priors(),
                                       m0_model=serve.load_m0_model(), m0_rung_enabled=True))
    assert got.m0_scc_id is None
    assert _strip_cascades(got) == _strip_cascades(today)
    assert _warnings(caplog, "m0_failed") == ["bill_coding_decision.m0_failed err=LeakageError"]


def test_corrupt_model_file_means_no_pick(tmp_path, caplog):
    bad = tmp_path / "m0_model.json"
    bad.write_text("{not json", encoding="utf-8")
    with patch.object(serve.load_m0_model, "__defaults__", (bad,)):
        today, got = _baseline_and_with({"m0_priors": _priors()}, caplog)
    assert got.m0_scc_id is None
    assert _strip_cascades(got) == _strip_cascades(today)
    assert _warnings(caplog, "m0_disabled") == ["decision_model.m0_disabled err=JSONDecodeError"]


def test_missing_model_file_means_no_pick(tmp_path, caplog):
    with patch.object(serve.load_m0_model, "__defaults__", (tmp_path / "absent.json",)):
        today, got = _baseline_and_with({"m0_priors": _priors()}, caplog)
    assert got.m0_scc_id is None and _strip_cascades(got) == _strip_cascades(today)
    assert _warnings(caplog, "m0_disabled") == ["decision_model.m0_disabled err=FileNotFoundError"]


def test_m0_scoring_error_is_isolated(caplog):
    with patch.object(serve, "m0_score", side_effect=ZeroDivisionError("secret")):
        today, got = _baseline_and_with({"m0_priors": _priors(), "m0_rung_enabled": True}, caplog)
    assert got.m0_scc_id is None and _strip_cascades(got) == _strip_cascades(today)
    assert _warnings(caplog, "m0_failed") == ["bill_coding_decision.m0_failed err=ZeroDivisionError"]
    assert not any("secret" in r.getMessage() for r in caplog.records)


def _read(repo, **kw):
    base = dict(company_id=1, vendor_id=5, as_of=b3.AS_OF, exclude_bill_id=77,
                settings=SimpleNamespace(bill_text_token_hash_key="k" * 40))
    base.update(kw)
    return asyncio.run(bts._read_m0_priors(repo, **base))


@pytest.mark.parametrize(
    "repo,err",
    [
        (FakePriorsRepo(prior_error=RuntimeError("db said secret")), "RuntimeError"),
        (FakePriorsRepo(count_error=ConnectionError("db said secret")), "ConnectionError"),
    ],
)
def test_raising_reads_mean_no_priors(repo, err, caplog):
    with caplog.at_level(logging.WARNING):
        assert _read(repo) is None
    assert _warnings(caplog, "m0_priors_failed") == [f"bill_coding_decision.m0_priors_failed err={err}"]
    assert not any("secret" in r.getMessage() for r in caplog.records)


def test_slow_read_times_out(caplog, monkeypatch):
    monkeypatch.setattr(bts, "M0_READ_TIMEOUT_SECONDS", 0.05)
    with caplog.at_level(logging.WARNING):
        assert _read(FakePriorsRepo(hold=0.5)) is None
    assert _warnings(caplog, "m0_priors_failed") == ["bill_coding_decision.m0_priors_failed err=TimeoutError"]


def test_reads_pass_the_query_scope_and_cache_label_counts():
    prior = PriorBill(1, 5, b3.AS_OF - timedelta(days=1), 1, None, 10.0)
    repo = FakePriorsRepo(priors=[prior], counts={1: 4, 2: 1})
    got = _read(repo)
    assert got == serve.M0Priors((prior,), {1: 4, 2: 1})
    kw = repo.calls[0]
    assert (kw["company_id"], kw["vendor_id"], kw["as_of"], kw["exclude_bill_id"]) == (1, 5, b3.AS_OF, 77)
    assert kw["tokenizer_version"] is not None
    _read(repo)
    assert repo.count_calls == 1  # cached per company
    _read(repo, company_id=2)
    assert repo.count_calls == 2


@pytest.mark.parametrize(
    "overrides",
    [
        {"company_id": None},
        {"amount": None},
        {"accessible_project_ids": frozenset()},
        {"settings": SimpleNamespace(bill_coding_candidate_sccs="full", bill_text_token_hash_key=None)},
    ],
)
def test_no_reads_when_m0_cannot_answer(overrides):
    """No neighbour lookup possible -> no M0 task, no repository, no reads."""
    settings = overrides.pop(
        "settings",
        SimpleNamespace(bill_coding_candidate_sccs="full", bill_text_token_hash_key="k" * 40),
    )
    repo_cls = MagicMock()
    with patch.object(bts, "DecisionModelPriorsRepository", repo_cls), patch.object(
        bts, "_read_m0_priors"
    ) as read:
        _inputs, deps = b3._wrapper(settings, b3.FakeSimilar(), MagicMock(), **overrides)
    assert deps.m0_priors is None
    repo_cls.assert_not_called()
    read.assert_not_called()


def test_neighbour_lookup_possible_predicate():
    ok = dict(query_token_hashes=frozenset({1}), amount=Decimal("1"), company_id=1,
              accessible_project_ids=None)
    assert neighbour_lookup_possible(**ok)
    assert neighbour_lookup_possible(**{**ok, "accessible_project_ids": frozenset({5})})
    for bad in ({"query_token_hashes": frozenset()}, {"amount": None}, {"company_id": None},
                {"accessible_project_ids": frozenset()}):
        assert not neighbour_lookup_possible(**{**ok, **bad})


def test_label_count_cache_ttl_and_failed_reload():
    now = [0.0]
    cache = serve.LabelCountCache(ttl_seconds=10, now=lambda: now[0])
    assert cache.get(1, lambda _c: {1: 1}) == {1: 1}
    assert cache.get(1, lambda _c: {1: 9}) == {1: 1}
    now[0] = 11.0

    def boom(_c):
        raise RuntimeError("x")

    with pytest.raises(RuntimeError):
        cache.get(1, boom)  # an expired entry is never served after a failed reload
    assert cache.get(1, lambda _c: {1: 9}) == {1: 9}


def test_label_count_cold_load_is_single_flight():
    cache = serve.LabelCountCache()
    calls = []
    gate = threading.Barrier(8)

    def loader(company_id):
        calls.append(company_id)
        time.sleep(0.2)
        return {1: 3}

    results = []

    def worker():
        gate.wait()
        results.append(cache.get(1, loader))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert calls == [1]
    assert results == [{1: 3}] * 8


def test_label_count_single_flight_follower_sees_the_leader_error_and_is_bounded():
    cache = serve.LabelCountCache(follower_wait_seconds=0.1)
    started = threading.Event()
    release = threading.Event()

    def slow_loader(_c):
        started.set()
        release.wait(2)
        raise ConnectionError("x")

    leader_err = []
    t = threading.Thread(target=lambda: _capture(leader_err, lambda: cache.get(1, slow_loader)))
    t.start()
    started.wait(1)
    with pytest.raises(TimeoutError):
        cache.get(1, lambda _c: {1: 1})  # follower: bounded wait, never a second scan
    release.set()
    t.join(3)
    assert [type(e) for e in leader_err] == [ConnectionError]
    assert cache.get(1, lambda _c: {1: 1}) == {1: 1}  # the failed flight is cleared


def _capture(sink, fn):
    try:
        fn()
    except Exception as error:  # noqa: BLE001
        sink.append(error)


def test_m0_shadow_pick_without_priors_is_silent():
    assert m0_shadow_pick(
        b3._inputs(), catalog=b3._catalog(), priors=None, model=None,
        vote_neighbours=[_nb(1, 1)], choosable_nums=frozenset({"43.10"}),
    ) == (None, None)


# ---------------------------------------------------------------- 6: storage + scorecard


def _decision(**kw):
    base = dict(m0_scc_id=3, m0_confidence=Decimal("0.7123"))
    base.update(kw)
    return b3._decision(**base)


def test_drop_intake_upsert_writes_m0_columns():
    repo = MagicMock()
    with patch("entities.coding_suggestion.business.bill_text_token_hash.feature_enabled", return_value=True):
        b3._record_read(BillIntakePredictionService(repo=repo), coding_decision=_decision())
    params = repo.upsert.call_args.kwargs
    assert (params["M0SubCostCodeId"], params["M0Confidence"]) == (3, Decimal("0.7123"))
    assert params["SimilarPredSubCostCodeId"] == 7
    assert _intake_decision_params(_decision(m0_scc_id=None, m0_confidence=None))["M0SubCostCodeId"] is None


def test_tick_complete_writes_m0_columns():
    kw = bts._decision_complete_kwargs(_decision())
    assert (kw["M0SubCostCodeId"], kw["M0Confidence"]) == (3, Decimal("0.7123"))


def test_tick_reuse_copies_the_intake_m0_pick():
    intake = b3._intake(m0_sub_cost_code_id=3, m0_confidence=Decimal("0.6"))
    kw = bts._intake_reuse_kwargs(intake)
    assert (kw["M0SubCostCodeId"], kw["M0Confidence"]) == (3, Decimal("0.6"))
    legacy = bts._intake_reuse_kwargs(b3._intake(coding_source=None, m0_sub_cost_code_id=3))
    assert "M0SubCostCodeId" not in legacy


def test_tick_end_to_end_stores_the_m0_shadow():
    similar = b3.FakeSimilar([_nb(10, 1, 0.8), _nb(11, 2, 0.4)])
    def repo():
        return FakePriorsRepo(priors=_priors(6).vendor_priors, counts={1: 6, 2: 3})

    with patch.object(bts, "DecisionModelPriorsRepository", repo):
        run = b3._run_tick(
            transport=b3.FakeTransport({b3.HAIKU: b3._ok("44.20", 0.9), b3.SONNET: b3._ok("44.20", 0.9)}),
            similar=similar,
        )
    kw = run.repo.complete.call_args.kwargs
    assert kw["status"] == "done" and kw["DecisionSource"] in ("primary", "escalate")
    assert kw["M0SubCostCodeId"] in (1, 2) and isinstance(kw["M0Confidence"], Decimal)
    assert kw["SimilarVoteSubCostCodeId"] == 1


def test_drop_presents_m0_like_the_vote():
    from entities.bill.business.read_attachment_service import _suggestion_from_decision

    scc = SimpleNamespace(public_id="p", number="44.20", name="Electrical")
    engine = MagicMock()
    engine.sub_cost_code_service.read_by_id.return_value = scc
    engine.sub_cost_code_service.is_holding_code.return_value = False
    with patch(
        "entities.bill.business.read_attachment_service.resolve_accessible_text_project_id",
        side_effect=lambda pid, **_k: pid,
    ):
        s = _suggestion_from_decision(
            SimpleNamespace(source="m0", sub_cost_code_id=2, project_id=None, mode="B",
                            confidence=Decimal("0.7"), history=None),
            chosen_project_id=None, engine=engine,
        )
    assert (s.source, s.reason, s.project_id) == (
        SUGGESTION_SOURCE_SIMILAR_BILLS, SIMILAR_BILLS_SUGGESTION_REASON, None
    )


def test_tick_presents_m0_like_the_vote():
    suggestion = SimpleNamespace(source="llm", reason="r", decision_source=None)
    _apply_ladder_v2_presentation(
        suggestion, text_row=SimpleNamespace(decision_source="m0", sub_cost_code_id=2),
        intake=lambda: None, sub_cost_code_service=MagicMock(),
    )
    assert (suggestion.source, suggestion.reason) == (
        SUGGESTION_SOURCE_SIMILAR_BILLS, SIMILAR_BILLS_SUGGESTION_REASON
    )


def _row(**kw):
    base = dict(finalized_datetime=datetime(2026, 10, 29), final_sub_cost_code_id=1,
                model_version=sc.LADDER_V2)
    base.update(kw)
    return base


def test_scorecard_m0_rung_scores_every_v2_row_and_abstains_on_null():
    rows = [
        _row(m0_sub_cost_code_id=1),
        _row(m0_sub_cost_code_id=2),
        _row(m0_sub_cost_code_id=None),
        _row(model_version="u654b-text-v1", m0_sub_cost_code_id=1),  # not V2: not scored
    ]
    m0 = sc.build_coding_scorecard(rows)["rungs"]["m0"]
    assert (m0["n"], m0["right"], m0["abstain"], m0["basis"]) == (3, 1, 1, sc.BASIS_EVERY_V2_ROW)
    assert sc.scorecard_row_from_db(SimpleNamespace(M0SubCostCodeId=5))["m0_sub_cost_code_id"] == 5
    assert "| m0 |" in sc.format_coding_scorecard_markdown(sc.build_coding_scorecard(rows))
