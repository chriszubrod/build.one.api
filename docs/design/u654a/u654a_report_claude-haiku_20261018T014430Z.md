# U-654a text backtest report (20261018T014430Z)

- ModelVersion: `u654a-text-backtest-v1`
- Task: `bill_coding_suggestion`
- Ladder: `claude-haiku`
- Run status: complete
- Evaluation window: 2026-04-11 .. 2026-10-10
- Sample size: 500
- Populations (eval window): all Bills=2548; text tier=2367
- Bills scored (text cascade finished): 500
- Metric denominators: Mode A n=500, Mode B n=500
- Label SCC not in text catalog: 0 (min catalog size 474, median 474)

## Mode A (project fixed to label project)

Top-1 uses every Bill in the scored set (missing suggestion = miss). Combined uses history when raw score ≥ 60%, else text.

| Suggester | Coverage | Precision | Top-1 (Bills) | Top-1 ($) |
|-----------|----------|-----------|---------------|-----------|
| history | 83.20% | 65.14% | 54.20% | 48.21% |
| text | 22.60% | 56.64% | 12.80% | 3.62% |
| combined | 57.80% | 76.12% | 44.00% | 37.13% |

## Mode B (project predicted)

| Suggester | Coverage | Top-1 (Bills) | Top-1 ($) | Project top-1 |
|-----------|----------|---------------|-----------|---------------|
| history | 83.20% | 46.20% | 49.67% | 12.74% |
| text | 18.00% | 14.60% | 6.18% | 91.04% |

### Per-stratum Mode A top-1 (text vs history)

| Stratum | n | History | Text |
|---------|---|---------|------|
| 50-70% | 83 | 66.27% | 4.82% |
| 70-90% | 85 | 83.53% | 12.94% |
| <50% | 83 | 44.58% | 13.25% |
| >=90% | 83 | 93.98% | 38.55% |
| cold_start | 83 | 0.00% | 7.23% |
| mixed_code | 83 | 36.14% | 0.00% |

### Rung usage (text cascade)

{
  "claude-haiku-5-5": 955,
  "needs_human": 45
}

- needs_human count: 797
- harness rate-limit retries: 0
- throttled final failures: 0
- harness retries by kind: {}
- cascade task errors: 0
- duplicate SCC numbers excluded: 0
- duplicate project abbrevs excluded: 0

### Mixed-code Bills (all sampled with mixed_code=true)

- n=83
- history top-1: 36.14%
- text top-1: 0.00%
- combined top-1: 16.87%

- Total tokens in: 16975409
- Total tokens out: 115127
- Total cost USD: 1.7551

### Call failures by kind (text cascade)

{
  "none": 871,
  "parse": 45,
  "validation": 84
}

### Confidence-bar sweep (raw predictions; §10.5 verdict unchanged)

- Mode A combined top-1 maximized at confidence bar **0.5** (history when raw score ≥ 60%, else text at bar)

| Bar | Mode A text cov | Mode A text prec | Mode A text top-1 | Mode A combined top-1 | Δ vs hist |
|-----|-----------------|------------------|-------------------|----------------------|-----------|
| 0.50 | 85.00% | 48.47% | 41.20% | 55.00% | 0.80% |
| 0.60 | 70.00% | 49.43% | 34.60% | 50.40% | -3.80% |
| 0.70 | 61.80% | 49.51% | 30.60% | 49.00% | -5.20% |
| 0.80 | 59.40% | 49.16% | 29.20% | 48.40% | -5.80% |
| 0.85 | 23.40% | 55.56% | 13.00% | 44.00% | -10.20% |
| 0.90 | 16.40% | 67.07% | 11.00% | 43.60% | -10.60% |

| Bar | Mode B text cov | Mode B text top-1 | Mode B project top-1 | Mode B combined top-1 |
|-----|-----------------|-------------------|----------------------|------------------------|
| 0.50 | 82.80% | 43.40% | 94.83% | 54.60% |
| 0.60 | 68.60% | 39.60% | 94.89% | 53.00% |
| 0.70 | 59.60% | 36.40% | 94.85% | 52.00% |
| 0.80 | 56.00% | 34.60% | 94.44% | 50.80% |
| 0.85 | 18.20% | 14.60% | 91.04% | 40.60% |
| 0.90 | 14.80% | 12.80% | 94.12% | 40.00% |

### Combined vs history (excluding Bills whose label SCC is absent from the text catalog)

- n=500
- history top-1: 54.20%
- combined top-1: 44.00%
- delta: -10.20%

(§10.5 M4 verdict below uses the Mode A denominator; the full sample on a complete run.)

**§10.5 M4: DO NOT wire text — Mode A combined top-1 44.0% vs history-only 54.2% (delta -10.2%, need ≥ 10%)**
