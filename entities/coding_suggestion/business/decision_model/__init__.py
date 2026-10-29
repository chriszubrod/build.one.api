"""U-698 phase 2 — trained bill-coding decision model (BACKTEST ONLY).

Pure Python, no I/O, no third-party numerics. Nothing in the live path imports this package;
the only caller is ``scripts/u698_decision_model_backtest.py``. Wiring is a separate, gated unit
(``docs/design/u698/DESIGN.md`` §6).

- ``features``: as-of prior index, label definition, candidate set, per-candidate features
  (M0 = F1-F15 non-LLM; M1 = the small LLM stacking layer).
- ``learn``: L2 logistic regression (Newton/IRLS), isotonic (PAV) and Platt calibration,
  standardisation, per-decision explanation.
- ``stats``: sign test, paired bootstrap, ECE / reliability, coverage at confidence bars.
"""
