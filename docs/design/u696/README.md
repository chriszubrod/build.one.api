# U-696 — Similar bills evidence in the U-654a text backtest

Measures whether the bill-coding LLM improves when the harness adds **similar past bills**
(U-691/U-694 PDF-text neighbours) to the cascade payload. Production
`BILL_CODING_SUGGESTION_TASK` is unchanged; only the backtest harness gains flags.

## Arms (same pinned sample recommended)

Pin the U-654a evaluation sample so arms are comparable:

```bash
CSV=docs/design/u654a/u654a_predictions_claude-haiku_<stamp>.csv

# H0 — baseline (today's payload shape)
./.venv/bin/python -m scripts.u654a_text_backtest \
  --ladder claude-haiku --candidate-sccs narrowed \
  --bill-ids-from "$CSV"

# H2 — full invoice body + similar bills block
./.venv/bin/python -m scripts.u654a_text_backtest \
  --ladder claude-haiku --candidate-sccs narrowed \
  --bill-ids-from "$CSV" \
  --bill-text full_content --similar-bills-evidence

# S0 / S2 — same with claude-sonnet
./.venv/bin/python -m scripts.u654a_text_backtest \
  --ladder claude-sonnet --candidate-sccs narrowed \
  --bill-ids-from "$CSV"
```

Add `--run` to call models (requires credentials and blob access). Default is dry-run JSON on stdout.

Each arm should pass an explicit **`--max-cost-usd`** cap (default **$10**). Dry-run cost is a first-rung estimate only.

## Harness behaviour (U-696 fix round)

### Neighbour SCC filter (F1)

Neighbours are computed from **PDF tokens only** with rolling as-of priors (strictly earlier bills).
Before union or `similar_bills` blocks, neighbour label SCCs are filtered through the same
**as-of catalog** as production: `build_candidate_sccs_for_bill(scc_by_id, as_of=bill.created, …)`
(excludes future SCC rows, suspense `00.02`, and ambiguous numbers). The model never sees a
neighbour whose SCC is not choosable on that bill. Dry-run and run reports include
`neighbour_filter_drops` (`neighbours_dropped_not_in_as_of_catalog`, suspense, ambiguous).

### Resume guard (F2)

`--resume-from` requires the prior run’s **ladder**, **candidate_sccs**, **`bill_text_mode`**
(missing summary field ⇒ `envelope_truncated`), **`similar_bills_evidence`** (missing ⇒ false),
and **`arm_hash`** (bill text mode + similar flag + system prompt + message-builder identity).
Mismatch exits non-zero.

### PDF blob load stats (F4)

When PDF text is loaded (similar-bills and/or `full_content`), dry-run JSON includes
`pdf_blob_load`: `linked`, `read_ok`, `stale_redaction`, `read_error`, `empty_content`
(U-694 workers/timeout). Bills with no blob URL are counted in **`bills_with_no_linked_pdf`**
(separate from read failures). Bills whose PDF read fails get no neighbours; count in
`bills_with_pdf_read_failure_for_neighbours` (each bill at most once across full_content + similar).

### Dry-run token estimate (F5)

Non-default arms estimate input tokens using **mean real per-call** sizes from the sample (one mode),
plus **`estimated_input_tokens_sample_one_mode_total`** (sum over the sample). Legacy full-catalog
dry-run still uses max payload size per bill. A separate
**`estimated_input_tokens_per_call_worst_case_upper_bound`** uses the max char budget
(6k envelope / 60k full content) as a labelled upper bound.

### Full-content empty/stale (F6)

`full_content_empty_or_stale` counts sample bills where `full_content` mode produced empty text
(stale envelope, empty content, or unreadable blob).

### Arm identity (C6)

`arm_hash` fingerprints the full arm (not just the system prompt). `prompt_variant_hash` remains
the system-prompt-only digest for compatibility.

## Dry-run fields

