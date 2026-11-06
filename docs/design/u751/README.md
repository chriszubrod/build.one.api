# U-751 — single-code follow-up proposals, re-scored from cached U-750 Sonnet answers ($0)

Harness: `scripts/u751_single_code_rescore.py`, tests in `tests/test_u751_single_code_rescore.py`.
Cache-only: no LLM call, no transport, no network. DB access only through U-750's read-only loader
(`load_corpus_from_db`: SELECT-only, rolled back). Dry-run is the default and touches no DB.

## Business case

[U-750](../u750/README.md) measured a follow-up email that proposes a Contract Labor crew-day's coding
(codes + hour split) from same-project precedent. It FAILED: on the realistic uncoded@T population
Sonnet (arm B) was about 65 % exact, and its splits were the weak part. U-751 tests a narrower product at
no cost. The email proposes **one code for all the day's hours**. When the day looks multi-task it says
**"may need splitting - please give codes and hours"** and does not guess a split. U-751 re-scores the
Sonnet answers U-750 already cached. No new calls are made.

## Pre-registered rule (Gate 1, Chris, 2026-11-05; fixed before any scoring, not tuned)

Headline rule (`headline`), per holdout day and K:

1. **FLAG `may_need_splitting`** when Sonnet's parsed proposal is a split, **or** arm A (U-750
   `analyze_day` at that K's fitted τ, m) resolves ≥ 2 distinct codes across the day's segments. This
   counts the resolved-segment code set, with or without a split precedent.
2. Else, when Sonnet proposed a single code → **PROPOSE** that code for all the day's hours.
3. Else (Sonnet abstained, or its answer was invalid) → **ABSTAIN**.

Secondary rules (reported, not part of the verdict):

- `R_sonnet_only`: flag only when Sonnet proposed a split. Otherwise the same as the headline rule.
- `R_dominant`: never flags. A Sonnet split becomes a single proposal of its largest-hours code
  (tie → lower SubCostCodeId) for all hours.

## Population and cache-only method

- The U-750 population is rebuilt with U-750's own functions: `load_corpus_from_db`, `build_crew_days`
  (TimeLog-sourced days only), calibration 2026-06-01..08-15 and holdout 2026-08-16..10-15,
  `evidence_for_day` (calibration capped at the calibration end; holdout in hindsight mode),
  `analyze_day`, and per-K `fit_thresholds` on calibration. The small setup block that U-750 keeps in
  `run_backtest` locals (label-free IDF, token weights, per-project index) is mirrored in
  `build_population`. A test pins it to `run_backtest`: same days, same evidence line ids, same fitted pair.
- Realistic population **uncoded@T** (`uncoded_at_T_k<K>`): holdout days whose own ContractLabor
  `coded_at` is NULL or ≥ T = LineDate + K (U-750 `CrewDay.uncoded_at`).
- Per holdout day and K ∈ {7, 14} (headline K = 7), the arm-B prompt is rebuilt the way U-750
  `run_arm_b` builds it: `build_llm_prompt` → `prompt_hash(model, LLM_SYSTEM, user)`, with model =
  `sonnet_model_id()`. The answer is read with `LlmCache.get` from that K's cache dir (K=7 →
  `.data/u750/armB/llm_cache`, K=14 → `.data/u750/armB14/llm_cache`; both can be changed on the CLI).
  - A **cache miss** is counted and reported, and is never scored.
  - A hit is parsed by U-750 `parse_llm_response` with the same validation. An invalid answer is an
    abstain, and its status is counted.
- A test pins key parity: the real U-750 `run_backtest` → `run_arm_b` path, with a fake transport, writes
  the cache, and U-751 has to find every one of those keys. A second test pins cache-only behaviour: the
  transport constructor, `call_llm` and `run_arm_b` are patched to raise, and a run with misses never
  reaches them.

## Metrics and bar

Metrics are computed per K and per rule, on uncoded@T and on all holdout days (for context). Only cache
hits are scored.

- Counts: days scored, cache misses, proposals, flags, abstains.
- Coverage = proposals / days scored, with a Wilson CI. Flags and abstains count as not proposed.
- Exact-correct among proposals, with a Wilson CI. A proposal is exact only when the truth is that one
  code with all the hours (U-750 `exact_correct`).
