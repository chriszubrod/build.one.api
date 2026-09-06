# Design: Edit a Bill mid-review, then resubmit with a threaded notification

**Status:** DESIGN APPROVED, BOOKED — nothing in this doc is built yet. **Unit id: U-371**
(program), phased as **U-371a–U-371e** per §6's sequencing — booked 2026-09-06 on
`build.one.team/BOARD.md` (In flight + Ready/queued). Chris approved the design and settled
all four §7 decisions before booking. Next step: Chris opens Gate 1 on a phase (starting
with U-371a) to begin building. Two related out-of-scope findings were spun off as their own
units rather than folded in: **U-372** (iOS Draft-toggle bypasses the completion pipeline)
and **U-373** (audit `RoleModule.can_submit` vs `can_update` grants) — both already running
as separate Chris-started sessions outside this program.
**Origin:** Chris, 2026-09-06 conversation.

## 1. Problem

An AP user needs to edit a Bill that has already been submitted for review — header
fields, `BillLineItem` rows, and/or the PDF attachment — then resubmit it. The
resubmit must produce a reviewer notification that is **associated with** the
notification already drafted (and possibly already sent) for the first submission,
not a disconnected new email.

## 2. Current state (verified against code, not memory)

- **Editing already works mechanically.** `BillService.update_by_public_id`
  (`entities/bill/business/service.py:1002`) and
  `BillLineItemService.update_by_public_id` (`entities/bill_line_item/business/service.py:155`)
  are plain row-version-guarded read-modify-write with **no review-status guard**.
  `BillEdit.tsx`'s only lock is `is_draft`.
- **There is no resubmit transition.** `ReviewService.build_submit_payload`
  (`entities/review/business/service.py:249`) allows `/submit` only when there is no
  current review row or the current one is Declined; it 409s
  ("a review is already in progress") for Submitted/In Review, and refuses
  ("already approved") once Approved. `ReviewTimeline.tsx`'s `canSubmit` mirrors this
  — no button renders in the Submitted/In Review state at all. The *only* existing
  "Resubmit" affordance is the Declined-state relabeling of the same Submit button.
- **The notification service is v2 (purpose-built new email), not the v1
  forward-of-original design in stale memory** (`project_review_notifications.md`
  describes the superseded design). `ReviewNotificationService._do_enqueue`
  (`entities/review/business/notification_service.py:72`) always builds a fresh email
  with the bill's own re-attached PDF and enqueues `MsOutboxService.enqueue_send_mail`
  with `forward_message_id=None`. It fires whenever `ReviewService.create()` writes a
  row landing at the review ladder's first status — true first submit **and**
  decline→resubmit both qualify identically today.
- **Attachment replace has no atomic primitive.** `BillLineItemAttachmentService.create()`
  dedupes by `bill_line_item_id` and silently returns the existing link if one exists,
  so the only replace sequence today is delete-link → upload → create-link. No
  `Update` sproc/route exists despite a dead `BillLineItemAttachmentUpdate` schema.
- **`Bill.TotalAmount` is not server-derived.** No trigger, no recompute in either
  service. `BillEdit.tsx`'s `saveAll()` computes the sum client-side and PATCHes it.
- **MS Graph mail client already has the reply primitives** (`create_reply_draft`,
  `reply_to_message` in `integrations/ms/mail/external/client.py`) and the proven
  2-step POST+PATCH pattern (`create_forward_draft`) that a reply-threading path would
  reuse. Verified against Microsoft's docs: `createReplyAll` takes **no recipient
  parameters** at creation (recipients auto-populate from the original message;
  correction requires a follow-up PATCH), and reply drafts do **not** inherit the
  original's attachments (only Forward does).

## 3. Method

This doc went through a 10-dimension parallel hunt (review state machine,
notification content, outbox lifecycle, Graph mail client contract, attachment
chain, web UI flow, concurrency/multi-user, security/RBAC, data integrity/money,
SQL conventions) over both `build.one.api` and `build.one.web`, producing 154 raw
findings. Findings were semantically clustered (76 clusters), each adversarially
verified by two independent skeptics with a third tiebreaker on splits (**47
confirmed, 29 refuted, 0 unresolved**), then critiqued by three judge panels
(correctness/failure-modes, production-ops/safety, product/reviewer-experience) and
one completeness critic checking for unexamined subsystems. All findings below carry
file:line citations from that process; nothing here is asserted from memory.

