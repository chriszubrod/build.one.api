# U-486 — Coding an expense through draft → review, not through a cockpit

**Status:** DESIGN, awaiting Gate 1. Nothing built.
**Author:** `/em`, 2026-09-19. All figures measured against prod that day.
**Supersedes** U-477 Phase 4's "coding queue as a tab". Chris's direction, 2026-09-19:
the cockpit goes; coding becomes ordinary expense editing under `draft`.

---

## 1. The three-sentence answer

Card spend lands in QuickBooks on the 58999 placeholder and today is coded in a separate
cockpit whose one click writes to QBO with no review. Instead: the uncoded expenses become
**`draft`**, you code one by editing it like any other record — pick a Project, pick a
SubCostCode — and **Submit For Review** persists locally *and* pushes a surgical recode to
QBO. That makes the six lifecycle tabs real for Expense for the first time, and puts a named
human on every GL decision.

---

## 2. Measured ground truth (prod, 2026-09-19)

| | |
|---|---|
| `dbo.Expense` | 11,824 — **11,814 completed / 10 draft / 0 everything else** |
| Expense `Review` rows, ever | **0** (all 1,876 reviews belong to other entities) |
| Completed expenses with a line **labelled** 58999 | 354 |
| …**genuinely uncoded** (no `ItemRef`) — **the work queue** | **316** |
| …already coded, label merely stale | **38** — ⛔ must NOT be re-opened |
| Uncoded lines per expense | **1 for all 316** — no multi-line case exists |
| Of the 316, have a coding item (carries QBO line id + SyncToken) | **316 / 316** |
| SubCostCodes mapped to a QBO Item | **475 / 475** |

⛔ **The 354 vs 316 gap is the U-484 trap.** The cockpit's recode sets `ItemRef` and leaves
`AccountRefName` on the placeholder, so 38 finished lines still *read* as 58999. Selecting on
the account label alone re-opens finished work and invites a duplicate QBO write. **The
predicate must be `AccountRefName LIKE '%NEED TO CATEGORIZE%' AND ItemRefValue IS NULL`.**

---

## 3. Why four tabs are empty, and why this fixes it

U-470 ported Bill's six lifecycle tabs wholesale. For Bill they are real — `BillService.create`
auto-writes a Submitted review. **Nothing does that for an expense**: they arrive from the QBO
pull already `completed`, and `ExpenseCreate` has no submit-for-review flag. So `submitted`,
`in_review`, `approved` and `declined` are not empty today — they are *unreachable*.

This design gives them their first real population.

---

## 4. The flow

```
  QBO pull ──► completed (as today)
                  │
   backfill ──────┤  316 genuinely-uncoded → draft      [§6.1]
                  ▼
               DRAFT ──► you edit: Project + SubCostCode   (local auto-save, as today)
                  │      ⛔ pull must NOT clobber this     [§6.2 — the blocker]
                  ▼
          SUBMIT FOR REVIEW
                  ├─ status draft → submitted
                  ├─ create the Review row (new for Expense)
                  └─ enqueue surgical QBO recode           [§5]
                  ▼
              IN REVIEW ──► PM / Owner approves or declines
                  │
      approved ───┴──► completed   ·   declined ──► back to draft
```

---

## 5. The QBO write — already built, already surgical

`PurchaseExpenseConnector.recode_purchase_line` is the **only** sanctioned app→QBO expense
write and needs no change. It fetches the live raw Purchase JSON, finds **only** the target
line by Id, and:

```python
target["DetailType"] = "ItemBasedExpenseLineDetail"     # account → line item
target["ItemBasedExpenseLineDetail"] = {ItemRef, CustomerRef}
target.pop("AccountBasedExpenseLineDetail", None)
```

carrying `ClassRef` / `BillableStatus` / `TaxCodeRef` / `MarkupInfo` forward. **Amount is never
touched** (absent from the mutation). The QBO `Line.Id` survives because the dict is edited in
place — which is why cockpit-coded lines keep their staging row (all 38 `written`, zero
orphaned). Siblings and header go back byte-identical.

