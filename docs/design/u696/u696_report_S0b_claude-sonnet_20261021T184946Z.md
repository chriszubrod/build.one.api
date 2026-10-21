# U-654a text backtest report (20261021T184946Z)

- ModelVersion: `u654a-text-backtest-v1`
- Task: `bill_coding_suggestion`
- Ladder: `claude-sonnet`
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
| text | 87.60% | 54.34% | 47.60% | 49.56% |
| combined | 90.60% | 64.02% | 58.00% | 59.56% |

## Mode B (project predicted)

| Suggester | Coverage | Top-1 (Bills) | Top-1 ($) | Project top-1 |
|-----------|----------|---------------|-----------|---------------|
| history | 83.20% | 46.20% | 49.67% | 12.74% |
| text | 92.80% | 52.00% | 57.64% | 96.62% |

### Per-stratum Mode A top-1 (text vs history)

| Stratum | n | History | Text |
|---------|---|---------|------|
| 50-70% | 128 | 56.25% | 54.69% |
| 70-90% | 101 | 76.24% | 52.48% |
| <50% | 104 | 42.31% | 28.85% |
| >=90% | 83 | 93.98% | 74.70% |
| cold_start | 84 | 0.00% | 27.38% |

### Rung usage (text cascade)

{
  "claude-sonnet-5-5": 1000
}

- needs_human count: 98
- harness rate-limit retries: 0
- throttled final failures: 0
- harness retries by kind: {}
- cascade task errors: 0
- duplicate SCC numbers excluded: 0
- duplicate project abbrevs excluded: 0

### Mixed-code Bills (all sampled with mixed_code=true)

- n=83
- history top-1: 36.14%
- text top-1: 46.99%
- combined top-1: 49.40%

- Total tokens in: 8456831
- Total tokens out: 39632
- Total cost USD: 17.3100

### Call failures by kind (text cascade)

{
  "none": 1000
}

### Confidence-bar sweep (raw predictions; §10.5 verdict unchanged)

- Mode A combined top-1 maximized at confidence bar **0.5** (history when raw score ≥ 60%, else text at bar)

| Bar | Mode A text cov | Mode A text prec | Mode A text top-1 | Mode A combined top-1 | Δ vs hist |
|-----|-----------------|------------------|-------------------|----------------------|-----------|
| 0.50 | 87.60% | 54.34% | 47.60% | 58.00% | 3.80% |
| 0.60 | 61.40% | 67.75% | 41.60% | 54.60% | 0.40% |
| 0.70 | 48.40% | 71.90% | 34.80% | 52.40% | -1.80% |
| 0.80 | 47.20% | 73.31% | 34.60% | 52.40% | -1.80% |
| 0.85 | 27.00% | 80.74% | 21.80% | 47.20% | -7.00% |
| 0.90 | 24.80% | 83.87% | 20.80% | 46.80% | -7.40% |

| Bar | Mode B text cov | Mode B text top-1 | Mode B project top-1 | Mode B combined top-1 |
|-----|-----------------|-------------------|----------------------|------------------------|
| 0.50 | 92.80% | 52.00% | 96.62% | 57.20% |
| 0.60 | 76.00% | 47.60% | 96.21% | 53.60% |
| 0.70 | 58.20% | 42.20% | 96.93% | 51.40% |
| 0.80 | 55.40% | 41.40% | 96.76% | 51.00% |
| 0.85 | 30.40% | 27.00% | 97.18% | 46.00% |
| 0.90 | 23.60% | 21.60% | 98.15% | 43.80% |

### Combined vs history (excluding Bills whose label SCC is absent from the text catalog)

- n=500
- history top-1: 54.20%
- combined top-1: 58.00%
- delta: 3.80%

(§10.5 M4 verdict below uses the Mode A denominator; the full sample on a complete run.)

**§10.5 M4: DO NOT wire text — Mode A combined top-1 58.0% vs history-only 54.2% (delta 3.8%, need ≥ 10%)**
