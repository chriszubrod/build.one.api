# U-701b Phase 1 — measuring the live coding ladder before building it

Prod, read-only, 2026-10-28. Harness: `scripts/u654a_text_backtest.py` (U-701b arm, `1340539a`).
Same 500-bill seeded sample for both runs (seed 654, default window); the CSVs here pin it for
future arms (`--bill-ids-from`). Narrowed candidates, full PDF text, similar-bills evidence,
Mode A (project known) and Mode B (project unknown). Spend went to the U-702 isolated ledger.

| run | files (stamp) | flags beyond the common set | cost |
|---|---|---|---|
| E1 | `…20261028T150518Z` | — | $5.68 |
| E2 | `…20261028T150623Z` | `--line-items-rule` | $6.59 |

Common: `--ladder claude-haiku --escalate-ladder claude-sonnet --escalate-below 0.70
--candidate-sccs narrowed --bill-text full_content --similar-bills-evidence
--extra-bill-ids 40921,40939 --max-cost-usd 10 --run`. Both runs complete, 500/500 scored,
spend reconciled to the metered total.

## Results — top-1 over all 500 bills (abstain = wrong)

| rule | Mode A | Mode B |
|---|---|---|
| history if strong (≥ 0.60), else Haiku with today's input (live; U-696 H0) | 57.8 % | 57.6 % |
| history if strong, else word-matching vote (production vote, top 10) | 60.0 % | 59.2 % |
| **history if strong, else Haiku → Sonnet below 0.70 (E1)** | **64.2 %** | **64.4 %** |
| same + line-items rule (E2) | 62.2 % | 61.0 % |
| E1 + similar-evidence override of strong history | 64.6 % | 64.0 % |

Text tier alone (E1): Haiku 60.0 % / 61.0 % → ladder 63.2 % / 61.8 %. Sonnet on the escalated
calls, paired against Haiku's own answer: Mode A fixed 29 / broke 13 (sign test p ≈ 0.02),
Mode B 24 / 20 (p ≈ 0.65). Escalation rate 26.4 %; cost $0.0056 per (bill, mode) —
Haiku $0.93 + Sonnet $4.72 for 1,000 calls.

## Conclusions

- **Build the ladder** (Haiku, Sonnet when confidence < 0.70): +6.4 / +6.8 points over live,
  +4.2 / +5.2 over the word-vote fallback, at about a quarter of Sonnet-on-every-bill cost.
- **Drop the line-items rule.** It cut Haiku alone from 60.0 % to 49.0 % (Mode A) and pushed
  more calls to Sonnet; the ladder still lost 2–3 points.
- **Drop the history override.** 78 bills fired in Mode A: 11 fixed, 9 broke — a wash.
- Walker Lumber #273811 (bill 40921, label 157): Mode B correct via Sonnet (Haiku 158 @ 0.62 →
  Sonnet 157). Mode A wrong: strong vendor history (0.71) picks 96, and Haiku was confidently
  wrong (158 @ 0.80, no escalation). Nacho's #417821 (bill 40939) was still in review, so
  unscored. Strong-but-wrong history is not addressed by anything measured here.

Decision (Chris, 2026-10-28): build the ladder without the rule or the override (U-701b Phase 2).
