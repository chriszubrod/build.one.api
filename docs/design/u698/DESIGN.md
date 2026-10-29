# U-698 — Trained decision model for the bill cost-code suggestion (DESIGN, phase 1)

Status: design only, 2026-10-29. Worktree `unit/u-698` at `81804905` (origin/master). No code, no
backtest run, no data read beyond the committed, ids-only U-701b CSVs. Phase 2 needs owner approval.

Scope note: U-698 was first booked as "vector neighbours in the live suggester". U-697 measured that
negative and suggested closing or re-scoping it (`docs/design/u697/README.md:184-186`). The board row
(`build.one.team/BOARD.md:620`) re-scopes it to this unit: a trained, non-LLM model that decides which
signal to trust. **Word matching only, no vectors.**

---

## 1. Problem and the decision being learned

### 1.1 What decides today (live ladder V2, U-701b3)

`choose_final` (`entities/coding_suggestion/business/bill_coding_decision.py:400-465`) is a fixed
priority list:

- **Mode A** (user chose the project): strong history wins. If history is not strong, take the model
  pick (Sonnet if it answered, else Haiku at any confidence, else Foundry at ≥ 0.5), then the filtered
  similar-bills vote, then blank (`:424-429`, `:312-324`). When history is strong, the code returns
  before any model call (`:633-647`).
- **Mode B** (no project): one LLM answer supplies both code and project P (`mode_b_answer`,
  `:339-354`). Strong history *for P* overrides the code. With no P, strong vendor-wide history
  applies, then the model code, then the vote (`:430-463`).
- **"Strong"** means `raw_score = top_count / (total_count + 2) ≥ 0.60`
  (`bill_suggestion_service.py:39`, `:84-85`, `:195-202`; `history_is_strong`,
  `bill_coding_decision.py:190-191`).
- **Escalation:** Haiku is accepted at ≥ 0.70; below that the bill goes to Sonnet
  (`intelligence/cascade/ladder_config.py:32-38`; `DROP_ESCALATE_BELOW`,
  `bill_coding_decision.py:76`).

Every threshold is a single hand-set number. Each one ignores what the other signals say.

### 1.2 The decision to learn

For each bill and mode, build a **candidate set** C:

- the top 3 history codes,
- the top 3 codes by filtered word-vote weight,
- Haiku's code,
- Sonnet's code (when it ran).

That is at most about 8 distinct codes. The model scores every candidate c in C with
P(c is the label | features), picks the arg-max, and **abstains** (leaves the code blank) when the
calibrated probability is below a bar. That is ranking plus a reject option. It is not a classifier
over all ~400 codes, because the label is in C for most bills (see the ceiling below).

### 1.3 Why a model and not more rules (measured)

- **Hand-tuned overrides wash out.** Letting similar-bills evidence override strong history fixed 11
  and broke 9 on the 500 bills (`docs/design/u701b/README.md:26,39`). A blanket line-items rule cost
  2–3 points (`:25,38`). Both are on/off rules. The signal they carry is conditional, and a rule
  cannot express "override only when the neighbours are strong and agree with the AI".
- **The conditional signal exists.** These counts come from the committed U-701b E1 CSV. They are
  in-sample and illustrative only. I recomputed them for this doc from ids and codes only.
  - When history's top code and the ladder's answer **disagree** (Mode A 121 bills / Mode B 179):
    history is right on 35 / 37, the ladder on 45 / 88.
  - When the word vote's top neighbour **sides with the ladder**, the ladder is right on 30/45 (A) and
    66/82 (B).
  - When the top neighbour sides with history, history is right on only 16/36 and 18/35.
  - Agreement is a strong feature that today's ladder never reads.
- **Ceiling.** The label is among {history top code, Haiku, Sonnet, top-neighbour code} on
  **76.8 % (A) / 77.8 % (B)** of the 501 ids. The live ladder scores 64.2 / 64.4. A perfect chooser
  over the existing signals would be worth about 13 points. A realistic model captures a fraction of
  that.
