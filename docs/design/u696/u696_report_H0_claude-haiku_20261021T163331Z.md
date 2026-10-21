# U-654a text backtest report (20261021T163331Z)

- ModelVersion: `u654a-text-backtest-v1`
- Task: `bill_coding_suggestion`
- Ladder: `claude-haiku`
- Candidate SCCs: `narrowed`
- Bill text mode: `envelope_truncated`
- Similar bills evidence: `False`
- Prompt variant hash: `3184ec31632cd22d`
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
- Mean candidate_scc count (text calls): 204.12
- Mean input tokens per CASCADE (all rungs): 8456.8
- Text calls with narrowed catalog: 722

## Mode A (project fixed to label project)

Top-1 uses every Bill in the scored set (missing suggestion = miss). Combined uses history when raw score ≥ 60%, else text.

| Suggester | Coverage | Precision | Top-1 (Bills) | Top-1 ($) |
|-----------|----------|-----------|---------------|-----------|
| history | 83.20% | 65.14% | 54.20% | 48.21% |
| text | 91.40% | 48.14% | 44.00% | 40.64% |
| combined | 93.20% | 60.73% | 56.60% | 51.00% |

## Mode B (project predicted)

| Suggester | Coverage | Top-1 (Bills) | Top-1 ($) | Project top-1 |
|-----------|----------|---------------|-----------|---------------|
| history | 83.20% | 46.20% | 49.67% | 12.74% |
| text | 87.60% | 46.00% | 47.47% | 92.47% |

### Per-stratum Mode A top-1 (text vs history)

| Stratum | n | History | Text |
|---------|---|---------|------|
| 50-70% | 128 | 56.25% | 46.88% |
| 70-90% | 101 | 76.24% | 48.51% |
| <50% | 104 | 42.31% | 30.77% |
| >=90% | 83 | 93.98% | 74.70% |
| cold_start | 84 | 0.00% | 20.24% |

### Rung usage (text cascade)

{
  "claude-haiku-5-5": 997,
  "needs_human": 3
}

- needs_human count: 105
- harness rate-limit retries: 0
- throttled final failures: 0
- harness retries by kind: {}
- cascade task errors: 0
- duplicate SCC numbers excluded: 0
- duplicate project abbrevs excluded: 0

### Mixed-code Bills (all sampled with mixed_code=true)

- n=83
- history top-1: 36.14%
- text top-1: 42.17%
- combined top-1: 44.58%

- Total tokens in: 8456831
- Total tokens out: 44484
- Total cost USD: 0.8679

### Call failures by kind (text cascade)

{
  "none": 992,
  "validation": 5,
  "parse": 3
}

### Confidence-bar sweep (raw predictions; §10.5 verdict unchanged)

- Mode A combined top-1 maximized at confidence bar **0.5** (history when raw score ≥ 60%, else text at bar)

| Bar | Mode A text cov | Mode A text prec | Mode A text top-1 | Mode A combined top-1 | Δ vs hist |
|-----|-----------------|------------------|-------------------|----------------------|-----------|
| 0.50 | 91.40% | 48.14% | 44.00% | 56.60% | 2.40% |
| 0.60 | 77.80% | 51.16% | 39.80% | 55.00% | 0.80% |
| 0.70 | 62.20% | 54.34% | 33.80% | 51.80% | -2.40% |
| 0.80 | 59.80% | 55.18% | 33.00% | 51.60% | -2.60% |
| 0.85 | 22.20% | 64.86% | 14.40% | 44.40% | -9.80% |
| 0.90 | 17.20% | 68.60% | 11.80% | 43.80% | -10.40% |

| Bar | Mode B text cov | Mode B text top-1 | Mode B project top-1 | Mode B combined top-1 |
|-----|-----------------|-------------------|----------------------|------------------------|
| 0.50 | 88.40% | 46.20% | 92.47% | 55.40% |
| 0.60 | 76.00% | 42.80% | 92.49% | 53.80% |
| 0.70 | 63.80% | 37.40% | 93.19% | 52.00% |
| 0.80 | 59.00% | 35.40% | 93.02% | 51.40% |
| 0.85 | 19.40% | 15.80% | 94.05% | 42.80% |
| 0.90 | 12.20% | 11.00% | 96.15% | 39.20% |

### Combined vs history (excluding Bills whose label SCC is absent from the text catalog)

- n=500
- history top-1: 54.20%
- combined top-1: 56.60%
- delta: 2.40%

(§10.5 M4 verdict below uses the Mode A denominator; the full sample on a complete run.)

**§10.5 M4: DO NOT wire text — Mode A combined top-1 56.6% vs history-only 54.2% (delta 2.4%, need ≥ 10%)**
