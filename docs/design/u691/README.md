# U-691 — Similar past bills backtest (Scenario 2a)

Read-only rolling-origin measurement comparing **token similarity over prior bills** to **U-653b vendor history** (Modes A/B). No production code changes; one SELECT load with rollback, all scoring in memory.

## What it measures

- **Similarity variants** (each uses top-K neighbours with similarity &gt; 0, SCC vote weighted by similarity):
  - `V_same_vendor` — priors from the same vendor only.
  - `V_any_vendor` — priors from any vendor.
  - `V_any_vendor_same_project` — any vendor, same dominant project label as the evaluated bill. **Pair with history Mode A only** — Mode A conditions on the label project; Mode B does not. The report marks this variant; combined on Mode B uses only `V_same_vendor` / `V_any_vendor`.
- **History** — U-653b Mode A (project given) and Mode B (predict project + SCC), imported unchanged.
- **Head-to-head** on the evaluation window using a **common bill set**:
  - **Top-1 over all** — every evaluated bill counts; abstention counts as wrong (bill- and dollar-weighted).
  - **Coverage** — share of bills with an answer (bill- and dollar-weighted).
  - **Precision on covered** — top-1 only on bills where the method answered.
  - Combined-vs-history **lift** uses top-1-over-all on this same set.
- **Fill-in** — accuracy when history abstains (&lt; `MIN_PRIOR_BILLS` priors) or is weak (raw &lt; `HISTORY_STRONG_RAW_SCORE`), plus neighbour coverage (`top_k_used ≥ 1`, i.e. at least one neighbour with similarity &gt; 0).
- **Combined policy (this harness)** — history Mode A when strong (raw ≥ `HISTORY_STRONG_RAW_SCORE`), else the **calibration-selected** best similarity variant, else history Mode A (may abstain). **Not the same as U-654a** `combined_mode_a_predicted_scc`, which gates on `COMBINED_HISTORY_RAW_SCORE_THRESHOLD` and falls back to **text** suggestions only (returns `None` when text abstains; no weak-history fallback).
- **U-639b trigger** — among history misses (wrong or abstained), share each variant would have gotten right.
- **Breakdowns** — vendor-consistency buckets (U-653b), line count (1 / 2–3 / 4+), confidence-bar sweep on the best variant (coverage denominator = all eval bills).

## Similarity definition

1. **Text** — concatenate line `Description` values and `Memo`; tokenize (lowercase, letters-only tokens, drop stop words and tokens shorter than 2, dedupe per bill). Each bill tokenizes once per scoring run (cached token set + IDF weight sum).
2. **IDF (eval scoring)** — `log((N+1)/(df+1))` from the **calibration-window** corpus (fixed corpus; not refit per eval bill).
3. **IDF (variant selection)** — same formula, corpus = **calibration-window bills only** (token df from calibration dates; labels are not used in IDF, so no label leakage). Keeps selection IDF aligned with tokens that appear during calibration scoring; a pre-cal-only corpus can be degenerate when few pre-cal bills share vocabulary with calibration bills.
4. **Token score** — IDF-weighted Jaccard on token sets; union weight = W(a)+W(b)−intersection (cached per bill).
5. **Amount band** — multiply by a factor: **1.0** if bill totals within **±25%**; linear decay in `|log(ratio)|` to **0** at **4×** ratio (CLI: `--amount-band`, `--amount-cap-ratio`).
6. **Rolling origin** — priors must have `created` **strictly before** the evaluated bill; same timestamp does not count; self never included. Candidates come from an inverted token index; similarity is computed once per eligible candidate; variants filter that list before top-K voting.
7. **Default top-K** — 10 (`--top-k`).

Constants live in `scripts/u691_similar_bills_backtest.py` (`MODEL_VERSION`, stop-word list, band defaults).

## Run

From the API repo root (with `.env` pointing at the target database):

```bash
./.venv/bin/python -m scripts.u691_similar_bills_backtest              # dry-run: population counts
./.venv/bin/python -m scripts.u691_similar_bills_backtest --run        # score + write outputs
```

