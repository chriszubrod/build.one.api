"""U-698 trained bill-coding decision model; M0 served live since U-745.

Pure Python, no third-party numerics. ``features`` / ``learn`` / ``stats`` are shared by the
backtest (``scripts/u698_decision_model_backtest.py``) and the live path, so training and
serving compute the same features with the same code.

- ``features``: as-of prior index, label definition, candidate set, per-candidate features
  (M0 = F1-F15 non-LLM; M1 = the small LLM stacking layer, backtest only).
- ``learn``: L2 logistic regression (Newton/IRLS), isotonic (PAV) and Platt calibration,
  standardisation, per-decision explanation.
- ``stats``: sign test, paired bootstrap, ECE / reliability, coverage at confidence bars.
- ``serve`` (U-745): loads the shipped ``m0_model.json`` and scores M0 on every ladder V2
  decision beside the filtered similar vote (shadow; ``bill_coding_m0_rung_enabled``
  switches the vote's rung to M0). See ``docs/design/u745/README.md``.
"""
