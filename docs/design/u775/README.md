# U-775 — multi-task-day detector + single-code follow-up, re-scored end to end

Harness: `scripts/u775_multitask_detector.py`, tests in `tests/test_u775_multitask_detector.py`.
Read-only measurement. DB access goes only through U-750's read-only loader (`load_corpus_from_db`:
SELECT-only, rolled back). Dry-run is the default and touches no DB and no LLM.

## Business case

[U-751](../u751/README.md) tested the narrower follow-up product: propose **one code for all the day's
hours**, and flag multi-task days "may need splitting - please give codes and hours". It FAILED at
~70 % exact (K=7 and K=14, uncoded@T). The bottleneck was split **detection**. Flag recall was 34-40 %.
With every split day flagged, the same proposals would be 101/121 = 83.5 % exact.

**Coverage correction.** Perfect flagging alone does not pass. Flagged days are not proposals, so with
every split day flagged, coverage (proposals / days scored) would be **42.5 % at K=7** and **45.5 % at
K=14**. Both are under the 50 % bar. For that reason U-775 reports two coverages (Chris, Option C):

- **Coverage A (OFFICIAL verdict)** = proposals / days scored.
- **Coverage B (shown alongside, never the verdict)** = (proposals + flags) / days scored. It counts a
  flagged email as "handled".

U-775 builds a better detector, combines it with U-751's cached Sonnet single proposals, and re-scores.

## Population

The population is exactly U-751's (`u751.build_population`). It uses the U-750 windows (calibration
2026-06-01..08-15, holdout 2026-08-16..10-15) and TimeLog-sourced crew-days only. K is 7 or 14, with K = 7
as the headline. The realistic stratum is **uncoded@T**. Everything is also reported on all holdout days.
Only Sonnet cache hits are scored. A cache miss is excluded and counted (`u751.rescore_k`).

## Detection signals (booleans per holdout day per K)

| signal | cost | definition |
|---|---|---|
| `S_text` | $0 | ≥ 2 distinct codes resolve across the day's segments at the **detection** thresholds (τ_d, m_d). It uses U-750 `SegmentScore.pick` via U-751 `arm_a_resolved_codes`, on U-750 `analyze_day` scores and U-750's as-of evidence for that day and K. |
| `S_haiku` | paid, `--llm-run` only | One Haiku call per holdout crew-day, the same for every K: the day's rebuilt TimeLog notes (U-750's input text, never the stored Description) and total hours. Haiku must return `{"multi_task": bool, "tasks": [str, ...]}`. Invalid JSON, a bad shape or a truncated answer counts as **False** (no flag), and its status is counted. |
| `S_sonnet` | $0 | U-751's cached Sonnet answer parsed as a split (cache-only, exactly as U-751). |

Without `--llm-run`, `S_haiku` and every rule or detector that uses it are reported as **`not_run`**.
They are never computed as False. If some holdout days get no Haiku answer (`--max-calls` reached, or a
transport error), those days are excluded and counted (`haiku_missing_days`), and the verdicts are
`incomplete`.

### S_text cross-validation design, and why

τ_d ranges over 0.20..0.90 in steps of 0.05, and m_d over {1, 2, 3}. The objective is **F1 for "truth is
split"**. Ties go to higher precision, then higher τ_d, then higher m_d. All comparisons are exact
`Fraction`s.

The fit is a **grouped 5-fold cross-validation** over **all** labelled TimeLog crew-days in the corpus
(calibration, holdout and any others). Fold = `int(sha256("u775-fold:<contract_labor_id>"), 16) mod 5`,
so the assignment is deterministic and every day of one ContractLabor sits in one fold. Each holdout day's
`S_text` uses the pair fitted on the four folds that do **not** contain it (out-of-fold;
`s_text_out_of_fold`). The report lists each fold's fitted pair and its training F1, precision and recall.

Why not a calibration-only fit (as in U-750/U-751): only ~15 split days fall outside the holdout. That is
too few to fit a detection threshold. CV uses every labelled split day for fitting and still scores every
holdout day out of sample. Grouping by ContractLabor stops a crew member's other days from leaking the
style of their own notes into the fit that scores them.

**Fold-excluded evidence (Pass 1 fix).** U-750 evidence only drops the query's own ContractLabor. Without
an extra rule, a held-out day's labelled lines could be evidence for a training day and shape that day's
grid features, and through them the fit that then scores the held-out day. So when fold f is fitted, every
training day's grid is rebuilt from U-750's as-of evidence **minus every line whose ContractLabor is in fold
f** (`fold_training_grid_days`). The base evidence is unchanged: calibration days capped at the calibration
end (as U-750), other days U-750's default (hindsight). This is deterministic and costs one analysis per
(day, fold, K). `cross_validate` refuses a training set that holds any day of its own fold.

