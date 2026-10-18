# U-676a — Bill header field bake-off

Read-only offline measurement for [U-676](../u676-bill-drop-prefill.md). Compares:

- **Arm A:** `BillExtractionMapper` on the stored extraction blob (**text + key-value pairs only** — U-535a blobs drop DI tables/paragraphs).
- **Arm B:** `bill_header_extraction` cascade on masked blob text (Foundry ladder), vendor resolved via the same mapper matcher.
- **Arm C (optional):** same masked text + matcher as B, but Claude via `--claude-ladder` (`claude-haiku` default).

## Run

From repo root (system-admin CLI context, prod DB read-only with rollback):

```bash
./.venv/bin/python -m scripts.u676a_bill_field_bakeoff
./.venv/bin/python -m scripts.u676a_bill_field_bakeoff --run --max-cost-usd 10
./.venv/bin/python -m scripts.u676a_bill_field_bakeoff --run --arms A,B,C --claude-ladder claude-haiku
```

Defaults: eval window from U-653b (`--eval-since` / `--eval-until`), sample 200, seed 676, arms `A,B`, outputs under `docs/design/u676a/`. `--max-cost-usd` applies to **B+C LLM spend combined**.

Intake strata in reports: **manual** / **agent** / **other** (QBO-pulled, NULL intake, email, etc.).

There is **no resume**: every `--run` re-scores the full sample. At ~200 docs and ~$0.25 per full pass, rerunning from scratch is cheaper than resume complexity.

## Outputs

- `u676a_report_<stamp>.md` — summary + verdicts (complete runs only)
- `u676a_summary_<stamp>.json` — metrics JSON
- `u676a_per_doc_<stamp>.csv` — Bill ids, booleans, latencies, costs only (privacy asserted)

Partial runs (SIGINT, cost cap, or high blob-skip share) still write stamped outputs with `run_status` ≠ `complete` and verdicts withheld.

## Results — prod runs 2026-10-17/18 (complete)

Same 200 Bills (seed 676; 1,903 eligible), arm A = rules on stored text + KV only, arm B = Foundry ladder, arm C = Claude (direct API).

| Field | A rules | B Foundry | C Sonnet 5.5 | C Haiku 5.5 |
|---|---|---|---|---|
| Vendor | 23.0% | 85.0% | 82.0% | invalid |
| Bill number | 13.0% | 82.5% | 72.5% | invalid |
| Bill date | 66.5% | 93.0% | 90.5% | invalid |
| Amount | 46.5% | 93.0% | 90.0% | invalid |
| Latency p50 / p90 | 6 ms / 0.4 s | 4.7 s / 6.3 s | 1.9 s / 2.6 s | — |
| Cost per Bill | — | $0.0002 | $0.0033 | — |

- Reports: [`u676a_report_20261017T140130Z.md`](u676a_report_20261017T140130Z.md) (arm C = Haiku), [`u676a_report_20261018T010007Z.md`](u676a_report_20261018T010007Z.md) (arm C = Sonnet). Arm B repeated within a point across both runs (85/83.5/92.5/93 → 85/82.5/93/93).
- **The Haiku arm is invalid:** 166 of 200 calls failed fast at the API (median 206 ms, most likely rate limiting on the org; the harness did not record error codes). On the 34 that succeeded it matched Foundry (vendor 79%, number 88%, date 94%, amount 100%) — too few to decide. Sonnet had 10 failed calls (counted as misses).
- Read: Foundry is the most accurate; Sonnet is close on vendor/date/amount, weaker on bill number, and ~2.5× faster. Rules are out. Next: a harness round that records error codes, backs off on rate limits and keeps below-threshold Claude answers with confidence, then a fair Haiku re-run (U-676e).
