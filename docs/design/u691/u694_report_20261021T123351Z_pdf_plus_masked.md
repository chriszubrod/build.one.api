# U-691 similar bills backtest (20261021T123351Z)

- Harness: `u691-similar-bills-v1` (history `u653a-history-v1`)
- Text source: `pdf_plus_masked_descriptions`
- Bills loaded: 19283
- Runtime (scoring): 37.68s
- Scoring: head_to_head top-1 over all evaluated bills; abstention counts as wrong; coverage and covered precision reported separately
- Calibration window: 2025-10-11 .. 2026-04-10
- Evaluation window: 2026-04-11 .. 2026-10-10
- Top-K neighbours: 10
- Amount band: ±25% full weight; decay to 0 at 4.0× ratio
- IDF (eval scoring): 2025-10-11..2026-04-10
- IDF (variant selection): 2025-10-11..2026-04-10
- Best similarity variant (calibration top-1-over-all): `V_any_vendor`
- Best variant for Mode B combined: `V_any_vendor`

- History Modes A/B use all coded bills before each eval bill (not restricted to the PDF-text population).

## population

- all coded bills: 19283
- with usable PDF text: 3757
- eval window (all / PDF population): 2555 / 2374
- cal window (all / PDF population): 16659 / 1338

## pdf_blob_load

- linked: 3757
- read_ok: 3757
- stale_redaction: 0
- read_error: 0
- empty_content: 0

## leakage_check (eval population)

- share tokens ∩ own label SCC name: 36.02%
- share tokens ∩ any catalog name: 98.53%

## leakage_check (line descriptions on same PDF eval bills)

- share tokens ∩ own label SCC name: 67.99%
- share tokens ∩ any catalog name: 90.99%

## head_to_head (top-1 over all | coverage bill | covered bill | covered dollar)

| method | bill | dollar | cov_bill | cov_$ | cov_prec_bill | cov_prec_$ |
|--------|------|--------|----------|-------|---------------|-------------|
| history_a | 58.26% | 60.12% | 96.34% | 95.38% | 60.47% | 63.03% |
| history_b | 44.78% | 50.88% | 96.34% | 95.38% | 46.48% | 53.35% |
| V_same_vendor | 60.61% | 65.26% | 94.99% | 92.57% | 63.81% | 70.49% |
| V_any_vendor | 60.99% | 64.85% | 99.92% | 100.00% | 61.05% | 64.85% |
| V_other_vendor | 27.67% | 9.98% | 99.83% | 99.99% | 27.72% | 9.98% |
| V_any_vendor_same_project (Mode-A-paired) | 51.43% | 49.06% | 98.32% | 99.76% | 52.31% | 49.18% |
| combined_A | 62.64% | 64.49% | 100.00% | 100.00% | 62.64% | 64.49% |
| combined_B | 61.16% | 65.34% | 100.00% | 100.00% | 61.16% | 65.34% |

V_any_vendor_same_project: pair with history_a only (uses label project).

## calibration_variant_selection (top-1 over all on calibration window)

| variant | bill | cov_bill | cov_prec_bill |
|---------|------|----------|---------------|
| V_same_vendor | 45.74% | 78.18% | 58.51% |
| V_any_vendor | 47.68% | 99.63% | 47.86% |
| V_any_vendor_same_project | 41.48% | 92.60% | 44.79% |
| V_other_vendor | 14.20% | 99.48% | 14.27% |

## fill_in

- abstain_count: 87
- weak_count: 1087

| variant | abstain_acc | weak_acc | coverage (top_k≥1) |
|---------|-------------|----------|-------------------|
| V_same_vendor | 22.99% | 51.89% | 94.99% |
| V_any_vendor | 34.48% | 52.25% | 99.92% |
| V_any_vendor_same_project | 31.03% | 43.05% | 98.32% |
| V_other_vendor | 25.29% | 27.78% | 99.83% |

## trigger

| variant | miss_fix_rate |
|---------|---------------|
| V_same_vendor | 23.71% |
| V_any_vendor | 26.14% |
| V_any_vendor_same_project | 23.81% |
| V_other_vendor | 16.95% |

## combined

- history_a top-1 over all: 58.26%
- combined top-1 over all: 62.64%
- lift (combined − history_a): 4.38%
- history_b top-1 over all: 44.78%
- combined_B top-1 over all: 61.16%
- lift (combined_B − history_b): 16.39%

### vendor_consistency

| bucket | n | coverage | top-1 over all | cov_prec_bill |
|--------|---|----------|----------------|---------------|
| 50-70% | 843 | 100.00% | 66.07% | 66.07% |
| 70-90% | 421 | 99.52% | 74.35% | 74.70% |
| <50% | 874 | 100.00% | 46.68% | 46.68% |
| >=90% | 207 | 100.00% | 80.19% | 80.19% |
| no_prior | 29 | 100.00% | 13.79% | 13.79% |

### line_count

| lines | n | coverage | top-1 over all | cov_prec_bill |
|-------|---|----------|----------------|---------------|
| 1 | 2219 | 99.91% | 62.01% | 62.07% |
| 2-3 | 130 | 100.00% | 50.00% | 50.00% |
| 4+ | 25 | 100.00% | 28.00% | 28.00% |

### confidence_sweep (coverage over all eval bills)

| bar | precision | coverage |
|-----|-----------|----------|
| 0.50 | 71.90% | 70.89% |
| 0.60 | 78.13% | 55.10% |
| 0.70 | 81.62% | 41.03% |
| 0.80 | 85.34% | 27.30% |
| 0.85 | 89.15% | 19.80% |
| 0.90 | 90.53% | 15.12% |
