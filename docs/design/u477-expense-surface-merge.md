# U-477 — DESIGN: merge the Expense and Expense-Coding endpoints onto Bill's functionality

> **Status: DESIGN ONLY. Nothing is built.** Per [[feedback_two_phase_dispatch_design_gated]] this document is
> the unit; `/em` dispatches build phases only after Chris approves it. Each phase then opens its own Gate 1.
>
> **Assignment (Chris, 2026-09-17):** *"merge the Expense and Expense Coding endpoints, and consolidate into
> the functionality that exists for the Bill endpoints."*
>
> ⚠️ **This is the SECOND time this assignment has been given.** The first (2026-09-03, via `/pm`) was
> *reframed* rather than built — see the un-park note in
> `build.one.team/product/specs/unified-expenses-surface.md`. It is not reframed again here. The design
> delivers the endpoint merge as asked. Where it declines something, it says so plainly and gives the reason,
> rather than redefining the request.

---

## 1. The three-sentence answer

The Expense ledger and the coding cockpit read **different tables in different keyspaces** — the ledger reads
`dbo.Expense`, the cockpit reads `qbo.Purchase`/`qbo.PurchaseLine` plus a side-car — and **38% of open coding items
cannot be reached from an Expense at all**, so the merge cannot be a re-keying or a data migration. It is a
**read-composition plus a route consolidation**: one `/expense/...` family, coding state exposed on every
expense read, coding actions still addressable by their own identity, and **exactly one writer** of the GL code
(QuickBooks, via the surgical recode). Bill parity is then four capabilities, three of which are cheaper than
they look because their server-side machinery already exists.

---

## 2. Measured ground truth (read-only, 2026-09-17 — refresh before building)

| Fact | Value | Why it matters |
|---|---|---|
| `dbo.ExpenseCodingItem` rows | **588** — 396 pending, 103 suggested, 45 flagged, 38 written, 6 changed_in_qbo | The queue |
| QBO lines still on 58999 | **465** | Live inflow |
| Coding items resolving to a local `ExpenseLineItem` | **368 of 588** | The clean join |
| Coding items with **no** local line | **220** | July's spec assumed ~88. It has grown. |
| …**orphans** — the `qbo.PurchaseLine` itself is GONE | **134** | ⭐ Invisible in BOTH surfaces (the queue reads FROM that table). All non-written: 62 pending / 54 suggested / 14 flagged / 4 changed_in_qbo |
| …live line, but **no Expense at all** | **73** | Structurally unaddressable by expense identity |
| …**Expense exists, only the map row is missing** | **13** | The genuinely fixable pull gap |
| Completed Expenses carrying an OPEN coding item | **330** | The contradiction the merge exists to end |
| Uncoded spread | 2022-05 → 2026-09, **10 in September** | Ongoing inflow, not a backlog |
| `dbo.Expense` lifecycle | 11,746 `completed` / 10 `draft`, all completed via `qbo_pull` | Post U-467 |

> ⚠️ **CORRECTED 2026-09-18 — the first draft of this table was WRONG.** It claimed **144** items had a parent
> Expense with only the line-map row missing, and called that "a fixable pull gap worth its own unit". The real
> figure is **13**. The 144 came from a looser join (`qbo.Purchase` via the coding item's stored
> `QboPurchaseId`) that counted rows whose staging **line** had already vanished. Re-measured with the line
> join: 134 orphans / 73 no-Expense / 13 map-missing. Recorded rather than silently edited, because the wrong
> number was quoted in the U-480 commit message and on the board.
>
> **Of the 550 OPEN items: 330 mapped (the badge works, and these ARE exactly the 330 contradictory rows
> Phase 1 set out to fix) · 134 orphans · 73 no-Expense · 13 map-missing.**

**The unaddressable set decides the architecture.** Any merged endpoint keyed solely on `expense.public_id`
silently drops the **73** items with a live line but no Expense, **and the 134 orphans** — together **207 of
550 open items, 38%**, every one a real card charge that still needs coding. So coding items keep their own
addressable identity. This is not a compromise on the merge; it is what the data requires.

**The 134 orphans and the 13 map-missing rows are open questions, not design constraints** — see §8 Q1.

---

## 3. What already shipped since the July spec

The July spec (U-057) was written against a world that no longer exists. Four of its preconditions are now met:

| Spec assumption | Reality now |
|---|---|
| "Expenses has **no ledger surface at all** (parked, unrouted)" | Routed since U-124; **U-470** shipped the card layout, six lifecycle tabs, server-side filters and pagination |
| "**🔴 THE BLOCKER** — the coding queue is not row-scoped" | **Closed by U-059.** All three scans carry `UserCanAccessProject` |
| "what's left is the **pagination rewire**" | **Done, unknowingly, by U-470** |
| Expense has no lifecycle column | **U-467** gave it stored `Status`; **U-468** added the terminal lock |