## 4. Settled decisions — as originally proposed, then as amended by the review

### D1. New "resubmit" transition

**As proposed:** `ReviewService.build_resubmit_payload`, valid when a current review
row exists, is not declined, and `Bill.is_draft` is True. New
`POST /api/v1/resubmit/review/bill/{public_id}`. Restarts the ladder at the first
status; auto-advances to In Review as today.

**Judge scores:** correctness 3/5, prod-ops 3/5, product 3/5 — all "keep with
amendment." **Amendments required:**

- **Include Declined**, not just Submitted/In Review/Approved. Today's *only*
  existing "Resubmit" button is the Declined-state relabel of `/submit`
  (`ReviewTimeline.tsx:241`), and it does **not** flush `BillEdit`'s autosave or set
  `is_resubmit`. Excluding Declined from D1 leaves two Resubmit affordances on the
  same page with different (and for the old one, broken) semantics — **P1, confirmed**.
  Make `/resubmit` the single transition for *any* existing current row (including
  Declined) on a draft Bill, and retire (or delegate) the timeline's own submit
  dialog for `parentType='bill'`.
- **Make the transition atomic in SQL**, not check-then-insert in Python.
  `build_*_payload` reads current in one connection; `CreateReview` inserts in
  another, with no lock or expected-current predicate — two overlapping
  `/resubmit` calls (double-click, two tabs, agent + human) both pass and each
  enqueues its own notification — **P2, confirmed**. Add a
  `CreateReviewIfCurrentStatusIn` sproc that reads current
  `WITH (UPDLOCK, HOLDLOCK)` inside the transaction, re-validates the allowed-from
  set, inserts only if valid, and returns empty on conflict (validate-first /
  always-COMMIT, per this repo's stored-procedure convention). Today's plain
  `/submit` has the identical hole and should get the same fix.
- **Actually enforce the is_draft guard**, and do it consistently. Today *only*
  `apply_reviewer_decision` checks `is_draft`; `/submit`, the line/header update
  services, and the planned D3 swap do not — a completed bill already pushed to
  QBO/SharePoint/Box can still have its header, lines, or PDF evidence changed with
  no review row and no drift signal — **P2, confirmed**. Fold this into the same
  atomic sproc so the guard can't be TOCTOU'd against `_run_complete_bill`'s
  background `is_draft` flip.
- **No idempotent guard against duplicate resubmits.** Add a lightweight check (was
  a Submitted/In Review row created by the same user in the last N minutes with no
  Bill/line/attachment change since?) or a per-bill rate limit — a scripted or
  looped caller can otherwise flood the shared `invoice@` mailbox and the Review
  timeline with duplicate rows (**P3, confirmed**).

### D2. Threaded notification on resubmit

**As proposed:** look up prior `send_mail` outbox rows for the bill; branch on
whether the prior Graph message is delivered (reply-all it), an unsent draft
(replace it), still pending/failed (update in place), or nothing found (today's
fresh email).

**Judge scores: correctness 2/5, prod-ops 2/5 — "change," not "keep."** This is the
part of the design that needed the most rework. Confirmed defects, roughly in the
order they'd bite:

- **No durable anchor exists to key branch (a) on — P1, confirmed.** The only id the
  worker stamps (`worker.py:698`, `payload["graph_message_id"] = message_id`) is the
  Drafts-folder-relative id of the *draft as created*. Sending it from Outlook moves
  it to Sent Items under a **different** id — so a `GET` on the stored id 404s for
  exactly the "delivered" case branch (a) targets, and every miss silently
  degrades to a disconnected new email, the outcome this feature exists to prevent.
  Fix: stamp `conversation_id` and `internet_message_id` (`_format_message` already
  returns both, `client.py:69-70`) alongside `graph_message_id` at write time; on a
  404 of the stored id, resolve the sent copy by `conversationId`/`internetMessageId`
  instead.
- **Branch selection must move from enqueue time to drain time — P1, confirmed.**
  As designed, the live Graph `get_message` call (and, for branch (b), the delete)
  runs inside the notification service on the HTTP request thread. A Graph outage
  silently no-ops (Submitted row written, no notification, no outbox row, no error
  surfaced); a slow Graph call blocks the user's `/resubmit` click for up to the
  retry budget; and the `is_draft` state verified at enqueue time can flip (human
  sends it from Outlook) before the worker actually acts 5–60s later. The
  notification service must stay Graph-free (snapshot recipients/subject/body/PDF
  bytes only) and enqueue one `send_mail` row whose payload carries the resubmit
  intent; **all** Graph calls and the branch decision move into
  `MsOutboxWorker._handle_send_mail`, where they're naturally retryable and
  dead-letter-able like every other outbox write.
- **A 404 at drain time must fall through to a fresh draft, not dead-letter — P1,
  confirmed.** The worker's existing convention
  (`_raise_if_external_error` → non-retryable `MsNotFoundError` → immediate
  dead-letter) is correct for ordinary writes but wrong here: if the anchor message
  is gone by drain time (sent-then-moved, deleted, folder rule), dead-lettering means
  the reviewer is never notified while the Review already shows In Review. The new
  branch must catch 404 on its own verify-GET/DELETE explicitly and fall through to
  branch (c) before reaching the generic error mapper.
- **Two version-discrimination gaps, both P1, both closable in the same fix.**
  (1) `apply_reviewer_decision` (`entities/bill/business/service.py:1186`) and the
  agent's `find_bill_by_conversation_id` lookup compare nothing — no reply timestamp,
  no in-reply-to id, no submission generation — against the latest Submitted row.
  Because D4 (below) deliberately keeps replies in the same Graph conversation, a
  PM replying "approved" to the *first* notification after AP has resubmitted
  changed amounts/lines/PDF gets that approval stamped onto the **revised** bill.
  (2) The reviewer-reply lookup resolves purely on `ConversationId` /
  `BillNumber`+project fuzzy-match — it has no way to tell "reply to round 1" from
  "reply to round 2" even in principle. Fix: read the latest first-status Review row
  as the version anchor; compare the reply's timestamp (or its Graph parent/
  in-reply-to id vs. the outbox row's stamped `graph_message_id`) against it; if the
  reply predates the current Submitted row, write it `Comments`-only /
  `superseded_submission` and route it to `flagged_needs_review` instead of Approved.
  This closes both the correctness gap and the "first reviewer's decision wins" vs.
  "latest reply wins" ambiguity already baked into the bill_specialist prompt.
- **Branch (b) discards human-in-the-loop edits and races a human hitting Send —
  P2, confirmed.** "Still a draft" does not mean "nobody has looked at it" — the
  product model is a human editing the draft in Outlook before sending. Delete-and-
  recreate can lose their edits, or race their Send between the is_draft check and
  the DELETE (which is itself a soft move to Deleted Items, so the stale draft
  remains sendable). Prefer PATCHing the existing draft in place
  (`update_draft`, which already replaces subject/body/recipients) plus adding the
  new PDF, over delete+recreate; if delete-then-create is kept, re-verify state
  immediately before deleting and fall back to reply-all if it was sent in the
  interim.
- **Branch (d) (update the still-pending outbox row) needs real care, not a copy of
  the upload-coalesce pattern — P2, confirmed (two findings, same root).** A row
  the worker has already claimed is `in_progress` — invisible to both the pending
  and completed reads — so a resubmit landing mid-drain sees "nothing" and creates a
  second, disconnected draft while the stale first one lands seconds later anyway.
  And a pending row IS visible but the RowVersion can advance between read and
  update, so `UpdateMsOutboxPayload` matches zero rows and returns `None` — this
  must be treated as "lost the race, re-run branch selection," never as "nothing to
  do." The in-place update must also rebuild the **whole** payload (fresh PDF bytes
  if the attachment changed, fresh recipients, fresh body) — copying only
  subject/body and leaving a stale attachment field is a silent partial fix.
  Given the scheduler drains the MS outbox **one row per tick** behind whatever
  SharePoint/Excel rows a recent bill-completion enqueued (verified — not the 30s-
  per-row-type cadence the docs currently claim), branch (d) will be the **common**
  case for a quick fix-and-resubmit, not an edge case.
