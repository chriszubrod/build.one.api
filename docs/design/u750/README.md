# U-750 — labor coding follow-up proposal, from precedent (backtest)

Harness: `scripts/u750_labor_followup_backtest.py`, tests in `tests/test_u750_labor_followup_backtest.py`.
Read-only: every SQL batch is SELECT-only and rolled back (U-673 `_select_all`). Dry-run is the default.

## Business case

A Contract Labor crew-member day is submitted uncoded. At follow-up time T an email proposes the coding
(codes + hour split) from same-project precedent and a reviewer approves. Motivating case — HP2,
2026-09-29: the note describes L-beads/corner beads/drywall mud, door protection, and a shower U-channel;
the proposal 35.00 / 44.00 / 51.00 at 4h : 2h : 2h of 8h (precedents 9/24, 9/25, 9/28, 9/30) is exactly how
it was coded.

## Design

- **Unit of evaluation = a crew-member day:** the labelled lines sharing (ContractLabor, Project, LineDate) —
  U-673's group truth and label rule (approved/completed CLs, non-holding SCC). Truth = {SCC: summed hours},
  with codes whose merged hours are 0 (NULL-hours lines) dropped: a 0-h code never makes a day split and is
  not part of exact-correct (a day with no positive hours keeps its raw truth). A day is **split** when it
  has ≥ 2 distinct positive-hour codes. The same rule applies to evidence: 0-hour lines are dropped from
  precedent groups (code sets and hour ratios) and from segment scoring and support, and the dry-run SQL
  counts only positive-hour codes when it counts split days. Only TimeLog-sourced days are queried (Excel-era days
  have no note and are counted as excluded); every labelled line can be evidence.
- **Input text** = the rebuilt TimeLog notes (U-673 `build_lines` / `rebuild_note_text`); the stored
  `Description` is never loaded.
- **Follow-up time** T = LineDate + K calendar days, K ∈ {3, 7, 14}; **K = 7 is the headline**.
- **Evidence** for query day d on project p: labelled lines on p with |workdays| ≤ 10 of d (symmetric),
  **excluding every line dated d on p** (the whole crew-day is uncoded at follow-up time) **and every line of
  the query's own ContractLabor on any date** (as U-673), visible only if
  coded before T — `coded_at < T` (T = midnight starting day d+K) when the prior's CL has a coded_at (first
  final, non-declined Review, U-673), otherwise `prior.LineDate + 2 days ≤ d + K`. Later-dated priors are
  allowed when visible by T (hindsight mode). **Calibration query days additionally see only evidence dated
  ≤ the calibration end**, so no holdout row can move the fitted pair (pinned by a test that goes red if
  the cap is removed).
- **Arm A (no LLM):**
  1. Split the note with U-673 `split_segments`; token-less segments are ignored. Each segment scores codes
     by IDF-weighted Jaccard (`idf_weighted_jaccard`, U-673 tokenizer) against each evidence line's note,
     weighted 1 / (1 + workday distance), summed per code. A segment resolves to its top code when the
     code's share ≥ τ and ≥ m evidence lines support it.
  2. One distinct resolved code → **single** (all hours). Two or more (set D) → **split** only if an evidence
     crew-member day has code set exactly D; hours = that precedent's ratio × the query's total hours,
     nearest precedent in workdays (tie → earlier date → lower CL id), rounded to 0.25 h with the remainder on
     the largest share so the total matches exactly. No such precedent, or a scaled line that would be
     ≤ 0 h → **split fallback**: single code = the resolved segment pick with the highest share (counted as
     `split_fallbacks`).
  3. Nothing resolves → **abstain**. Some segments unresolved → proceed on the resolved ones, flagged
     (`unresolved_segment_flags`).
  - τ ∈ 0.40..0.90 step 0.05, m ∈ {1, 2, 3}, fitted per K on **calibration only** (2026-06-01..08-15): max
    coverage subject to exact-correct ≥ 0.80, false-split ≤ 0.05 and ≥ 20 proposals; if no pair qualifies
    the pair with the best exact-correct is used and `fit.status = no_pair_qualified_best_exact_used`.
    Measured on **holdout** 2026-08-16..10-15.
- **Realistic population — `uncoded_at_T_k<K>`:** per K, the query days whose own ContractLabor
  `coded_at` is NULL or ≥ T. Those are the days a follow-up email would actually be sent for (days coded
  before T need no email). Every arm and baseline gets full metrics and its own Phase B verdict on this
  stratum. The markdown headline shows it beside the all-days verdict with its day count, and the population
  block reports `holdout_uncoded_at_T_k<K>_days`.
- **Evidence provenance (arm A and arm B, per K):** counts and shares of evidence lines that entered via
  `coded_at` vs the no-coded_at date fallback, and earlier- vs later-dated than the query; the same for the
  split precedents arm A actually chose. **Sensitivity row "past-only priors":** arm A on the holdout at the
  fitted pair with later-dated priors removed, to show how much the hindsight mode contributes.
