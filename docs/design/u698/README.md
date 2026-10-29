# U-698 phase 2 — trained bill-coding decision model: backtest results

Status: **harness built, not yet run.** Design and owner answers: [`DESIGN.md`](DESIGN.md) (Gate
2026-10-29: no scikit-learn; dominant-by-amount label; gate on the PDF bills; measure coded-time
leakage; M1 out-of-fold on the U-701b E1 bills; abstain = wrong; escalation decision out of scope).
Backtest only. Nothing in the live path imports the model.

## How to run

From the repo root (prod read-only, $0 LLM spend, about 3.8k blob reads, same as U-697):

```sh
.venv/bin/python -m scripts.u698_decision_model_backtest          # dry-run: population counts only
.venv/bin/python -m scripts.u698_decision_model_backtest --run    # train + evaluate + write outputs
```

If the Q4 line says `rerun_with_coded_cutoff_recommended: true`, rerun with
`--prior-cutoff coded` and report both. That run admits priors whose coded time is unknown, and
prints a fixed-split sensitivity line with those priors dropped (`--unknown-coded drop` makes the
drop the main arm). `--live-weak-share` (default 0.68, from the U-744 live scorecard) sets the
live-mix weighting. `--backfill-coded-at-created` treats `StatusOrigin = 'backfill'` bills as coded
at creation; by default their coded time is unknown.

Outputs land here, stamped, and each one passes `assert_outputs_privacy_safe`:

- `u698_predictions_<stamp>.csv`: one row per (bill, mode), with ids, codes, probabilities and the
  top-3 feature contributions;
- `u698_summary_<stamp>.json`: every metric below;
- `u698_model_<stamp>.json`: M0 weights, scales, calibration map, M1 weights for review, windows,
  data hash and git sha;
- `u698_report_<stamp>.md`.

## Code

| file | role |
|---|---|
| `entities/coding_suggestion/business/decision_model/features.py` | label, `AsOfIndex` (strictly-earlier priors), U-653b history scope, candidate set, M0 / M1 features |
| `entities/coding_suggestion/business/decision_model/learn.py` | L2 logistic regression (Newton, sign constraints), isotonic / Platt, ranking + abstain, explanation |
| `entities/coding_suggestion/business/decision_model/stats.py` | sign test, paired bootstrap, ECE / reliability, coverage at bars |
| `scripts/u698_decision_model_backtest.py` | population load, rolling neighbours, CV + fixed split, M1 cross-fitting, metrics, gates, outputs |
| `tests/test_u698_decision_model.py` | fakes only, with mutation-proved guards (as-of cutoff, out-of-fold, label, privacy) |

## Results (run 2026-10-29, stamp `20261029T185458Z`, prod read-only, $0)

Committed: `u698_report_20261029T185458Z.md`, `u698_summary_20261029T185458Z.json`,
`u698_model_20261029T185458Z.json`. The per-row predictions CSV is kept locally, not committed.

### Population

| item | count |
|---|---|
| coded bills | 19,348 |
| PDF-linked (usable text) | 3,822 |
| eval window, PDF | 2,325 |
| calibration window, PDF | 1,327 |
| history-only (bill, mode) rows | 31,004 |
| E1 bills scored | 500 of 501 ids |

### Q3: training variant

Kept **`pdf_plus_history`** on fold 0's calibration slice (48.5 vs 47.2 top-1). Pooled CV, for
information: pdf_only 64.6 / 63.1, pdf_plus_history 64.8 / 63.3 (Mode A / B).

### M0 (no LLM), expanding monthly CV, PDF eval population (n = 2,325 per mode)

| mode | M0 | hist-strong-else-vote | history | diff | p | fixed / broke | live-mix M0 / baseline |
|---|---|---|---|---|---|---|---|
| A | **64.8** | 62.3 | 58.1 | +2.5 | 4.9e-07 | 98 / 39 | 61.2 / 57.9 |
| B | **63.3** | 60.7 | 45.2 | +2.6 | 5.6e-06 | 116 / 56 | 62.3 / 59.5 |

Baseline hits M0 could not pick: 0.0 %. The baseline reproduces U-694's 62.3 / 60.7 exactly.

### The 500-bill table (E1 ids)

| mode | ladder recomputed / ref (parity) | word vote | M0 | M1 forward-chained | M1 cross-fit | diff fwd (p) | diff cross-fit (p) |
|---|---|---|---|---|---|---|---|
| A | 64.2 / 64.2 (+0.0) | 59.8 | 62.0 | **66.4** | 68.0 | +2.2 (0.117) | +3.8 (0.0013) |
| B | 64.4 / 64.4 (+0.0) | 59.0 | 62.2 | **66.8** | 67.8 | +2.4 (0.134) | +3.4 (0.016) |

| mode | stratum-reweighted ladder / M0 / M1 fwd / M1 xfit | live-mix (weak 0.68) ladder / M0 / M1 fwd / M1 xfit |
|---|---|---|
| A | 62.5 / 63.4 / 65.9 / 67.5 | 58.9 / 55.9 / 61.9 / 64.1 |
| B | 62.8 / 65.5 / 67.2 / 67.4 | 62.4 / 59.8 / 65.0 / 66.0 |

### Q4: coded-time leakage

0.3 % of strictly-earlier priors with a known coded time were coded after the query bill; 0.0 % of
priors have an unknown coded time. No coded-time rerun needed. Non-converged fits: 0.

### Gate (DESIGN §6)