- **Named misses.**
  - **Walker Lumber #273811** (bill 40921, label 157). In Mode A, project history 0.71 picks 96 and
    Haiku answers 158 @ 0.80. Both are wrong, and the bill's vendor stratum is `<50%`
    (U-701b CSV; README `:40-43`). In Mode B, Sonnet gets it right.
    - The model's lever: a vendor whose overall history is inconsistent should discount a strong
      project-scoped share.
    - Even so, A cannot be fixed unless the label is in C (157 came only from Sonnet, which never ran
      in A). This is the case for the escalation sub-decision in §4.4.
  - **Nacho's #417821** (bill 40939). Strong vendor history (49.00) overrides drywall (35.01). It is
    not in the scored CSV: the bill was still in review on 2026-10-28.
    - The model's lever: when the word vote's top codes (built from the vendor's own PDF lines) and
      the AI agree against history, the item lines must outweigh vendor history.
    - It must be checked by hand on the phase-2 output once the bill is coded.

---

## 2. Label and leakage rules

### 2.1 Label

**Primary label:** the bill's **dominant SCC by absolute line amount** (ties go to the lowest line
id). This is the definition every number we compare against uses:

- the U-653b load SQL (`scripts/u653b_backtest.py:46-85`), reused by U-691/U-694/U-697,
- the U-654a/U-701b harnesses (`label_scc_id` in the CSVs).

The brief asks for the "final line-1 code". On single-code bills the two definitions are identical.
They differ only on `MixedCode = 1` bills (`u653b_backtest.py:121`). The phase-2 report gives **both**
labels on the mixed-code stratum, but trains and gates on the dominant label so that 64.2 / 64.4
stays comparable.

**Multi-line bills:** one label per bill. Multi-line or mixed status becomes a feature (§3) and a
reporting stratum, not a separate target.

**Population:** completed, non-draft, coded bills. About **19.2k in total** (19,238 in
`u691/README.md:82`; 19,283 in `:167`). Only **3,813** have usable PDF text
(`u697/README.md` population table). PDF links are sparse before 2026: the calibration window has
1,338 PDF bills out of 16,659 (`u691/README.md:167`).

### 2.2 Leakage rules (hard requirements, checked by tests in phase 2)

1. **No coding-time text.** Line descriptions and memo are never used, raw or masked. 69 % of
   descriptions contain words from their own label code's name (`u691/README.md:97-116`), and masked
   descriptions add nothing once PDF text is present (`:180`). The only text used is the
   vendor-written PDF extraction, the same text `BillCodingInputs.bill_text` carries
   (`bill_coding_decision.py:107`).
2. **Strictly-earlier priors.** History counts, vote neighbours and vendor statistics use only bills
   with `created < bill.created`. An equal timestamp does not count, and the bill itself is excluded
   (`u691/README.md:29`). Reuse `is_prior_eligible` (`similar_bills.py:200`) and the U-653b history
   votes. Do not re-implement them.
3. **As-of catalog.** Candidate codes and neighbour labels are filtered through the as-of catalog at
   `Bill.CreatedDatetime` (`bill_text_suggestion_service.py:160-162`). This excludes:
   - suspense, holding and ambiguous codes,
   - codes created after the bill (U-696 F1, `u696/README.md`; `filtered_vote`,
     `similar_bills.py:332-359`).
4. **IDF is fit on the calibration window only** (`u691/README.md:25-26`). Under time-series CV, it is
   fit on each training fold's dates only.
5. **The model never trains on a bill later than the one it scores.** Train/test splits are by time
   (§5). Vendor-level target statistics (e.g. "this vendor's historical history-hit rate") are
   computed rolling, from strictly-earlier bills.
6. **Known approximation, to be measured.**
   - "Strictly earlier" uses the prior's *created* time, not the time it was coded. A prior created
     before the query but coded after it leaks its label slightly.
   - Phase 2 measures the gap between created and coded time on the PDF population. If more than 5 %
     of priors were coded after the query bill's creation, it reruns with the coded-time cut-off
     (open question Q4).
7. **Privacy.** Outputs are ids, codes and numbers only (`assert_outputs_privacy_safe`, as U-697). No
   bill text is written anywhere.

---

## 3. Features

Each row of the training table is one (bill, mode, candidate c). "Live" means the signal can be
computed at **drop time** (PDF drop, `stage="drop"`) and on the **background tick**
(`tick_full` / `tick_escalate`).

