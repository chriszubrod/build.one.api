# Bill attachment sprocs + list scoping → /em hand-off

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
  SQL:     ALL FOUR FILES ALREADY APPLIED to prod by Chris, 2026-09-09.
  Left:    ONE prod action — the API container deploy. That is what you are
           approving.

--------------------------------------------------------------------------
⚠ CURRENT PROD STATE — TWO ENDPOINTS ARE RETURNING EMPTY RIGHT NOW
--------------------------------------------------------------------------
  The SQL is in; the container still runs the OLD code. The old repo layer
  calls ReadBillLineItems with params={}, so the new @ActorUserId /
  @ActorIsSystemAdmin bind to their NULL defaults, and
  dbo.UserCanAccessBill(NULL, NULL, ...) returns 0 for every row.

  Until the container ships:
      GET /api/v1/get/bill_line_items              -> empty list
      GET /api/v1/get/bill-line-item-attachments   -> empty list
  for EVERY caller, system admins included.

  This is the fail-closed direction and it is by design — the leak these
  commits close is shut right now, and no data is at risk. It is still a live
  regression on two list surfaces, so the deploy is time-sensitive rather than
  routine. Confirm whether build.one.web or the iOS app reads either endpoint
  before deciding how fast to move.

  UNAFFECTED: every other bill line-item read. by-bill, by-project, by-id and
  by-public-id were always service-gated via assert_can_access_*; none of their
  sprocs changed signature.

WHY THESE TWO FIXES
  1. CreateBillLineItemAttachment (and the Expense + Invoice siblings) each
     existed TWICE — the good body carrying @CreatedByUserId in
     scripts/migrations/gap2_adjacent_threading.sql, a stale 2-param duplicate
     in the entity base file. Base files are CREATE OR ALTER and get re-run
     routinely, so whichever ran last won. A base re-run would have reverted
     the threading; every *LineItemAttachmentRepository.create sends
     CreatedByUserId, so the next call fails in the driver with "too many
     arguments". BillService.create and ExpenseService.create both roll the
     PARENT back when the attachment link fails, so that break took out every
     Bill create carrying a PDF and every Expense create carrying a receipt.
     Same class dbo.bill.sql fixed for CreateBill on 2026-07-12 — that one was
     found by incident, these three by review before they fired.

  2. GET /get/bill_line_items and GET /get/bill-line-item-attachments returned
     EVERY row in the database to any caller holding module read — amounts,
     descriptions, rates, project ids, across all companies. Both services
     passed no actor and neither sproc carried a UserCanAccessBill predicate.
     Every sibling read was already gated; these two list paths were missed.

--------------------------------------------------------------------------
STEP 1 — DEPLOY THE API IMAGE   (the only remaining prod action)
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
      BEFORE deploy : data == []        (0 rows — the current broken state)
      AFTER  deploy : data is NON-EMPTY (admin bypass restored)

    GET /api/v1/get/bill-line-item-attachments
      BEFORE deploy : data == []
      AFTER  deploy : data is NON-EMPTY

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

  A failure here looks like a driver "too many arguments" error AND a missing
  parent row, because both services roll the parent back on link failure.

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