| Flag | Default | Purpose |
|------|---------|---------|
| `--cal-since` / `--cal-until` | 2025-10-11 .. 2026-04-10 | Calibration window (eval IDF corpus + variant selection) |
| `--eval-since` / `--eval-until` | 2026-04-11 .. 2026-10-10 | Evaluation window (reported metrics) |
| `--out-dir` | `docs/design/u691` | Report, CSV, JSON |
| `--top-k` | 10 | Neighbours per variant |
| `--amount-band` | 0.25 | Full-weight amount band (±fraction) |
| `--amount-cap-ratio` | 4.0 | Zero weight beyond this total ratio |
| `--text-source` | `descriptions` | `descriptions` (line text), `pdf` (vendor PDF extraction), or `pdf_plus_masked_descriptions` |
| `--mask-catalog-in-descriptions` | off | Strip SubCostCode/CostCode name tokens from description/memo tokens (leakage probe; also reported on PDF population when set) |
| `--pdf-read-workers` | 8 | Concurrent blob reads (PDF modes only) |
| `--pdf-read-timeout` | 120 | Per-blob read timeout seconds (enforced on each storage read; slow reads count as `read_error`) |

Requires CLI system-admin context (`assert_cli_system_admin`). The loader extends U-653b’s `_LOAD_SQL` with line text (read-only transaction, rollback after SELECT).

## Privacy

Outputs (**CSV, JSON summary, markdown report**) must contain **numeric ids and scores only** — never vendor names, memos, line descriptions, or other free text. Enforced via the same guard as U-654a (`assert_outputs_privacy_safe`).

## Outputs (timestamped under `--out-dir`)

- `u691_report_<stamp>.md`
- `u691_predictions_<stamp>.csv`
- `u691_summary_<stamp>.json`

## Tests

```bash
./.venv/bin/python -m pytest -o addopts="" -p no:cacheprovider -q tests/test_u691_similar_bills_backtest.py
```

Includes a Zipf-like synthetic fixture: optimized vs reference parity on a small set, scaling check at 2k/4k/8k, and a loose absolute time bound.

## Runtime

Dry-run prints population counts only. `--run` prints measured scoring time. Scoring is QUADRATIC in bill count (common words make most earlier bills candidates): a synthetic Zipf fixture measured ~2 s at 2k, ~9 s at 4k and ~39 s at 8k bills per pass; `main` runs a calibration pass plus the scoring pass, so ~20k bills takes roughly 8-10 minutes on a laptop. The vendor-consistency breakdown includes a `no_prior` bucket for vendors with no earlier bills.

## Results — prod run 2026-10-21 (read-only; one SELECT + rollback; no LLM)

19,238 completed coded bills loaded; **2,549 evaluated** (2026-04-11..2026-10-10). Scoring took 36 s on prod data (real descriptions overlap far less than the synthetic fixture). Reports: `u691_report_20261021T033559Z.md` (raw descriptions) and `u691_report_20261021T033754Z_catalog_masked.md` (leakage probe); predictions CSVs are not committed.

### Run 1 — raw line descriptions + memo

| method (top-1 over ALL bills; abstain = wrong) | bill | dollar | coverage |
|---|---|---|---|
| history Mode A (project known) | 59.6% | 60.7% | 96.4% |
| history Mode B | 47.0% | 52.2% | 96.4% |
| similar, same vendor | 67.2% | 72.4% | 90.6% |
| **similar, any vendor** (calibration-selected) | **69.6%** | **74.3%** | 99.1% |
| similar, any vendor + same project (Mode-A-paired) | 54.8% | 49.3% | 83.2% |
| combined A (history when strong, else similar) | 69.6% | 73.4% | 99.9% |

Similar bills fixed 52% of history's misses (U-639b trigger: ≥ half) and got 60% of cold-start bills right.

### Leakage probe — the headline above is NOT trustworthy