| gate | verdict | detail |
|---|---|---|
| G1 | **FAIL** | forward-chained M1 +2.2 / +2.4 over the ladder, p 0.12 / 0.13 (bar: +3 with p < 0.05); cross-fit +3.8 / +3.4 is significant but optimistic. Ladder parity exact. |
| G2 | **PASS** | M0 +2.5 / +2.6 over hist-strong-else-vote on 2,325 bills, p < 1e-5 |
| G3 | **PASS** | Mode-A strong-history stratum (n = 1,186): M0 74.0 vs history 73.6; 13 overrides, 8 fixed / 3 broke |
| G4 | **FAIL** (narrow) | ECE A 0.045 (pass), B 0.067 (bar 0.05); precision at 0.90: 0.898 / 0.886 (pass) |
| G5 | not testable | Walker (bill 40921) and Nacho's (bill 40939) are not in the scored population (the dry run listed 1 of 2 in the coded population; neither reached the PDF eval rows) - checked by hand later |

### Conclusions

- **Do not wire M1 into the live ladder yet.** The direction is consistent (+2.2 / +2.4 forward,
  +3.8 / +3.4 cross-fit; +3.0 / +2.6 on the live mix) but n = 500 LLM-labelled bills cannot reach
  the p < 0.05 bar. Re-run G1 and G4 once U-744's live scorecard has several hundred completed
  `u701b-text-v2` bills with per-rung answers (about 4-6 weeks at current volume).
- **M0 passes its own gate.** It beats the filtered word vote (the ladder's last rung before blank)
  by +2.5 points with strong significance and does no harm on strong-history bills. Replacing the
  vote rung with M0 is a candidate small unit on its own.
- Mode B calibration (ECE 0.067) needs work before any confidence bar is set from M1/M0
  probabilities.

## Deviations from the design (and why)

1. **Package, not a single file.** `decision_model/` (features / learn / stats) replaces the
   `decision_model.py` the design names. Same location, new files only.
2. **No line-count feature (F14).** The only line counts available offline are the coded
   BillLineItems. Rule 1 and F14's note forbid those, and the vendor's extracted line items are
   not in the blob text the harness reads. M0 is therefore the drop variant (R4). The PDF length
   feature is the log of the PDF token count.
3. **Features are scaled, not centred.** An absent signal then contributes exactly 0 to a
   decision's explanation, and rows stay sparse for the pure-Python solver. The bias absorbs the
   offset. Predictions are unaffected up to the L2 penalty.
4. **CV folds are monthly blocks anchored at `--eval-since` (04-11..05-10, …, 09-11..10-10),**
   not calendar months. Six folds then cover the evaluation window exactly, with no partial
   October.
5. **Calibration slice.** Each fold takes ONE date cut from the PDF bills of its training window:
   the first bill of the last 20 % of those bills.
   - Rows before the cut fit M0, history-only rows included in `pdf_plus_history`.
   - PDF rows at or after the cut fit the calibrator.
   - History-only rows at or after the cut are dropped.

   Both variants therefore calibrate on identical PDF bills (fix round 2), and the comparison is
   like for like. The final model is not refit on 100 %, which keeps the calibrator honest.
6. **Baseline vote.** The baseline is history-if-strong-else-the-filtered any-vendor top-10 vote
   (the live `filtered_vote` rung). It is not U-691's calibration-selected variant, so it may
   differ slightly from the 62.3 / 60.7 cited in the design. Both arms run on the same bills.
7. **Ladder on the 500.** History-if-strong comes from the harness's own U-653b recompute. Its
   fallback is the E1 text-tier `predicted_scc_id`. The recomputed figure is printed beside the
   64.2 / 64.4 reference as a parity check.
8. **M1 cross-fitting** uses the other four time blocks (including later ones), as the design's
   "5-fold cross-fitting" implies. M1 is applied only where an LLM answered, and never to Mode-A
   strong-history bills (live never calls the LLM there). Those rows keep M0's pick.
9. **G2 is required in both modes**, since the design cites two baseline numbers.
10. **The coded-time rerun (`--prior-cutoff coded`)** filters history priors and neighbours by
    coded time. The global base rate (F15) stays on created time. Coded time is resolved in this
    order:
    - `BillCompletionResult.CompletedAt`;
    - else `Bill.StatusDatetime` when `StatusOrigin = 'completion'`;
    - else the creation time when `StatusOrigin = 'qbo_pull'` (the pull creates the bill already
      coded);
    - `'backfill'` is unknown unless `--backfill-coded-at-created` is set;
    - anything else is unknown.

    Unknown-coded priors are admitted by default. The drop arm is the sensitivity line.
11. **M1 input and ties (fix round 1, P1-a).** M1 reads M0's **raw** logit. An isotonic step
    would tie candidates inside one block, and the tie then fell to the lowest scc id. Only M1's
    output is calibrated, by a map fit on its training blocks (it moves probabilities, never the
    ranking). Every rank tie falls back to the raw M0 score, then to candidate order (history
    rank, vote rank, LLM), never to the scc id.
12. **Forward-chained M1 is the G1 basis (P2-a).** Fold f trains on earlier blocks only. Fold 0
    has no model and keeps M0's pick, which biases forward-chained M1 toward M0.
13. **The calibrator is fit on PDF rows only (P2-e)**, including in `pdf_plus_history`.
14. **Ambiguity is evaluated as of the bill (P2-d).** A number is ambiguous only when two or more
    codes carrying it existed before the bill. The live path evaluates ambiguity on today's
    catalog. The report also gives the share of baseline hits whose label M0 could not choose.
15. **Live-mix weak** means "not `history.strong`" in the history scope the ladder reads
    (project-scoped where U-653b scopes it). The U-744 figure is vendor-level, so the split is an
    approximation.
