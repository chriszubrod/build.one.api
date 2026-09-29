# U-551 — DESIGN: transactional edit-and-decide with a client idempotency key

**Status:** DESIGN, awaiting `/em` Gate-1 approval. No code until approved (two-phase dispatch).
**Repo:** `build.one.api` (+ a client contract consumed by `build.one.ios` U-541/U-543b/U-547).
**Scope:** **Bills only** for v1, per Chris 2026-09-29 ("fully complete our work for bills, before we pull in
Expense transactions, and our other entities"). Expense/BillCredit/Invoice/ContractLabor are named here only
where the design must not *foreclose* them.
**Gates:** U-541, U-543b and U-547 are all blocked on this document.

---

## 1. Why this unit exists

A reviewer in the iOS Tasks module approves a bill. **Approval IS the GL coding** — the reviewer picks a
SubCostCode and that choice is the product of the approval, not a separate edit
(`feedback_approval_carries_the_coding`). So one reviewer gesture must produce two writes: the SubCostCode
onto the bill's line, and the terminal Review row. Today those two writes cannot be made to happen together,
and the client cannot tell whether they did.

Four defects, each verified against the tree at 2026-09-29 (not inherited on faith):

### D1 — The two writes are in two separate transactions. **CONFIRMED.**
`shared/database.py:76` opens every connection `autocommit=False`, and `get_connection()`
(`shared/database.py:80`) **commits on clean exit** of its own `with` block. Each repository opens its own:

- `BillLineItemRepository.update_by_id` — `entities/bill_line_item/persistence/repo.py:278`, no `conn`
  parameter, calls `UpdateBillLineItemById` (`:308`) inside its own `get_connection()`.
- `ReviewRepository.create` — `entities/review/persistence/repo.py:70`, no `conn` parameter, its own
  `get_connection()` at `:86`, calls `CreateReview` (`:90`, sproc at
  `entities/review/sql/dbo.review.sql:432`).

`BillService.apply_reviewer_decision` calls them in sequence — the line edit at
`entities/bill/business/service.py:1321`, the Review insert at `:1377`. **The line edit has already
committed** by the time the Review insert runs. Anything that fails in between (a `ValueError` from status
resolution, a connection drop, a process recycle) leaves the money document **recoded with no approval
recorded**. The reviewer's task stays in their inbox; the coding silently landed anyway.
`ExpenseService.apply_reviewer_decision` is byte-similar (`entities/expense/business/service.py:891`
then `:931`).

> The board row called this atomicity "UNVERIFIED". It is now **verified broken**, and the mechanism is the
> per-repository `get_connection()`, not a missing `BEGIN TRAN`.

### D2 — The client-side history check is not idempotency. **CONFIRMED.**
`dbo.Review` has **no uniqueness constraint** of any kind (`entities/review/sql/dbo.review.sql:6-27` —
`CK_Review_OneParent` constrains parentage, nothing constrains duplication). An email approval from Austin
and an app approval from Austin write rows with the **same `UserId`** and the same status. A client
inspecting review history cannot attribute a row to its own queued action, so it marks that action done
when only the *decision* landed and the *line edit* never did — **silently losing the coding the reviewer
typed**.

### D3 — The draft guard is a TOCTOU. (Known in `TODO.md`, re-verified; its cited lines have drifted.)
`entities/bill/business/service.py:1256` reads `bill.is_draft` from a row fetched earlier; the writes happen
at `:1321` / `:1377`. Nothing holds the state between. Completion can finalize the bill in that window and
the decision lands on a non-draft bill — exactly what the guard exists to forbid. (`TODO.md` cites
`:1162`/`:1214`; the file has grown above them. Cite the current lines, and re-verify before building —
`feedback_verify_a_design_doc_by_its_citations`.)

### D4 — Provenance: the DB cannot tell "the PM approved" from "the agent approved as the PM."
`apply_reviewer_decision` passes `user_id=reviewer_user_id` **and**
`created_by_user_id=reviewer_user_id`. `CreatedByUserId` exists to hold the actual writer. This is booked as
**U-563**; it is listed here because the new app path **fixes it for free** (§4.F) and because a design that
ignored it would re-create it.

---

## 2. The seam already exists — do not invent one

`shared/database.py:122` defines `conn_ctx(conn)`:

> *"The seam that lets a repository method either join a caller's existing transaction/connection or stand
> alone… **Canonical home for the pattern.**"*

Five repositories already take a `conn`: `entities/expense/persistence/repo.py`,
`entities/bill/persistence/repo.py`, `entities/vendor/persistence/repo.py`,
`entities/ramp_transaction_follow_up/persistence/repo.py`, `entities/ramp_chaser_digest/persistence/repo.py`.
**`review` and the two line-item repos do not.** That is the entire gap.

⚠️ `entities/bill/persistence/repo.py:27` carries a **private `_conn_ctx` copy** predating the canonical one
(used at `:164`, `:527`, `:593`). The canonical docstring says to *migrate* these, **not add a fourth
variant**. This unit uses `shared.database.conn_ctx` and does not grow a new copy. Migrating the existing
three is out of scope here — book it.

---

## 3. Where the composite lives — and why it is a new verb

There is already a decide route: `POST /advance/review/bill/{public_id}`
(`entities/review/api/router.py:142`). It is **decide-only** — `ReviewAdvanceRequest`
(`entities/review/api/schemas.py:17`) carries `comments` and nothing else. That is precisely the
"decide-only approve" the house rule forbids shipping to a reviewer.

Chris's standing instinct is right and was right for the feed: *don't add an endpoint when one endpoint plus
the caller's identity will do.* For the **read** side that settled it — the Tasks feed takes identity from the
token and needs no per-scope route. The **write** side is different, for two reasons that are facts about the
tree rather than preferences:

1. **The permission gate on `/advance` is the wrong one, and the right one already exists.** All fifteen
   submit/advance/decline routes are gated `require_module_api(<module>, "can_submit")`
   (`entities/review/api/router.py:131,146,161,188,…,393`). `can_approve` is a real column
   (`entities/role_module/sql/dbo.rolemodule.sql:17`), is resolved into the auth payload
   (`entities/auth/api/router.py:661`), and **Time Tracking already gates its approve routes on it**
   (`entities/time_entry/api/router.py:489,511,575`). So the review module gates *approval* on the
   *submitter's* permission while a sibling module does it correctly. Changing `/advance`'s gate in place is
   a breaking RBAC change to the web's live flow; a new verb gets the correct gate without touching it.
2. **An atomicity guarantee that is optional-by-payload is not a guarantee.** If `/advance` grows an
   optional coding block, a caller that omits it keeps the old non-atomic semantics, and "is this request
   atomic?" becomes a question about payload shape. The transactional command should be the only shape.

**Decision:** new verb `POST /apply/review-decision/bill/{public_id}`, gated
`require_module_api(Modules.BILLS, "can_approve")`. `/advance` and `/decline` are left untouched for the web.

⚠️ **Recorded divergence risk, not waved away:** two server paths will then advance a review. This is
acceptable only because the new path is the *only* one the app uses and the only one that is atomic. The
convergence question — retiring `/advance` for bills, or re-pointing it at the new core — is **booked, not
solved here** (§7).

---

## 4. The design

### A. Transaction boundary
The **command handler in the service** opens exactly one `get_connection()` and threads it into both writes
via `conn_ctx`:

```
with get_connection() as conn:          # one transaction, commits once, rolls back whole
    assert identity / authorization     # BEFORE any write (see F)
    re-assert IsDraft = 1 inside txn    # closes D3
    re-assert expected review state     # closes the review-state coupling gap
    line edit   -> UpdateBillLineItemById(conn)
    review insert -> CreateReview(conn, @IdempotencyKey)
```

Required signature additions (`conn: Optional[pyodbc.Connection] = None`, defaulting to today's behavior so
every existing caller is unchanged):
- `ReviewRepository.create` (`entities/review/persistence/repo.py:70`)
- `BillLineItemRepository.update_by_id` (`entities/bill_line_item/persistence/repo.py:278`)
- the service methods that call them, threaded through.

**D3's fix rides inside this boundary.** `FinalizeBillById` is already guarded on `IsDraft = 1`, so the same
predicate gates the decision: the draft check becomes a guarded UPDATE/SELECT *inside* the transaction whose
zero-row result aborts it, not a read taken 65 lines earlier.

### B. Idempotency key — on the Review row, enforced by the database
**`dbo.Review.IdempotencyKey UNIQUEIDENTIFIER NULL`** + a **filtered unique index**
(`WHERE [IdempotencyKey] IS NOT NULL`). New migration `entities/review/sql/migrations/009_*.sql`; the
`CreateReview` sproc (`entities/review/sql/dbo.review.sql:432`) gains an optional `@IdempotencyKey`.

Why here, rather than a separate `RequestIdempotency` table or a deterministic key:
- The Review row **is** the thing being deduplicated. A side table adds a second write to the transaction
  whose only job is to describe the first.
- A unique index makes **the database** the enforcement point. Application-level "check then insert" is the
  same TOCTOU shape as D3.
- `NULL`-able + *filtered* means every existing writer (the email path, web `/advance`, historical rows)
  keeps working untouched and legacy `NULL`s don't collide.
- The house precedent for deterministic keys is the time-entry digest (idempotent per `(worker, work_date)`
  via a deterministic GUID + `CountMsOutboxByEntity`). It is a good pattern where the *server* can derive the
  key. Here the unit of work is **a reviewer's queued gesture**, which only the client can identify — a
  server-derived key cannot distinguish "the same approval retried" from "a second, deliberate approval
  after a decline". So: client-supplied.

**Semantics**
- The client generates one UUID **per queued action, not per retry**, and persists it in the offline queue so
  a retry after app restart reuses it.
- On unique violation the command **re-reads the Review by that key and returns `200` with the identical
  result plus `replayed: true`** — an idempotent replay, never a 409. This is what makes the offline queue
  safe to retry blindly.
- **It covers decline.** A decline is equally a terminal decision queued offline and equally duplicable.
  Same key, same dedupe, same replay.
- **Retention:** the key lives exactly as long as its Review row. No TTL, no dedup window to tune — a
  deliberate simplification a side table would have forced us to invent.

### C. Precondition — `expected_review_public_id`
The client sends the `public_id` of the latest Review it knew about (`null` when none). Inside the
transaction the command asserts that is *still* the latest for this parent. This supplies the **review-state
coupling that `BillLineItemService.update_by_public_id` provably lacks** — it checks only its own
`row_version`, so today a queued edit can recode a document whose review moved underneath it.

### D. Error codes must be distinguishable, because recovery differs
| Code | Meaning | What the client must do |
|---|---|---|
| `200` + `replayed: true` | key already applied | mark the queued action done; do not re-send |
| `409 line_row_version_stale` | someone else edited the line's coding | refetch, re-present the coding to the reviewer |
| `409 review_state_stale` | the review moved underneath | the decision is already made — **drop** the queued action |
| `422 status_locked` | bill was completed underneath | drop the action and tell the reviewer why |
| `409 idempotency_key_conflict` | the key was already used on a DIFFERENT bill | a client key bug — do not retry |
| `422 multi_line_not_supported` | more than one line (see E) | send the reviewer to the web |

A single opaque 409 would force the client to guess, and the wrong guess either **loses the reviewer's
coding** or **re-applies it over someone else's**. This table is the contract U-547's offline queue is built
against.

### E. One line edit, not many — and it refuses loudly
v1 targets the **sole** line item. Precedent: the expense twin already raises on `len(line_items) != 1`
("would stamp one cost code over a manual split", `entities/expense/business/service.py:868-878`), and the
house convention is **one BLI per vendor invoice** (`feedback_one_bli_per_vendor_invoice`). A multi-line bill
returns `422 multi_line_not_supported`. This is a real limit, stated in the UI, not hidden behind a partial
write.

### F. Does the email path converge? **Partially — and the boundary is the point.**
- **Shared:** the transactional core — given resolved ids and an open `conn`, do the draft re-assert, the
  line edit and the Review insert.
- **NOT shared:** identity. The email path resolves an attacker-supplied `reviewer_email` to a user and calls
  `assert_may_act_as` because **an agent is acting AS the reviewer** (delegation, U-459). In the app path the
  reviewer **is** the authenticated bearer: no delegation, no `reviewer_email`, and `CreatedByUserId` = the
  authenticated caller — which **closes D4/U-563 for this path by construction**.

> ⚠️ **HARD CONSTRAINT — and the way it fails is silent.**
> `tests/test_u459_reviewer_impersonation.py:156` holds a hand-maintained `CALL_SITES` list of three
> `(module, class, method)` triples and, via `inspect.getsource()`, asserts that `assert_may_act_as(` appears
> in each body **and textually precedes** any `repo.create(` / `_repo.create(` / `update_by_` / `.create(`
> (`:165-195`).
>
> If the writes are extracted into a shared core, those writer tokens **leave the three method bodies**. The
> ordering test then finds no writer (`at == -1`), skips its assertion, and **passes vacuously**. That is
> worse than a break: a guard that stops guarding while staying green. This is the third hand-maintained
> registry to bite this module (U-524's `CALL_SITES`, `AGENTS_THAT_APPLY_REVIEWER_DECISIONS`, unit-ID
> allocation).
>
> **Therefore the build MUST:** (1) re-aim the guard at whatever body ends up holding the writes, (2)
> **mutation-prove it** — delete the identity check and watch it go RED; a passing guard is not evidence, and
> (3) add the new app command to `CALL_SITES` *or* replace the textual-source guard with a behavioral one.
> Option (3) is the right altitude and is **booked** (§7).

---

## 5. What this unit does NOT do
- No Expense/BillCredit/Invoice/ContractLabor command (Bills-only v1). The core takes a parent type so the
  arms are cheap later, but none are built or tested here.
- No migration of the three hand-rolled `_conn_ctx` copies.
- No retirement of `/advance` / `/decline`.
- No change to the email path's identity model.
- No multi-line coding.

## 6. Build decomposition (each a normal three-pass unit after Gate 1)
1. **SQL** — `Review.IdempotencyKey` + filtered unique index + `CreateReview` `@IdempotencyKey`.
   ⚠️ Prod apply is **Chris's**, never the builder's (`feedback_builders_never_mutate_prod_data`).
2. **`conn` seam** — thread `conn` through `ReviewRepository.create` and
   `BillLineItemRepository.update_by_id` via canonical `conn_ctx`; default `None` preserves every caller.
3. **The transactional core** + the U-459 guard re-aim and its mutation proof.
4. **The route** `POST /apply/review-decision/bill/{public_id}` (`can_approve`) + the §D error contract.
   → this is **U-541**.

**P0-surface: yes** — money document, RBAC, ROWVERSION concurrency, and a new write gate. Pass 3 is owed, and
the builder pin is the non-fast `composer-2.5`.

## 7. Follow-ups to book (not done here)
- Replace the `inspect.getsource()` U-459 guard with a behavioral one (registry drift, three occurrences).
- Migrate the three `_conn_ctx` copies onto `shared.database.conn_ctx`.
- Converge or retire `/advance`+`/decline` for bills.
- Re-gate the fourteen other review routes from `can_submit` to `can_approve` — a real RBAC defect, out of
  scope for a Bills v1 and **must not** be smuggled in.
- **U-563** (provenance) for the *email* path; the app path is fixed by construction here.

## 8. Risks
- **The `conn` thread is wide.** Two repos + their services, on P0-surface money paths. Default-`None`
  keeps it additive, but every existing caller must be proven unchanged by the suite, not by reading.
- **A filtered unique index on a live table.** Cheap (all-`NULL` today), but it is a prod DDL on a money
  table: Chris applies it, and the design must not assume it exists before it does.
- **`CreateReview` is called by paths this unit does not own.** An added optional parameter must be provably
  optional at every call site.
- ~~Unverified: whether the sprocs contain their own transaction control.~~ **SETTLED 2026-09-29 — see §9.**


---

## 9. Settled after first draft (2026-09-29) — the sprocs compose, and D3 is already defended

§8 listed "do the sprocs manage their own transactions?" as the build's first question. It is answered, and
the answer is **favourable on the main point and a correction to my own D3 framing.**

### Both sprocs DO manage their own transactions — and they compose correctly
- `UpdateBillLineItemById` (`entities/bill_line_item/sql/dbo.bill_line_item.sql:403`) opens
  `BEGIN TRANSACTION`, and on refusal does `COMMIT TRANSACTION` then `RAISERROR`.
- `CreateReview` (`entities/review/sql/dbo.review.sql:432`) does the same.
- **Executable `ROLLBACK` statements in either sproc: ZERO.** The only occurrence of the word is a comment in
  `CreateReview` explaining the discipline: *"COMMIT then RAISERROR, never ROLLBACK inside a sproc: pyodbc runs
  autocommit-off, so an in-proc rollback zeroes the implicit outer transaction and SQL Server raises error 266
  instead of this message."*

That discipline is exactly what makes threading one `conn` work. Under SQL Server nesting, the sproc's
`BEGIN TRANSACTION` increments `@@TRANCOUNT` and its `COMMIT` merely decrements it — only the OUTERMOST commit
durably commits, and that outermost commit is Python's. On a refusal the sproc's `COMMIT` decrements, the
`RAISERROR` surfaces to pyodbc, and `get_connection`'s `except` branch rolls back the whole outer transaction.
So **§4.A's single-transaction plan is sound as written**, and it is sound *because* the codebase already
adopted "never ROLLBACK in a sproc" for an unrelated reason. Had either sproc rolled back, the plan would have
needed rework.

⚠️ The build must NOT add a `ROLLBACK` to either sproc, and must not "tidy" the COMMIT-then-RAISERROR shape
into one. It looks wrong and is load-bearing.

### CORRECTION to D3 — the draft guard is already defended in depth
My D3 said the draft check is a TOCTOU that this unit's transaction boundary would close. The service-level
TOCTOU is real (the read at `entities/bill/business/service.py:1256`, the writes at `:1321`/`:1377`), but I
under-read the SQL layer: **both sprocs already re-check the parent's terminal state INSIDE their own writing
transaction.**
- `CreateReview` carries a U-454 guard that takes `UPDLOCK, HOLDLOCK` on the parent **unconditionally** and
  refuses conditionally, with a comment making the same RCSI-snapshot argument this design made independently:
  a completion committing between the service's read and the INSERT would otherwise land a review on a
  completed parent.
- `UpdateBillLineItemById` refuses with `STATUS_LOCKED: the line items of a completed Bill cannot be changed.`

So D3 is **not** an open hole that must be closed to ship — it is defence-in-depth that already exists at the
layer that matters. The transaction boundary still improves it (one atomic unit rather than two independently
guarded writes), but **D3 must not be presented to the builder as a defect to fix**, or it will "add" a guard
that is already there and possibly weaken the existing one. Revised ranking of what this unit actually closes:

1. **D1 atomicity across the two writes** — genuinely open, the real defect, unchanged.
2. **D2 idempotency** — genuinely open; `dbo.Review` still has no uniqueness constraint.
3. **Review-state coupling** (`expected_review_public_id`) — genuinely open; the line-item sproc's guard covers
   *completed* parents, not *a review that moved underneath the edit*.
4. **D3 draft state** — already defended at the SQL layer; this unit consolidates rather than fixes it.
5. **D4 provenance** — closed by construction for the app path only.


---

## 10. CORRECTION to §4.D's error table (2026-09-29) — I got a status code wrong

§4.D originally specified `409 parent_not_draft` for "the bill was completed underneath the reviewer." **That
is wrong, and it would have shipped a client-side infinite loop.** Corrected above; recorded here because the
mistake is instructive and because U-547 is built against this table.

`entities/review/api/router.py:103-113` carries the frozen rule verbatim, established by U-454:

> 422 `status_locked` … **NOT the 409 below (U-454)** … installed iOS routes 409 to its reload-and-retry
> CONFLICT path, so a completed parent would make the client spin on a refusal that will never change.
> **409 means "try again"; this means "never again".**

So the code must be **422 `status_locked`**, raised through `raise_workflow_error(...)` like every sibling
route, and U-547's offline decision queue must treat it as terminal-drop, never retry. I wrote the table from
the design's own logic without checking the convention in the file the route lives in — the 409/422 split here
is not a style choice, it is the contract that decides whether a queued action retries forever.

**Second correction, same cause:** the table listed bare codes without saying WHICH FIELD carries them.
`shared/api/errors.py:1-4` defines the body as `{"detail": <human text>, "error_code": <machine code>}`, and
iOS decodes `error_code`. The first build put machine codes in `detail` and left `error_code` null, inverting
it. Every code in the table above travels in `error_code`, with a human sentence in `detail`, raised via
`ApiError` with the code appended to the `ErrorCode` enum — never as a bare `HTTPException`, and never as a
scattered string literal.

**Third, added in the same round:** `idempotency_key_conflict` (409) now exists because
`review_state_stale` was doing double duty. A key replayed against a DIFFERENT bill is a client key bug, not a
moved review, and the two want opposite client behaviour — drop-and-report versus drop-silently. Its
non-concurrent and unique-violation-recovery paths must both return it; the recovery path originally re-raised a
raw `DatabaseConstraintError`, which the router does not map, so it escaped as a **500**.