Line descriptions on historical bills were typed by the bookkeeper **while coding**, so they can encode the chosen code. On the 2,549 evaluated bills, **69.0% of descriptions share a word with their own label cost code's name** (91.3% with some cost-code name). Re-running with every token that appears in any SubCostCode / CostCode name removed (538 tokens) from descriptions and memos:

| method | bill | dollar | coverage | history misses fixed |
|---|---|---|---|---|
| history Mode A | 59.6% | 60.7% | 96.4% | — |
| similar, any vendor | 26.4% | 9.5% | 47.8% | 13.8% |
| similar, same vendor | 26.6% | 10.7% | 39.7% | 10.8% |
| combined A | 62.0% | 61.3% | 96.9% | — (lift +2.4 pts) |
| combined B vs history B | 53.0% vs 47.0% | | | (lift +6.0 pts) |

The masked run is deliberately too strict (it also removes genuine vendor words such as "lumber" that appear in code names), so the true value of structured description similarity lies between the two runs — but most of Run 1's gain is the coder's own words coming back.

### Conclusion

- **Structured similarity over human-typed descriptions (Scenario 2a) is not a reliable signal to build on**: it largely re-reads the coding decision. Real but modest residual value: +2–6 points as a fill-in behind history.
- The question the business actually needs answered is similarity over **vendor-originated text** — the invoice's own words, available before anyone codes it: the drop-path's PDF-read description (U-676h, 5-word Title Case) and the extracted PDF text (U-535a). That is Scenario 2b's data; bills linked to their PDF are 90% in 2026 but ~0–13% before (U-535b backfill).
- **U-639b trigger: not met on leakage-free evidence** (13.8% of history misses fixed, vs the ≥ 50% bar).
- Method lesson for every future coding backtest: features written at coding time (line description, memo) must be masked or excluded, or the evaluation measures the label.

## U-694 — vendor PDF text

Extends this harness to score similarity on **vendor-originated PDF text** (U-535a extraction blobs) instead of human-typed line descriptions. Default `--text-source descriptions` is unchanged from U-691.

### Modes

```bash
# Default — line descriptions + memo (same as U-691)
./.venv/bin/python -m scripts.u691_similar_bills_backtest --run

# Vendor PDF text only (current redaction stamp + content field)
./.venv/bin/python -m scripts.u691_similar_bills_backtest --run --text-source pdf

# PDF tokens ∪ catalog-masked description/memo tokens
./.venv/bin/python -m scripts.u691_similar_bills_backtest --run --text-source pdf_plus_masked_descriptions

# Leakage probe on descriptions (U-691 README run 2)
./.venv/bin/python -m scripts.u691_similar_bills_backtest --run --mask-catalog-in-descriptions

# On PDF population, also report masked descriptions (like-for-like bill set)
./.venv/bin/python -m scripts.u691_similar_bills_backtest --run --text-source pdf --mask-catalog-in-descriptions
```

### Population (PDF modes)

For `pdf` and `pdf_plus_masked_descriptions`, **evaluated bills and similarity priors** are restricted to bills with usable PDF text (linked completed extraction, current TIN-masking stamp, non-empty `content`). Blob reads and `pdf_blob_load` counts (`linked`, `read_ok`, …) include **coded bills only** — URLs for non-coded bills with extractions are dropped before any read. **History Modes A/B still use all coded bills** before each eval bill as priors; the report states this explicitly. Population counts are printed and written under `population` / `pdf_blob_load` in the summary.

In PDF modes the summary also reports **leakage on line descriptions** for the same PDF eval bill set (`leakage_check_descriptions_on_pdf_eval`), alongside leakage for the active text source.

When `--mask-catalog-in-descriptions` is set with a PDF text source, the **descriptions_catalog_masked** row uses catalog-masked description tokens with **priors and IDF restricted to the PDF population** (same bills as the main PDF eval set), reusing history from the main scoring pass. The report table includes the same head-to-head columns as the main block.

### Similarity variant `V_other_vendor`