Dry-run reports `bill_text_mode`, `similar_bills_evidence`, `prompt_variant_hash`, `arm_hash`,
`full_content_cap_hits`, `full_content_empty_or_stale` (full_content mode),
`candidate_union_bills_with_additions`, and optional `pdf_blob_load` / `neighbour_filter_drops`.

## Privacy

Outputs remain numeric-only: predictions CSV adds neighbour **SCC ids** and counts, never bill
text, vendor names, or neighbour bill ids. The in-memory `similar_bills` block uses SCC
catalog names only (same as `history_evidence`).

## Results

Prod, read-only, 2026-10-21. Same 500 bills as the U-676o Haiku run (pinned via `--bill-ids-from`), narrowed candidates, Mode A (project known) and Mode B (project unknown). Reports and summaries (numeric only) are in this folder. "Word matching" is U-694's PDF-text similar-bills predictor (no LLM) on the same bills; history is U-653b.

| arm | model | input | cost | calls answered |
|---|---|---|---|---|
| H0 | Haiku 5.5 | today's live payload | $0.87 | 997 / 1000 |
| H2 | Haiku 5.5 | full PDF text + similar_bills | $0.90 | 998 / 1000 |
| S0b | Sonnet 5.5 | today's live payload | $17.31 | 1000 / 1000 |
| S2b | Sonnet 5.5 | full PDF text + similar_bills | $8.24 | **434 / 1000** — stopped by the live Claude daily cap (see below) |

An earlier Sonnet pair (S0/S2) is invalid: the backtest's spend counted against the live app's $6.45/UTC-day cap in prod `dbo.LlmDailySpend`, which blocked the rest of its calls as silent `needs_human` (and the live app's Claude calls until 00:00 UTC). U-702 isolates backtest spend.

**All 500 bills — top-1 over all bills (abstain = wrong), "history if strong (≥ 60 %), else X":**

| X | project known | project unknown |
|---|---|---|
| word matching | 61.4 % | 60.6 % |
| Haiku, today's input (= live) | 57.8 % | 57.6 % |
| Haiku + similar bills + full text | **63.8 %** | **62.0 %** |

**The 217 bills S2b answered in both modes** (all arms comparable):

| X | project known | project unknown |
|---|---|---|
| word matching | 61.8 % | 60.8 % |
| Haiku, today's input | 58.5 % | 56.2 % |
| Haiku + similar bills + full text | 62.7 % | 61.3 % |
| Sonnet, today's input | 56.7 % | 56.7 % |
| **Sonnet + similar bills + full text** | **64.1 %** | **65.4 %** |

Paired vs word matching on those 217 (bills only one of the two gets right): Sonnet + similar bills 22 vs 12 project-unknown (net +4.6 pts, sign test p ≈ 0.09), 17 vs 12 project-known (+2.3, p ≈ 0.35); Haiku + similar bills 17 vs 16 (+0.5) and 19 vs 17 (+0.9).

The full PDF text changed little on its own (only 16 of 500 extractions exceed the old 6,000-char cut). Haiku followed the top similar bill on 574 of 1,000 calls.

### Conclusion

- **Similar past bills are the evidence the AI was missing.** Without them both models trail plain word matching; with them both catch up or pass it.
- **Haiku + similar bills ≈ word matching** (within 1 pt) — not worth an LLM call per bill.
- **Sonnet + similar bills clears the Gate-1 bar** (≥ 2 pts project-unknown: +4.6) but on 217 bills and not yet significant (p ≈ 0.09).
- Decision (Chris, 2026-10-21): wire word matching as the live fallback now (U-701); finish the Sonnet sample with isolated spend (U-702) and move the fallback to Sonnet + similar bills only if the lead holds (~$0.02/bill, ~$4/month).
- Known LOW gaps (reporting only): stale PDFs are counted under read failures; the H0 narrowed dry-run sample total omits the prompt overhead; no test pins the equal-timestamp case of "strictly earlier".
