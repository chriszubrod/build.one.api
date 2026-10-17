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
