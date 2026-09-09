# Bill attachment sprocs + list scoping → /em hand-off

> **STATUS 2026-09-09: COMPLETE.** SQL applied by Chris; container deployed by
> Claude under an explicit one-time override of
> `feedback_builders_never_mutate_prod_data.md`. Image
> `sha256:da2321e4…` (`:latest` == `:998f9504`), previous
> `sha256:6d47e17e…`. Admin sentinel green on both endpoints. The **non-admin**
> half of the sentinel is still UNVERIFIED — see OPEN below. Retained as the
> deploy record.

## OPEN — the one check still owed

The sentinel was run as the Claude Agent (User 33, `IsSystemAdmin=1`), which
proves the actor now reaches the sproc and the admin bypass works. It does NOT
prove non-admins get *filtered* — that is the half that shows the leak is
actually closed rather than merely that admins still see everything.

Run as a non-admin with `UserProject` access to exactly one project:

    GET /api/v1/get/bill_line_items
      EXPECT: only that project's lines.
      0 rows       -> the actor is not reaching the sproc.
      23,949 rows  -> the predicate is not applied; the leak is STILL OPEN.

A read-only `sys.parameters` confirmation of the deployed sproc signatures was
attempted and blocked by the sandbox classifier; it was not worked around.

Copy the block below into an `/em` session.