- **Multi-step Graph write sequence needs local idempotency — P2, confirmed.** The
  create→PATCH→attach→refetch sequence (mirroring `create_forward_draft`) has no
  checkpoint; a client-side timeout after the create-POST succeeded server-side
  causes a full retry that creates a second draft (Graph's `x-ms-client-request-id`
  is a diagnostics header, not an idempotency key — verified, it is not honored as
  one for message-creation endpoints). Checkpoint the created draft id into the
  outbox row's payload immediately (same pattern the resumable-upload path already
  uses) and resume from it on retry instead of recreating.
- **Attachment-add failure must be fatal, not a warning — P2, confirmed.**
  `create_draft`'s existing pattern downgrades a failed attachment POST to
  `logger.warning` and returns success; mirrored into the new reply path, a
  transient or size-related attachment failure ships a "Bill revised — PDF
  replaced" email with **no PDF**, silently, while `Review.Comments` claims
  otherwise. Roll back the draft and let the worker's normal retry/dead-letter
  machinery handle it.
- **Recipient PATCH must set To/Cc/Bcc as explicit lists, never `or None` — P2,
  confirmed.** `createReplyAll` pre-populates To/Cc from the **original** message
  and never populates Bcc. If the fresh-recipient PATCH follows this codebase's
  existing `cc_addresses or None` convention, an empty fresh Cc list silently keeps
  a removed Owner or a persona test account on the thread from the original
  message, and the `invoice@` archive Bcc never gets added. Always pass real
  (possibly empty) lists so the PATCH fully replaces Graph's pre-population.
