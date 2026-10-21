# U-654a text backtest report (20261021T190457Z)

- ModelVersion: `u654a-text-backtest-v1`
- Task: `bill_coding_suggestion`
- Ladder: `claude-sonnet`
- Candidate SCCs: `narrowed`
- Bill text mode: `full_content`
- Similar bills evidence: `True`
- Prompt variant hash: `37474fc97566eba8`
- Run status: complete
- Evaluation window: 2026-04-11 .. 2026-10-10
- Sample size: 500
- Populations (eval window): all Bills=2555; text tier=2374
- Bills scored (text cascade finished): 500
- Metric denominators: Mode A n=500, Mode B n=500
- Label SCC not in text catalog: 0 (min catalog size 3, median 107)
- Narrowing misses (label in full catalog, not in narrowed list): 31

## SubCostCode candidate prompt size (U-676o)

- Candidate SCC mode: `narrowed`
- Mean candidate_scc count (text calls): 204.4
- Mean input tokens per CASCADE (all rungs): 4031.0
- Text calls with narrowed catalog: 722

## Mode A (project fixed to label project)

Top-1 uses every Bill in the scored set (missing suggestion = miss). Combined uses history when raw score ≥ 60%, else text.

| Suggester | Coverage | Precision | Top-1 (Bills) | Top-1 ($) |
|-----------|----------|-----------|---------------|-----------|
| history | 83.20% | 65.14% | 54.20% | 48.21% |
| text | 40.00% | 65.50% | 26.20% | 32.75% |
| combined | 70.20% | 72.08% | 50.60% | 46.34% |

## Mode B (project predicted)

| Suggester | Coverage | Top-1 (Bills) | Top-1 ($) | Project top-1 |
|-----------|----------|---------------|-----------|---------------|
| history | 83.20% | 46.20% | 49.67% | 12.74% |
| text | 40.00% | 26.80% | 34.80% | 94.86% |

### Per-stratum Mode A top-1 (text vs history)

| Stratum | n | History | Text |
|---------|---|---------|------|
| 50-70% | 128 | 56.25% | 19.53% |
| 70-90% | 101 | 76.24% | 23.76% |
| <50% | 104 | 42.31% | 25.00% |
| >=90% | 83 | 93.98% | 42.17% |
| cold_start | 84 | 0.00% | 25.00% |

### Rung usage (text cascade)

{
  "claude-sonnet-5-5": 434,
  "needs_human": 566
}

- needs_human count: 600
- harness rate-limit retries: 0
- throttled final failures: 0
- harness retries by kind: {}
- cascade task errors: 0
- duplicate SCC numbers excluded: 0
- duplicate project abbrevs excluded: 0

### Mixed-code Bills (all sampled with mixed_code=true)

- n=83
- history top-1: 36.14%
- text top-1: 14.46%
- combined top-1: 26.51%

### Similar bills evidence (Mode A text vs top neighbour SCC)

- Bills that followed top neighbour: 127 (accuracy 83.46%)
- Bills that overrode top neighbour: 72 (accuracy 33.33%)
- Candidate catalog union added SCCs on 81 bill(s)

- Full-content char cap hits: 0 (cap=60000)

- Total tokens in: 4031024
- Total tokens out: 17632
- Total cost USD: 8.2384

### Call failures by kind (text cascade)

{
  "none": 1000
}

### Confidence-bar sweep (raw predictions; §10.5 verdict unchanged)

- Mode A combined top-1 maximized at confidence bar **0.5** (history when raw score ≥ 60%, else text at bar)

| Bar | Mode A text cov | Mode A text prec | Mode A text top-1 | Mode A combined top-1 | Δ vs hist |
|-----|-----------------|------------------|-------------------|----------------------|-----------|
| 0.50 | 40.00% | 65.50% | 26.20% | 50.60% | -3.60% |
| 0.60 | 30.20% | 76.16% | 23.00% | 48.40% | -5.80% |
| 0.70 | 22.80% | 79.82% | 18.20% | 46.60% | -7.60% |
| 0.80 | 21.80% | 79.82% | 17.40% | 46.20% | -8.00% |
| 0.85 | 13.00% | 86.15% | 11.20% | 43.60% | -10.60% |
| 0.90 | 11.60% | 86.21% | 10.00% | 43.40% | -10.80% |

| Bar | Mode B text cov | Mode B text top-1 | Mode B project top-1 | Mode B combined top-1 |
|-----|-----------------|-------------------|----------------------|------------------------|
| 0.50 | 40.00% | 26.80% | 94.86% | 47.40% |
| 0.60 | 30.80% | 23.80% | 94.16% | 45.00% |
| 0.70 | 24.00% | 19.80% | 96.23% | 42.80% |
| 0.80 | 21.80% | 18.60% | 95.88% | 42.40% |
| 0.85 | 13.20% | 12.60% | 96.61% | 39.80% |
| 0.90 | 9.00% | 8.40% | 95.00% | 38.00% |

### Combined vs history (excluding Bills whose label SCC is absent from the text catalog)

- n=500
- history top-1: 54.20%
- combined top-1: 50.60%
- delta: -3.60%

(§10.5 M4 verdict below uses the Mode A denominator; the full sample on a complete run.)

**§10.5 M4: DO NOT wire text — Mode A combined top-1 50.6% vs history-only 54.2% (delta -3.6%, need ≥ 10%)**
