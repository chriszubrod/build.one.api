"""U-797a: pure ladder logic for the Contract Labor coding shadow (no DB, no LLM)."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from entities.coding_suggestion.business.similar_bills import compute_idf_from_doc_token_sets
from entities.contract_labor.business.coding_shadow import (
    LLM_SYSTEM,
    TAU,
    M,
    CatalogEntry,
    DayInputs,
    PrecedentDay,
    arm_a_resolved_codes,
    build_prompt,
    candidate_codes,
    decide,
    history_stats,
    is_history_strong,
    parse_model_response,
    rebuild_note_text,
    sonnet_model_id,
    split_segments,
    tokenize_labor_text,
    top_codes,
    workday_distance,
)


def _allowed(*ids: int) -> dict[int, str]:
    return {i: f"0{i}.00" for i in ids}


def _precedent_idf(precedents) -> dict[str, float]:
    """The pre-fix IDF: one document per precedent day (kept for the day-only corpus tests)."""
    return compute_idf_from_doc_token_sets(
        [tokenize_labor_text(d.note_text) for d in precedents if d.note_text]
    )


# A broad labelled corpus (one document per labelled line), as ReadContractLaborCodingIdfCorpus returns it.
_CORPUS_ROWS = [
    (100, datetime(2026, 9, 1, 8, 0), "framing wall"),
    (101, datetime(2026, 9, 2, 8, 0), "drywall hanging"),
    (200, datetime(2026, 9, 3, 8, 0), "pavers crates hauling"),
    (201, datetime(2026, 9, 4, 8, 0), "pavers crates loading"),
    (202, datetime(2026, 9, 5, 8, 0), "pavers crates"),
]


def _corpus_idf() -> dict[str, float]:
    from entities.contract_labor.business.coding_shadow import build_corpus_idf

    return build_corpus_idf(_CORPUS_ROWS)


# --- history rung ---------------------------------------------------------


def test_history_strong_needs_three_priors_not_two():
    assert not is_history_strong(history_stats({5: 2}))
    assert is_history_strong(history_stats({5: 3}))


def test_history_share_boundary_59_vs_60():
    assert not is_history_strong(history_stats({1: 59, 2: 41}))
    assert is_history_strong(history_stats({1: 60, 2: 40}))


def test_history_share_three_of_five_is_exactly_sixty_percent():
    stats = history_stats({7: 3, 8: 2})
    assert stats is not None and stats.top_sub_cost_code_id == 7
    assert is_history_strong(stats)


def test_history_top_tie_breaks_on_lower_id():
    stats = history_stats({9: 3, 4: 3})
    assert stats is not None and stats.top_sub_cost_code_id == 4


def test_history_ignores_zero_counts_and_empty_is_none():
    assert history_stats({}) is None
    assert history_stats({3: 0}) is None


def test_decide_history_strong_wins_without_model_and_keeps_split_flag():
    decision = decide(
        history=history_stats({5: 4}),
        model=None,
        arm_a_codes=frozenset({5, 6}),
        total_hours=Decimal("8"),
    )
    assert decision.decision_source == "history"
    assert decision.sub_cost_code_id == 5
    assert decision.confidence == Decimal("1.0000")
    assert decision.may_split is True
    assert decision.model_proposal is None


# --- model rung -----------------------------------------------------------


def _model(proposal, *pairs, status="ok"):
    from entities.contract_labor.business.coding_shadow import ModelAnswer

    return ModelAnswer(proposal=proposal, lines=tuple(pairs), status=status)


def test_decide_weak_history_model_single_suggests_the_code():
    decision = decide(
        history=history_stats({5: 2}),
        model=_model("single", (6, Decimal("8"))),
        arm_a_codes=frozenset(),
        total_hours=Decimal("8"),
    )
    assert decision.decision_source == "model"
    assert decision.sub_cost_code_id == 6
    assert decision.model_proposal == "single"
    assert decision.may_split is False


def test_decide_model_split_picks_code_with_most_hours_and_flags_split():
    decision = decide(
        history=None,
        model=_model("split", (5, Decimal("3")), (6, Decimal("5"))),
        arm_a_codes=frozenset(),
        total_hours=Decimal("8"),
    )
    assert decision.decision_source == "model"
    assert decision.sub_cost_code_id == 6
    assert decision.may_split is True
    assert decision.model_proposal == "split"
    assert decision.model_split_codes == (5, 6)


def test_decide_model_abstain_is_none_with_abstain_proposal():
    decision = decide(
        history=None, model=_model("abstain"), arm_a_codes=frozenset(), total_hours=Decimal("8")
    )
    assert decision.decision_source == "none"
    assert decision.sub_cost_code_id is None
    assert decision.model_proposal == "abstain"


def test_decide_model_unavailable_is_none_with_reason():
    decision = decide(history=None, model=None, arm_a_codes=frozenset(), total_hours=Decimal("8"))
    assert decision.decision_source == "none"
    assert decision.sub_cost_code_id is None
    assert decision.model_proposal is None
    assert decision.failure_reason == "model_unavailable"


def test_decide_invalid_model_reply_is_none_with_status_reason():
    from entities.contract_labor.business.coding_shadow import ModelAnswer

    decision = decide(
        history=history_stats({5: 2}),
        model=ModelAnswer(proposal=None, lines=(), status="invalid_json"),
        arm_a_codes=frozenset(),
        total_hours=Decimal("8"),
    )
    assert decision.decision_source == "none"
    assert decision.model_proposal is None
    assert decision.failure_reason == "invalid_json"


def test_split_flag_from_arm_a_alone_when_model_says_single():
    decision = decide(
        history=None,
        model=_model("single", (6, Decimal("8"))),
        arm_a_codes=frozenset({6, 7}),
        total_hours=Decimal("8"),
    )
    assert decision.sub_cost_code_id == 6
    assert decision.may_split is True


def test_no_split_flag_when_arm_a_resolves_one_code_and_model_single():
    decision = decide(
        history=None,
        model=_model("single", (6, Decimal("8"))),
        arm_a_codes=frozenset({6}),
        total_hours=Decimal("8"),
    )
    assert decision.may_split is False


def test_frozen_thresholds_match_u751_calibration():
    assert TAU == 0.60
    assert M == 2


# --- catalog --------------------------------------------------------------


def test_candidate_codes_is_the_union_of_precedent_and_vendor_top():
    assert candidate_codes({1, 2}, {2, 3, 4}) == frozenset({1, 2, 3, 4})


def test_top_codes_returns_three_by_count_then_id():
    assert top_codes({5: 3, 6: 3, 7: 1, 8: 2}, n=3) == (5, 6, 8)


# --- response parse / resolve ---------------------------------------------


def test_parse_single_valid():
    answer = parse_model_response(
        '{"proposal":"single","lines":[{"sub_cost_code_id":6,"sub_cost_code_number":"06.00","hours":8}],"evidence_dates":[]}',
        _allowed(6),
        Decimal("8"),
    )
    assert answer.proposal == "single"
    assert answer.lines == ((6, Decimal("8")),)
    assert answer.status == "ok"


def test_parse_split_hours_must_sum_to_total():
    text = (
        '{"proposal":"split","lines":['
        '{"sub_cost_code_id":5,"sub_cost_code_number":"05.00","hours":3},'
        '{"sub_cost_code_id":6,"sub_cost_code_number":"06.00","hours":4}]}'
    )
    answer = parse_model_response(text, _allowed(5, 6), Decimal("8"))
    assert answer.proposal is None
    assert answer.status == "hours_mismatch"


def test_parse_abstain():
    answer = parse_model_response('{"proposal":"abstain","lines":[]}', _allowed(), Decimal("8"))
    assert answer.proposal == "abstain"
    assert answer.status == "abstain"


def test_parse_non_json_is_invalid():
    answer = parse_model_response("not json at all", _allowed(6), Decimal("8"))
    assert answer.proposal is None
    assert answer.status == "invalid_json"


def test_parse_unknown_code_id_is_rejected():
    text = '{"proposal":"single","lines":[{"sub_cost_code_id":99,"hours":8}]}'
    answer = parse_model_response(text, _allowed(6), Decimal("8"))
    assert answer.proposal is None
    assert answer.status == "unknown_code"


def test_parse_resolves_by_number_when_id_missing():
    text = '{"proposal":"single","lines":[{"sub_cost_code_number":"06.00","hours":8}]}'
    answer = parse_model_response(text, _allowed(6), Decimal("8"))
    assert answer.proposal == "single"
    assert answer.lines == ((6, Decimal("8")),)


def test_parse_single_with_two_lines_is_invalid_shape():
    text = (
        '{"proposal":"single","lines":['
        '{"sub_cost_code_id":5,"hours":4},{"sub_cost_code_id":6,"hours":4}]}'
    )
    answer = parse_model_response(text, _allowed(5, 6), Decimal("8"))
    assert answer.proposal is None
    assert answer.status == "invalid_shape"


def test_parse_duplicate_codes_are_invalid_shape():
    text = (
        '{"proposal":"split","lines":['
        '{"sub_cost_code_id":5,"hours":4},{"sub_cost_code_id":5,"hours":4}]}'
    )
    answer = parse_model_response(text, _allowed(5), Decimal("8"))
    assert answer.proposal is None
    assert answer.status == "invalid_shape"


def test_parse_zero_hours_is_invalid_shape():
    text = '{"proposal":"single","lines":[{"sub_cost_code_id":6,"hours":0}]}'
    answer = parse_model_response(text, _allowed(6), Decimal("8"))
    assert answer.proposal is None
    assert answer.status == "invalid_shape"


# --- prompt ---------------------------------------------------------------


def test_build_prompt_lists_only_candidate_codes_with_tags():
    catalog = {
        5: CatalogEntry(5, "05.00", "Framing"),
        6: CatalogEntry(6, "06.00", "Misc Labor"),
        9: CatalogEntry(9, "09.00", "Unrelated"),
    }
    day = DayInputs(
        line_date=date(2026, 11, 11),
        project_id=3,
        total_hours=Decimal("5.0"),
        note_text="Helped the crew with crates of pavers",
    )
    precedents = [
        PrecedentDay(
            contract_labor_id=11,
            line_date=date(2026, 11, 7),
            lines=((5, Decimal("5.0")),),
            note_text="framing the wall",
        )
    ]
    text, allowed = build_prompt(day, precedents, catalog, vendor_top=(6,))
    assert allowed == {5: "05.00", 6: "06.00"}
    assert "Misc Labor" in text and "Framing" in text
    assert "Unrelated" not in text
    assert "Helped the crew with crates of pavers" in text


def test_build_prompt_drops_zero_hour_lines_and_merges_hours_per_code_per_day():
    catalog = {5: CatalogEntry(5, "05.00", "Framing"), 6: CatalogEntry(6, "06.00", "Misc Labor")}
    day = DayInputs(date(2026, 11, 11), 3, Decimal("8"), "framing")
    precedents = [
        PrecedentDay(
            contract_labor_id=11,
            line_date=date(2026, 11, 7),
            lines=((5, Decimal("4")), (5, Decimal("4")), (6, Decimal("0"))),
            note_text="framing",
        )
    ]
    text, allowed = build_prompt(day, precedents, catalog, vendor_top=())
    assert allowed == {5: "05.00"}
    assert "id 5 (05.00): 8h" in text
    assert "id 6" not in text


def test_llm_system_prompt_pins_the_json_contract():
    assert '"proposal"' in LLM_SYSTEM and "split" in LLM_SYSTEM and "abstain" in LLM_SYSTEM
    assert "sub_cost_code_id" in LLM_SYSTEM


def test_sonnet_model_is_resolved_from_the_v2_ladder():
    assert "sonnet" in sonnet_model_id()


# --- notes and arm A ------------------------------------------------------


def test_rebuild_note_text_orders_by_clock_in_and_trims():
    logs = [
        (datetime(2026, 11, 11, 13, 0), "  afternoon framing  "),
        (datetime(2026, 11, 11, 8, 0), "morning layout"),
        (datetime(2026, 11, 11, 9, 0), None),
        (datetime(2026, 11, 11, 10, 0), "   "),
    ]
    assert rebuild_note_text(logs) == "morning layout; afternoon framing"


def test_rebuild_note_text_none_when_nothing_to_join():
    assert rebuild_note_text([(datetime(2026, 11, 11, 8, 0), None)]) is None


def test_split_segments_on_semicolon_newline_comma_and():
    assert split_segments("ran to HD, loaded truck and; moved pallets\nlunch") == [
        "ran to HD",
        "loaded truck",
        "moved pallets",
        "lunch",
    ]


def test_workday_distance_skips_weekend():
    # Fri 2026-11-06 to Mon 2026-11-09 is one workday.
    assert workday_distance(date(2026, 11, 6), date(2026, 11, 9)) == 1
    assert workday_distance(date(2026, 11, 9), date(2026, 11, 6)) == 1


def test_corpus_idf_is_one_document_per_labelled_line_from_rebuilt_notes():
    from entities.contract_labor.business.coding_shadow import build_corpus_idf

    rows = [
        (1, datetime(2026, 11, 9, 8, 0), "pavers"),
        (1, datetime(2026, 11, 9, 9, 0), "crates"),  # same line, two notes -> one rebuilt document
        (2, datetime(2026, 11, 9, 8, 0), "pavers"),
    ]
    idf = build_corpus_idf(rows)
    # N = 2 documents; pavers in both (df 2) -> log(3/3) = 0; crates in one -> log(3/2).
    import math

    assert idf["pavers"] == 0.0
    assert abs(idf["crates"] - math.log(1.5)) < 1e-9


def test_corpus_idf_empty_corpus_is_empty():
    from entities.contract_labor.business.coding_shadow import build_corpus_idf

    assert build_corpus_idf([]) == {}


# Unrelated precedents keep the day-only IDF non-degenerate (a token in every note weighs zero).
_UNRELATED = [
    PrecedentDay(8, date(2026, 11, 9), ((5, Decimal("4")),), "framing wall"),
    PrecedentDay(9, date(2026, 11, 9), ((5, Decimal("4")),), "drywall hanging"),
]


def test_arm_a_resolves_a_code_when_precedent_has_enough_support():
    precedents = [
        PrecedentDay(1, date(2026, 11, 9), ((7, Decimal("4")),), "pavers crates hauling"),
        PrecedentDay(2, date(2026, 11, 9), ((7, Decimal("4")),), "pavers crates loading"),
        *_UNRELATED,
    ]
    codes = arm_a_resolved_codes(
        "hauling pavers crates", precedents, date(2026, 11, 10), idf=_precedent_idf(precedents)
    )
    assert codes == frozenset({7})


def test_arm_a_needs_support_of_m_lines():
    precedents = [PrecedentDay(1, date(2026, 11, 9), ((7, Decimal("4")),), "pavers crates"), *_UNRELATED]
    assert (
        arm_a_resolved_codes("pavers crates", precedents, date(2026, 11, 10), idf=_precedent_idf(precedents))
        == frozenset()
    )


def test_arm_a_weights_precedent_by_workday_distance():
    """Nearer precedent outweighs farther identical precedent (U-750 1/(1+distance)); mutation survivor."""
    precedents = [
        PrecedentDay(1, date(2026, 11, 9), ((7, Decimal("4")),), "pavers crates"),
        PrecedentDay(2, date(2026, 11, 9), ((7, Decimal("4")),), "pavers crates"),
        PrecedentDay(3, date(2026, 10, 27), ((8, Decimal("4")),), "pavers crates"),
        PrecedentDay(4, date(2026, 10, 27), ((8, Decimal("4")),), "pavers crates"),
        *_UNRELATED,
    ]
    assert arm_a_resolved_codes(
        "pavers crates", precedents, date(2026, 11, 10), idf=_precedent_idf(precedents)
    ) == frozenset({7})


def test_arm_a_ignores_zero_hour_precedent_lines():
    precedents = [
        PrecedentDay(1, date(2026, 11, 9), ((7, Decimal("0")),), "pavers crates"),
        PrecedentDay(2, date(2026, 11, 9), ((7, Decimal("0")),), "pavers crates"),
        *_UNRELATED,
    ]
    assert (
        arm_a_resolved_codes("pavers crates", precedents, date(2026, 11, 10), idf=_precedent_idf(precedents))
        == frozenset()
    )


def test_arm_a_single_precedent_date_resolves_only_with_the_corpus_idf():
    # Two crew members on ONE precedent date. Precedent-only IDF is degenerate (both docs identical,
    # weight 0), so arm A cannot resolve. The broad labelled corpus gives the tokens weight.
    precedents = [
        PrecedentDay(1, date(2026, 11, 9), ((7, Decimal("4")),), "pavers crates hauling"),
        PrecedentDay(2, date(2026, 11, 9), ((7, Decimal("4")),), "pavers crates hauling"),
    ]
    assert arm_a_resolved_codes(
        "hauling pavers crates", precedents, date(2026, 11, 10), idf=_precedent_idf(precedents)
    ) == frozenset()
    assert arm_a_resolved_codes(
        "hauling pavers crates", precedents, date(2026, 11, 10), idf=_corpus_idf()
    ) == frozenset({7})


def test_arm_a_merges_hours_per_code_per_precedent_day_before_counting_support():
    # One crew member, one day, the same code on two lines: that is ONE supporting group, not two.
    precedents = [
        PrecedentDay(1, date(2026, 11, 9), ((7, Decimal("2")), (7, Decimal("2"))), "pavers crates hauling"),
    ]
    assert arm_a_resolved_codes(
        "hauling pavers crates", precedents, date(2026, 11, 10), idf=_corpus_idf()
    ) == frozenset()


def test_build_prompt_takes_the_nearest_precedent_days_that_have_hours_not_all_zero_days():
    # U-750 precedent_groups behaviour: a day whose lines merge to nothing (all zero hours) must
    # neither print nor take one of the PRECEDENT_MAX slots. Thirty all-zero days on the nearest date
    # would otherwise fill every slot and push the one real precedent out.
    from entities.contract_labor.business.coding_shadow import PRECEDENT_MAX

    catalog = {5: CatalogEntry(5, "05.00", "Framing")}
    day = DayInputs(date(2026, 11, 11), 3, Decimal("8"), "framing")
    zero_days = [
        PrecedentDay(
            contract_labor_id=cl_id,
            line_date=date(2026, 11, 10),
            lines=((5, Decimal("0")),),
            note_text="zero",
        )
        for cl_id in range(1, PRECEDENT_MAX + 1)
    ]
    real = PrecedentDay(
        contract_labor_id=999,
        line_date=date(2026, 10, 28),
        lines=((5, Decimal("8")),),
        note_text="framing",
    )
    text, _allowed = build_prompt(day, [*zero_days, real], catalog, vendor_top=())
    assert "- 2026-10-28 | id 5 (05.00): 8h" in text
    assert "2026-11-10" not in text  # no all-zero day is printed