| # | feature | as-of source | live drop / tick | paid LLM? |
|---|---|---|---|---|
| F1 | c is history top-1 / top-2 / top-3 (one-hot rank) | U-653b strictly-earlier vendor (B) or vendor+project (A) votes | yes / yes | no |
| F2 | history share of c: `count_c / (total + 2)` | same | yes / yes | no |
| F3 | history total prior bills (log1p) | same | yes / yes | no |
| F4 | history top-1 share minus top-2 share (dominance margin) | same | yes / yes | no |
| F5 | vendor-wide consistency of the vendor (the stratum signal; Walker = `<50%`) | strictly-earlier vendor bills | yes / yes | no |
| F6 | project-scoped share ÷ vendor-wide share for c (Mode A only) | same | yes / yes | no |
| F7 | vote weight share of c among filtered top-10 neighbours | `filtered_vote` / `vote_from_scored_neighbours` (`similar_bills.py:222-237`) | yes / yes | no |
| F8 | best neighbour match strength for c (max `combined`, and `match` = `token_match_ratio`) | `Neighbour` (`similar_bills.py:252-278`) | yes / yes | no |
| F9 | count of neighbours with label c; same-vendor share of those | same | yes / yes | no |
| F10 | filtered neighbour count; top-1 combined score of the bill | same | yes / yes | no |
| F11 | agreement flags: c is history top-1 AND vote top-1; and (if present) c = Haiku, c = Sonnet; number of sources naming c | derived | yes / yes | partly |
| F12 | cold-start: zero strictly-earlier bills for vendor; zero earlier PDF bills for vendor (U-697 stratum) | strictly-earlier | yes / yes | no |
| F13 | log bill total; amount ratio to c's prior median for this vendor | header total (drop) / bill total (tick) | yes (header read) / yes | no |
| F14 | mode (A/B); line count bucket; PDF text length bucket | bill / extraction | line count: **no at drop** (lines not yet made); yes on tick | no |
| F15 | c's global prior frequency in the as-of window (base rate) | strictly-earlier all bills | yes / yes | no |
| L1 | c = Haiku answer; Haiku confidence (raw) | cascade primary | drop yes (unless Mode A strong-history short-circuit); tick yes | **Haiku** |
| L2 | c = Sonnet answer; Sonnet confidence; Haiku/Sonnet disagree | cascade escalate | tick only (drop runs Haiku only, `ladder_config.py:80-91`) | **Sonnet** |
| L3 | Haiku "followed top neighbour" | derived from L1 + F7 | yes | Haiku |

Exclusions: memo, line descriptions, the bill number, the creator user, intake source, and anything
written at coding time. F14's line count is computed from the *vendor's* extracted line items, never
from the coded BillLineItems.

### 3.1 The small-n problem for LLM features

The only labelled LLM answers are the U-701b E1 CSV: **501 ids × 2 modes, Haiku on all, Sonnet on the
26.4 % escalated** (`u701b/README.md:30`; `escalate_scc_id` populated on 265 of 1,002 text rows). That
is far too few rows to fit about 10 LLM interaction weights on top of 20 others without overfitting.
The CSV also carries no history share or vote weights, so phase 2 must rebuild F1-F15 from the
database for those ids and join on (BillId, mode).

**Recommended: a two-layer model.**

- **Layer 1, non-LLM ranker (M0).** Features F1-F15 only. Trained on the PDF population (~3.8k bills,
  time-split) plus history-only rows from the ~15k pre-2026 bills, which carry F7-F10 as missing with
  an indicator. Layer 1 replaces "history if strong, else vote", runs free on every bill, and is the
  model that can be measured at scale.
- **Layer 2, LLM stacker (M1).** A tiny logistic layer on the 500 bills: about 6 parameters over
  M0's calibrated probability for c, plus L1/L2 indicators, Haiku and Sonnet confidence, and the
  agreement count.
  - Fit with **5-fold cross-fitting split by time**.
  - Strong L2 regularisation. Weights must be sign-constrained or reviewed by eye.
  - Reported out-of-fold only.
- **Grow the layer-2 sample from live data (U-744), not more paid backtests.** Live rows already
  persist per-rung code and confidence, plus the filtered vote, in:
  - `dbo.BillIntakePrediction` (drop; `bill_intake_prediction/business/service.py:164-177`),
  - `dbo.BillTextCodingSuggestion` (tick; `bill_text_suggestion_service.py:300-317`).

  U-744's scorecard joins those to final codes. At roughly the current drop volume, that gives on the
  order of 400 labelled bills per month (to be confirmed from U-744). That is a second, independent
  500-bill layer-2 set within about 6 weeks, at no backtest spend.
