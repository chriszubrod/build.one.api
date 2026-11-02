# U-745a — M0 under the live similar-bills token cap

Status: **run 2026-11-02 — verdict: do NOT flip `BILL_CODING_M0_RUNG_ENABLED` (rule 3).** Backtest
only; nothing in the live path changes. See [Results](#results-run-2026-11-02).

## Why

The U-698 backtest (`scripts/u698_decision_model_backtest.py`, results in
[`../u698/README.md`](../u698/README.md)) built its word neighbours from **every** PDF token of
every bill. The live similar-bills path does not: `config.py`
`similar_bills_max_tokens_per_bill = 50` and `SimilarBillsService._max_tokens_per_bill` keep only
each bill's top-50 tokens by IDF (`similar_bills.cap_tokens_by_idf`), for the corpus bills and
the query alike. So the shipped M0 (`entities/coding_suggestion/business/decision_model/m0_model.json`)
was trained and measured on neighbours it will never see in production. Its features F7–F10 (vote
share, best combined / match, neighbour counts, top combined) all move with the neighbour list.
This unit measures M0 on capped neighbours before `BILL_CODING_M0_RUNG_ENABLED` is flipped.

## The harness options

- `--max-tokens-per-bill N` (int ≥ 1; default none = the U-698 uncapped behaviour). It goes into the
  `u691.TokenSourceConfig` of **every** neighbour pass: the CV folds, the fixed split and the
  coded-cutoff sensitivity pass. u691's `token_profile` applies `cap_tokens_by_idf` to every bill it
  profiles. That covers the query bill and every prior, since each bill is profiled once, used as
  the query, then stored as a prior. A test pins both sides, with a case where query-only or
  priors-only capping gives different neighbours. The cap ranks tokens by the IDF this harness
  computes for that pass, which is leak-free: from bills strictly before the fold start (CV) or
  from the calibration window (fixed split). That IDF is fit on **uncapped** token sets, as the
  live snapshot fits its IDF before capping.
- `--score-model PATH`: a saved M0 JSON (`m0_model.json`, or a `u698_model_<stamp>.json` run
  output; the second has no top-level `features` key and is normalised from `m0.features`).
  - **Loading:** the file is read once, and its sha256 is taken from those bytes. The live loader
    `serve.load_m0_model` then parses a copy of the same bytes. Before that, the harness checks
    the file's feature list against `M0_FEATURES` (names and order) and fails loudly on a
    mismatch, before any population load.
  - **Scoring:** **no M0 is fitted or calibrated anywhere.** Every CV fold and the fixed split score
    their eval rows with the frozen weights, scales and isotonic map. They go through the same
    `score_candidates` call that `serve.m0_score` makes. Rows, baselines and metrics are unchanged.
  - **M1:** out of scope in this mode. It is skipped and labelled `skipped`, and G1 reads `SKIPPED`.
  - **Model JSON:** none is written for a frozen run. The model is the file itself, and its own
    provenance (path, sha256, trained_through, training data_hash, windows) sits under
    `score_model` in the summary.
  - **In-sample guard:** if `--eval-since` is on or before the file's `trained_through`, the run
    prints and the report header carries `IN-SAMPLE: eval window overlaps model training through
    <date>`. The summary then records `in_sample: true`. Otherwise it records `in_sample: false`.
    Fitted runs record `in_sample: null`.

Every run records `max_tokens_per_bill`, the M0 source (`fitted` / `frozen`) and
`population_data_hash`, the hash of the bills it loaded. The report header prints all three.

### Folds on a short window

CV folds are monthly from `--eval-since`. Every fold end is clamped to `--eval-until`, and empty or
reversed folds are dropped. The default window (2026-04-11..2026-10-10) still gives exactly the six
U-698 folds; a test pins them. A shorter window gives fewer folds: 2026-09-12..2026-10-31 gives
09-12..10-11 and 10-12..10-31. No row created after `--eval-until` is scored anywhere: not in
pooled CV, not in the fixed split, not in the gates.

### Live abstain rule

Live `serve.m0_pick` abstains when the filtered vote has no neighbours. The harness's headline M0
figure (`m0`) scores those rows anyway, as U-698 did, so arm 1 stays comparable. A cap shrinks
neighbour lists, so the headline flatters M0 against the vote more under the cap than without it.
Every mode block therefore also reports the following:

- `m0_live_abstain`: M0 top-1 with rows whose catalog-filtered neighbour list is empty counted as
  abstain = wrong, exactly as live. Its diff and sign-test p against the hist-strong-else-vote
  baseline are in `m0_live_abstain_vs_baseline`. Its live-mix figure is in
  `live_mix.m0_live_abstain`.
- `no_neighbour_rows`: the count of rows with no filtered neighbours.
- `no_neighbours` / `has_neighbours` strata. Every stratum also carries `m0_live_abstain`.

The report has a table of these.

### The decision metric: `m0_live_on_vs_off`

Raw `m0_live_abstain` against the hist-strong-else-vote baseline is still not the live
counterfactual, for two reasons:

- **Rung order.** Live `bill_coding_decision.choose_final` lets strong history win first, and M0
  only takes the vote's place at the last rung, so live M0 never acts on a strong-history row.
- **Fallbacks.** Live never falls back to weak history, and an M0 no-pick falls through to the vote.
  The U-698 baseline (`BillContext.baseline_pick`) does fall back to weak history when there is no
  vote. So a weak row with no neighbours, where weak history happens to be right, would count as an
  M0 loss even though live is blank both ways.

The decision metric therefore pairs **two live ladders on identical rows**:

- `live_off_pick` (M0 rung off): strong history, then the filtered vote, then blank.
- `live_on_pick` (M0 rung on): strong history, then M0 under the live abstain rule, then the
  filtered vote (an M0 no-pick falls through), then blank.

"Strong" and the vote are exactly the `history.strong` / `vote_top` the baseline uses. The two
ladders therefore agree on every strong row and on every weak row where M0 abstains; they differ
only where M0 picks on a weak row. `live_off` equals the baseline except on weak rows with no
filtered vote, where live is blank and the baseline uses weak history.

Each mode block reports the following:

- `m0_live_on` / `m0_live_off`: top-1 of each ladder;
- `m0_live_on_vs_off`: diff, sign-test p, fixed / broke and bootstrap CI;
- `m0_live_on_vs_off_weak_only`: the same comparison on weak-history rows, with their n;
- `live_mix.m0_live_on` / `live_mix.m0_live_off`: the live-mix figures.

The report has a table of these. **Decision rules 1 and 2 read `m0_live_on_vs_off`.**

Remaining approximations to live:

- **No LLM rung.** The harness has no LLM rung, which live runs between strong history and M0. So
  `m0_live_on_vs_off` measures M0 against the vote with no LLM ahead of either, over all weak-history
  rows. Live, the switch only matters on the weak rows where the LLM rung yields no valid pick
  (Claude and Foundry unavailable or capped, an invalid answer, or a Mode-B answer with no code).
  That subset is not random (plausibly harder, text-poor bills, where the capped vote is also
  weakest), so the lift there can differ in size **and in sign** from the harness figure. Read a
  passing rule 1 as "M0 beats the vote as a last rung", not as the size of the lift from flipping the
  switch; while the LLM ladder is healthy, the switch reaches few bills. The live scorecard's `m0`
  rung on `u701b-text-v2` rows is the measurement of that subset.
- **Neighbour pre-truncation (pre-existing, both ladders alike).** The harness takes the raw top 10
  neighbours and filters after; live filters every returned neighbour and then takes the top 10, so
  live can backfill where the harness sees fewer than 10.
- **Mode B strong predicate.** In Mode B, live judges history "strong" on the LLM's text project.
  The harness uses its own project-scoped history predicate, in both ladders alike.

## The arms

All arms use the U-698 defaults except for the flags listed.

| arm | flags | what it answers |
|---|---|---|
| 1 parity | *(none)* | Does the harness still reproduce U-698? See the parity rule below. If it does not, stop: no other arm is comparable. |
| 2a shipped, capped, in-sample | `--max-tokens-per-bill 50 --score-model <m0_model.json>` | **Informational only.** See below. |
| 2b shipped, capped, out-of-sample | `--max-tokens-per-bill 50 --score-model <m0_model.json> --eval-since 2026-09-12 --eval-until <run date>` | The shipped M0, frozen, on live-shaped neighbours, on bills it never trained on, vs the **capped** vote on the same rows. **Decision rule 1 rests on this arm only.** |
| 3 retrained, capped | `--max-tokens-per-bill 50` | M0 refit per fold on capped neighbours, over the full U-698 window (out-of-fold). |
| 3b retrained, capped, 2b window | `--max-tokens-per-bill 50 --eval-since 2026-09-12 --eval-until <run date>` | The refit on exactly 2b's eval rows: the like-for-like comparison with 2b. |

**Why 2a is in-sample.** The shipped model is U-698's last fit. Its `trained_through` is 2026-09-11,
the start of the last CV fold. It was fit on every bill before that date and calibrated on the last
20 % of those PDF bills. In the default eval window (2026-04-11..2026-10-10), CV folds 0–4
(04-11..09-10) are therefore training data: about **five sixths of the eval rows, and the
calibration slice too**. The fixed split's eval window overlaps the same way. 2a's top-1 and its
calibration (G4) are optimistic, so read 2a for direction only.

**2b's limits.** The window holds about one to two months of bills (09-12 to the run date), so its n
is small and its p-values are wide. It covers only bills already coded at load time. That biases it
toward bills that are quick to code: recent bills still waiting for a code are absent. Its E1 table
(the U-701b 500 bills) is mostly or wholly empty, because those bills lie earlier.

**3b is meaningful.** CV training is expanding, not windowed. With `--eval-since 2026-09-12`, fold 0
trains on every bill created before 09-12 (fit on the first 80 % of those PDF bills by date,
calibrated on the last 20 %) and scores 09-12..10-11. Fold 1 trains on everything before 10-12.
So 3b refits on roughly the shipped model's own training range, with capped neighbours, and scores
2b's rows out-of-fold. The fixed split in 3b still trains on the default calibration window
(2025-10-11..2026-04-10), so its gap to the eval rows is about five months; treat it as
informational. Arm 3 (full window) stays the comparison with U-698's published CV.

## Decision rules

Read the decision figures from the summary's `variants.<chosen>.cv_pdf_eval[mode]` block, per mode.

1. **Keep the shipped model** when arm **2b** passes both of the following in **both modes**. These
   are G2's thresholds, applied to the two live ladders:
   - `m0_live_on_vs_off.diff_points >= 2.0`;
   - `m0_live_on_vs_off.sign_test_p < 0.05`.

   Quote the weak-only n, the diff and the CI beside the result. The run must also state 2b's
   **G4** result, with its n: ECE <= 0.05 and precision at the 0.9 bar >= 0.87. Live M0 has no
   confidence bar (Gate 1 owner rule), so G4 informs the confidence column and does not block.

   **G3 is not part of this rule.** It is informational, and only for the U-698 design, where M0
   could override strong history. Live is protected by rung order: M0 never acts on strong-history
   rows. `gate_check`'s G3 code is unchanged.
2. **Retrain**: open a retrain unit with its own Gate 2 when arm **3b** clearly beats arm 2b on the
   same rows. That means 3b's `m0_live_on_vs_off` lift is materially larger than 2b's in both modes,
   beyond 2b's small-n noise. The `live_off` ladder is identical on identical rows, so this is the
   gap between the two models' `m0_live_on`. Arm 3 should agree in direction. The unit would ship a new `m0_model.json` trained on
   capped neighbours.
3. **Do not flip `BILL_CODING_M0_RUNG_ENABLED`** when neither 2b nor 3b clears rule 1's bar on
   `m0_live_on_vs_off`.

### Arm 1 parity rule

The population is reloaded from live prod, so it may have moved since U-698.

1. Compare arm 1's `population_data_hash` (summary, report header) with U-698's committed one.
   The U-698 hash is `data_hash` in `docs/design/u698/u698_model_20261029T185458Z.json`:
   `8f1efee83116cf7c45470f49bad634bbb081d0896711dff92a1e8bbcf958288f`.
2. **Equal hashes**: pooled CV M0 must match **64.8 / 63.3** (Mode A / B) exactly, to the published
   precision. Any difference is a harness regression: stop.
3. **Different hashes**: accept within **±0.5 point per mode**, and say in the results that the
   population moved. Expect this case: bills coded since 2026-10-29 change the hash.

### Reading gates at small n

The report prints the n behind every gate verdict:
- G1: the E1 rows per mode;
- G2 and G4: the CV rows per mode;
- G3: the Mode A strong-history rows and the override count;
- G5: the number of modes present.

Read every PASS with its n beside it. 2b's n is small, so a PASS there is weak evidence. **G3 passes
vacuously with zero overrides**: `fixed >= 1.5 × broke` holds at 0 >= 0.

## Run commands

From the repo root of a clean checkout. Each run reads production read-only and spends $0 on LLMs.
It needs roughly 3.8k blob reads, the same as U-698. Run the arms one at a time. Replace
`<run date>` with the run day (ISO).

Outputs go to `.data/u745a/` under the umbrella (derived data, never committed: the predictions CSV
carries bill ids and codes). Only each arm's `u698_report_*.md` and `u698_summary_*.json` are copied
into `docs/design/u745a/results/` and committed.

```sh
M0=entities/coding_suggestion/business/decision_model/m0_model.json

# arm 1: uncapped parity
.venv/bin/python -m scripts.u698_decision_model_backtest --run --out-dir /Users/chris/Applications/build.one/.data/u745a/arm1_parity

# arm 2a: shipped M0, frozen, cap 50, default window (IN-SAMPLE, informational)
.venv/bin/python -m scripts.u698_decision_model_backtest --run --out-dir /Users/chris/Applications/build.one/.data/u745a/arm2a_frozen_cap50_insample \
    --max-tokens-per-bill 50 --score-model "$M0"

# arm 2b: shipped M0, frozen, cap 50, out-of-sample window (decision arm)
.venv/bin/python -m scripts.u698_decision_model_backtest --run --out-dir /Users/chris/Applications/build.one/.data/u745a/arm2b_frozen_cap50_oos \
    --max-tokens-per-bill 50 --score-model "$M0" --eval-since 2026-09-12 --eval-until <run date>

# arm 3: M0 refit, cap 50, default window
.venv/bin/python -m scripts.u698_decision_model_backtest --run --out-dir /Users/chris/Applications/build.one/.data/u745a/arm3_retrained_cap50 \
    --max-tokens-per-bill 50

# arm 3b: M0 refit, cap 50, the 2b window
.venv/bin/python -m scripts.u698_decision_model_backtest --run --out-dir /Users/chris/Applications/build.one/.data/u745a/arm3b_retrained_cap50_oos \
    --max-tokens-per-bill 50 --eval-since 2026-09-12 --eval-until <run date>
```

Without `--run`, any of these prints the population counts only. With `--score-model`, a bad model
file fails before the population load. 2b must print no `IN-SAMPLE` line. If it prints one, the
window is wrong.

## Known differences from live

- **IDF source.** The harness ranks tokens for the cap by the **as-of** IDF of each pass: bills
  before the fold start (CV), or the calibration window (fixed split). Live ranks by the IDF of its
  **corpus snapshot**, built from every corpus bill at snapshot time. The two can disagree on which
  50 tokens survive, mostly for bills near the top-50 boundary. The as-of IDF is used deliberately:
  the snapshot IDF would leak later bills into earlier decisions.
- **Base rate (feature F15).** The frozen model scores harness features. Those come from the same
  `build_context` and `m0_features` code that `serve.py` calls, so feature semantics match. The
  inputs differ in one place: the harness `base_rate` is the as-of share of strictly-earlier
  labelled bills, while live `_LiveIndex.base_rate` uses cached company-wide label counts. That
  difference is the same leak-avoidance choice as the IDF.
- **Abstain rule and rung order.** The live abstain rule is reported as `m0_live_abstain`. The live
  rung order (strong history, then M0 or the vote, never weak history) is reported as the paired
  ladders `m0_live_on_vs_off`, which drive the decisions. The headline `m0` keeps U-698's
  score-every-row rule for parity.

## Results (run 2026-11-02)

Prod read-only, $0 LLM, all five arms on api `b491ce52` + this unit's harness. Reports and summaries
are in [`results/`](results/); the per-row predictions CSVs stay in `.data/u745a/` (never committed).
Population data_hash `f7ac6f3b…19c1` for every arm (U-698's was `8f1efee8…288f`: bills have been
coded since 10-29). Numbers are percent top-1, Mode A / Mode B.

### Arm 1 — parity: PASS

The hash differs, so the ±0.5 tolerance applies. M0 64.9 / 63.3 vs U-698's 64.8 / 63.3; baseline
62.3 / 60.7 exact; the 500-bill E1 table reproduces exactly (ladder parity +0.0 / +0.0). The harness is
comparable. Uncapped, the decision metric `m0_live_on_vs_off` is **+2.4 / +2.2** (p 8e-7 / 3e-5).

### The decision metric, `m0_live_on_vs_off`

| arm | rows | live_off | live_on | diff | p | fixed / broke | 95 % CI |
|---|---|---|---|---|---|---|---|
| 1 uncapped (refit, CV) | 2,325 | 62.3 / 60.7 | 64.7 / 62.8 | +2.4 / +2.2 | 8e-7 / 3e-5 | 92/36 · 94/44 | +1.5..+3.4 · +1.2..+3.1 |
| 2a shipped, cap 50, **in-sample** | 2,325 | 63.1 / 61.3 | 65.1 / 62.9 | +1.9 / +1.6 | 5e-5 / 9e-4 | 83/38 · 78/41 | +1.1..+2.8 · +0.7..+2.5 |
| **2b shipped, cap 50, out-of-sample** | **390** | 64.9 / 62.8 | 64.9 / 63.1 | **+0.0 / +0.3** | **1.0 / 1.0** | 11/11 · 12/11 | −2.3..+2.3 · −2.1..+2.6 |
| 3 refit, cap 50 (CV, out-of-fold) | 2,325 | 63.1 / 61.3 | 65.1 / 63.1 | +2.0 / +1.8 | 4e-5 / 2e-4 | 85/39 · 81/39 | +1.1..+3.0 · +0.9..+2.8 |
| 3b refit, cap 50, 2b's rows | 390 | 64.9 / 62.8 | 65.4 / 63.8 | +0.5 / +1.0 | 0.82 / 0.52 | 11/9 · 13/9 | −1.8..+2.6 · −1.3..+3.3 |

### Verdict against the decision rules

- **Rule 1 (keep the shipped model): FAIL.** On bills it never saw (2b, 09-12..11-01), the shipped M0
  ties the capped vote (+0.0 / +0.3, p = 1.0) — nowhere near +2.0 at p < 0.05. n = 390, so the CI
  (about ±2.3 pts) cannot rule a 2-point lift in or out: "not shown", not "shown to be zero".
- **Rule 2 (retrain): not met.** The capped refit on the same rows (3b) is +0.5 / +1.0 (p 0.82 /
  0.52): directionally better than the shipped model, not materially.
- **Rule 3: neither 2b nor 3b clears rule 1 → do not flip `BILL_CODING_M0_RUNG_ENABLED`.**

### What else the arms show

- **The cap helps the vote, which shrinks M0's edge.** Capped, the vote baseline rises (62.3 → 63.1
  Mode A on the full window) and M0's lift falls from +2.4 / +2.2 uncapped to +2.0 / +1.8 (arm 3,
  refit) and +1.9 / +1.6 (arm 2a, shipped, in-sample). Even in-sample the shipped model no longer
  clears +2.0 under the live cap.
- **The lift is concentrated in the earlier months.** Over six out-of-fold months, the capped refit is
  +2.0 / +1.8 and significant; on the last seven weeks (3b) it is +0.5 / +1.0. The weak-history share is
  similar in both (weak n 183 / 390 vs 1,139 / 2,325), so this is not a mix shift. Whether the recent
  period is noise at n = 390 or a real fade is the question the December re-run answers.
- **Calibration (G4) fails in every arm**, informational only (live M0 has no confidence bar).
- G1 and M1 are out of scope here (G1 PASS on arm 3 concerns M1, not the M0 switch).

### What follows

- Keep `BILL_CODING_M0_RUNG_ENABLED` unset. Keep the M0 shadow: it costs nothing and gives the live
  scorecard its `m0` rung.
- Re-run 2b and 3b with the December U-698 re-run, when the out-of-sample window is about twice as
  long; the switch-on bar stays "live scorecard m0 >= similar_vote on the same v2 rows" **and** rule 1
  on the longer 2b.
- Do not ship the arm-3 capped refit: its lift on the recent rows is not distinguishable from the
  shipped model's.
