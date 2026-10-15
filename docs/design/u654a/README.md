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
| `--concurrency` | 4 | Parallel cascade calls (429/rate-limit backoff on throttled cascade attempts) |
| `--out-dir` | `docs/design/u654a` | Report + CSV + JSON |

Dry-run first: confirm population sizes, stratum counts, and estimated cost. The script uses a read-only transaction (rollback after SELECTs), same as U-653b. Live runs always write report + spend; interrupted runs are stamped **PARTIAL**. Ctrl-C while texts are still being read makes no model call at all.

## Privacy rule

Reports and machine outputs include **numeric identifiers only** — Bill/Vendor/Project/SCC ids, SCC numbers, scores, rung names, token/cost totals. They must **never** contain bill text, vendor names, memos, rationales, or other free-text fields. The harness enforces this on CSV/JSON columns and values at write time.

## Outputs (timestamped under `--out-dir`)

- `u654a_report_<stamp>.md` — coverage, precision, top-1 (Bill- and dollar-weighted) for history, text, and combined; Mode B project accuracy; mixed-code accuracy over all sampled mixed-code Bills; per-stratum table; throttling retries/failures; rung usage; §10.5 verdict (or INCOMPLETE).
- `u654a_predictions_<stamp>.csv` — per-Bill numeric predictions only.
- `u654a_summary_<stamp>.json` — aggregate metrics, `cap_tripped`, scored/unscored counts, usage.

## Tests

```bash
./.venv/bin/python -m pytest -o addopts="" -p no:cacheprovider -q tests/test_u654a_text_backtest.py tests/test_u654a_bill_coding_task.py
```

All tests use fakes (no DB, no LLM, no Azure).
