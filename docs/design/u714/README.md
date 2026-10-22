# U-714 — Expense coding suggestion backtest

Read-only harness to size **U-715** (suggestion-only coding on the expense edit page). Mirrors the U-653b bill-history backtest shape: one DB read (rollback at end), in-memory rolling-origin replay, calibration window + evaluation window, decile calibration, and `--out-dir` artifacts.

## What it measures

Per **completed expense line** (non-draft header and line, labeled `SubCostCodeId` not holding/suspense):

| Suggester | Logic |
|-----------|--------|
| `engine` | Production `CodingSuggestionEngine.suggest_for_item`: `extract_hints` on memo → project abbreviation / address / SCC shorthand → as-of vendor modal SCC (≥3 priors). Holding SCC dropped; **only `engine`** applies the `SubCostCode.QboId` writable gate. |
| `bill_style` | U-653b Mode B ladder at **line grain**: vendor×project history, then vendor-wide, smoothed-share confidence, `MIN_PRIOR_BILLS=3`. **Memo is ignored** — memo on/off rows are **identical by design** for this suggester (not evidence that leakage is absent). |

Each suggester runs twice: **memo on** (header `Expense.Memo` fed to hints) and **memo off** (memo masked — leakage probe per U-691). Line `Description` is loaded but not used by either suggester today; it is masked in the same flag for future use.

**Labels** are the line’s **current** coded `SubCostCodeId` / `ProjectId` (U-653b residual — not a point-in-time historical code).

**Segments:** `all`, `qbo_pulled` / `manual` (`Expense.QboId`; the old `qbo.PurchaseExpense` mapping was retired by U-354), `card` (`qbo.Purchase.PaymentType = CreditCard` when QBO-linked), `vendor_has_history_yes` / `_no` (≥3 strict priors at score time). The **`manual` row is always present in segment tables** (`n_eval=0` when every loaded line is QBO-pulled, which is the current prod corpus).

**Metrics (per segment):** coverage, SCC top-1 on covered, project coverage (share of covered with a project prediction), project accuracy on lines where a project was predicted, both-correct (SCC correct **and** predicted project equals label project, with `None == None`), reliability by calibrated-confidence decile (`n`, mean calibrated confidence, observed accuracy), confident-and-wrong SCC rate at 0.6 / 0.7 / 0.85.

**Attachment feasibility (aggregates only):** counts by `Attachment.ExtractionStatus` on expense-line attachments; token-count proxy buckets via `AttachmentTextTokenHash.TokenCount` for completed rows; count of expense attachments with a hash row. No memo/description/vendor/project names in outputs.

## Rolling-origin / as-of clock

Production timestamps on `Expense` are **not** usable as a completion or import clock for history:

- **`StatusDatetime`:** 11,418 of 11,654 completed expenses share **2026-09-16** from the U-357 status backfill (median “completion lag” vs `CreatedDatetime` ≈ 229 days).
- **`CreatedDatetime`:** 9,878 rows stamped **2026-01-30** on initial import; earliest `CreatedDatetime` is 2026-01-30 while **`ExpenseDate` spans back to 2022-03-31**.

So each evaluated line is scored as of its **`ExpenseDate`**. Vendor priors are earlier **lines** (not expenses) whose **`ExpenseDate` ≤ evaluated `ExpenseDate` − `--coding-lag-days`** (default **7**), approximating purchase-to-coding delay. With **`--coding-lag-days 0`**, only **strictly earlier** `ExpenseDate` counts (same calendar day excluded). **Same-expense sibling lines never count** toward each other. Per vendor, priors are kept in an **`ExpenseDate`-sorted index** with bisect (not an O(n²) rescan).

**`CreatedDatetime`** is used only to break ties when two lines share the same `ExpenseDate` (processing order). **`StatusDatetime` is not used for prior inclusion**; run metadata may still report StatusDatetime−Created lag as a **data-quality note** only.

**Residual:** labels are today’s codes; ordering history by `ExpenseDate` assumes coding happened within the coding-lag buffer (or earlier).

**Evaluation window** filters on **`ExpenseDate`** (default last six months). Calibration window is the six months immediately before evaluation. History for both windows uses all earlier lines under the rule above.

In prod measurement, **every completed expense is QBO-pulled** (`QboId` set); the manual segment is **empty (`n=0`)** but still reported.

## In-memory vs production

| Production call | Harness |
|-----------------|--------|
| `ProjectService.read_by_abbreviation` | Active projects loaded once; case-insensitive abbreviation match |
| `ProjectService.find_for_invoice` | Address strategies only (0.85 / 0.75 / 0.65 substring-on-Name); tie-break Confidence DESC, Name ASC |
| `SubCostCodeService.find_for_reply` | Python port of `FindSubCostCodeForReply` ranking; tie-break Confidence DESC, Number ASC |
| `ReadVendorDominantSubCostCode` | Line-grain counts with ExpenseDate-as-of cutoff + holding exclusion |
| `resolve_qbo_item_ref` | `SubCostCode.QboId IS NOT NULL` on loaded reference rows (**engine only**) |

Pure functions reused: `extract_hints`, U-653b `predict_mode_b`, `validate_windows`, `CalibrationMap` / decile fitting pattern.

## How to run

From repo root ( **production credentials** — EM / system admin only):

```bash
./.venv/bin/python -m scripts.u714_expense_coding_backtest
./.venv/bin/python -m scripts.u714_expense_coding_backtest \
  --cal-since 2025-10-23 --cal-until 2026-04-22 \
  --eval-since 2026-04-23 --eval-until 2026-10-22 \
  --coding-lag-days 7 \
  --out-dir docs/design/u714/results
```

Optional per-line CSV (ids only): `--write-lines`.

Outputs (timestamped under `--out-dir`, default `docs/design/u714/results`): `u714_summary_*.md`, `u714_metrics_*.json`, `u714_attachment_feasibility_*.json`; `u714_predictions_*.csv` only with `--write-lines`.

Unit tests (no DB):

```bash
./.venv/bin/python -m pytest -q tests/test_u714_expense_coding_backtest.py
```

See also generated `u714_summary_*.md` in results for the narrative mirror of rolling-origin and memo-probe notes.