- Single proposals on split-truth days: the count, and its rate among proposals. These are always wrong.
- Flag precision = flagged days whose truth is a split / flags.
- Split recall = flagged split-truth days / split-truth days scored.
- Response statuses.

**Phase B verdict** per K, headline rule, uncoded@T:

- **PASS** iff exact ≥ 0.80 AND coverage ≥ 0.50 AND proposals ≥ 20. Both comparisons are exact
  rationals, so a boundary value counts as a pass.
- Proposals < 20 → `insufficient_support`.
- Otherwise **FAIL**.

There is no false-split criterion, because single-code rules never split.

Named case: the HP2 2026-09-29 crew-member days, reported per K with truth, cache hit or miss, Sonnet's
status and proposal, arm A's resolved codes, and each rule's outcome. The report holds ids, code numbers
and hours only.

## Outputs

`u751_report_<stamp>.{md,json}` hold ids, codes, hours, rates and counts only. They are written to temp
names, and both files must pass U-673 `assert_outputs_privacy_safe` before either is renamed into place. A
failed check removes both files. The forbidden texts are the corpus notes, their segments and the catalog
names. `--out-dir` must resolve under `/Users/chris/Applications/build.one/.data/`. Results meant for the
repo are copied to `docs/design/u751/results/` by the EM.

## Run (EM only — prod read-only)

```sh
.venv/bin/python -m scripts.u751_single_code_rescore                    # dry-run plan: no DB, no LLM
.venv/bin/python -m scripts.u751_single_code_rescore --run --out-dir ../.data/u751
```

## Limits

- **Labels have grown since the U-750 run (11-02).** New or changed labels change the evidence, and so
  the prompt and its cache key. Those days are cache misses, which are counted and excluded, so the
  scored population can be smaller than U-750's.
- **Truth is the current labels**, not the labels at the time of the U-750 run.
- **The flag rule is unfitted by design.** It was fixed in advance from U-750's findings. Arm A's
  (τ, m) is still U-750's per-K calibration fit, which was tuned for proposals, not for flagging.
- The cache key includes the model id from the live ladder (`sonnet_model_id()`). If that rung changes,
  every day is a miss, and the miss count shows it.
- Truncated U-750 answers were never cached, so they show up as misses rather than abstains.

## Results

Run 2026-11-06 03:39Z against prod (read-only, cache-only, $0, no LLM calls): `results/u751_report_20261106T033935Z.{md,json}`.
Population: 315 holdout days (53 split), 303 uncoded@T at K=7, 213 at K=14. Cache hits: K=7 297/315 (18 misses: days that
gained labels after 11-02), K=14 315/315. Fitted arm-A pairs: K=7 tau 0.60 m 2, K=14 tau 0.65 m 2 (both qualified).

**Verdict: FAIL at both K on the realistic (uncoded@T) population.**

| headline rule, uncoded@T | K=7 | K=14 | bar |
|---|---|---|---|
| exact when proposed | **70.1 %** (101/144, CI 62-77) | **69.5 %** (82/118, CI 61-77) | >= 80 % |
| coverage | 50.5 % (144/285) | 55.4 % (118/213) | >= 50 % |
| flags / precision | 25 / 72 % | 23 / 83 % | |
| split-truth days flagged (recall) | 34 % (18/53) | 40 % (19/48) | |
| single proposals on split-truth days | 23 (16 % of proposals) | 21 (18 %) | |

Secondary rules (uncoded@T): R_sonnet_only 69.7 % / 68.3 %, R_dominant 64.6 % / 62.3 % exact (K=7 / K=14).
On all holdout days the headline rule reaches 80.9 % at K=14 (coverage 64.8 %), lifted by days already coded before T.

Findings:
- Flagging helps: U-750's Sonnet arm was 65.2 % on the same population; flagging moves it to ~70 %.
- The bottleneck is flag recall. Of the 43 wrong K=7 proposals, 23 are split days nobody flagged and 20 are wrong codes on
  single-code days. With every split day flagged, the same proposals would be 101/121 = 83.5 % exact - the bar sits behind
  split detection, not behind code choice.
- HP2 2026-09-29 is still not caught: arm A resolves no codes, Sonnet proposes 35.00 x 8 h (or abstains), so no rule flags it.
