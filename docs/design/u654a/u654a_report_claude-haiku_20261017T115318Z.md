# U-654a text backtest report (20261017T115318Z)

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
| text | 22.00% | 59.09% | 13.00% | 4.48% |
| combined | 58.00% | 76.21% | 44.20% | 36.85% |

## Mode B (project predicted)

| Suggester | Coverage | Top-1 (Bills) | Top-1 ($) | Project top-1 |
|-----------|----------|---------------|-----------|---------------|
| history | 83.20% | 46.20% | 49.67% | 12.74% |
| text | 19.20% | 15.80% | 9.98% | 97.37% |

### Per-stratum Mode A top-1 (text vs history)

| Stratum | n | History | Text |
|---------|---|---------|------|
| 50-70% | 83 | 66.27% | 7.23% |
| 70-90% | 85 | 83.53% | 11.76% |
| <50% | 83 | 44.58% | 16.87% |
| >=90% | 83 | 93.98% | 34.94% |
| cold_start | 83 | 0.00% | 6.02% |
| mixed_code | 83 | 36.14% | 1.20% |

### Rung usage (text cascade)

{
  "claude-haiku-5-5": 961,
  "needs_human": 39
}

- needs_human count: 794
- throttled cascade retries: 0
- throttled final failures: 0
- cascade task errors: 0
- duplicate SCC numbers excluded: 0
- duplicate project abbrevs excluded: 0

### Mixed-code Bills (all sampled with mixed_code=true)

- n=83
- history top-1: 36.14%
- text top-1: 1.20%
- combined top-1: 16.87%

- Total tokens in: 16975409
- Total tokens out: 114555
- Total cost USD: 1.7548

### Combined vs history (excluding Bills whose label SCC is absent from the text catalog)

- n=500
- history top-1: 54.20%
- combined top-1: 44.20%
- delta: -10.00%

(§10.5 M4 verdict below uses the Mode A denominator; the full sample on a complete run.)

**§10.5 M4: DO NOT wire text — Mode A combined top-1 44.2% vs history-only 54.2% (delta -10.0%, need ≥ 10%)**