A held-out day is **scored** on its normal U-750 evidence (hindsight mode, U-751's own analyses), which is
exactly what a follow-up would see.

## Pre-registered decision rules (fixed 2026-11-06 before any scoring; not tuned)

- **headline:** FLAG if `S_text` OR `S_haiku` OR `S_sonnet`. Otherwise PROPOSE Sonnet's single code for all
  hours if Sonnet proposed a single code. Otherwise ABSTAIN.
- **secondary** (reported, not the verdict):
  - `R_text`, `R_haiku`, `R_sonnet`: one signal on its own, then a Sonnet single proposal, else abstain.
  - `R_haiku_or_sonnet` and `R_text_or_sonnet`.
  - `U751_headline`: U-751's headline outcome, for reference.

## Metrics

Metrics are computed per K, per rule, on uncoded@T and on all holdout days. Only cache hits are scored.

- Counts: days scored, cache misses, proposals, flags, abstains.
- exact_correct among proposals (U-750 `exact_correct`), with a Wilson CI.
- Single proposals on split-truth days.
- Flag precision and split recall.
- Coverage A and coverage B, each with a Wilson CI.

**Detector-only metrics** are computed per signal and for the combinations `S_text_or_S_haiku_or_S_sonnet`,
`S_haiku_or_S_sonnet` and `S_text_or_S_sonnet`: recall, precision, F1, and flags on single-truth days.

**Verdict A (official)**, per K, headline rule, uncoded@T: PASS iff exact ≥ 0.80 AND coverage_A ≥ 0.50 AND
proposals ≥ 20. Fewer than 20 proposals → `insufficient_support`. Otherwise FAIL. This is U-751 `verdict`
unchanged, on exact rationals. **Verdict B** applies the same thresholds with coverage_B. It is shown
beside A and is never the verdict.

Named case: the HP2 2026-09-29 crew days, per K. Each row shows truth, cache hit or miss, fold, the S_text
fit used and its value, S_haiku (value and status), Sonnet's status and proposal, S_sonnet, and every
rule's outcome. The report holds ids, code numbers and hours only.

## Haiku cost, budget and ledger isolation

- **Model:** the single anthropic rung of `DEFAULT_BILL_CODING_LADDER_V2` whose id contains "haiku". It is
  read at run time (`haiku_model_id`, `claude-haiku-5-5` today) and is never hardcoded. Anything other than
  exactly one such rung raises.
- **Plumbing (U-750's):**
  - Calls go through `u750.call_llm` on an `AnthropicTransport`, so the U-702 pre-call gate and metering
    apply.
  - Generation params are U-750 `LLM_GEN_PARAMS` (temperature 0, thinking off), with `max_tokens` = 600.
  - Answers are cached by `u750.LlmCache` under `<out-dir>/haiku/llm_cache`, keyed by
    `u750.prompt_hash(model, HAIKU_SYSTEM, user)`. The system prompt carries the harness version
    `u775-multitask-detector-v1`. The key has no K in it, so K=7 and K=14 share one answer per crew-day.
    Two days with identical notes and hours also share one answer.
  - Truncated answers are never cached. A cache file that is valid JSON but not an object (`[]`, `null`, a
    number or a string), or that has a non-string `text`, counts as a miss and is asked again. This is the
    same guard U-751 uses, and the Sonnet reads go through U-751's guarded `rescore_k`.
- **Budget:** `--budget-usd` defaults to 2.00 and `--max-calls` to 400.
  - Before any call, the padded `u750.estimate_call_cost_usd` for every uncached prompt plus the spend so
    far must fit the budget, or the run refuses (exit 2).
  - Before each call, the spend so far plus that call's estimate must fit, or the run aborts with
    `per_call_estimate_exceeds_budget` (exit 1, cache kept).
  - The U-750 `transport_error_limit` (max(3, 5 %)) applies to transport errors and to truncated answers.
    The limit is computed as U-750 does: during the loop, "planned" = the days already cached at start plus
    `--max-calls`. After the loop, a final check runs against the actual attempts (calls plus cache hits).
- **Ledger:** `claude_budget.use_isolated_spend_ledger(--budget-usd)` is set before the run and
  `clear_isolated_spend_ledger()` is called in `finally`, so `dbo.LlmDailySpend` is never touched.
- **Expected cost:** ~315 holdout crew-days means ≤ 315 calls. The padded estimate is ~$0.0005 per call,
  so ~$0.16 for all of them. Real spend should be a few cents.
- **Privacy:** Haiku's `tasks` labels stay in the cache only and are never written to the report. The
  writer refuses a report in which any JSON key or value equals a seen label (case-insensitive, ≥ 3 chars).
  Labels are also passed to U-673's substring guard. The cache holds prompts (note text) and lives under
  `.data/`.

## Outputs

`u775_report_<stamp>.{md,json}` hold ids, codes, hours, rates and counts only. They are written to temp
names, and both files must pass U-673 `assert_outputs_privacy_safe` before either is renamed into place.
The forbidden texts are the notes, the segments, the catalog names and the Haiku task labels. Any failure
removes both files. `--out-dir` must resolve under `/Users/chris/Applications/build.one/.data/`.

## Run (EM only — the worktree `.env` holds PRODUCTION credentials; prod read-only)

```sh
.venv/bin/python -m scripts.u775_multitask_detector                                  # dry-run plan: no DB, no LLM
.venv/bin/python -m scripts.u775_multitask_detector --run \
    --out-dir /Users/chris/Applications/build.one/.data/u775                          # S_text + S_sonnet, $0; S_haiku not_run
.venv/bin/python -m scripts.u775_multitask_detector --run \
    --out-dir /Users/chris/Applications/build.one/.data/u775 \
    --llm-run --budget-usd 2.00 --max-calls 400                                       # + S_haiku (paid, isolated ledger)
```

The Sonnet caches default to U-751's (`.data/u750/armB/llm_cache` for K=7 and `.data/u750/armB14/llm_cache`
for K=14). Override them with `--sonnet-cache-k7` and `--sonnet-cache-k14`. `--k 7` or `--k 14` runs one K.

## Limits

- Sonnet cache misses (days whose labels changed after 11-02) are excluded, as in U-751.
- Truth is the current labels.
- **CV evidence.** Training grids for fold f exclude fold f's lines, but a training day's evidence can still
  include lines from the other training folds. That is legitimate, because those labels are all training
  data.
- IDF is computed once over the whole corpus (label-free, not as-of), the same limit as U-673/U-750.
- `S_haiku` judges notes alone. It has no project precedent and no catalog, so "more than one cost
  category" is Haiku's reading, not the project's code list.
- The headline verdict needs `--llm-run`. Without it the verdict is `not_run`, and only the S_text and
  S_sonnet rules are scored.

## Results

Run 2026-11-06 04:48Z against prod (read-only; Sonnet cache-only; Haiku `claude-haiku-5-5` on the isolated ledger: 269 calls,
46 cache hits, **$0.0114 actual** of the $2.00 cap, 315/315 days answered, 0 errors/truncations; `dbo.LlmDailySpend` untouched).
Reports: `results/u775_report_20261106T044852Z.{md,json}` (the earlier 04:44Z free-stage run is superseded).

**Verdict A (official): FAIL at K=7 and K=14. Verdict B: PASS, but hollow - see below.**

| uncoded@T | K=7 | K=14 | bar |
|---|---|---|---|
| headline exact when proposed | 86.9 % (53/61, CI 76-93) | 80.4 % (37/46, CI 67-89) | >= 80 % |
| headline coverage A (official) | **21.4 %** | **21.6 %** | >= 50 % |
| headline coverage B (shown) | 82.5 % | 83.1 % | >= 50 % |
| headline flags / days | 174 / 285 (61 %) | 131 / 213 (62 %) | |
| best non-Haiku rule (text or Sonnet): exact / coverage A | 76.7 % / 40.7 % | 77.1 % / 49.3 % | |
| U-751 headline (reference): exact / coverage A | 70.1 % / 50.5 % | 69.5 % / 55.4 % | |

Detectors on uncoded@T (split recall / precision): S_text 60 % / 62 % (K=7), 54 % / 90 % (K=14); S_haiku 96 % / 30 % (K=7),
96 % / 35 % (K=14); S_sonnet 26 % / 74 %; all three ORed 98 % / 30 %.

Findings:
- The text detector (no LLM, out-of-fold) roughly doubles split recall over Sonnet (26 % -> 60 %) and, ORed with Sonnet,
  reaches 72 %; it lifts exactness from 70 % to ~77 % but costs coverage (50 % -> 41 % at K=7).
- Haiku says "more than one cost category" on 61 % of days: it catches almost every real split but flags 121 single-code
  days at K=7. That is what makes the headline 87 % exact on only 21 % coverage, and why Verdict B's PASS is hollow - an email
  that asks "please give codes and hours" on 6 days in 10 is today's ask, not a proposal.
- No rule measured reaches >= 80 % exact with >= 50 % coverage A. The trade-off is set by precision of detection, not recall.
- HP2 2026-09-29: three of the four crew-member days are now flagged "may need splitting" (the right action for a split day);
  the fourth abstains (Sonnet abstained, no flag).