⚠️ **It is a full-document PUT, not a QBO sparse update.** The safety is that the body is
QuickBooks' *own* document with one line different — not that less was sent. The SyncToken
check is what makes that safe.

**Non-destructive guarantees, all existing:**
- Acts **only** on lines still on the 58999 placeholder — structurally cannot touch coded work.
- **Fail-closed on SyncToken drift** → `PurchaseChangedInQboError` → `changed_in_qbo`, never
  overwrites a concurrent QBO/Ramp edit.
- **Idempotent**: already our item → `already_recoded`, no write. A *foreign* recode → raises.
- Missing item mapping → raises **before** any write.
- Double-gated: `ALLOW_QBO_WRITES` **and** `ALLOW_EXPENSE_RECODE_WRITES`.

⛔ **Never reuse `sync_to_qbo_purchase`** — it rebuilt every line from local data and silently
dropped any lacking a mapping (sibling loss, verified P0). Deleted in U-354. Do not resurrect.

🔴 **`ALLOW_EXPENSE_RECODE_WRITES` is `false` in prod today** (`ALLOW_QBO_WRITES` is `true`).
Nothing reaches QBO until that flips, and CLAUDE.md requires one controlled live recode + a
re-pull + a live fail-closed test first. **That is a separate Gate 2, not part of the build.**

---

## 6. The two hard problems

### 6.1 The backfill fights the terminal lock

U-468 makes `completed` expenses refuse edits (`assert_editable`). The backfill therefore needs
the same kind of exempted path U-483's reconcile used — a dedicated sproc, not the service
update path. Must be **snapshotted first** (Id, Status, prior values) into
`docs/operations/`, exactly as U-483 was, so the flip is reversible row-by-row. Idempotent and
re-runnable; selects on the §2 predicate, never the account label alone.

### 6.2 ⚠️ CORRECTED 2026-09-19 — there is NO pull-clobber blocker

**This section originally claimed the pull erases a local coding edit within 15 minutes. That was
WRONG, and Phase A was scoped and built on it before the error was caught. Phase A was reverted.**

What is true: `sync_from_qbo_purchase_line`'s update path does pass QBO-derived values through —
`sub_cost_code_id=sub_cost_code_id`, `project_public_id=project_public_id` — and both resolve to
`None` for a line still on the 58999 placeholder.

What was missed: **`ExpenseLineItemService.update_by_public_id` only assigns fields that arrive
non-`None`** (`if sub_cost_code_id is not None:` …). So sending `None` has always meant *leave it
alone*, and a local coding edit already survives the pull. Proven directly: a pull-shaped call with
`sub_cost_code_id=None, project_public_id=None` against a row holding `481` / `77` leaves both intact.

Why `quantity` / `rate` / `markup` DO need `preserve_stored_value` and these two do not: those three
carry **non-None defaults** (`default_qty`, `default_rate`, `default_markup`) which would be written
over a local value even when QBO omits the field. `sub_cost_code_id` and `project_public_id` have no
defaults — they are `None` on omission — so they were never exposed.

⛔ **Do not "fix" this again.** Routing the two coding fields through `preserve_stored_value` is a
provable no-op: `preserve_stored_value(None, None, stored)` returns `None`, which is exactly what the
unprotected call already passed. A mutation reverting such a "fix" survives every test, because the
two forms are behaviourally identical.

**Root cause of the error:** the call site was read and an overwrite inferred, without checking the
receiving service's `None` handling. The answer was in `preserve_stored_value`'s own docstring.

**Residual, genuinely open (smaller, different):** if a line's dbo-native identity is ever lost and the
line is re-CREATED by the pull, the new row takes QBO's values and a local coding edit would not
survive that. That is line churn, not the 15-minute update path, and it is not on the critical path
for this design. Worth a look before Phase D.

---

## 7. Submit For Review — ordering is load-bearing

Three effects: flip status, create the Review, enqueue the recode.

