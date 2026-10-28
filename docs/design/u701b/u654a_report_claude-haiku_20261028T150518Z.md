# U-654a text backtest report (20261028T150518Z)

- ModelVersion: `u654a-text-backtest-v1`
- Task: `bill_coding_suggestion`
- Ladder: `claude-haiku`
- Candidate SCCs: `narrowed`
- Bill text mode: `full_content`
- Similar bills evidence: `True`
- Prompt variant hash: `37474fc97566eba8`
- Run status: complete
- Evaluation window: 2026-04-11 .. 2026-10-10
- Sample size: 501
- Populations (eval window): all Bills=2506; text tier=2325
- Bills scored (text cascade finished): 500
- Metric denominators: Mode A n=500, Mode B n=500
- Label SCC not in text catalog: 0 (min catalog size 6, median 107)
- Narrowing misses (label in full catalog, not in narrowed list): 28

## SubCostCode candidate prompt size (U-676o)

- Candidate SCC mode: `narrowed`
- Mean candidate_scc count (text calls): 213.22
- Mean input tokens per CASCADE (all rungs): 11411.2
- Text calls with narrowed catalog: 708

## Mode A (project fixed to label project)

Top-1 uses every Bill in the scored set (missing suggestion = miss). Combined uses history when raw score ≥ 60%, else text.

| Suggester | Coverage | Precision | Top-1 (Bills) | Top-1 ($) |
|-----------|----------|-----------|---------------|-----------|
| history | 83.00% | 65.06% | 54.00% | 49.51% |
| text | 100.00% | 63.20% | 63.20% | 71.63% |
| combined | 100.00% | 64.20% | 64.20% | 69.27% |

## Mode B (project predicted)

| Suggester | Coverage | Top-1 (Bills) | Top-1 ($) | Project top-1 |
|-----------|----------|---------------|-----------|---------------|
| history | 83.00% | 43.40% | 49.93% | 13.29% |
| text | 100.00% | 61.80% | 69.57% | 92.57% |

### Per-stratum Mode A top-1 (text vs history)

| Stratum | n | History | Text |
|---------|---|---------|------|
| 50-70% | 84 | 66.67% | 67.86% |
| 70-90% | 83 | 78.31% | 71.08% |
| <50% | 84 | 51.19% | 51.19% |
| >=90% | 83 | 92.77% | 87.95% |
| cold_start | 83 | 0.00% | 42.17% |
| mixed_code | 83 | 34.94% | 59.04% |

### Rung usage (text cascade)

{
  "claude-haiku-5-5": 999,
  "claude-sonnet-5-5": 264,
  "needs_human": 1
}

- needs_human count: 0
- harness rate-limit retries: 0
- throttled final failures: 0
- harness retries by kind: {}
- cascade task errors: 0
- duplicate SCC numbers excluded: 0
- duplicate project abbrevs excluded: 0

### Mixed-code Bills (all sampled with mixed_code=true)

- n=83
- history top-1: 34.94%
- text top-1: 59.04%
- combined top-1: 55.42%

### Similar bills evidence (Mode A text vs top neighbour SCC)

- Bills that followed top neighbour: 313 (accuracy 82.43%)
- Bills that overrode top neighbour: 186 (accuracy 31.18%)
- Candidate catalog union added SCCs on 81 bill(s)

- Full-content char cap hits: 0 (cap=60000)

- Total tokens in: 11411241
- Total tokens out: 50113
- Total cost USD: 5.6818

### Call failures by kind (text cascade)

{
  "none": 1000
}

### Confidence-bar sweep (raw predictions; §10.5 verdict unchanged)

- Mode A combined top-1 maximized at confidence bar **0.5** (history when raw score ≥ 60%, else text at bar)

| Bar | Mode A text cov | Mode A text prec | Mode A text top-1 | Mode A combined top-1 | Δ vs hist |
|-----|-----------------|------------------|-------------------|----------------------|-----------|
| 0.50 | 93.80% | 66.10% | 62.00% | 63.40% | 9.40% |
| 0.60 | 83.40% | 70.02% | 58.40% | 60.80% | 6.80% |
| 0.70 | 79.20% | 71.21% | 56.40% | 59.00% | 5.00% |
| 0.80 | 76.00% | 72.37% | 55.00% | 58.20% | 4.20% |
| 0.85 | 34.80% | 83.91% | 29.20% | 46.60% | -7.40% |
| 0.90 | 25.00% | 86.40% | 21.60% | 44.00% | -10.00% |

| Bar | Mode B text cov | Mode B text top-1 | Mode B project top-1 | Mode B combined top-1 |
|-----|-----------------|-------------------|----------------------|------------------------|
| 0.50 | 93.00% | 59.40% | 92.57% | 62.40% |
| 0.60 | 85.80% | 57.00% | 91.95% | 60.20% |
| 0.70 | 81.80% | 55.60% | 91.60% | 59.00% |
| 0.80 | 76.20% | 53.40% | 91.30% | 57.60% |
| 0.85 | 29.00% | 26.40% | 95.42% | 43.00% |
| 0.90 | 19.80% | 17.80% | 96.63% | 38.80% |

### Escalation ladder (U-701b)

- Primary calls: 1000
- Escalated calls: 264
- Escalation rate: 26.40%
- Primary cost USD: 0.9257
- Escalation cost USD: 4.7236
- Sample text spend USD: 5.6493 (reconciled=True; orphaned=0.0, unattributed=0.0 - on a resumed run this includes spend the earlier run paid for rows it left unscored)
- Extra text spend USD: 0.0325

### History override scoring (similar-bills disagree; U-701b)

- override_mode_a_text: fired=78 fixed=11 broke=9
- override_mode_a_vote: fired=78 fixed=8 broke=9
- override_mode_b_text: fired=63 fixed=8 broke=10
- override_mode_b_vote: fired=63 fixed=7 broke=4

- mode_a: {"text_final_top1_all_bills": 0.632, "combined_text_top1_all_bills": 0.642, "combined_text_with_override_top1_all_bills": 0.646, "word_vote_top1_all_bills": 0.576, "combined_vote_top1_all_bills": 0.6, "combined_vote_with_override_top1_all_bills": 0.598}
- mode_b: {"text_final_top1_all_bills": 0.618, "combined_text_top1_all_bills": 0.644, "combined_text_with_override_top1_all_bills": 0.64, "word_vote_top1_all_bills": 0.576, "combined_vote_top1_all_bills": 0.592, "combined_vote_with_override_top1_all_bills": 0.598}

### Extra bills (outside eval window; excluded from sample metrics)

- Count: 1
- BillId 40921 mode A: label_scc=157 history_pred=96 history_score=0.7078651685393258 vote_pred=96 vote_conf=0.397354362209179 primary_pred=158 primary_conf=0.8 escalate_pred=None escalate_conf=None combined=96 combined_override=158 text_final=158
- BillId 40921 mode B: label_scc=157 history_pred=95 history_score=0.48554913294797686 vote_pred=96 vote_conf=0.397354362209179 primary_pred=158 primary_conf=0.62 escalate_pred=157 escalate_conf=0.5 combined=157 combined_override=157 text_final=157

### Combined vs history (excluding Bills whose label SCC is absent from the text catalog)

- n=500
- history top-1: 54.00%
- combined top-1: 64.20%
- delta: 10.20%

(§10.5 M4 verdict below uses the Mode A denominator; the full sample on a complete run.)

**§10.5 M4: KEEP text path — Mode A combined top-1 64.2% beats history-only 54.0% by 10.2% (≥ 10% bar)**
