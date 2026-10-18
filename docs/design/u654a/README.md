# U-654a — Bill text LLM coding backtest (offline harness)

Read-only evaluation of the M4 text suggester against the U-653b history baseline on the same Bills (U-639 §10.3–§10.5). This unit ships **harness code only**; the EM runs `--run` against a real DB and Foundry under a separate gate.

## What it measures

- **Population:** completed coded Bills in the evaluation window that have `Attachment.ExtractionStatus='completed'` and an extracted-text blob (one attachment per Bill — earliest `BillLineItemAttachment` link). Dry-run and reports stamp both the eval-window Bill count and the text-tier count; the stratified sample is drawn only from the text tier and **changes as extraction coverage grows**.
- **Label:** dominant SubCostCode and its project (same as U-653b).
- **Suggesters compared (like-for-like sample):**
  - **History** — U-653b in-memory ladder (`vendor×project` → vendor-wide, ≥3 priors).
  - **Text** — `bill_coding_suggestion` cascade (Foundry: DeepSeek-V4-Flash → gpt-5.4-nano → gpt-5.4-mini) on extracted PDF text + history evidence + SCC catalog (SCC rows with `CreatedDatetime` strictly before the Bill’s `CreatedDatetime`, matching priors).
  - **Combined (§10.3 production rule)** — use the history prediction when history **raw** smoothed modal share `top / (total + 2)` is **≥ 0.60** (`COMBINED_HISTORY_RAW_SCORE_THRESHOLD` in `scripts/u654a_text_backtest.py`); otherwise use the text prediction. A Bill with no suggestion counts as a miss for top-1 on every suggester (same denominator). **“History confident” in this harness uses that raw smoothed share** — the same quantity U-653’s history suggester exposes as `raw_score` / confidence today. U-639 §10.3 describes a **calibrated** probability bar for production; this backtest deliberately keeps the raw share until a calibrated mapping exists (document the gap when reading M4).
- **Modes:** A (project fixed to label project), B (project predicted from text + candidate project list).
- **§10.5 M4 bar:** KEEP the text path only if Mode A **combined** top-1 beats history-only top-1 by **≥ 10 percentage points** on the scored sample (same denominator). The verdict is printed only for a **complete** run — every sampled Bill finished its text cascade in both modes (accepted, or genuinely failed). A Bill the cost cap skipped or cut off, or that an interrupt stopped, stays **unscored**; any unscored Bill makes the run **INCOMPLETE** (or **PARTIAL** after Ctrl-C), metrics are restricted to the scored Bills, and the **M4 gate is not evaluated**.
- **Stratification:** vendor-consistency buckets plus a `mixed_code` overlay (Gate-1 narrowing). **Origin stratification is omitted on purpose** (U-639 §8 origin breakdown deferred until Gate-1).

## Run

From the API repo root (`.env` for read-only DB; system-admin CLI context):

```bash
# Default: dry-run — population sizes, sample composition + token/cost estimate, no model calls
./.venv/bin/python -m scripts.u654a_text_backtest

# Live cascade (Foundry + blob reads) after Gate approval
./.venv/bin/python -m scripts.u654a_text_backtest --run
```

| Flag | Default | Purpose |
|------|---------|---------|
| `--eval-since` / `--eval-until` | 2026-04-11 .. 2026-10-10 | Evaluation window |
| `--sample-size` | 500 | Stratified sample cap |
| `--seed` | 654 | Reproducible stratified draw |
| `--max-cost-usd` | 10 | Stops STARTING new Bills once the next call could exceed it; Bills already in flight finish, so spend can overshoot by up to about concurrency × one full ladder (× throttle retries) — the overshoot is reported. Skipped Bills stay unscored ⇒ INCOMPLETE |
| `--concurrency` | 4 | Parallel cascade calls (Foundry ladders) |
| `--claude-concurrency` | 2 | Parallel cascade calls when `--ladder` is `claude-*` |
| `--out-dir` | `docs/design/u654a` | Report + CSV + JSON |
| `--ladder` | `foundry` | `foundry` (default production Foundry ladder), `claude-haiku`, or `claude-sonnet` for Claude API backtests |
| `--resume-from` | — | Prior `u654a_predictions_<ladder>_<stamp>.csv`; **must match** `--ladder` |

Dry-run first: confirm population sizes, stratum counts, and estimated cost. The script uses a read-only transaction (rollback after SELECTs), same as U-653b. Live runs always write report + spend; interrupted runs are stamped **PARTIAL**. Ctrl-C while texts are still being read makes no model call at all.

## Privacy rule

Reports and machine outputs include **numeric identifiers only** — Bill/Vendor/Project/SCC ids, SCC numbers, scores, rung names, token/cost totals. They must **never** contain bill text, vendor names, memos, rationales, or other free-text fields. The harness enforces this on CSV/JSON columns and values at write time.

## Outputs (timestamped under `--out-dir`)

