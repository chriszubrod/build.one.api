# U-654a text backtest report (20261018T021338Z)

- ModelVersion: `u654a-text-backtest-v1`
- Task: `bill_coding_suggestion`
- Ladder: `claude-sonnet`
- Run status: incomplete
- Evaluation window: 2026-04-11 .. 2026-10-10
- Sample size: 500
- Populations (eval window): all Bills=2548; text tier=2367
- Cost cap: tripped (scored=213, unscored=287)
- Bills scored (text cascade finished): 213
- Metric denominators: Mode A n=213, Mode B n=213
- Label SCC not in text catalog: 0 (min catalog size 474, median 474)

## Mode A (project fixed to label project)

Top-1 uses every Bill in the scored set (missing suggestion = miss). Combined uses history when raw score ≥ 60%, else text.

| Suggester | Coverage | Precision | Top-1 (Bills) | Top-1 ($) |
|-----------|----------|-----------|---------------|-----------|
| history | 79.34% | 68.05% | 53.99% | 48.20% |
| text | 25.35% | 75.93% | 19.25% | 11.15% |
| combined | 60.56% | 75.97% | 46.01% | 40.24% |

## Mode B (project predicted)

| Suggester | Coverage | Top-1 (Bills) | Top-1 ($) | Project top-1 |
|-----------|----------|---------------|-----------|---------------|
| history | 79.34% | 45.07% | 44.16% | 14.20% |
| text | 23.00% | 20.66% | 8.04% | 97.73% |

### Per-stratum Mode A top-1 (text vs history)

| Stratum | n | History | Text |
|---------|---|---------|------|
| 50-70% | 33 | 63.64% | 9.09% |
| 70-90% | 35 | 80.00% | 17.14% |
| <50% | 45 | 53.33% | 22.22% |
| >=90% | 38 | 94.74% | 44.74% |
| cold_start | 43 | 0.00% | 9.30% |
| mixed_code | 19 | 31.58% | 5.26% |

### Rung usage (text cascade)

{
  "claude-sonnet-5-5": 422,
  "needs_human": 4
}

- needs_human count: 323
- harness rate-limit retries: 0
- throttled final failures: 0
- harness retries by kind: {}
- cascade task errors: 0
- duplicate SCC numbers excluded: 0
- duplicate project abbrevs excluded: 0

### Mixed-code Bills (all sampled with mixed_code=true)

- n=19
- history top-1: 31.58%
- text top-1: 5.26%
- combined top-1: 15.79%

- Total tokens in: 7254643
- Total tokens out: 52817
- Total cost USD: 15.0375

### Call failures by kind (text cascade)

{
  "none": 381,
  "validation": 41,
  "parse": 4
}

### Confidence-bar sweep (raw predictions; §10.5 verdict unchanged)

- Mode A combined top-1 maximized at confidence bar **0.5** (history when raw score ≥ 60%, else text at bar)

| Bar | Mode A text cov | Mode A text prec | Mode A text top-1 | Mode A combined top-1 | Δ vs hist |
|-----|-----------------|------------------|-------------------|----------------------|-----------|
| 0.50 | 91.08% | 46.39% | 42.25% | 55.40% | 1.41% |
| 0.60 | 63.85% | 54.41% | 34.74% | 50.70% | -3.29% |
| 0.70 | 48.36% | 65.05% | 31.46% | 49.30% | -4.69% |
| 0.80 | 47.89% | 65.69% | 31.46% | 49.30% | -4.69% |
| 0.85 | 26.29% | 75.00% | 19.72% | 46.01% | -7.98% |
| 0.90 | 24.41% | 76.92% | 18.78% | 46.01% | -7.98% |

| Bar | Mode B text cov | Mode B text top-1 | Mode B project top-1 | Mode B combined top-1 |
|-----|-----------------|-------------------|----------------------|------------------------|
| 0.50 | 90.61% | 46.01% | 95.21% | 54.46% |
| 0.60 | 70.42% | 40.85% | 96.21% | 51.17% |
| 0.70 | 49.30% | 34.27% | 97.80% | 48.83% |
| 0.80 | 46.95% | 32.86% | 97.70% | 47.89% |
| 0.85 | 23.47% | 21.13% | 97.78% | 42.72% |
| 0.90 | 19.25% | 17.37% | 97.22% | 42.25% |

### Combined vs history (excluding Bills whose label SCC is absent from the text catalog)

- n=213
- history top-1: 53.99%
- combined top-1: 46.01%
- delta: -7.98%

(§10.5 M4 verdict below uses the Mode A denominator; the full sample on a complete run.)

**§10.5 M4: INCOMPLETE — run status 'incomplete'; metrics restricted to scored Bills; M4 gate not evaluated (no KEEP/DROP)**