⛔ **Do not repeat the U-232 shape.** `confirm()` commits its confirmation *before* enqueueing
with no wrapping try/except, so a DB error at enqueue strands a `confirmed` row with no outbox
entry. The 2026-07-16 incident was 44 codings that looked confirmed and never reached QBO.
Enqueue must be part of the same failure domain as the status flip, or the flip must be
recoverable by a reconcile that finds submitted-but-unenqueued rows.

**Reviewer resolution:** Project PMs + Owners (Chris, 2026-09-19), walking
`ExpenseLineItem → Project → UserProject`. ⭐ Neat consequence: coding *assigns the Project*,
so by submit time a project always exists — the "most expense lines have no project" problem
that blocked this in U-477 §8 Q3 **evaporates for this path**. A fallback is still required for
safety, and must be explicit rather than silent.

---

## 8. Stated consequence — review does NOT gate the QBO write

Push happens at **Submit**, per Chris 2026-09-19 (the alternative, push-on-approval, was
offered and declined). So QuickBooks already holds the new coding by the time a reviewer looks.
The review is an **audit and accountability record, not a pre-write gate**. This matches today's
cockpit (one click writes) while adding a named reviewer, which is strictly better than now —
but it is not the same as "nothing reaches QBO until approved." Moving the push to approval is
a one-line change of trigger if that is ever wanted.

A **declined** expense returns to `draft` for re-edit; re-submitting pushes again, which is
safe because the recode is idempotent and fail-closed.

---

## 9. Non-negotiables

1. ⛔ Selection predicate is `AccountRefName LIKE '%NEED TO CATEGORIZE%' **AND ItemRefValue IS
   NULL**`. Never the account label alone. (§2)
2. ⛔ `recode_purchase_line` is the only write path. Never `sync_to_qbo_purchase`.
3. ⛔ Backfill snapshots before mutating, into `docs/operations/`, reversible per row.
4. ⛔ The draft-authorship rule must be proven in **both** directions (§6.2).
5. ⛔ No change to the 38 stale-label rows, and none to any `written` /
   `resolved_externally` coding item.
6. ⛔ `ALLOW_EXPENSE_RECODE_WRITES` stays `false` through the build. Flipping it is its own Gate 2.
7. The `ExpenseCodingItem` tracker stays as instrumentation — submit drives it
   `confirmed → enqueued → written`, so U-483/U-484 metrics keep working.

---

## 10. Phases — each independently shippable, each its own Gate 1

| # | Phase | Why this order |
|---|---|---|
| ~~**A**~~ | ~~Draft-authorship rule~~ — **WITHDRAWN 2026-09-19**, see §6.2 | The blocker it addressed does not exist. Built, proven a no-op, reverted. |
| **B** | **Backfill** 316 → draft, snapshotted | Gives you the 326-row work queue to look at. No new UI needed — the Draft tab already exists. |
| **C** | **Submit For Review** — status flip + Review row + enqueue (§7) | The workflow itself. |
| **D** | **Web**: expense edit page gains Project + SubCostCode selection and a Submit For Review action | The surface you actually use. |
| **E** | **Retire the cockpit** — page, nav entry, route redirect | Only once D works. Nothing is removed before its replacement is live. |

**Sequencing note (CORRECTED 2026-09-19):** the original "A before B or B destroys work" constraint was
based on the §6.2 error and is void. **B is now the first phase**, and has no prerequisite.

---

## 11. Open questions

- **Q1 — the other 111 open coding items.** 427 coding items are open but only 316 map to a
  completed local Expense. The rest have no local Expense, or no map row. They have no home in
  this design. Options: leave them to the U-483 `resolved_externally` path, or fix the map gap.
- **Q2 — what clears `draft` if coding happens in QBO instead?** If you code one directly in
  QuickBooks, the pull heals the line but nothing returns the expense to `completed`. Needs a
  rule, or those rows sit in Draft forever.
- **Q3 — reviewer fallback** when a Project somehow still does not resolve (§7).