```text
============ BEGIN /em HAND-OFF: attachment sprocs + list scoping ============

UNIT
  Two fixes from the 2026-09-08 Bill-entity review, plus a docs pass.
  Repo:    build.one.api
  Commits: 4de54010  Single-source the three line-item attachment Create sprocs
           5e9d3ef7  Scope the two unscoped bill line-item list reads
           a0c1026c  Correct the documented run_sql.py invocation
  Status:  pushed to origin/master. Suite 3367 green, and each commit verified
           green independently in a detached worktree.
  SQL:     ALL FOUR FILES APPLIED to prod by Chris, 2026-09-09.
  Image:   DEPLOYED 2026-09-09 13:42 UTC. sha256:da2321e4...
           (:latest == :998f9504, verified same digest; previous 6d47e17e...).
           Container log shows a genuine restart, not a cache hit: gunicorn
           master down 13:41:53, NEW master up 13:42:21, startup 13:42:31.
  Left:    the non-admin sentinel (see OPEN, above the block).

--------------------------------------------------------------------------
THE INTERMEDIATE STATE — RESOLVED 2026-09-09 13:42 UTC

  Recorded because it will recur on any deploy that adds actor params to a
  sproc ahead of the code that passes them.

  Between the SQL apply and the container deploy, the old repo layer called
  ReadBillLineItems with params={}, so the new @ActorUserId /
  @ActorIsSystemAdmin bound to their NULL defaults and
  dbo.UserCanAccessBill(NULL, NULL, ...) returned 0 for every row. For that
  window BOTH list endpoints returned an empty list to EVERY caller, system
  admins included:
      GET /api/v1/get/bill_line_items
      GET /api/v1/get/bill-line-item-attachments

  That is the fail-closed direction and was by design — the leak was shut for
  the whole window and no data was at risk — but it was a live regression on
  two list surfaces, which is why the deploy was time-sensitive rather than
  routine. Both are serving again (23,949 / 3,937 rows to an admin).

  UNAFFECTED throughout: every other bill line-item read. by-bill, by-project,
  by-id and by-public-id were always service-gated via assert_can_access_*;
  none of their sprocs changed signature.

WHY THESE TWO FIXES
  1. CreateBillLineItemAttachment (and the Expense + Invoice siblings) each
     existed TWICE — the good body carrying @CreatedByUserId in
     scripts/migrations/gap2_adjacent_threading.sql, a stale 2-param duplicate
     in the entity base file. Base files are CREATE OR ALTER and get re-run
     routinely, so whichever ran last won. A base re-run would have reverted
     the threading; every *LineItemAttachmentRepository.create sends
     CreatedByUserId, so the next call fails in the driver with "too many
     arguments". That break took out every Bill create carrying a PDF and every
     Expense create carrying a receipt.
     CORRECTION (U-426, 2026-09-09): an earlier version of this note said both
     services "roll the PARENT back" on link failure. ExpenseService.create does
     — it deletes the placeholder line item FIRST, with a comment explaining that
     a bare expense delete would 547. BillService.create does NOT: it calls
     repo.delete_by_id(bill.id) while the placeholder BillLineItem still holds
     FK_BillLineItem_Bill (no CASCADE, and DeleteBillById touches only the Bill
     row), so the rollback itself trips SQL 547, is swallowed by the nested
     except, and a Bill + orphan line item persist with NO attachment — the exact
     invariant the block exists to enforce. Worse, every retry with the same
     (vendor, bill_number, bill_date) then trips the duplicate check, so the bill
     cannot be re-created via the API until someone hand-deletes rows. The
     severity claim above is unaffected (creates fail either way); the described
     cleanup does not happen. Booked as its own finding.
     Same class dbo.bill.sql fixed for CreateBill on 2026-07-12 — that one was
     found by incident, these three by review before they fired.

  2. GET /get/bill_line_items and GET /get/bill-line-item-attachments returned
     EVERY row in the database to any caller holding module read — amounts,
     descriptions, rates, project ids, across all companies. Both services
     passed no actor and neither sproc carried a UserCanAccessBill predicate.
     Every sibling read was already gated; these two list paths were missed.

--------------------------------------------------------------------------
STEP 1 — DEPLOY THE API IMAGE   [DONE 2026-09-09 13:42 UTC]
--------------------------------------------------------------------------
  Standard flow, DEPLOY.md. Tag MUST be :latest.

  Use stop + start, NOT `az webapp restart` — restart can relaunch the CACHED
  image and still report success (it did on the 2026-09-08 U-410 deploy:
  correct :latest tag, digest flipped in ACR, 200 in 28s, old code still
  serving). A sub-30s "up" is a cache-hit tell, not a fast deploy.

    az webapp stop  --name <webapp-name> --resource-group <resource-group>
    az webapp start --name <webapp-name> --resource-group <resource-group>

  Also confirm :latest and the short-sha tag resolve to the SAME digest.

  Code shipping: BillLineItemService.read_all and
  BillLineItemAttachmentService.read_all now forward the request ContextVars;
  both repos bind the admin flag as a SQL BIT rather than a Python bool.
  BillLineItemAttachmentService.read_by_id / read_by_public_id are now gated
  too (they had the same hole on the single-row endpoint) — an inaccessible
  link raises EntityNotAccessibleError -> 404, never 403, so the URL does not
  confirm the row exists.

--------------------------------------------------------------------------
STEP 2 — VERIFY WITH A BEHAVIORAL SENTINEL, NOT A 200
--------------------------------------------------------------------------
  The old image returns 200 on these routes too. Use the row COUNT — it is
  unambiguous here because the pre-deploy state is empty for everyone.

  Read-only, as Chris (User 17, IsSystemAdmin=1):

    GET /api/v1/get/bill_line_items
      BEFORE deploy : data == []        (0 rows — the broken intermediate state)
      AFTER  deploy : data is NON-EMPTY (admin bypass restored)
      MEASURED 2026-09-09: 23,949 rows. PASS.

    GET /api/v1/get/bill-line-item-attachments
      BEFORE deploy : data == []
      AFTER  deploy : data is NON-EMPTY
      MEASURED 2026-09-09: 3,937 rows. PASS.

  NOTE on why that is sufficient for "is the new code serving": with the SQL
  already applied, the OLD code (which passes no actor) binds @ActorUserId /
  @ActorIsSystemAdmin to their NULL defaults and returns ZERO rows. A non-empty
  admin result is therefore only reachable from the new code.

  If either is still empty after the deploy, the container did NOT pull —
  that is the cache-hit symptom, not a SQL problem. Re-check the digest.

  Then the half that actually matters, as a NON-admin with UserProject access
  to exactly one project:

    GET /api/v1/get/bill_line_items
      EXPECT: only that project's lines. Not zero, and not everything.

  Zero means the actor is not reaching the sproc. Everything means the
  predicate is not being applied — that would be the leak still open.

--------------------------------------------------------------------------
STEP 3 — SMOKE THE WRITE PATHS (one each, ~2 minutes)
--------------------------------------------------------------------------
  The attachment Create sprocs are unchanged in BEHAVIOR (prod was already
  running the good 3-param gap2 bodies; the applies only moved the canonical
  home). These confirm that:

    * Create one Bill with a PDF attachment      -> succeeds, bill persists
    * Create one Expense with a receipt          -> succeeds, expense persists

  A failure here looks like a driver "too many arguments" error. For EXPENSE it
  also leaves no parent row (ExpenseService.create deletes the placeholder line
  item, then the expense). For BILL the parent row SURVIVES along with an orphan
  line item and no attachment — BillService.create's rollback 547s and is
  swallowed (see the CORRECTION above). So a Bill smoke failure looks like a
  phantom draft you must delete by hand before retrying, not a clean no-op.

--------------------------------------------------------------------------
DRIFT RISK — CHECKED AND REFUTED, NO ACTION NEEDED
--------------------------------------------------------------------------
  Those four base files are CREATE OR ALTER, so they re-applied 33 sprocs, not
  just the 5 this unit changed. That is the exact failure class this unit is
  about, so it was worth checking whether any of the 28 riders had a newer
  definition elsewhere that the apply would have silently reverted.

  Result: CLEAN. All 33 have exactly ONE definition in the repo — no competing
  bodies in scripts/migrations/ or anywhere else. The applies could not have
  reverted anything. The 28 riders were rewritten with identical text.

  (Checked statically across every .sql in the repo. Worth re-running before
  NOTE on refutation reliability: U-426 overturned a
  SEPARATE refutation from the same session — I had dismissed a blob-collision
  finding after sampling two blob paths, finding them UUID-based, and
  generalising; two collidable paths existed that I never sampled. Treat any
  "refuted" call in these notes as provisional unless it names the exhaustive
  check that backs it. This one does: all 33 sprocs, every .sql file.
  any future base-file apply — normalize CREATE OR ALTER -> CREATE first if
  comparing against sys.sql_modules, or all of them falsely diff.)

--------------------------------------------------------------------------
ROLLBACK
--------------------------------------------------------------------------
  Redeploy the previous image (stop + start). The SQL needs no rollback and
  should NOT be rolled back: the new params are additive with = NULL defaults,
  so the OLD code runs against the new sprocs — that is precisely the state
  prod is in right now. Rolling the image back returns you to two empty list
  endpoints, not to the leak.

  To genuinely revert the scoping you would have to drop the predicate from
  ReadBillLineItems / ReadBillLineItemAttachments, which re-opens the leak.
  Do not, without Chris saying so explicitly.

--------------------------------------------------------------------------
NOT IN THIS UNIT
--------------------------------------------------------------------------
  * 8 further findings from the same review are unfixed and unbooked, incl.
    stale Excel row indices in the outbox insert path (rows land in the wrong
    cost-code block), BillLineItem.quantity typed int against DECIMAL(18,4)
    (blocks completion of fractional-qty lines), and apply_reviewer_decision
    writing the SCC to the NEWEST line rather than the summary line
    (ReadBillLineItemsByBillId is ORDER BY CreatedDatetime DESC).
  * The U-357 lifecycle work is uncommitted in Chris's working tree and was
    deliberately left alone — it is gated on §9's 24 open decisions.

============= END /em HAND-OFF: attachment sprocs + list scoping =============
```