**So the remaining U-057 scope is the "needs coding" badge — which this design absorbs as Phase 1.**

One thing got *harder*: U-468's terminal lock plus U-467's `qbo_pull → completed` binding means 11,746 of
11,756 expenses are terminal and refuse header edits. The coding path is unaffected (it never touches `dbo`),
but any future idea of "edit the expense to code it" is now blocked by design as well as by policy.

---

## 4. The write-path decision — re-argued, not inherited

**Decision: ONE WRITER. The merged endpoint routes a coding action to the surgical QBO recode; it does not
write `dbo.ExpenseLineItem.SubCostCodeId`.**

This was decided in July after a `/redteam` pass. It is re-validated here because a design that silently
inherits a decision is indistinguishable from one that never noticed it. Four grounds, three measured:

1. **The heal is guaranteed by construction, and measured.** Confirm → outbox → recode writes the `ItemRef` in
   QuickBooks; the next purchase pull (**every 15 min**, on :07/:22/:37/:52) resolves it back to
   `SubCostCodeId`. Live-proven **38/38 healed, 0 NULL**. `confirm()` refuses a SubCostCode with no resolvable
   QBO Item (`mapping_missing` → 422), so every confirmable coding has a mapping in both directions.
2. **A wrong local write is permanent.** `UpdateExpenseLineItemById` preserves-on-NULL, so the pull can never
   clear a bad local code. A dual-write turns a recoverable QBO edit into an unrecoverable local one.
3. **The gate is off at rest.** With `ALLOW_EXPENSE_RECODE_WRITES=false`, a local write would feed budget
   variance — and a col-Z-frozen Box DETAILS row — from a coding QuickBooks never received.
4. **NEW since July: the alternative no longer exists.** `sync_to_qbo_purchase` / `sync_expense_to_qbo` were
   **deleted in U-354**. `recode_purchase_line` is now the only app→QBO purchase write in the codebase. The
   prohibition is enforced by absence rather than discipline. ⛔ **No phase of this design may reintroduce a
   full-line-list rebuild under any name** — it silently drops sibling lines, a verified P0.

**What this costs, stated plainly:** up to ~15 minutes between confirming a code and the local ledger agreeing.
Phase 1's badge is derived from `ExpenseCodingItem.Status`, **never** from `SubCostCodeId`, precisely so the UI
stays honest during that window.

**If Chris wants the local write to be authoritative instead**, that is a different system: it needs a
reconciler that can detect and correct divergence, because the pull would then be fighting the local value.
That is a larger unit than this one and is not designed here. See §8 Q4.

---

## 5. The merge — target shape

### 5.1 Route map

**Principle: one `/expense` family; old paths survive as aliases.** Aliases are near-free (FastAPI route
decorators can stack) and they protect three surfaces a rename would break: the **iOS shipped binary**, the
**in-repo agent tools**, and the **agent prompt text**, which names literal paths.

| Today | Merged | Alias kept? | Notes |
|---|---|---|---|
| `GET /expense-coding/queue` | `GET /get/expense/coding/queue` | yes | 1 web consumer |
| `POST /expense-coding/suggest` | `POST /expense/coding/suggest` | yes | |
| `GET /expense-coding/metrics` | `GET /get/expense/coding/metrics` | yes | keeps `recode_writes_enabled` |
| `POST /expense-coding/{id}/claim` | `POST /expense/coding/{id}/claim` | yes | **zero consumers** — see §8 Q2 |
| `POST /expense-coding/{id}/release` | `POST /expense/coding/{id}/release` | yes | **zero consumers** |
| `POST /expense-coding/{id}/flag` | `POST /expense/coding/{id}/flag` | yes | |
| `POST /expense-coding/{id}/confirm` | `POST /expense/coding/{id}/confirm` | yes | ⛔ status-code contract is load-bearing: 422 vs 409 and the `enqueued` boolean are all consumed by the UI |
| — | `GET /get/expense/{public_id}/coding` | new | coding state for one expense (may be a list: an expense can have several coded lines) |

⚠️ **Route declaration order matters.** Bill declares its literal segments (`by-bill-number-and-vendor`,
`find-by-conversation-id`) *before* `/{public_id}`. Adding `/get/expense/coding/...` alongside
`/get/expense/{public_id}` requires the same discipline or `coding` is swallowed as a public_id.

### 5.2 Read-composition — the substantive half

