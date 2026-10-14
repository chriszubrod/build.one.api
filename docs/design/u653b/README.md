# U-653b — Bill history backtest harness

Read-only replay of the U-653a history suggester over completed coded Bills (U-639 §10). Memo and text rungs are **out of v1**; only vendor×project → vendor-wide history is measured.

## Run

From the API repo root (with `.env` pointing at the target database):

```bash
./.venv/bin/python -m scripts.u653b_backtest
```

Optional flags:

| Flag | Default | Purpose |
|------|---------|---------|
| `--cal-since` / `--cal-until` | 2025-10-11 .. 2026-04-10 | Calibration window (score → confidence) |
| `--eval-since` / `--until` | 2026-04-11 .. 2026-10-10 | Evaluation window (reported metrics) |
| `--verify-sample N` | 200 | After scoring, assert N Bills match `ReadVendorDominant*` procs (0 = skip) |
| `--out-dir` | `docs/design/u653b` | Report, CSV, and JSON output directory |

The script opens a transaction, runs one SELECT, then **rolls back** — no writes.

Calibration and evaluation windows must be strictly ordered and **non-overlapping** (`cal-until` &lt; `eval-since`); overlapping or reversed windows exit non-zero before any load.

Requires the same CLI system-admin context as other sync scripts (`assert_cli_system_admin`).

## Outputs (timestamped)

- `u653b_report_<stamp>.md` — coverage, top-1/top-3 (Bill- and dollar-weighted), reliability table, breakdowns, §10.5 verdict strings (informational only).
- `u653b_predictions_<stamp>.csv` — per-Bill rows on the **evaluation** window only; numeric ids and scores only (no memos or names).
- `u653b_calibration_<stamp>.json` — decile bin edges and calibrated confidence per mode for reuse.

## How to read the report

- **Mode A** — Project is taken as the Bill’s dominant project (as if the user already chose it). Measures SCC prediction quality.
- **Mode B** — Project is predicted from vendor history first, then SCC. Project accuracy is reported separately.
- **Coverage** — Share of evaluation Bills that receive any history suggestion (≥3 prior Bills at the winning grain).
- **Calibration** — Raw score `top / (total + 2)` is binned on the calibration window; each bin’s observed accuracy becomes calibrated confidence on the evaluation window.
- **Cold-start** — Vendors with fewer than three prior Bills before the scored Bill.
- **Mixed-code** — Bills with more than one SubCostCode on lines; top-1 is against the dominant SCC label only.
- **Dollar weights** — Sum of `ABS(Amount)` over **all** lines on the Bill (NULL amounts → 0; uncoded lines included). NULL-SCC exclusion applies only to label/dominant-SCC selection.
- **Vendor consistency** — At each evaluation Bill’s cutoff (only that vendor’s completed Bills created strictly before that Bill), share of prior Bills on the vendor-wide modal SCC; buckets match U-639 §8: **≥90%**, **70–90%**, **50–70%**, **&lt;50%**; top-1 accuracy per bucket. Future Bills after the evaluation window do not change a vendor’s bucket for an earlier eval Bill.
- **Origin** — Per Bill, first match wins (U-639 §8): `IntakeSource='bill_folder'` → `sharepoint_folder`; `SourceEmailMessageId` set → `email`; a `qbo.Bill` staging row matched on the dbo-native identity (`QboId` + `RealmId`) with `CreatedDatetime` no later than the dbo Bill’s `CreatedDatetime` + 5s → `qbo_pull`; creator `User.IsAgent=1` → `agent`; otherwise `person`. The loader is one query with no fallback: a schema missing any of these columns fails the run rather than degrading the breakdown.
- **Mode A without a project label** — Matches live `BillCodingSuggester` history order: dominant project (≥3 Bills) → vendor×project SCC → vendor-wide SCC (memo excluded).
- **Known residual** — Labels reflect current DB codes, not point-in-time codes (§10.2).

## Tests

```bash
./.venv/bin/python -m pytest -o addopts="" -p no:cacheprovider -q tests/test_u653b_backtest.py
BUILDONE_TEST_SQLSERVER='...' ./.venv/bin/python -m pytest -o addopts="" -p no:cacheprovider -q tests_sqlserver/test_u653b_backtest_equivalence_sql.py
```

## Runtime

In-memory scoring is O(n log n) per vendor sort; ~20k Bills typically completes in well under a minute after the single load query (network/DB bound).