- **Selection bias to correct for.** Live never calls an LLM on Mode-A strong-history bills
  (`bill_coding_decision.py:633-647`), so live L1 is *missing not at random* there.
  - Layer 2 is applied only where an LLM answer exists. Mode-A strong-history bills are decided by
    layer 1 alone, which is the live behaviour anyway.
  - U-744 rows must record whether the LLM was skipped (`DecisionSource = history` with no primary
    model).
- **Ask of U-744 (and later of the wiring unit).** Live rows do not store F2-F4, F8 or F9 (only the
    vote code, share and neighbour count). Ask U-744 to snapshot a compact feature vector per
    decision, or accept that phase 2 recomputes the features as of `created` from the database. The
    recompute is exact as long as priors are strictly earlier, so this is a nice-to-have, not a
    blocker.

---

## 4. Model family, calibration, explainability, runtime

### 4.1 Family

| option | pros | cons | verdict |
|---|---|---|---|
| **Conditional-logit / per-candidate logistic regression** (softmax over C) | ~25 weights; explainable by per-feature contribution; trains in pure Python; inference is a dot product | misses interactions unless hand-crossed (F11 supplies the key ones) | **primary** |
| Gradient-boosted trees (pairwise or pointwise) | learns interactions (consistency × share) | needs sklearn or lightgbm to train; ~100s of trees to walk at inference; harder to explain | **comparison arm only**, offline, if Q1 allows a dev-only dependency |
| Keep rules, tune thresholds by grid search | zero new machinery | measured to wash (§1.3) | baseline only |

Dependencies:

