# U-654a text backtest report (20261028T150623Z)

- ModelVersion: `u654a-text-backtest-v1`
- Task: `bill_coding_suggestion`
- Ladder: `claude-haiku`
- Candidate SCCs: `narrowed`
- Bill text mode: `full_content`
- Similar bills evidence: `True`
- Prompt variant hash: `1ca4fe85305312e5`
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
- Mean input tokens per CASCADE (all rungs): 11937.6
- Text calls with narrowed catalog: 708

## Mode A (project fixed to label project)

Top-1 uses every Bill in the scored set (missing suggestion = miss). Combined uses history when raw score ≥ 60%, else text.

| Suggester | Coverage | Precision | Top-1 (Bills) | Top-1 ($) |
|-----------|----------|-----------|---------------|-----------|
| history | 83.00% | 65.06% | 54.00% | 49.51% |
| text | 100.00% | 56.00% | 56.00% | 65.54% |
| combined | 100.00% | 62.20% | 62.20% | 65.48% |

## Mode B (project predicted)

| Suggester | Coverage | Top-1 (Bills) | Top-1 ($) | Project top-1 |
|-----------|----------|---------------|-----------|---------------|
| history | 83.00% | 43.40% | 49.93% | 13.29% |
| text | 100.00% | 55.60% | 65.93% | 92.20% |

### Per-stratum Mode A top-1 (text vs history)

| Stratum | n | History | Text |
|---------|---|---------|------|
| 50-70% | 84 | 66.67% | 54.76% |
| 70-90% | 83 | 78.31% | 59.04% |
| <50% | 84 | 51.19% | 45.24% |
| >=90% | 83 | 92.77% | 81.93% |
| cold_start | 83 | 0.00% | 45.78% |
| mixed_code | 83 | 34.94% | 49.40% |

### Rung usage (text cascade)

{
  "claude-haiku-5-5": 999,
  "claude-sonnet-5-5": 325,
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
- text top-1: 49.40%
- combined top-1: 49.40%

### Similar bills evidence (Mode A text vs top neighbour SCC)

- Bills that followed top neighbour: 268 (accuracy 83.21%)
- Bills that overrode top neighbour: 231 (accuracy 24.24%)
- Candidate catalog union added SCCs on 81 bill(s)

- Full-content char cap hits: 0 (cap=60000)

- Total tokens in: 11937596
- Total tokens out: 60419
- Total cost USD: 6.5946

### Call failures by kind (text cascade)

{
  "none": 1000
}

### Confidence-bar sweep (raw predictions; §10.5 verdict unchanged)

- Mode A combined top-1 maximized at confidence bar **0.5** (history when raw score ≥ 60%, else text at bar)

| Bar | Mode A text cov | Mode A text prec | Mode A text top-1 | Mode A combined top-1 | Δ vs hist |
|-----|-----------------|------------------|-------------------|----------------------|-----------|
| 0.50 | 93.80% | 58.42% | 54.80% | 61.00% | 7.00% |
| 0.60 | 81.40% | 61.92% | 50.40% | 58.20% | 4.20% |
| 0.70 | 77.00% | 61.82% | 47.60% | 56.60% | 2.60% |
| 0.80 | 73.20% | 62.84% | 46.00% | 55.60% | 1.60% |
| 0.85 | 27.00% | 82.22% | 22.20% | 45.40% | -8.60% |
| 0.90 | 18.00% | 85.56% | 15.40% | 43.20% | -10.80% |

| Bar | Mode B text cov | Mode B text top-1 | Mode B project top-1 | Mode B combined top-1 |
|-----|-----------------|-------------------|----------------------|------------------------|
| 0.50 | 93.00% | 52.80% | 92.16% | 58.40% |
| 0.60 | 86.80% | 50.40% | 91.62% | 56.20% |
| 0.70 | 81.80% | 49.00% | 91.13% | 55.60% |
| 0.80 | 73.80% | 46.80% | 91.69% | 54.40% |
| 0.85 | 23.60% | 19.00% | 93.33% | 40.80% |
| 0.90 | 15.00% | 12.80% | 92.42% | 36.60% |

### Escalation ladder (U-701b)

- Primary calls: 1000
- Escalated calls: 325
- Escalation rate: 32.50%
- Primary cost USD: 0.9385
- Escalation cost USD: 5.6533
- Sample text spend USD: 6.5917 (reconciled=True; orphaned=0.0, unattributed=-0.0 - on a resumed run this includes spend the earlier run paid for rows it left unscored)
- Extra text spend USD: 0.0029

### History override scoring (similar-bills disagree; U-701b)

- override_mode_a_text: fired=78 fixed=9 broke=15
- override_mode_a_vote: fired=78 fixed=8 broke=9
- override_mode_b_text: fired=63 fixed=8 broke=11
- override_mode_b_vote: fired=63 fixed=7 broke=4

- mode_a: {"text_final_top1_all_bills": 0.56, "combined_text_top1_all_bills": 0.622, "combined_text_with_override_top1_all_bills": 0.61, "word_vote_top1_all_bills": 0.576, "combined_vote_top1_all_bills": 0.6, "combined_vote_with_override_top1_all_bills": 0.598}
- mode_b: {"text_final_top1_all_bills": 0.556, "combined_text_top1_all_bills": 0.61, "combined_text_with_override_top1_all_bills": 0.604, "word_vote_top1_all_bills": 0.576, "combined_vote_top1_all_bills": 0.592, "combined_vote_with_override_top1_all_bills": 0.598}

### Extra bills (outside eval window; excluded from sample metrics)

- Count: 1
- BillId 40921 mode A: label_scc=157 history_pred=96 history_score=0.7078651685393258 vote_pred=96 vote_conf=0.397354362209179 primary_pred=158 primary_conf=0.8 escalate_pred=None escalate_conf=None combined=96 combined_override=158 text_final=158
- BillId 40921 mode B: label_scc=157 history_pred=95 history_score=0.48554913294797686 vote_pred=96 vote_conf=0.397354362209179 primary_pred=158 primary_conf=0.8 escalate_pred=None escalate_conf=None combined=158 combined_override=158 text_final=158

### Combined vs history (excluding Bills whose label SCC is absent from the text catalog)

- n=500
- history top-1: 54.00%
- combined top-1: 62.20%
- delta: 8.20%

(§10.5 M4 verdict below uses the Mode A denominator; the full sample on a complete run.)

**§10.5 M4: DO NOT wire text — Mode A combined top-1 62.2% vs history-only 54.0% (delta 8.2%, need ≥ 10%)**