- **Branch ordering: check pending/failed (d) before completed (a), not after —
  P3, confirmed.** The first notification's outbox row stays `done` forever, so
  branch (a)'s precondition is permanently true post-delivery; a second resubmit
  landing while the first resubmit's reply row is still pending would otherwise spawn
  a second reply-all instead of coalescing into the pending one.
- **Mark superseded rows so the dead-letter retry script can't resurrect them —
  P3, confirmed.** `scripts/retry_ms_outbox_dead_letters.py` doesn't know a bill was
  resubmitted; running it after a resubmit can drain a dead-lettered first-submit
  row with stale content as a *second* draft. Stamp `superseded_by` on prior rows at
  resubmit time and have both the retry script and the worker skip them.
- **Fall back to a fresh email when the recipient set changed — P3, confirmed.**
  If a line's project (and therefore its PM/Owner set) changed mid-review, reply-all
  on the old thread sends a subject and quoted history naming the old project to the
  new reviewer while silently dropping the old one. Compare the freshly-resolved
  To/Cc against the prior notification's before choosing branch (a); diverge → fresh
  email.
- **All four branches must share one body builder, parameterized on
  `is_resubmit`** — reusing `_build_html_body` verbatim for branches (b)/(c)/(d)
  currently says "A new bill has been submitted" and stamps the *original* bill's
  creation date, which misreports a resubmit and contradicts the reply-all
  preamble sitting above it in branch (a). Product panel's recommendation, adopted:
  the resubmit body's first line must state the delta (old amount → new amount,
  "PDF replaced," etc.) — the PM's phone-glance cue, since D4 keeps the stale
  amount in the subject.

### D3. Attachment swap (BillLineItemAttachment → Attachment)

**As proposed:** a real `UPDATE` sproc for `BillLineItemAttachment.AttachmentId`
(one atomic statement), replacing delete-link+create-link; leave the superseded
`Attachment` row and blob untouched; record the swap in the resubmit's
`Review.Comments`.

**Judge scores:** correctness 3/5, prod-ops 2/5 ("change" — sequencing, not the
primitive itself), product 4/5. **Amendments required:**

- **The BLIA base SQL file is stale vs. prod — P1, confirmed, and this blocks
  everything else in D3.** `dbo.bill_line_item_attachment.sql`'s `Create` sproc is
  the pre-gap2 2-parameter body; prod's live sproc already carries
  `@CreatedByUserId`. Appending the new `Update` sproc to this file and applying it
  the normal way (`run_sql.py` on the whole file) **re-applies the stale Create**,
  and every subsequent attachment link 500s in prod until someone notices and
  re-applies the gap2 migration — this repo's documented U-037-class incident
  shape. **This must ship as its own SQL-only prerequisite unit**, following the
  existing base==live reconciliation playbook (`reference_base_vs_live_sproc_diff.md`):
  diff `sys.parameters` against the live sproc, reconcile the base file, stub the
  superseded gap2 migration entry to a pointer, add the entity to the single-source
  ledger, *then* add the Update sproc in a follow-up change.