- `requirements.txt` has **no** numpy, scipy or scikit-learn, and none of them is in the venv. U-697
  hit the same limit and computed cosines in pure Python (`u697/README.md`, "Truncation, spend and
  runtime").
- Recommendation: **no new runtime dependency.** Train with a pure-Python solver.
  - The table is about 30k candidate rows × ~25 features.
  - Batch gradient descent with L2 converges in seconds to a minute.
  - Use deterministic seeds.
- The model artefact is a small JSON file: weights, feature means and scales, calibration map,
  training window, data hash and git sha. It is versioned under `entities/coding_suggestion/`
  (wiring unit) or `docs/design/u698/` (phase 2).
- If the GBDT arm is wanted, put scikit-learn in a dev-only requirements file that the deployed image
  never installs. That is owner question Q1.

### 4.2 Calibration

- The softmax output is mis-calibrated under L2 regularisation and class imbalance.
- Fit **isotonic regression (pool-adjacent-violators, ~20 lines of pure Python)** on the chosen
  candidate's score against correctness. Fit it on a held-out *time* slice, never the training slice.
  - With fewer than 300 calibration bills, use Platt (1-D logistic) instead.
  - This mirrors U-653b's decile binning (`u653b/README.md:39`).
- Report expected calibration error (ECE, 10 bins) and reliability tables per mode.
- Purpose: the "auto-accept at p ≥ bar" threshold then means what it says. "0.90" should be right
  about 90 % of the time, not like Haiku's raw 0.80 on Walker.

### 4.3 Explainability

Every decision emits:

- the winning code,
- the calibrated probability,
- the runner-up,
- the top 3 signed feature contributions (weight × standardised value), in plain words. For example:
  "history share 0.71 (+), vendor consistency below 50 % (−), vote and AI agree on another code (−)".

Logistic regression makes this exact. This is the field the review UI and the U-744 scorecard can
show.

### 4.4 Out of scope for this model, deferred

- **Whether to escalate to Sonnet** is a separate decision (value of information). It needs Sonnet
  answers on non-escalated bills, which exist only as the S0b/S2b arms (217 bills in both modes,
  `u696/README.md:112-120`) and no production-shaped set. Book it as a follow-up once M1 exists.
- Walker A (label reachable only via Sonnet) is the motivating case.

### 4.5 Size and latency

- **Inference:** fewer than 10 candidates × 25 multiply-adds plus an isotonic lookup, which is
  microseconds. No change to the drop's latency budget.
- **Features:** all non-LLM features come from data the drop already computes. History rows and
  neighbours are already loaded (`decide_bill_coding`, `bill_coding_decision.py:585-602`). F2-F4 need
  the full per-code vote counts, not just the top code. That is a small change to the history read in
  the wiring unit.
- **Artefact:** under 10 KB.
- **Retrain:** monthly. See R5.

---

## 5. Offline evaluation protocol

1. **Fixed split, for comparability.**
   - Calibration window 2025-10-11..2026-04-10 trains M0. Evaluation window 2026-04-11..2026-10-10 is
     scored. These are the same windows as U-691/U-694/U-697 (`u697/README.md:3-6`).
   - Weakness: only 1,338 calibration bills have PDF text, so vote features are thin in training.
2. **Expanding-window time-series CV (primary for M0).**
   - Six monthly folds, 2026-04 through 2026-09. For fold m, train on every bill created before month
     m and test on month m. Use the last 20 % of the training dates for isotonic calibration.
   - Report the pooled out-of-fold eval-window number. It is the closest analogue to monthly retrain.
3. **Population:** the PDF population in the eval window (~2,325 bills, `u697/README.md`). Bills
   without PDF text get M0 with missing vote features and are reported separately.
4. **Metrics, always per mode A and B, by bill and by dollar:**
   - top-1 over all bills (abstain = wrong), the house metric;
   - coverage and precision at calibrated bars 0.5 / 0.7 / 0.8 / 0.9;
   - ECE and a reliability table;
   - per-stratum top-1: vendor consistency `<50/50-70/70-90/≥90`, cold start, mixed code, Mode-A
     strong-history;
   - number of times M0 overrides strong history, with fixed/broke counts.
5. **Baselines on the same bills:**
   - history-if-strong-else-vote: 62.3 / 60.7 on the PDF eval population (`u697/README.md:50-51`);
   - history alone: 58.1 / 45.2 (`:52`).
6. **The 500-bill table (M1 and the headline gate).**
   - Bills: the U-701b E1 ids (`--bill-ids-from docs/design/u701b/u654a_predictions_claude-haiku_20261028T150518Z.csv`),
     the 500 in the eval window.
   - Rows: live ladder (64.2 / 64.4), word vote (60.0 / 59.2), M0, M1 out-of-fold.
   - **Weighting caveat.** The sample is *stratified*: about 83 bills per stratum, six strata, which I
     counted from the CSV (`u654a/README.md:15`). Raw 500-bill accuracy over-weights cold-start and
     mixed-code bills. Also report a **population-reweighted** figure using eval-window stratum shares.
7. **Significance.**
   - Two-sided exact sign test on the bills where one arm is right and the other wrong, per mode, as in
     U-697 and U-701b.
   - M1 is out-of-fold, so the paired test against the ladder is honest.
   - Also report a bootstrap 95 % confidence interval of the difference (1,000 resamples by bill).
8. **Cost per bill.**
   - M0: $0.
   - M1: the ladder's $0.0056 per (bill, mode) (`u701b/README.md:30`). It is unchanged because M1
     consumes the same calls.
   - No new Claude spend is needed for phase 2 (§7).

---

## 6. Success bar for wiring

**Gate for a later wiring unit.** All of these must hold:

- **G1, M1 on the 500:** beats the live ladder (64.2 / 64.4) by **≥ 3 points in at least one mode
  with sign-test p < 0.05, and is not lower in the other mode** (difference ≥ −0.5).
  - Why 3 points: with about 60-80 discordant bills, 3 points (net 15 bills) is roughly the smallest
    lift that reaches p < 0.05.
  - If only reweighted figures clear the bar, report that; it does not pass.
- **G2, M0 on the PDF eval population:** beats history-if-strong-else-vote (62.3 / 60.7) by
  **≥ 2 points with p < 0.05** in time-series CV. This is the free path and must stand on its own.
- **G3, no regression on Mode-A strong-history bills.** Top-1 within −1.0 point of history on that
  stratum, and override fixed ≥ 1.5 × broke. This blocks the "wash" in `u701b/README.md:39`.
- **G4, calibration:** ECE ≤ 0.05 on the eval slice. At the proposed auto-accept bar, observed
  precision ≥ the bar minus 0.03.
- **G5, named cases:** Nacho's #417821 is decided correctly, or the miss is explained in the report.
  Walker #273811 is reported either way.

**Recommendation: phase 2 = backtest-only build.** Harness, model, report and tests. Nothing goes in
the live path. Wiring is a separate unit, gated on G1-G5 and on the U-744 live sample confirming the
direction on at least 300 fresh bills.

---

## 7. Phase-2 scope

| file | purpose | size |
|---|---|---|
| `entities/coding_suggestion/business/decision_model.py` | pure-Python feature builder (F1-F15, L1-L3), candidate set, logistic fit/predict, isotonic, explain; no I/O | ~400 lines |
| `scripts/u698_decision_model_backtest.py` | loads the PDF population via the U-691 loader (`load_pdf_text_by_bill_id`, `score_all_rolling`), builds rolling as-of features, runs fixed split + monthly CV, joins U-701b CSV for M1, writes report/summary JSON + model JSON; read-only transaction; `--dry-run` default | ~700 lines |
| `tests/test_u698_decision_model.py` | leakage guards (strictly-earlier, as-of catalog, no description/memo columns), deterministic fit on a fixture, calibration monotone, explain sums, mutation-proved where a guard is load-bearing | ~400 lines |
| `docs/design/u698/README.md` + outputs | results, ids-only | — |

Total about 1.5k lines. Non-P0. Passes 1 and 2 apply. Pass 3 applies only if the reviewers judge the
read path P0.

**Spend:** $0 Claude. The LLM features reuse the committed E1 CSV. A fresh paid arm (for example
Sonnet on all 500, for §4.4) would go only through the U-702 isolated ledger
(`claude_budget.use_isolated_spend_ledger`, `scripts/u654a_text_backtest.py:4563`) with
`--max-cost-usd`, after a separate ask. Prod reads: one read-only load plus about 3.8k blob reads, the
same as U-697.

---

## 8. Risks and open questions for the owner (recommended answer in bold)

- **Q1. Add scikit-learn?**
  - **No runtime dependency. Pure-Python logistic regression and isotonic.** GBDT runs as an offline
    comparison only if you approve a dev-only requirements file. Otherwise skip GBDT.
- **Q2. Label: dominant-by-amount or line-1?**
  - **Dominant-by-amount**, for comparability with every prior number. Line-1 is reported on the
    mixed-code stratum.
- **Q3. Training population: the PDF bills only (~3.8k), or all 19k with vote features missing?**
  - **Both. Gate on PDF-population CV**, since every live drop has a PDF. The 15k history-only rows
    are kept only if they improve the CV number.
- **Q4. Coded-time vs created-time "strictly earlier".**
  - **Measure the gap in phase 2.** Rerun with coded time if more than 5 % of priors are affected.
    This needs the coding-completion timestamp. Phase 2 will find the column and cite it.
- **Q5. Is the LLM layer (M1) worth fitting on n = 500?**
  - **Yes, as out-of-fold stacking with about 6 parameters.** Confirm it on U-744 live rows before
    wiring. Do not buy more backtest LLM calls for it.
- **Q6. Abstain policy.**
  - **Keep "abstain = wrong" as the headline metric.** Propose the live auto-accept bar from the
    calibrated curve, but leave choosing it to the wiring unit.
- **Q7. Escalation decision (when to call Sonnet).**
  - **Out of scope here; book it as a follow-up** once M1 exists. It is the only route to fixing
    Walker-type Mode-A misses.

**Risks:**

- **R1. Small layer-2 n.** Overfitting shows up as an optimistic number. Mitigations: out-of-fold
  only, sign constraints, and the U-744 confirmation.
- **R2. Stratified 500 ≠ population.** Mitigation: reweighted figures (§5.6).
- **R3. Feature drift.** The vote corpus grows, and the PDF share rises after 2026. Mitigation:
  monthly CV is the retrain simulation. Alert if live ECE exceeds 0.08 (wiring unit).
- **R4. Live/offline skew.** Line count is unavailable at drop (F14), and live rows lack F2-F4/F8-F9.
  Mitigation: train a drop-variant of M0 without F14, and have the wiring unit snapshot features.
- **R5. Retrain governance.**
  - A monthly retrain changes behaviour silently. Each retrain must beat the incumbent on the last
    month's out-of-fold data before it is promoted.
  - The model JSON is versioned. Rollback = point back to the previous file.
