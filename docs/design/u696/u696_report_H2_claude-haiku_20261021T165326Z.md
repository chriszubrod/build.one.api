# U-654a text backtest report (20261021T165326Z)

- ModelVersion: `u654a-text-backtest-v1`
- Task: `bill_coding_suggestion`
- Ladder: `claude-haiku`
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
- Mean input tokens per CASCADE (all rungs): 8790.7
- Text calls with narrowed catalog: 722

## Mode A (project fixed to label project)

Top-1 uses every Bill in the scored set (missing suggestion = miss). Combined uses history when raw score ≥ 60%, else text.

| Suggester | Coverage | Precision | Top-1 (Bills) | Top-1 ($) |
|-----------|----------|-----------|---------------|-----------|
| history | 83.20% | 65.14% | 54.20% | 48.21% |
| text | 95.40% | 60.80% | 58.00% | 62.68% |
| combined | 97.00% | 65.15% | 63.20% | 63.40% |

## Mode B (project predicted)

| Suggester | Coverage | Top-1 (Bills) | Top-1 ($) | Project top-1 |
|-----------|----------|---------------|-----------|---------------|
| history | 83.20% | 46.20% | 49.67% | 12.74% |
| text | 94.80% | 57.40% | 60.49% | 91.69% |

### Per-stratum Mode A top-1 (text vs history)

| Stratum | n | History | Text |
|---------|---|---------|------|
| 50-70% | 128 | 56.25% | 60.94% |
| 70-90% | 101 | 76.24% | 64.36% |
| <50% | 104 | 42.31% | 38.46% |
| >=90% | 83 | 93.98% | 84.34% |
| cold_start | 84 | 0.00% | 44.05% |

### Rung usage (text cascade)

{
  "claude-haiku-5-5": 998,
  "needs_human": 2
}

- needs_human count: 49
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
- combined top-1: 48.19%

### Similar bills evidence (Mode A text vs top neighbour SCC)

- Bills that followed top neighbour: 287 (accuracy 80.84%)
- Bills that overrode top neighbour: 189 (accuracy 30.69%)
- Candidate catalog union added SCCs on 81 bill(s)

- Full-content char cap hits: 0 (cap=60000)

- Total tokens in: 8790679
- Total tokens out: 39546
- Total cost USD: 0.8989

### Call failures by kind (text cascade)

{
  "none": 985,
  "validation": 13,
  "parse": 2
}

### Confidence-bar sweep (raw predictions; §10.5 verdict unchanged)

- Mode A combined top-1 maximized at confidence bar **0.5** (history when raw score ≥ 60%, else text at bar)

| Bar | Mode A text cov | Mode A text prec | Mode A text top-1 | Mode A combined top-1 | Δ vs hist |
|-----|-----------------|------------------|-------------------|----------------------|-----------|
| 0.50 | 96.80% | 60.95% | 59.00% | 63.40% | 9.20% |
| 0.60 | 88.80% | 63.51% | 56.40% | 61.40% | 7.20% |
| 0.70 | 73.80% | 67.75% | 50.00% | 57.20% | 3.00% |
| 0.80 | 70.60% | 68.27% | 48.20% | 56.20% | 2.00% |
| 0.85 | 32.20% | 81.37% | 26.20% | 47.40% | -6.80% |
| 0.90 | 22.80% | 85.96% | 19.60% | 44.80% | -9.40% |

| Bar | Mode B text cov | Mode B text top-1 | Mode B project top-1 | Mode B combined top-1 |
|-----|-----------------|-------------------|----------------------|------------------------|
| 0.50 | 95.80% | 57.80% | 91.69% | 61.20% |
| 0.60 | 87.20% | 55.00% | 91.75% | 59.80% |
| 0.70 | 75.60% | 51.80% | 92.19% | 57.80% |
| 0.80 | 70.20% | 49.20% | 92.88% | 56.60% |
| 0.85 | 29.00% | 25.60% | 93.70% | 44.40% |
| 0.90 | 18.20% | 16.40% | 91.14% | 40.60% |

### Combined vs history (excluding Bills whose label SCC is absent from the text catalog)

- n=500
- history top-1: 54.20%
- combined top-1: 63.20%
- delta: 9.00%

(§10.5 M4 verdict below uses the Mode A denominator; the full sample on a complete run.)

**§10.5 M4: DO NOT wire text — Mode A combined top-1 63.2% vs history-only 54.2% (delta 9.0%, need ≥ 10%)**