- `u654a_report_<ladder>_<stamp>.md` — coverage, precision, top-1 (Bill- and dollar-weighted) for history, text, and combined; Mode B project accuracy; mixed-code accuracy over all sampled mixed-code Bills; per-stratum table; **call failures by kind**; **confidence-bar sweep** on raw text predictions; harness rate-limit retries; rung usage; §10.5 verdict (or INCOMPLETE).
- `u654a_predictions_<ladder>_<stamp>.csv` — per-Bill numeric predictions only (`winning_rung_provider` distinguishes Foundry vs Anthropic). Text rows include accepted predictions plus **raw** below-bar predictions (`raw_*`, `error_kind`, `http_status` — no error message bodies).
- `u654a_summary_<ladder>_<stamp>.json` — aggregate metrics, `ladder`, `cap_tripped`, scored/unscored counts, usage.

## Tests

```bash
./.venv/bin/python -m pytest -o addopts="" -p no:cacheprovider -q tests/test_u654a_text_backtest.py tests/test_u654a_bill_coding_task.py
```

All tests use fakes (no DB, no LLM, no Azure).

## Result — prod run 2026-10-16 (complete)

Report: [`u654a_report_20261016T044342Z.md`](u654a_report_20261016T044342Z.md) · summary: [`u654a_summary_20261016T044342Z.json`](u654a_summary_20261016T044342Z.json). 500 Bills, seed 654, window 2026-04-11..2026-10-10, $7.46.

| Measure | History | Text | Combined |
|---|---|---|---|
| Mode A top-1 (Bills) | 54.2% | 58.4% | **63.4%** |
| Mode A top-1 ($) | 48.2% | 64.8% | 65.0% |
| Cold start (n=83) | 0.0% | 44.6% | — |
| Mixed-code (n=83) | 36.1% | 53.0% | 50.6% |
| Mode B project top-1 | 12.7% | **94.2%** | — |

- **§10.5 M4 verdict: DO NOT wire** — combined beat history by 9.2 points against a 10-point bar. History still wins every stratum at ≥ 50% vendor consistency.
- **Run history.** The first leg ran while the host was offline: 426 network-failed cascades looked like `needs_human`. They were re-run with U-654d `--resume-from`, which took `needs_human` from 426 to 9. This report is the merged, complete run. The per-Bill predictions CSV (numeric ids only) is not committed, matching U-653b.
- **Decision (Chris, 2026-10-16, option b).** Wire the text suggester only where it wins: cold start, weak or mixed history (runtime proxy: history raw score < 0.60) and project prediction. Built as U-654b and deployed behind `BILL_CODING_TEXT_ENABLED` (default off). The flag stays off until U-674 (untouched pre-fill guard) ships.

## Result — Claude Haiku 5.5 ladder (U-654e), prod run 2026-10-17

Report: [`u654a_report_claude-haiku_20261017T115318Z.md`](u654a_report_claude-haiku_20261017T115318Z.md) · summary: [`u654a_summary_claude-haiku_20261017T115318Z.json`](u654a_summary_claude-haiku_20261017T115318Z.json). Same 500 Bills as the Foundry run, single Haiku rung, $1.75.

- **Inconclusive, not a rejection.** Haiku cleared the 0.85 confidence bar on only 22% (Mode A) / 19% (Mode B) of calls (794 needs_human, 0 task errors); when it did, it was right 59% (Mode A, 65/110) and 82% (Mode B, 79/96) of the time — Foundry-level precision. The bar was set for the Foundry ladder and its below-bar answers were not recorded, so the report's "DO NOT wire" (combined 44.2%) reflects the threshold, not the model.
- **U-676e harness (2026-10-17):** records `error_kind` / `http_status` per call, harness-level 429/529 retries (5s→60s), raw below-bar predictions + confidence-bar sweep in reports/CSV; Claude dry-run cost floors at ~17k input tokens/call (~$1.7–1.8 for 500 Bills × Mode A+B on Haiku).
- **Haiku call failures (cause unknown):** the 2026-10-17 run’s 166 fast failures were **not** HTTP 429/529 — the harness retries those with ≥2.25 s of backoff (observed median latency ~206 ms; throttle counters were 0). Likely a **non-retried** HTTP status (400/401/403/404/413) or an **in-stream** Anthropic error; the next run records `error_kind` / `http_status` per call for diagnosis.

## Re-run with failure kinds + confidence sweep (U-676e), Claude Haiku 5.5, 2026-10-18

Report: [`u654a_report_claude-haiku_20261018T014430Z.md`](u654a_report_claude-haiku_20261018T014430Z.md) · summary: [`u654a_summary_claude-haiku_20261018T014430Z.json`](u654a_summary_claude-haiku_20261018T014430Z.json). Same 500 Bills; $1.76.

- Failures: 871 `none`, 45 `parse`, 84 `validation` (13% of Haiku answers unusable as returned).
- **Haiku is clearly below Foundry on the cost code**: best bar 0.5 → text top-1 41.2% (Foundry 58.4%), precision ~48% (Foundry ~59%); combined 55.0% (+0.8 over history 54.2%; Foundry combined 63.4%). Confidence escalation cannot rescue it — it is wrong while confident.
- **Haiku matches Foundry on project prediction**: 94.8% (Foundry 94.2%).
- **Decisions (Chris, 2026-10-18):** project prediction → Haiku; cost code → measure Sonnet 5.5 (full 500 Bills, $15 cap) before choosing its ladder.