- **Arm B (LLM, `--llm-run` only):** for each holdout day (default K = 7 only, `--llm-k` to widen) Sonnet
  gets the notes, total hours, a precedent table (up to 30 nearest evidence crew-member days: date, codes
  with hours, rebuilt note) and the catalog entries (id + number + name) of the codes in the evidence; it
  must return `{"proposal": "single"|"split"|"abstain", "lines": [{"sub_cost_code_id",
  "sub_cost_code_number", "hours"}], "evidence_dates": [...]}` and may split only when a precedent day shows
  the same kind of split.
  - **Code identity:** the prompt lists each code as `id <SubCostCodeId> | number <Number> | name` and the
    model returns `sub_cost_code_id` with the number. The id is authoritative (unknown id → `unknown_code`;
    an id whose number contradicts the returned number → `invalid_shape`). Without an id the number is
    matched exactly, then numerically (`"35"`, `35`, `35.0` → `35.00`) only when exactly one listed code
    matches; two listed codes sharing a number → `ambiguous_code`. (Chosen over dropping colliding days.)
  - **Validation:** invalid JSON → `invalid_json`; bad shape, duplicate codes, non-positive hours →
    `invalid_shape`; a split whose hours do not sum to the day's total within 0.01 → `hours_mismatch`. All are
    abstains, counted per status. Single proposals take the day's total hours.
  - **Generation params:** the cascade defaults `{"temperature": 0, "reasoning_effort": "minimal",
    "thinking": "off"}` (`CascadeTask.gen_params`) are passed as `extra_body`; `max_tokens` = 600.
  - **Truncation:** an answer that stopped on `max_tokens` is counted `truncated`, scored as an abstain and
    **never cached** (a re-run pays for it again). More than max(3, 5 % of attempted) truncated answers
    aborts loudly (exit 1, cache kept), as for transport errors.
  - Model = the Sonnet rung of `DEFAULT_BILL_CODING_LADDER_V2` (the live U-701b ladder; `claude-sonnet-5-5`
    today), read at run time — not hardcoded.
  - Calls go through `AnthropicTransport`, so the U-702 pre-call gate and metering apply. The run sets
    `claude_budget.use_isolated_spend_ledger(--budget-usd)` (default 5.00) and clears it in `finally` —
    `dbo.LlmDailySpend` is never touched. A `claude_unavailable:*` skip **aborts the run** (exit 1, no
    report); cached responses survive, so a re-run resumes without re-paying.
  - **Budget (estimate-bounded):** each call's cost is estimated as (chars / 3 input tokens + 600 output
    tokens at list price) × **1.5**. Before a K's calls, isolated spend so far + the estimate for every
    uncached prompt must fit the budget, else the run **refuses** (exit 2). Before **each** uncached call,
    spend so far + that call's padded estimate must fit, else the run aborts loudly
    (`per_call_estimate_exceeds_budget`, exit 1, cache kept). The cap therefore holds as long as a real call
    costs no more than its padded estimate; it is not a hard guarantee against a pathological response.
    `--budget-usd` caps **one invocation** across all its `--llm-k` values: the isolated ledger starts at 0
    on every run, so a resumed run gets a fresh budget (cache hits cost nothing; only new calls spend).
    `--max-calls` (default 400) caps new calls.
  - **Transport errors (non-budget):** the day is **excluded** from the scored denominator (never an
    abstain) and counted (`days_excluded_transport_error`). More than max(3, 5 % of attempted) errors aborts
    loudly (exit 1, cache kept).
  - **Incomplete runs:** if any holdout day was not run (`--max-calls`) or excluded, arm B's verdicts are
    `incomplete`, never PASS/FAIL. Arm A at the fitted pair is always reported on exactly the days arm B
    attempted (`paired_arm_a_same_days`) for a paired comparison.
  - Responses are cached at `<out-dir>/llm_cache/<sha256>.json` keyed on (harness version, model,
    max_tokens, gen params, system prompt, user prompt). **The cache holds prompts (note text)** and is not a
    report output, so it is not privacy-guarded.
- **Baselines** on the same days (single-code proposals): U-749 / U-673 S0 vendor × project top-1 (as-of
  LineDate − 2) and U-673 S1 7-workday at-submit top-1 (falls back to S0), on the day's note.
