# U-652d3 — One definition of "uncategorized" (strict)

Approved by Chris at Gate 1, 2026-11-10, as TODO item U-652d (1).

## Problem

Two definitions of a QBO purchase line that "needs categorizing" are in use, hand-copied across 15 sites:

- **Loose:** `AccountRefName LIKE N'%NEED TO CATEGORIZE%'`.
  Used by the coding queue, the metrics count, submit's dominant coding, the seed gate, confirm validation, confirm's recoded-line stamping, and the recode worker's target selection.
- **Strict:** the same label **and** `ItemRefValue IS NULL`, meaning the line has not already been recoded to an Item.
  Used by the externally-resolved sweeper, the uncoded-candidates backfill, expense completion, pull-time status, and two scripts.

The loose sites treat a line that is already recoded, but still carries the label in staging, as uncategorized. On prod on 2026-11-10, read-only:

- 473 lines match the label; 386 of them are strict.
- 87 purchases' only placeholder line is already recoded.

Under the loose definition, those 87 purchases are still queued, still counted, and still targeted by the worker. A purchase with one genuine line and one recoded line is treated as multi-line and refused at confirm.

## Decision

The strict definition is the only one.

- **SQL home:** a computed column `qbo.PurchaseLine.IsUncodedPlaceholder`, defined as `BIT` = label match AND `ItemRefValue IS NULL`, `PERSISTED`. It is added idempotently in `integrations/intuit/qbo/purchase/sql/qbo.purchase.sql`. Every SQL site and every inline-SQL site in Python says `pl.[IsUncodedPlaceholder] = 1`.
- **Python home:** `integrations/intuit/qbo/purchase/placeholder.py`, holding `PLACEHOLDER_ACCOUNT_LABEL` and `is_uncoded_placeholder_line(line)`.
  - `PurchaseExpenseConnector._qbo_purchase_line_is_uncoded_58999_placeholder` delegates to it.
  - `_raw_line_is_categorize_placeholder` reads live QBO JSON, so it stays separate, but uses the constant.

## Sites

| Site | Today | After |
|---|---|---|
| `ReadExpenseCodingQueue`, `ReadExpenseCodingMetrics` (qbo.expense_coding_queue.sql) | loose | column (strict) |
| `ReadExternallyResolvedCodingSuggestionCandidates` gate | strict | column |
| `ReadUncodedCompletedExpenseCandidates` (dbo.expense.sql) | strict | column |
| `ExpenseService._resolve_dominant_expense_coding_for_submit` | loose | column (strict) |
| `ExpenseService._expense_has_genuine_uncoded_staged_lines` | strict | column |
| `CodingSuggestionService._count_uncategorized_purchase_lines`, `_uncategorized_staging_qbo_line_ids` | loose | column (strict) |
| `QboOutboxWorker._read_uncategorized_staging_line_ids` | loose | column (strict) |
| `scripts/redrive_skipped_expense_recodes.py`, `scripts/backfill_complete_coded_qbo_drafts.py` | strict | column |

U-652d2 already removed the legacy `QboLineId` CASE and `legacy_single_line_ids`.

## Behaviour changes (intended)

- A recoded line no longer counts.
  - A purchase whose placeholder lines are all recoded leaves the queue and metrics, and the worker no longer targets it.
  - It also no longer gets a suggestion seeded.
- A purchase with one genuine and one recoded line counts as single-line. It can be confirmed, and the worker targets only the genuine line.
- Submit: an expense whose placeholder lines are all recoded gets no dominant coding (`None`). The builder must verify that submit handles `None` cleanly, meaning nothing is enqueued and nothing errors.

## Apply order (Gate 2)

1. `qbo.purchase.sql` (adds the column)
2. `qbo.expense_coding_queue.sql`
3. `dbo.coding_suggestion.sql`
4. `dbo.expense.sql`
5. Then the API image.

The column must exist before any proc or image references it. Drift-check every base file against the live definitions first. Base files may be stale; never re-apply a file whose other procs differ from live without reconciling them.

## Deploy

- The column add is a PERSISTED computed column. On the live table (about 13.5K `qbo.PurchaseLine` rows) SQL Server rewrites the table under a Schema-Modification lock. Apply it in a quiet window, not while a `:07/:22/:37/:52` purchase pull is writing staging. Then apply the procs that reference the column.
- Apply order stays: `qbo.purchase.sql` (column), `qbo.expense_coding_queue.sql`, `dbo.coding_suggestion.sql`, `dbo.expense.sql`, then the API image. The image must not run before the column exists.

## F3 is the permanent rule (review_submit writes its own ELI coding)

The rule is permanent: a `review_submit` line is written with its own ELI coding at drain time, never `confirmed_*`. The deploy straddle is the case that exposed it, not the only reason for it.

A submit recorded under the OLD loose dominant can carry a recoded sibling's project and sub cost code in `confirmed_*`. If it drains after deploy, the strict set is one genuine line, so the single-line branch would write that sibling's coding onto the genuine line.

Fix: a `review_submit` row builds its single-line target from the local `ExpenseLineItem` of that line (the same `_recode_target_from_local_line` path as the multi-line rule). The written coding is always that line's own. If the ELI cannot resolve, the row errors and is not written from `confirmed_*`.

## Sticky ItemRef (F4)

Staging keeps `ItemRefValue` once set, so a line a human reverted to the placeholder kept an Item and dropped out of the strict set. `UpdateQboPurchaseLineById` now clears `ItemRefValue` and `ItemRefName` when the incoming `DetailType` is `AccountBasedExpenseLineDetail`. Item-based lines keep the old behaviour. The pull passes `DetailType` from QBO's `DetailType` field. If QBO omits it, the update keeps the old value (no clear).

## Empty strict set (F1)

When the strict set is empty, the drain probes the label-only lines of the purchase, recoded or not, using the same single and multi rules. The connector's live fence then decides `written` (adopted live token) or `changed_in_qbo`. Only an empty label set is `error: no_target_lines`. Repoints do not use the probe.

## Zero-count confirm (F2)

A confirm with no genuine placeholder line is refused as `nothing_to_recode` before any write. The router maps it to 409 with the status as detail, like the other non-enqueued refusals.