- **Resolve through `BillLineItemService` before mutating — P2, confirmed.** BLIA
  reads/deletes resolve by public id with no `UserProject`/bill-access check
  today — this is already a live hole in the web's existing replace flow, and a
  swap endpoint that mutates by BLIA public id alone would let any `can_update`
  attachments user repoint the PDF on a bill outside their assigned projects. Route
  through the bill first and reuse `assert_can_access_bill`.
- **Enforce PDF-ness on bytes, not the client-declared content-type string —
  P2, confirmed.** Today's upload route only compacts as PDF when the client
  *says* `application/pdf`; nothing sniffs magic bytes. A swap check that trusts
  the same string is both too strict (rejects a legitimately-PDF upload reported as
  `application/octet-stream` — the exact bug the email-attachment bridge already
  works around) and too weak (accepts relabeled non-PDF bytes, which then get
  emailed from the trusted `invoice@` mailbox and pushed to SharePoint/Box/QBO on
  completion).
- **Decide and document the swap route's RBAC module explicitly** — sibling BLIA
  routes gate on `ATTACHMENTS`, the bill-edit surface gates on `BILLS`; leaving it
  unstated (as originally scoped) reproduces this repo's own documented, still-open
  web↔API permission-parity gap (`build.one.web/TODO.md:407`) on a brand-new route.
- Keep: leave the superseded `Attachment` row + blob untouched (matches this
  repo's existing precedent in `BillService.delete_by_public_id`); record the
  old→new attachment id swap in `Review.Comments` (free-text, no schema cost).

### D4. Subject/threading policy

**As proposed:** keep Graph's auto `"RE: [Review] ..."` subject (stale amount
tolerated) to preserve conversation threading.

**Judge scores:** correctness 3/5, prod-ops 4/5 ("keep, this part is right"),
product 3/5. **Amendment required, and it turns out to be load-bearing for D2, not
cosmetic:** persist the notification's `conversationId` **and** `internetMessageId`
on the outbox row (and, so the reviewer-reply lookup can use it, on the Review row)
at stamp time. Today nothing stores either — v2 notifications are new emails with
their own conversation, unrelated to `Bill.SourceEmailMessageId`, so the existing
`FindBillForReviewerReply` fuzzy fallback (exact `BillNumber` + project hint) is the
*only* thing that resolves a reply, and it breaks the moment a mid-review edit
corrects the bill number. This is the same durable-anchor gap D2(a) needs — fixing
it once, at stamp time, unblocks both.

## 5. New findings from the completeness pass (not part of D1–D4, need a decision)