- **Metrics** per arm, per K, for calibration and holdout, in strata all / split_truth / single_truth:
  coverage (+ Wilson 95 % CI), abstain rate, exact_correct (codes equal AND every code's hours within 0.01;
  + CI) and set_correct among proposals, exact_correct of all days, false_split_rate = split proposals on
  single-truth days / single-truth days (+ CI), missed_split_rate = split-truth days without a split
  proposal / split-truth days, split precision (split proposals on split-truth days / split proposals),
  split exact, hours-weighted accuracy (Σ min(proposed, true) hours / Σ proposed days' hours),
  split-fallback and unresolved-segment counts.
- **Phase B verdict (holdout, all days):** PASS iff exact_correct ≥ 0.80 AND false_split_rate ≤ 0.05 AND
  coverage ≥ 0.50 AND ≥ 20 proposals; fewer than 20 proposals → `insufficient_support`; else FAIL.
- **Named case:** HP2 (Project.Abbreviation, case-insensitive) on 2026-09-29 — each crew-member day's truth
  and every arm's proposal (ids, code numbers, hours only).
- **Out dir:** `--run` (and therefore `--llm-run`) refuses any `--out-dir` that does not resolve under
  `/Users/chris/Applications/build.one/.data/`; nothing under it is ever committed.
- **Headline row:** each arm-A row also shows `fit.status` and the calibration proposal count.
- **Outputs:** `u750_report_<stamp>.{md,json}` — ids / codes / hours / rates only; written under temp names,
  both checked by U-673 `assert_outputs_privacy_safe` (forbidden texts = every note, every segment, every
  catalog name), renamed only after both pass, all removed on any failure.

## Run commands (repo root; EM only — the worktree `.env` holds PRODUCTION credentials)

```sh
./.venv/bin/python -m scripts.u750_labor_followup_backtest                                  # dry-run: day / split-day counts per window
./.venv/bin/python -m scripts.u750_labor_followup_backtest --run \
    --out-dir /Users/chris/Applications/build.one/.data/u750                                 # arm A + baselines, $0
./.venv/bin/python -m scripts.u750_labor_followup_backtest --run \
    --out-dir /Users/chris/Applications/build.one/.data/u750 \
    --llm-run --budget-usd 5.00 [--max-calls 400] [--llm-k 7]                                # + arm B (paid)
```

`--k 3,7,14` (default) picks the follow-up delays; `--cal-*` / `--holdout-*` override the windows.

## Results

Run 2026-11-02 against prod (read-only). Arm A: `results/armA` ($0, K=3/7/14). Arm B: Sonnet 5.5
(`claude-sonnet-5-5`, thinking off) on the isolated U-702 ledger: K=7 `results/armB` ($1.08, 272 calls) and
K=14 `results/armB14` ($1.34, 289 calls). The live `dbo.LlmDailySpend` row was untouched by both runs.
Labelled data grew between runs (approvals landed), so the K=14 holdout has 315 days vs 297; cross-K
comparisons are close, not exact.

**Verdict: FAIL against the Phase B bar for every arm and K on the realistic (uncoded@T) population.**

| exact codes+hours when proposed (holdout) | arm A K=7 | arm A K=14 | Sonnet K=7 | Sonnet K=14 | bar |
|---|---|---|---|---|---|
| all days: exact / coverage | 66.0 / 32.7 | 74.8 / 42.8 | 67.2 / 58.6 | 76.9 (CI 71-82) / 71.4 | >= 80 / >= 50 |
| **uncoded@T (realistic): exact** | 62.1 | 53.8 | 65.2 | **65.2** | >= 80 |
| false split (single-truth days) | 0.0 | 0.0 | 2.0 | 1.5 | <= 5 |
| single-truth days: exact | 75.3 | 88.8 | 81.6 | 88.7 | |
| split-truth days: exact (53 days) | 0.0 | 0.0 | 15.8 | 20.5 | |
| splits proposed (truly split / exact) | 0 | 0 | 19 (14 / 6) | 18 (14 / 8) | |

Baselines at K=7 (single code, at-submit rules): U-673 S1 7-day 56.9 % exact on 97.6 % coverage; U-749
vendor x project 37.6 %.

Findings:
- Arm A never proposes a split: the exact-code-set precedent rule matched 0 of 53 real split days
  (5-9 fell back to a single code). Later-dated evidence is only ~3 % of evidence lines - later days are
  usually still uncoded at T (HP2: 9/24 first reviewed 10/02, 9/25 and 9/28 on 10/05, 9/30 on 10/07).
  31 % of evidence enters via the no-coded_at date fallback.
- Sonnet is good on single-code days (82-89 % exact, 1.5-2 % false split) but mostly proposes only the
  dominant code on split days. The all-days 77 % at K=14 is lifted by ~100 days already coded before T;
  the 213 days a follow-up would actually see score 65 %.
- HP2 2026-09-29 (truth 35.00 4h / 44.00 2h / 51.00 2h for 4 men): arm A abstains at every K; Sonnet
  proposes 35.00 x 8h for three men and abstains on the fourth, at both K=7 and K=14. The real 10/16
  follow-up (Opus, ~17 days after the work, full thread context) is not reproduced.

Decision (Chris, 2026-11-02): close U-750 as FAIL; book a free follow-up measuring single-code-only
proposals with multi-task days flagged "may need splitting" (rescoring the cached Sonnet answers).

## Limits / simplifications

- IDF is computed once over every note in the corpus (label-free, but not as-of) — same limit as U-673.
- Evidence notes are whole-day notes: a split precedent day lends its full note to each of its codes, so
  it scores every one of its codes equally. Single-code precedent days carry most of the segment signal.
- "Segments disagree weakly" is not modelled separately: any two distinct resolved codes are a split
  candidate, gated only by the exact-code-set precedent rule.
- Labels are each line's current SCC; recodes after approval are not replayed.
- The arm-B token estimate is a character heuristic padded 1.5x. The per-call check keeps spend under
  `--budget-usd` only while a real call costs no more than its padded estimate (estimate-bounded, not a hard
  guarantee); the isolated ledger's pre-call gate then stops the next call. The budget is per invocation.