Cross-vendor neighbours only (excludes the evaluated bill’s vendor). Included in calibration selection and all report tables — separates vendor boilerplate in PDF text from content overlap.

### Privacy

Only blobs passing `extraction_payload_is_current` are used. After load, **only tokens** are retained in memory — raw PDF text is **never** written to CSV, JSON, or markdown (same numeric-only guard as U-654a).

### Tests

```bash
./.venv/bin/python -m pytest -o addopts="" -p no:cacheprovider -q tests/test_u694_pdf_text_backtest.py
```

### Results

Prod, read-only, 2026-10-21 (one SELECT + 3,757 blob reads per run; windows cal 2025-10-11..2026-04-10, eval 2026-04-11..2026-10-10). Reports: `u694_report_20261021T123148Z_pdf.md` (`--text-source pdf --mask-catalog-in-descriptions`) and `u694_report_20261021T123351Z_pdf_plus_masked.md`, with their summaries.

**Population.** 19,283 coded bills; 3,757 have usable PDF text (all 3,757 linked blobs read OK, 0 stale, 0 errors). Eval window: 2,374 of 2,555 bills (93%) have PDF text. Calibration window: only 1,338 of 16,659 (PDF links are sparse before 2026), so variant selection rests on a thinner sample. Similarity priors are limited to the 3,757 PDF bills; history uses all 19,283 — similarity is handicapped on its pool, not favoured.

| method (top-1 over ALL 2,374 bills; abstain = wrong) | bill | dollar | coverage | history misses fixed |
|---|---|---|---|---|
| history Mode A | 58.3% | 60.1% | 96.3% | — |
| history Mode B | 44.8% | 50.9% | 96.3% | — |
| PDF similar, same vendor | 60.5% | 65.3% | 95.0% | 23.8% |
| **PDF similar, any vendor** (calibration-selected) | **61.0%** | **65.0%** | 99.9% | **26.2%** |
| PDF similar, other vendors only | 27.6% | 9.9% | 99.8% | 16.7% |
| masked descriptions, any vendor (same bills, same prior pool) | 20.9% | 4.4% | 36.3% | — |
| combined A vs history A | 62.6% vs 58.3% | | | lift **+4.4 pts** |
| combined B vs history B | 61.1% vs 44.8% | | | lift **+16.3 pts** |

`pdf_plus_masked_descriptions` is within 0.1 pt of `pdf` on every row: masked descriptions add nothing once the PDF text is there.

**Leakage.** 36.0% of evaluated bills' PDF text shares a word with their own cost code's name (descriptions on the same bills: 68.0%). The PDF words are written by the vendor before anyone codes the bill, so this is signal, not leakage — the coding-time channel is closed. (98.5% share *some* catalog word: invoices are long, so that number carries no information.)

**Confidence.** Restricting to strong matches trades coverage for precision: 85.6% precise at 27.4% coverage (bar 0.80), 90.5% at 15.0% (bar 0.90).

### U-694 conclusion — Scenario 2b

- **Vendor PDF text is an honest, history-level signal.** Standalone it matches or slightly beats history (61.0% vs 58.3%) with no coding-time leakage, and it is far stronger than descriptions once the leak is removed (61.0% vs 20.9% on the same bills and prior pool).
- **Most of it is within-vendor.** Other-vendors-only scores 27.6%: the text mostly re-identifies the vendor and then finds which of that vendor's past bills it resembles. Cross-vendor content similarity is weak.
- **As a fill-in behind history it adds +4.4 pts when the project is known (Mode A) and +16.3 pts when it is not (Mode B).**
- **U-639b trigger: not met** (26.2% of history misses fixed vs the ≥ 50% bar), though it is double the leakage-free descriptions figure (13.8%).
- Known gap (deferred): with neither `--mask-catalog-in-descriptions` nor a PDF source, the catalog is not loaded and the descriptions-mode `leakage_check.own_label_scc_name` prints 0.0 instead of "unavailable". It does not affect any run above.
