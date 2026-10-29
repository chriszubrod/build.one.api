"""U-673 Phase A labor coding backtest: pure-logic tests on synthetic corpora (no DB)."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from scripts import u673_labor_coding_backtest as m
from scripts.u673_labor_coding_backtest import Corpus, LaborLine, SccRef

D0 = date(2026, 9, 14)  # Monday

FRAMING, DOORS, CHANNEL, DRYWALL = 101, 102, 103, 104

_ids = iter(range(1, 100000))


def ln(
    day: date,
    scc: int,
    note: str | None,
    *,
    project: int = 7,
    vendor: int = 50,
    hours: str = "8",
    te: int | None = -1,
    cl: int | None = None,
    desc: str | None = None,
    coded_at: datetime | None = None,
    cl_te: int | None = None,
) -> LaborLine:
    lid = next(_ids)
    return LaborLine(
        line_id=lid,
        contract_labor_id=cl if cl is not None else lid,
        line_date=day,
        project_id=project,
        vendor_id=vendor,
        label_scc_id=scc,
        hours=Decimal(hours),
        source_time_entry_id=(lid if te == -1 else te),
        coded_at=coded_at,
        cl_source_time_entry_id=cl_te,
        note_text=note,
        stored_description=desc,
    )


def bg() -> list[LaborLine]:
    """Background documents on another project/vendor so shared tokens get non-zero IDF."""
    return [ln(date(2025, 1, 6), 900, f"unrelated filler {w}", project=999, vendor=999, desc=f"unrelated filler {w}")
            for w in ("alpha", "bravo", "charlie", "delta")]


def by_id(scored, line):
    return next(s for s in scored if s.line.line_id == line.line_id)


# --------------------------------------------------------------------------- input text


def test_rebuild_note_text_mirrors_aggregate_sproc():
    t0 = datetime(2026, 9, 1, 7)
    logs = [(t0 + timedelta(hours=2), " hung drywall "), (t0, "set doors"), (t0, "  "), (t0, None)]
    assert m.rebuild_note_text(logs) == "set doors; hung drywall"
    assert m.rebuild_note_text([]) is None


def test_build_lines_uses_timelog_notes_filtered_to_project_not_description():
    line_rows = [
        SimpleNamespace(LineId=1, ContractLaborId=10, LineDate=D0, ProjectId=7, LabelSccId=FRAMING,
                        Hours=Decimal("8"), SourceTimeEntryId=99, ClSourceTimeEntryId=99, VendorId=50),
        SimpleNamespace(LineId=2, ContractLaborId=11, LineDate=D0, ProjectId=7, LabelSccId=FRAMING,
                        Hours=None, SourceTimeEntryId=None, ClSourceTimeEntryId=None, VendorId=None),
    ]
    desc_rows = [SimpleNamespace(LineId=1, Description="13.01 Framing per PM"),
                 SimpleNamespace(LineId=2, Description="excel note")]
    log_rows = [
        SimpleNamespace(TimeEntryId=99, ProjectId=7, ClockIn=datetime(2026, 9, 14, 7), Note="framed walls"),
        SimpleNamespace(TimeEntryId=99, ProjectId=8, ClockIn=datetime(2026, 9, 14, 12), Note="other job"),
    ]
    coded = [SimpleNamespace(ContractLaborId=10, CodedAt=datetime(2026, 9, 16, 9))]
    a, b = m.build_lines(line_rows, desc_rows, log_rows, coded)
    assert a.note_text == "framed walls"
    assert a.stored_description == "13.01 Framing per PM"
    assert m.input_text(a, probe=False) == "framed walls"
    assert m.input_text(a, probe=True) == "13.01 Framing per PM"
    assert a.coded_at == datetime(2026, 9, 16, 9)
    # Excel-era line: no TimeLog -> no input text outside the probe
    assert not b.is_timelog_sourced and m.input_text(b, probe=False) is None
    assert b.hours == Decimal("0") and b.vendor_id is None


def test_scoring_reads_timelog_notes_not_stored_description():
    # The note points at DOORS; the (reviewer-rewritten) description points at FRAMING.
    p_doors = ln(D0, DOORS, "protected doors plastic", desc="zulu yankee")
    p_frame = ln(D0, FRAMING, "xray whiskey", desc="13.01 framing walls")
    q = ln(D0 + timedelta(days=3), FRAMING, "protected doors plastic", desc="13.01 framing walls")
    lines = [p_doors, p_frame, q] + bg()
    for key in ("s1_w3", "s1s2_w3"):
        assert by_id(m.score_lines(lines, coding_lag_days=2), q).predictions[key].top1 == DOORS
        assert by_id(m.score_lines(lines, coding_lag_days=2, probe=True), q).predictions[key].top1 == FRAMING


def test_leakage_probe_toggle_only_changes_text_source():
    prior = ln(D0, DOORS, "protected doors plastic", desc="44.00 doors")
    q = ln(D0 + timedelta(days=3), DOORS, "random unrelated words", desc="44.00 doors")
    normal = by_id(m.score_lines([prior, q] + bg()), q).predictions["s1_w3"]
    probe = by_id(m.score_lines([prior, q] + bg(), probe=True), q).predictions["s1_w3"]
    assert normal.source.startswith("fallback")
    assert probe.source == "s1" and probe.top1 == DOORS


# --------------------------------------------------------------------------- as-of


def test_as_of_excludes_future_and_within_lag_priors():
    q = ln(D0 + timedelta(days=5), FRAMING, "framed walls")
    future = ln(D0 + timedelta(days=6), DOORS, "framed walls")
    same_day = ln(D0 + timedelta(days=5), DOORS, "framed walls")
    within_lag = ln(D0 + timedelta(days=4), DOORS, "framed walls")
    ok = ln(D0 + timedelta(days=3), FRAMING, "framed walls")
    vis = m.visible_priors([future, same_day, within_lag, ok, q], q, coding_lag_days=2)
    assert vis == [ok]
    pred = by_id(m.score_lines([q, future, same_day, within_lag, ok] + bg()), q).predictions["s1_w7"]
    assert pred.top1 == FRAMING and pred.confidence == Decimal("1")


def test_as_of_respects_coded_at_when_both_known():
    q = ln(D0 + timedelta(days=5), FRAMING, "x", coded_at=datetime(2026, 9, 20, 9))
    later_coded = ln(D0, DOORS, "x", coded_at=datetime(2026, 9, 25, 9))
    unknown = ln(D0, DOORS, "x", coded_at=None)
    assert m.visible_priors([later_coded, unknown], q, coding_lag_days=2) == [unknown]


def test_same_contract_labor_never_counts():
    q = ln(D0 + timedelta(days=5), FRAMING, "x", cl=900)
    sib = ln(D0, DOORS, "x", cl=900)
    assert m.visible_priors([sib], q, coding_lag_days=2) == []


# --------------------------------------------------------------------------- S0 / S1


def test_s0_vendor_needs_three_priors_and_project_variant_falls_back():
    priors = [ln(D0, FRAMING, None, project=1), ln(D0, FRAMING, None, project=1), ln(D0, DOORS, None, project=2)]
    p = m.predict_s0_vendor(priors)
    assert p.top1 == FRAMING and p.confidence == Decimal(2) / Decimal(3)
    assert m.predict_s0_vendor(priors[:2]).top1 is None
    assert m.predict_s0_vendor_project(priors, 2).source == "s0_vendor"


def test_s1_phrase_precedent_scoped_to_project_and_window():
    doors = ln(D0, DOORS, "protected the doors with plastic")
    channel = ln(D0 + timedelta(days=1), CHANNEL, "installing the u channel track")
    other_proj = ln(D0 + timedelta(days=1), DRYWALL, "installing the u channel track", project=99)
    old = ln(D0 - timedelta(days=60), DRYWALL, "installing the u channel track")
    q = ln(D0 + timedelta(days=4), CHANNEL, "finished installing u channel")
    sl = by_id(m.score_lines([doors, channel, other_proj, old, q]), q)
    assert sl.predictions["s1_w3"].top1 == CHANNEL
    assert DRYWALL not in sl.predictions["s1_w14"].ranked


def test_empty_note_falls_back_to_s0():
    priors = [ln(D0 + timedelta(days=i), FRAMING, "framing walls") for i in range(3)]
    q = ln(D0 + timedelta(days=7), DOORS, None)
    pred = by_id(m.score_lines(priors + [q]), q).predictions["s1_w7"]
    assert pred.source == "fallback:s0_vendor_project" and pred.top1 == FRAMING
    assert by_id(m.score_lines(priors + [q]), q).has_note is False


# --------------------------------------------------------------------------- S2


def _split_corpus():
    doors = ln(D0, DOORS, "protected doors plastic")
    channel = ln(D0 + timedelta(days=1), CHANNEL, "installed channel track")
    drywall = ln(D0 + timedelta(days=1), DRYWALL, "hung drywall sheets")
    text = "protected doors plastic; installed channel track and hung drywall sheets"
    # One TimeEntry -> one CL (migration 009). The aggregate sproc writes ONE line per project
    # (qa); the PM splits qb/qc off in PUT /{id}/bill, and those carry no line-level TimeEntry.
    qa = ln(D0 + timedelta(days=3), DOORS, text, cl=500, te=777, cl_te=777, hours="4")
    qb = ln(D0 + timedelta(days=3), CHANNEL, text, cl=500, te=None, cl_te=777, hours="2")
    qc = ln(D0 + timedelta(days=3), DRYWALL, text, cl=500, te=None, cl_te=777, hours="2")
    return [doors, channel, drywall, qa, qb, qc], (qa, qb, qc)


def test_s2_detects_three_way_split_and_scores_set_and_hours():
    lines, (qa, qb, qc) = _split_corpus()
    scored = m.score_lines(lines)
    pred = by_id(scored, qa).predictions["s1s2_w3"]
    assert {s for s, _ in pred.split} == {DOORS, CHANNEL, DRYWALL}
    groups = m.group_labels(scored)
    for q in (qa, qb, qc):
        assert m.line_correct(by_id(scored, q).predictions["s1s2_w3"], q.label_scc_id, groups[q.group_key])
    sm = m.split_metrics(scored, "s1s2_w3", groups, since=D0 + timedelta(days=3), until=D0 + timedelta(days=3))
    assert sm["split_precision"] == 1.0 and sm["split_recall"] == 1.0
    assert sm["set_correct_over_predicted_splits"] == 1.0
    # predicted 1/3 each vs actual 4:2:2 of 8h -> min(2.67,4)+2+2 = 6.67/8
    assert sm["hours_weighted_correct_actual_splits"] == pytest.approx(0.833333, abs=1e-5)


def test_s2_spurious_code_is_wrong_and_counts_false_positive():
    lines, (qa, _, _) = _split_corpus()
    lone = ln(D0 + timedelta(days=3), DOORS, "protected doors plastic and installed channel track", cl=600)
    scored = m.score_lines(lines[:3] + [lone])
    pred = by_id(scored, lone).predictions["s1s2_w3"]
    assert len(pred.split) == 2
    groups = m.group_labels(scored)
    assert not m.line_correct(pred, DOORS, groups[lone.group_key])
    sm = m.split_metrics(scored, "s1s2_w3", groups, since=lone.line_date, until=lone.line_date)
    assert sm["split_precision"] == 0.0 and sm["split_recall"] is None


def test_split_segments_simple_rules():
    assert m.split_segments("a; b\nc, d and e") == ["a", "b", "c", "d", "e"]
    assert m.split_segments(None) == []


# --------------------------------------------------------------------------- S3

CATALOG = [
    SccRef(1, "65.01", "Miscellaneous Materials"),
    SccRef(2, "65.02", "Miscellaneous Labor"),
    SccRef(3, "99.00", "Contingency"),
    SccRef(4, "98.01", "Home Buyer's Warranty"),
    SccRef(5, "13.02", "Framing Labor"),
    SccRef(6, "13.01", "Framing Materials"),
    SccRef(7, "34.00", "Drywall Materials"),
    SccRef(8, "35.00", "Drywall Labor"),
    SccRef(9, "36.00", "Paint Materials"),
]


@pytest.mark.parametrize(
    "reply, number_id, final_id",
    [
        ("Approved. 65.1 Miscellaneous Labor", 1, 2),
        ("99.0 Home Buyer's Warranty", 3, 4),
        ("13.2 Framing Labor", 5, 5),
        ("34.00 Materials", 7, 8),
    ],
)
def test_s3_known_cases_label_over_number(reply, number_id, final_id):
    res = m.resolve_reply(CATALOG, reply, labor_context=True)
    assert res.number_scc == number_id
    assert res.final_scc == final_id


def test_s3_label_wins_without_labor_context_and_metrics():
    res = m.resolve_reply(CATALOG, "65.1 Miscellaneous Labor", labor_context=False)
    assert res.disagreed and res.final_scc == 2
    replies = [m.ReplyRow(1, 10, "65.1 Miscellaneous Labor"), m.ReplyRow(2, 11, "nothing here"),
               m.ReplyRow(3, 12, "13.2 Framing Labor")]
    out = m.s3_report(CATALOG, replies, {10: {2}, 12: {5, 6}}, labor_context=True)
    a = out["all_parsed"]
    assert out["parsed"] == 2 and a["scored"] == 1 and out["ambiguous_or_unlabelled"] == 1
    assert a["number_resolver_accuracy"] == 0.0 and a["label_over_number_accuracy"] == 1.0
    assert a["disagreement_label_correct"] == 1


# --------------------------------------------------------------------------- calibration


def test_threshold_choice_uses_calibration_rows_only():
    cal_since, cal_until = date(2026, 6, 1), date(2026, 8, 15)
    lines = []
    # calibration: confident and correct
    for i in range(30):
        lines.append(ln(date(2026, 6, 1) + timedelta(days=i), FRAMING, "framing walls studs"))
    lines.append(ln(date(2026, 6, 20), FRAMING, "framing walls studs"))
    base = m.score_lines(lines)
    thr = m.choose_threshold(base, "s1_w14", m.group_labels(base), cal_since=cal_since, cal_until=cal_until)
    # eval rows (after cal) that are confidently WRONG must not move the choice
    bad = [ln(date(2026, 9, 1) + timedelta(days=i), DOORS, "framing walls studs") for i in range(6)]
    with_eval = m.score_lines(lines + bad)
    assert m.choose_threshold(
        with_eval, "s1_w14", m.group_labels(with_eval), cal_since=cal_since, cal_until=cal_until
    ) == thr
    assert thr is not None


# --------------------------------------------------------------------------- privacy


def test_privacy_guard_rejects_free_text(tmp_path):
    p = tmp_path / "x.json"
    p.write_text(json.dumps({"a": "protected the doors"}), encoding="utf-8")
    with pytest.raises(ValueError):
        m.assert_outputs_privacy_safe(p)
    p.write_text(json.dumps({"a": 1, "b": "s1_w3:timelog", "c": "2026-09-16"}), encoding="utf-8")
    m.assert_outputs_privacy_safe(p)
    md = tmp_path / "x.md"
    md.write_text("| s1 | 0.5 |\n note: protected doors with plastic\n", encoding="utf-8")
    with pytest.raises(ValueError):
        m.assert_outputs_privacy_safe(md, ["protected doors with plastic"])
    m.assert_outputs_privacy_safe(md, ["something else entirely"])


def _corpus():
    lines, _ = _split_corpus()
    lines = lines + [ln(date(2026, 7, 1) + timedelta(days=i), FRAMING, "framing walls studs",
                        desc="13.01 framing per reviewer") for i in range(6)]
    lines.append(ln(date(2026, 7, 2), FRAMING, None, te=None, desc="excel era note text"))
    return Corpus(
        lines=lines,
        catalog=CATALOG,
        labor_replies=[m.ReplyRow(1, lines[3].contract_labor_id, "65.1 Miscellaneous Labor please",
                                  datetime(2026, 9, 18), "65.1 miscellaneous labor please", 7, lines[3].line_date)],
        bill_replies=[m.ReplyRow(2, 77, "99.0 Home Buyer's Warranty")],
        bill_reply_labels={77: {4}},
    )


def test_end_to_end_outputs_are_privacy_safe(tmp_path):
    corpus = _corpus()
    result = m.run_backtest(
        corpus, eval_since=date(2026, 6, 1), eval_until=date(2026, 10, 15),
        cal_since=date(2026, 6, 1), cal_until=date(2026, 8, 15), coding_lag_days=2,
    )
    md, js = m.write_outputs(result, out_dir=tmp_path, stamp="T", forbidden_texts=corpus.free_texts())
    data = json.loads(js.read_text())
    assert "s1s2_w3" in data["metrics"] and "excel" in data["metrics"]["s0_vendor"]
    assert data["s3"]["bill_replies"]["all_parsed"]["label_over_number_accuracy"] == 1.0
    for corpus_key in ("bill_replies", "labor_replies"):
        assert "modified_after_review" not in data["s3"][corpus_key]
    assert "period_2026_09_16_30" in data["metrics"]["s1_w3"]
    assert "framing walls studs" not in md.read_text()


def test_write_outputs_deletes_files_when_guard_trips(tmp_path, monkeypatch):
    corpus = _corpus()
    result = m.run_backtest(
        corpus, eval_since=date(2026, 6, 1), eval_until=date(2026, 10, 15),
        cal_since=date(2026, 6, 1), cal_until=date(2026, 8, 15), coding_lag_days=2,
    )
    result["population"]["leak"] = "hung drywall sheets"
    with pytest.raises(ValueError):
        m.write_outputs(result, out_dir=tmp_path, stamp="T", forbidden_texts=corpus.free_texts())
    assert list(tmp_path.iterdir()) == []


# --------------------------------------------------------------------------- CLI


def test_dry_run_makes_zero_scoring_passes(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(m, "score_lines", lambda *a, **k: calls.append(1))

    def no_corpus():
        raise AssertionError("dry-run must not load the corpus")

    rc = m.main([], counts_loader=lambda: {"lines": 3}, corpus_loader=no_corpus)
    assert rc == 0 and calls == []
    assert '"lines": 3' in capsys.readouterr().out


def test_run_flag_scores_and_writes(tmp_path):
    rc = m.main(["--run", "--out-dir", str(tmp_path)], counts_loader=dict, corpus_loader=_corpus)
    assert rc == 0
    assert len(list(tmp_path.glob("u673_report_*.json"))) == 1


# =========================================================================== fix round 1


def _line_row(lid, cl, project, scc, li_te, cl_te, hours="4"):
    return SimpleNamespace(LineId=lid, ContractLaborId=cl, LineDate=D0, ProjectId=project, LabelSccId=scc,
                           Hours=Decimal(hours), SourceTimeEntryId=li_te, ClSourceTimeEntryId=cl_te,
                           VendorId=50)


def test_p1_1_split_off_line_inherits_parent_time_entry_notes():
    """A PM split-off line has li.SourceTimeEntryId NULL but its CL carries the TimeEntry."""
    rows = [_line_row(1, 10, 7, DOORS, 99, 99), _line_row(2, 10, 7, CHANNEL, None, 99),
            _line_row(3, 10, 8, DRYWALL, None, 99), _line_row(4, 11, 7, DOORS, None, None)]
    logs = [SimpleNamespace(TimeEntryId=99, ProjectId=7, ClockIn=datetime(2026, 9, 14, 7), Note="doors and channel"),
            SimpleNamespace(TimeEntryId=99, ProjectId=8, ClockIn=datetime(2026, 9, 14, 12), Note="drywall")]
    a, b, c, excel = m.build_lines(rows, [], logs, [])
    assert b.is_timelog_sourced and b.note_text == "doors and channel"  # project-scoped
    assert c.note_text == "drywall"
    assert not excel.is_timelog_sourced and excel.note_text is None


def test_p1_1_split_off_lines_share_group_truth_in_line_correct_and_split_metrics():
    lines, (qa, qb, qc) = _split_corpus()
    # PM split-offs: no line-level TimeEntry, but the CL is TimeLog-era
    qb = LaborLine(**{**qb.__dict__, "source_time_entry_id": None, "cl_source_time_entry_id": 777})
    qc = LaborLine(**{**qc.__dict__, "source_time_entry_id": None, "cl_source_time_entry_id": 777})
    qa = LaborLine(**{**qa.__dict__, "cl_source_time_entry_id": 777})
    scored = m.score_lines(lines[:3] + [qa, qb, qc] + bg())
    groups = m.group_labels(scored)
    assert groups[qa.group_key] == {DOORS, CHANNEL, DRYWALL}
    for q in (qa, qb, qc):
        sl = by_id(scored, q)
        assert "timelog" in m.segment_keys(sl)
        assert m.line_correct(sl.predictions["s1s2_w3"], q.label_scc_id, groups[q.group_key])
    sm = m.split_metrics(scored, "s1s2_w3", groups, since=qa.line_date, until=qa.line_date)
    assert sm["groups"] == 1 and sm["actual_split_groups"] == 1 and sm["split_recall"] == 1.0


def _labor_corpus_with_replies():
    corpus = _corpus()
    target = corpus.lines[3]  # DOORS, cl 500
    reply = lambda rid, txt, created: m.ReplyRow(rid, target.contract_labor_id, txt, created,
                                                 m.normalise_reply(txt), target.project_id, target.line_date)
    return corpus, target, reply


def test_p2_1_labor_replies_deduped_once_per_crew():
    corpus, target, reply = _labor_corpus_with_replies()
    rows = [reply(1, "65.1 Miscellaneous Labor", datetime(2026, 9, 18)),
            reply(2, "  65.1   miscellaneous labor ", datetime(2026, 9, 18)),
            reply(3, "65.1 Miscellaneous Labor", datetime(2026, 9, 18))]
    deduped = m.dedupe_replies(rows)
    assert [r.review_id for r in deduped] == [1]
    out = m.s3_report(CATALOG, rows, {target.contract_labor_id: {2}}, labor_context=True)
    assert out["raw_replies"] == 3 and out["deduped_replies"] == 1
    assert out["all_parsed"]["scored"] == 1


def test_p1_2_s3_reports_circular_and_correction_views_only():
    """ModifiedDatetime is bumped by bill generation, so it is not a correction signal: the
    modified-after slices are gone; all_parsed (circular) + truth_differs_from_number remain."""
    rows = [
        m.ReplyRow(1, 10, "65.1 Miscellaneous Labor", datetime(2026, 9, 18, 9)),  # label right, number wrong
        m.ReplyRow(2, 11, "13.2 Framing Labor", datetime(2026, 9, 18, 9)),        # number == truth
        m.ReplyRow(3, 12, "99.0 Home Buyer's Warranty", datetime(2026, 9, 18, 9)),
    ]
    labels = {10: {2}, 11: {5}, 12: {4}}
    for labor_context in (True, False):
        out = m.s3_report(CATALOG, rows, labels, labor_context=labor_context)
        assert out["all_parsed"]["scored"] == 3
        assert out["truth_differs_from_number"]["scored"] == 2
        assert out["truth_differs_from_number"]["label_over_number_accuracy"] == 1.0
        assert out["truth_differs_from_number"]["number_resolver_accuracy"] == 0.0
        assert "circular" in out["note_all_parsed"]
        assert not [k for k in out if "modified" in k]
    assert not hasattr(m, "REVIEW_MODIFY_GRACE") and not hasattr(m, "labor_parent_modified_at")
    assert "ModifiedDatetime" not in m._LINES_SQL
    assert "modified_at" not in LaborLine.__dataclass_fields__


def test_p2_2_threshold_requires_minimum_calibration_support():
    lines = [ln(date(2026, 6, 1) + timedelta(days=i), FRAMING, "framing walls studs") for i in range(8)] + bg()
    scored = m.score_lines(lines)
    assert m.choose_threshold(scored, "s1_w14", m.group_labels(scored),
                              cal_since=date(2026, 6, 1), cal_until=date(2026, 8, 15)) is None
    many = [ln(date(2026, 6, 1) + timedelta(days=i), FRAMING, "framing walls studs") for i in range(40)] + bg()
    sc2 = m.score_lines(many)
    assert m.choose_threshold(sc2, "s1_w14", m.group_labels(sc2),
                              cal_since=date(2026, 6, 1), cal_until=date(2026, 8, 15)) is not None


def test_p2_2_phase_b_insufficient_support():
    metrics = {k: {"timelog": {"top1": 0.9}} for k in m.signal_keys()}
    metrics["s0_vendor_project"]["timelog"]["top1"] = 0.5
    fitted = {k: {"shown": 5, "confident_wrong": 0.0} for k in m.signal_keys()}
    v = m.phase_b_verdict(metrics, fitted)
    assert v["candidates"]["s1_w3"]["verdict"] == "insufficient_support"
    assert v["candidates"]["s1_w3"]["shown"] == 5 and v["any_passes"] is False
    fitted = {k: {"shown": 25, "confident_wrong": 0.0} for k in m.signal_keys()}
    assert m.phase_b_verdict(metrics, fitted)["candidates"]["s1_w3"]["verdict"] == "pass"


def test_p2_3_privacy_guard_fail_closed_no_final_or_temp_files(tmp_path, monkeypatch):
    corpus = _corpus()
    result = m.run_backtest(
        corpus, eval_since=date(2026, 6, 1), eval_until=date(2026, 10, 15),
        cal_since=date(2026, 6, 1), cal_until=date(2026, 8, 15), coding_lag_days=2,
    )

    def boom(path, forbidden=()):
        assert not path.name.startswith("u673_report_T."), "guard must run on temp names"
        raise KeyboardInterrupt

    monkeypatch.setattr(m, "assert_outputs_privacy_safe", boom)
    with pytest.raises(KeyboardInterrupt):
        m.write_outputs(result, out_dir=tmp_path, stamp="T", forbidden_texts=[])
    assert list(tmp_path.iterdir()) == []


def test_p2_4_number_resolver_exact_alias():
    cat = CATALOG + [SccRef(20, "40.00", "Insulation", aliases="40|INS")]
    assert m.resolve_by_number(cat, "40") == 20
    assert m.resolve_by_number(cat, "65.1") == 1  # pad still beats alias