`GET /get/expenses` and `GET /get/expense/{public_id}` gain a **`coding`** block, batch-stitched exactly as
Bill already batch-stitches `project_id` / `review_status` (one lookup per page, never per row).

```
"coding": {
  "needs_coding": true,              // derived from ExpenseCodingItem.Status, NEVER from SubCostCodeId
  "open_items": 1,
  "items": [ { "public_id", "status", "confidence", "suggested_project_id",
               "suggested_sub_cost_code_id", "flag_reason" } ]
}
```

The join hop is fixed and must not be shortcut (`qbo.*.Id ≠ dbo.*.Id`):
`ExpenseCodingItem.QboPurchaseLineId → qbo.PurchaseLineExpenseLineItem.ExpenseLineItemId →
dbo.ExpenseLineItem.ExpenseId`.

This is what actually ends the "finalized here, needs coding there" split for the **330** expenses currently in
it, and it is the piece that makes one surface possible without moving any data.

### 5.3 What the merge deliberately does NOT do

- **No data migration.** `dbo.ExpenseCodingItem` keeps its QBO staging keys and its zero foreign keys. The
  header comment explains why: staging is re-pulled and replaced, so a FK would break on every churn.
- **No re-keying to expense identity.** The 76 orphans would vanish.
- **No new external write.** Confirm's path is untouched.
- **No local dual-write.** §4.

---

## 6. Bill parity — the four capabilities, scoped

