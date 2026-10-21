# U-691 similar bills backtest (20261021T033754Z)

- Harness: `u691-similar-bills-v1` (history `u653a-history-v1`)
- Bills loaded: 19238
- Runtime (scoring): 6.17s
- Scoring: head_to_head top-1 over all evaluated bills; abstention counts as wrong; coverage and covered precision reported separately
- Calibration window: 2025-10-11 .. 2026-04-10
- Evaluation window: 2026-04-11 .. 2026-10-10
- Top-K neighbours: 10
- Amount band: ±25% full weight; decay to 0 at 4.0× ratio
- IDF (eval scoring): 2025-10-11..2026-04-10
- IDF (variant selection): 2025-10-11..2026-04-10
- Best similarity variant (calibration top-1-over-all): `V_same_vendor`
- Best variant for Mode B combined: `V_same_vendor`

## head_to_head (top-1 over all | coverage bill | covered bill | covered dollar)

| method | bill | dollar | cov_bill | cov_$ | cov_prec_bill | cov_prec_$ |
|--------|------|--------|----------|-------|---------------|-------------|
| history_a | 59.63% | 60.72% | 96.43% | 95.35% | 61.84% | 63.68% |
| history_b | 46.96% | 52.15% | 96.43% | 95.35% | 48.70% | 54.70% |
| V_same_vendor | 26.64% | 10.66% | 39.74% | 16.04% | 67.03% | 66.48% |
| V_any_vendor | 26.44% | 9.54% | 47.82% | 25.14% | 55.29% | 37.95% |
| V_any_vendor_same_project (Mode-A-paired) | 22.28% | 5.64% | 33.93% | 15.13% | 65.66% | 37.25% |
| combined_A | 61.99% | 61.34% | 96.94% | 95.44% | 63.94% | 64.28% |
| combined_B | 52.96% | 56.16% | 96.94% | 95.44% | 54.63% | 58.84% |

V_any_vendor_same_project: pair with history_a only (uses label project).

## calibration_variant_selection (top-1 over all on calibration window)

| variant | bill | cov_bill | cov_prec_bill |
|---------|------|----------|---------------|
| V_same_vendor | 15.70% | 26.67% | 58.88% |
| V_any_vendor | 14.27% | 38.98% | 36.60% |
| V_any_vendor_same_project | 11.09% | 24.20% | 45.83% |

## fill_in

- abstain_count: 91
- weak_count: 1145

| variant | abstain_acc | weak_acc | coverage (top_k≥1) |
|---------|-------------|----------|-------------------|
| V_same_vendor | 10.99% | 23.23% | 39.74% |
| V_any_vendor | 18.68% | 23.84% | 47.82% |
| V_any_vendor_same_project | 15.38% | 19.56% | 33.93% |

## trigger

| variant | miss_fix_rate |
|---------|---------------|
| V_same_vendor | 10.79% |
| V_any_vendor | 13.80% |
| V_any_vendor_same_project | 12.63% |

## combined

- history_a top-1 over all: 59.63%
- combined top-1 over all: 61.99%
- lift (combined − history_a): 2.35%
- history_b top-1 over all: 46.96%
- combined_B top-1 over all: 52.96%
- lift (combined_B − history_b): 6.00%

### vendor_consistency

| bucket | n | coverage | top-1 over all | cov_prec_bill |
|--------|---|----------|----------------|---------------|
| 50-70% | 863 | 54.11% | 38.93% | 71.95% |
| 70-90% | 436 | 33.94% | 24.77% | 72.97% |
| <50% | 921 | 27.69% | 10.42% | 37.65% |
| >=90% | 300 | 47.67% | 46.33% | 97.20% |
| no_prior | 29 | 0.00% | 0.00% | n/a |

### line_count

| lines | n | coverage | top-1 over all | cov_prec_bill |
|-------|---|----------|----------------|---------------|
| 1 | 2388 | 37.40% | 26.42% | 70.66% |
| 2-3 | 135 | 75.56% | 33.33% | 44.12% |
| 4+ | 26 | 69.23% | 11.54% | 16.67% |

### confidence_sweep (coverage over all eval bills)

| bar | precision | coverage |
|-----|-----------|----------|
| 0.50 | 71.48% | 34.25% |
| 0.60 | 73.32% | 30.29% |
| 0.70 | 75.04% | 25.30% |
| 0.80 | 76.24% | 20.64% |
| 0.85 | 77.44% | 18.09% |
| 0.90 | 78.43% | 16.01% |