- **iOS is a fourth write path onto exactly the rows this feature is trying to
  protect, and it has no concept of Review at all.** `BillEndpoints.swift`'s
  `UpdateBillEndpoint` PUTs straight to the same generic
  `PUT /api/v1/update/bill/{publicId}` the web app uses, gated by the same `BILLS
  can_update` permission `/resubmit` would use — and `BillDetailView.swift`
  exposes a raw `Toggle("Draft", isOn: $vm.editIsDraft)` that flips `is_draft`
  directly, bypassing `_run_complete_bill`'s pipeline entirely. `BillLineItemService`
  additionally queues edits offline and applies them minutes-to-hours later with no
  Review awareness (a repo-wide search for "Review" in `build.one.ios` returns
  nothing). **Any `is_draft`-based guard this design adds (D1's resubmit
  eligibility, D3's completion boundary) can be defeated from a phone with zero
  server-side signal that a review was in progress.** This needs an explicit
  decision: gate the iOS write path too (same server-side guard, since it's
  enforced in the shared API layer either way — iOS doesn't need code changes for
  the *is_draft* enforcement piece), or consciously accept it as out of scope for
  this unit and track it.
- **`ReviewTimeline.tsx` is shared, unmodified, across Expense/BillCredit/
  Invoice/ContractLabor edit pages**, keyed generically on `parentType`. Any change
  scoped "for Bill" (retiring/unifying its declined-resubmit branch) touches a
  component none of those four pages have a dedicated test for — `BillEdit.test.tsx`
  already stubs it to null, and no `ReviewTimeline.test.tsx` exists anywhere. A
  Bill-specific change here needs either a `parentType === 'bill'` special case or a
  shared component test added alongside it, so Expense/BillCredit/Invoice/
  ContractLabor's Submit/Advance/Decline don't regress silently.
- **This feature adds no new web route**, so it's invisible to the `/docs`
  surface's route-derived web manifest, and the API's LIVE `/docs` section doesn't
  exist yet (still "planned" per the umbrella CLAUDE.md). Per the umbrella
  CLAUDE.md's per-unit pipeline, Docs is a required step regardless — this unit
  should add a short curated note (the nearest equivalent to a narrative doc this
  area currently has) rather than rely on auto-derivation that won't fire.
- **The existing, tracked web↔API RBAC parity gap** (`build.one.web/TODO.md:407`,
  "if the API flips `can_update`→`can_submit` on a review route, every web test
  stays green while the UI silently mis-gates") is directly relevant: `can_submit`
  already exists as a real, wired `RoleModule` column that zero Bill review route
  uses today. D1/D3 add two more routes onto the `can_update` convention without
  engaging with this — worth a deliberate call either way, made once, before adding
  more surface on top of it.
- **`ReviewService.create()` has a second `is_initial_submit` branch for
  ContractLabor**, sharing the same method D1/D2 modify. Threading `is_resubmit`
  through the shared signature is safe as long as the Bill and ContractLabor
  branches stay mutually exclusive on `bill_id`/`contract_labor_id` (they are,
  today) — flagged here so it's a checked fact, not an assumption, before the
  signature changes.

## 6. Recommended build sequencing

Per the correctness and prod-ops judges' explicit "sequence the build" guidance —
land the durable, low-risk pieces before the branchy ones, so a resubmit made
before the anchor exists doesn't just land as a disconnected email anyway:

1. **Prereq, SQL-only, `/em`-applied, zero behavior change:** reconcile
   `dbo.bill_line_item_attachment.sql` to prod (base==live diff first).
2. **Prereq, additive, zero behavior change:** stamp `conversation_id` +
   `internet_message_id` on every `send_mail` outbox row at write time (D4's
   amendment). Ships alone, deployable independently, and is the prerequisite both
   D2(a) and the reviewer-reply version fix need.
3. **Independent, closes a real safety gap on its own:** the `apply_reviewer_decision`
   version-discrimination fix (P1, §4/D2). Can ship before or after the rest.
4. **Atomic resubmit transition + attachment swap endpoint + web wiring**, behind an
   app-setting kill switch (e.g. `REVIEW_RESUBMIT_MODE=off|fresh|thread`) so the
   route can ship dark, then be flipped to "always send a fresh disconnected email"
   for a smoke test, then to "thread" once verified.
5. **Worker-side reply-threading branch** (D2's four-branch dispatch), landing last
   because it's the piece with the most external-system (Graph) uncertainty and the
   most confirmed findings.

## 7. Decisions (settled 2026-09-06)

**1. iOS scope: split the fix — fold in the guard, track the bypass separately.**
The only iOS-relevant piece this unit actually needs to build is already in scope:
the completion-boundary (`is_draft`) guard on `update_by_public_id`, the atomic
resubmit sproc, and the D3 swap endpoint are all **server-side, shared-API-layer**
checks. Because iOS's `UpdateBillEndpoint` hits the exact same generic
`PUT /api/v1/update/bill/{publicId}` the web app uses, adding that guard there
protects iOS automatically, with **zero iOS repo changes**. What does *not* belong
in this unit is the separate, pre-existing bug the critic surfaced alongside it:
iOS's raw `Toggle("Draft")` can flip `is_draft: True→False` directly through that
same generic endpoint, bypassing `_run_complete_bill`'s SharePoint/Excel/QBO
pipeline entirely — that's a completion-pipeline-integrity bug, not a
review-notification gap, it predates this feature, and nothing here makes it worse
(if anything, once the completion-boundary guard lands, a bill flipped to
`is_draft=False` this way correctly loses resubmit-eligibility, which is the
*right* protective side-effect). **Track that bypass as its own unit; don't bundle
it here.**

**2. Gate the new routes on `can_update`, matching every existing sibling route.**
`can_submit` is real and wired (`shared/rbac.py`'s permission tuple,
`RoleModule.can_submit`) but used by **zero** routes today — `/submit`, `/advance`,
`/decline` all gate on `can_update`. Splitting *just* the two new routes onto
`can_submit` would let a user with `can_update` (and therefore full submit/advance/
decline access today) hit a 403 on the one action — resubmit — that's conceptually
identical to submit, which is a worse, more confusing permission model than the one
that exists now. Migrating the *whole* family to `can_submit` is a legitimate
follow-up, but it's a live behavior change (it could revoke submit ability from any
role that currently has `can_update=True`/`can_submit=False`) that deserves its own
audit of current `RoleModule` grants before anyone flips it — not something to
decide silently as a side effect of this unit. **This unit: `can_update`, no
exceptions. Follow-up, tracked separately: audit prod `RoleModule` grants, then
decide whether to migrate the whole review-route family to `can_submit`.**

**3. D2 branch (b): PATCH the stale draft in place. Do not delete-and-recreate.**
This is actually the *more* conservative choice under this repo's own existing hard
rule (`feedback_never_patch_email_by_subject_match.md`: only ever PATCH a mailbox
message whose exact id you created and whose `is_draft==True` you verify
immediately before the write) — patching a message we created ourselves, verified
fresh, is exactly the case that rule sanctions. Delete-and-recreate needs an extra
destructive call, opens a race window against a human clicking Send in Outlook (the
delete is a soft move to Deleted Items — the stale draft stays sendable), and
throws away any edit a human already started on the pending draft. PATCH-in-place
(`update_draft`, which already replaces subject/body/recipients) plus adding the
current PDF avoids all three. **Implementation note for the build unit:** if the
attachment changed, the draft will need its stale attachment removed before the new
one is added (otherwise it ends up carrying both) — the mail client has no
attachment-delete-by-id helper today (`DELETE /messages/{id}/attachments/{id}`);
add one alongside `add_attachment_to_message`.

**4. Include a minimal duplicate-resubmit guard in v1, not a deferred nice-to-have.**
This repo's own operating history is the reason to build it now rather than later:
`project_bill_review_response_workflow.md` documents repeated real incidents of a
client-side timeout triggering an accidental repeat call against `submit`/`advance`/
`complete` — exactly the failure mode a resubmit rate guard exists to absorb, on a
route that (unlike those) is designed to embed a full PDF into the outbox and
message the shared `invoice@` mailbox on every call. It's also nearly free to add:
the atomic `CreateReviewIfCurrentStatusIn` sproc D1 already needs (§4/D1) can carry
one extra predicate in the same transaction — refuse (409, same shape as the
existing transition errors) when the latest Submitted-cycle row for this bill was
created by the same actor within the last few minutes **and** nothing on the
Bill/BillLineItem/BillLineItemAttachment has a newer `ModifiedDatetime` than that
row. **Build it as part of the same sproc, not a separate follow-up.**

## 8. Test plan additions (beyond fixing the 47 confirmed findings)

- `ReviewService` transition logic (`build_submit_payload`/`build_resubmit_payload`/
  `create`/`apply_reviewer_decision`) has **zero** existing regression coverage for
  Bill (only a ContractLabor-consolidation test references `ReviewService` at all).
  New atomic-sproc and version-discrimination logic needs tests before it ships.
- A `ReviewTimeline.test.tsx` (doesn't exist today) asserting Submit/Advance/Decline
  is unchanged for Expense/BillCredit/Invoice/ContractLabor once Bill-specific
  wiring lands in the shared component.
- `MsOutboxWorker._handle_send_mail` has zero unit coverage today; the new
  dispatch branch needs fail-capable fixtures (404 fallback, attachment-failure
  rollback, checkpoint/resume on retry) — mock the mail client, don't hit Graph.
- A reviewer-reply integration scenario: submit → notify → resubmit → the *original*
  reply arrives after the resubmit → assert it's flagged, not silently applied.
- A scoped version of the tracked-but-unbuilt web↔API route/permission parity test,
  covering at minimum the two new routes this unit adds.

## Related

Umbrella memory: `project_review_workflow.md`, `project_review_notifications.md`
(superseded v1 design — do not follow), `feedback_two_phase_dispatch_design_gated.md`,
`feedback_docs_keep_current.md`, `reference_base_vs_live_sproc_diff.md`.