| # | Capability | What exists already | What must be built | Cost |
|---|---|---|---|---|
| P1 | **Folder intake** (`/process/expense-folder*`) | ⭐ **Almost everything**: `ExpenseFolderProcessor`, `folder_run_repo` (method-for-method identical to Bill's), both run tables, the SharePoint connector, **and two live scheduler timers already driving it** | **6 user-facing routes + web affordance.** ⛔ Do NOT port Bill's `process/bill-folder-single` — it references an undefined name, fails on every call, and has no caller | **Low** |
| P2 | **`find-by-conversation-id`** | `Expense.SourceEmailMessageId` already threaded through create | 1 sproc (`FindExpenseForReviewerReply`), 1 repo method, 1 route, 1 agent tool | **Low** |
| P3 | **Durable completion result** | Bill's table + 2 sprocs are the template | `dbo.ExpenseCompletionResult` + 2 sprocs + 2 repo methods; replace the in-process dict | **Low-medium.** Today's dict is per-worker with `-w 2`, so a poll can miss the worker that ran the job. Both routes are currently **uncalled**, so this is correctness, not an unblock |
| P4 | **`apply-reviewer-decision`** | The generic `ReviewService` already handles expense parents | A **new** recipient sproc — Bill's walks Bill→BillLineItem→Project→UserProject; Expense must walk ExpenseLineItem→Project. Plus the service method and the agent tool | **Medium.** ⚠️ Also needs a product answer: who reviews an expense, and what a credit/refund review means. See §8 Q3 |

**Smaller parity gaps, all cheap, worth folding into P1:**

- `GET /get/expense/id/{id}` — service and repo methods already exist; route-only.
- `GET /get/expenses` echoes neither `project_id` nor `vendor_public_id` per row (Bill echoes both); `GET
  /get/expense/{id}` echoes no QBO deep link (Bill echoes `qbo_bill_url`).
- `ExpenseCreate` has no `submit_for_review` flag.
- **Every Expense handler is sync `def` and blocks the event loop**; Bill's equivalents offload via
  `asyncio.to_thread`. Pure refactor, no new backing. This one is a latency bug hiding as a style difference.

---

## 7. Phased build order

Each phase is independently shippable and independently valuable. **Each opens its own Gate 1.**

**Phase 1 — Read-composition + badge.** The `coding` block on both expense reads, batch-stitched; web shows a
"Needs coding" badge on ledger rows. *No route moves, no consumer breaks, nothing renamed.* Delivers the
headline value — 330 contradictory rows stop being contradictory — and is the cheapest thing here.
*Verify:* a row with an open coding item badges; coding it clears the badge after the pull; the badge derives
from coding status, provable by mutation.

**Phase 2 — Route consolidation.** Coding routes gain their `/expense/coding/*` spellings; old paths stay as
aliases; the cockpit's 5 call sites and 2 type names move. *Verify:* both spellings answer identically; a test
pins that the alias cannot be dropped without a deprecation cycle.

**Phase 3 — Bill parity, cheap-first.** P1 folder routes + the small gaps + the async offload → P2
conversation-id → P3 durable completion result. **P4 waits on §8 Q3.**

**Phase 4 — One web surface.** The coding queue becomes a view within the Expenses page rather than a separate
nav entry, per the July spec's Phase B/C. *Verify:* the spec's acceptance criteria, which are already written
and still valid.

**Sequencing note:** Phase 1 is genuinely independent — if Chris wants only one thing from this design, it is
Phase 1.

---

## 8. Open questions — `/em` needs answers before the matching phase

**Q1 — the orphans and the map gap (re-measured 2026-09-18).** Two separate problems, not one:
- **134 orphans** whose `qbo.PurchaseLine` no longer exists. The coding queue reads FROM that table, so it
  **structurally cannot show them** — and all 134 are non-written, so they read as open work in the table while
  being invisible in both surfaces. This is the cost of the coding item deliberately carrying **no FK to
  volatile staging** (right call for survivability, but it left no cleanup path). It also makes the cockpit's
  funnel internally inconsistent: the per-status counts aggregate `dbo.ExpenseCodingItem` while
  `TotalTargetLines` reads live staging, so the page reports **396 pending against 465 live target lines** —
  two different populations. *Recommend: a reconcile unit — close, re-link, or exclude — plus a metrics fix so
  the funnel counts only what the queue can surface.*
- **13** rows where the Expense exists and only the map row is missing. Small enough to fold into whichever
  unit next touches the pull.

**Q2 — claim / release.** Both endpoints have **zero consumers**; the UI never calls them, and confirm
auto-claims. There is also no UI path to clear a stale claim. Keep them as an API-only surface, or retire
them? *Recommend: keep the service-layer lease (confirm depends on it), drop the two HTTP routes.*

**Q3 — who reviews an Expense?** P4 needs it. Bill resolves reviewers to the Project's PMs and Owners. Expense
lines often have **no Project** (the majority of the 58999 population), and a credit/refund has no obvious
reviewer. *Recommend: defer P4 until this is answered; it is a product question, not an engineering one.*

**Q4 — the write path.** §4 recommends keeping QuickBooks as the single writer. Confirming this closes the
question for every later phase; overruling it makes Phase 1's badge semantics and the whole coding path a
different design.

---

## 9. Non-negotiables any build phase must preserve

Carried from the cockpit map; a reviewer should treat a violation of these as a P0.

1. **Both write gates checked at the top of `confirm()`, before any DB write** — either gate off ⇒ 422 with
   **zero** DB writes. This encodes a real incident: 44 codings once looked confirmed and never reached QBO.
2. **Default-deny gates**; resting state `false`; go-live is one reversible flip. A gate flip must **never**
   become a floodgate — confirm-while-off writes no outbox row, so nothing replays.
3. **Green toast only on `enqueued === true`.** A book-of-record tool must never say "done" while writing
   nothing.
4. **Surgical single-line RAW-JSON recode only.** Amount never touched, sibling lines and unmodeled fields
   byte-identical. ⛔ No full-rebuild push, under any name.
5. **Fail-closed on SyncToken drift and on foreign recodes** — bounce to re-review, never the generic
   refresh-and-retry.
6. **No local dual-write** (§4). The badge derives from coding status, never from `SubCostCodeId`.
7. **U-059 row-scoping on all three scans**, with uncoded rows visible to every Expenses user by policy. The
   queue and the ledger must enforce the same access model.
8. **RBAC split**: `can_read` for queue/metrics, `can_update` for suggest/flag/confirm.
9. **`qbo.*.Id ≠ dbo.*.Id`** — always the three-hop join; never alias a `qbo` PK as an `ExpenseId`.
10. **Human confirm is mandatory.** No auto-apply at any confidence.

---

## 10. Known defects to fix in passing (found while designing, not introduced here)

- `MarkExpenseCodingWritten` accepts a `@SyncToken` parameter and the repo passes it, but the UPDATE body never
  persists it and **no column exists**. "What token did we leave QuickBooks at" is unanswerable today.
- `RecordExpenseCodingConfirmation` has **no state guard** (its siblings do). Protection lives entirely in the
  service layer.
- U-232 follow-up, still open: `confirm()` commits the confirmation **before** enqueueing, with no wrapping
  try/except, so a DB error at enqueue can strand a `confirmed` row with no outbox entry.
- The suggestion engine resolves Project and SubCostCode **independently** and nothing validates that the SCC
  belongs to the Project.
- There is **no scheduler timer for `suggest_pending`** — the web button is the only trigger, so the queue goes
  stale whenever nobody clicks it.
- Stale doc: `build.one.api/CLAUDE.md` still warns against reusing `sync_to_qbo_purchase`, deleted in U-354,
  and still describes the mapping check as an `ItemSubCostCode` read when U-307c made it dbo-native.
