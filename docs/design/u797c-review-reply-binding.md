# U-797c: one review email and one reply binding for every review type

Status: **Gate 1 APPROVED 2026-11-20 (Chris)**: D1 yes (one ReviewNotification table), D2 yes (scheduled ingestion, copy only), D3 = slice c4 in this unit, D4 yes (60-day legacy window after c1), D5 yes (435 re-binds, zero disagreements, before c1 switches). Slices are U-797c1..c4, each with its own build gate. Parent U-797.
Inputs: two read-only code maps (outbound and inbound) plus prod counts, all quoted below.

## Why

Chris, 2026-11-16: Contract Labor (CL) should get the Bill process, then Expense and BillCredit should follow
with the same review and edits. U-797b ("Approved" applies the suggested codes) needs a reply to bind to
the exact email that carried the suggestions. Today each type binds differently, and BillCredit has no review email at all.

## What exists today

| | Bill | Expense | BillCredit | Contract Labor |
|---|---|---|---|---|
| Review email | Yes, per record (`ReviewNotificationService`, `BILL_SPEC`) | Yes, same pipeline (`EXPENSE_SPEC`) | **None.** Review rows can carry `BillCreditId`, but no email is sent | Yes, one per (project, date) for the whole crew (`ContractLaborReviewNotificationService`) |
| Subject | `[Review] {Vendor} — Bill #{n} — {Proj} — ${amt}` | `… — Expense {ref} — …` | n/a | `[Review] Contract Labor - {Proj} - {date}`. The U-798 prefix is live; the legacy form is still accepted |
| Sent when | First submit only | First submit only | n/a | When the last CL of the date leaves `pending_review` (date gate) |
| Dedup | None at enqueue (relies on the first-submit check) | None | n/a | Claim table `ContractLaborNotification` on (Project, WorkDate) |
| Recorded to bind a reply | Outbox payload JSON `conversation_id`, stamped by the MS worker on draft create (U-579) | Same | none | `ContractLaborNotification.OutboundSubject`, joined to a **sent-items** EmailMessage by exact subject, then by its ConversationId |
| Reply finder | Provenance: the reply's ConversationId must equal a stamped outbox `conversation_id`. Pre-2026-09-30 rows fall back to a subject match. `FindBillForReviewerReply`'s conversation branch is legacy (source vendor thread) | Same | none | `FindContractLaborForReviewerReply`: outbound subject plus conversation, with an (abbrev, date) fuzzy fallback that provenance rejects |
| Apply | `apply_emailed` (draft only, one line, sender in the PM/Owner envelope, provenance binds) | Same | none | `apply_reviewer_decision` (whole crew, every member's thread must bind) |
| Reply ingestion | On demand: `POST /bill/{id}/review-replies/sync` (Graph `conversationId eq`) | Same | none | Needs the sent-items copy from the manual `/admin/email/poll`; no scheduled job |

**Prod, 2026-11-20 (read-only):**
- Since 10/1, **243 bill, 185 CL and 7 expense** reviewer replies were applied with their bound EmailMessage. The bindings work.
- EmailMessage holds 4,792 inbox and 3,249 sent-items rows, the latest from 11/18.
- Everything is driven by hand: Chris pastes subject + code, and a session calls the APIs. **No timer polls mail or replies.**

**Duplication across the two notification services:**
- the `[Review]` subject prefix;
- BCC archive resolution;
- recipient → address mapping (CL re-implements `ReviewRecipientService._bucket`);
- the slash-joined PM greeting;
- the project label fallback;
- the "reply with approval + code" ask, whose wording has drifted;
- three near-identical recipient sprocs;
- the enqueue call.

**Type-specific, and staying that way:**
- the line rendering (amount table vs crew Hours/Billable/Overhead);
- the PDF attachment (Bill/Expense);
- the Expense memo line;
- the CL date gate, consolidation and claim;
- the Bill/Expense auto-advance to In Review.

**Known defects found on the way:**
- **(a) CL binding depends on a sent-items poll nobody schedules.** The conversation id the worker already stamps in the CL outbox payload goes unused.
- **(b) The CL outbox row is keyed on the *triggering* CL, not the claim's representative CL,** so the payload cannot be joined back to its claim.
- **(c) `ConversationId` is case-insensitively collated, while Graph ids are case-sensitive.** `FindBillForReviewerReply` and the CL sproc compare it case-insensitively; provenance and Expense re-check BIN.
- **(d) Bill/Expense have no enqueue dedup.**
- **(e) `docs/conventions/notifications.md` is stale.** It still describes forward-of-original.

## The design

### 1. One record of every review email: `dbo.ReviewNotification` (+ `dbo.ReviewNotificationTarget`)
- **`ReviewNotification`:** one row per outbound review email.
  - Identity and kind: `Id`, `PublicId`, `Kind` (bill / expense / bill_credit / contract_labor).
  - Outbound copy: `OutboxId`, `Subject`.
  - Graph ids:
    - `ConversationId`, `InternetMessageId` and `DraftGraphMessageId`, all **BIN collation**;
    - `StampedDatetime`.
  - CL grouping: `ProjectId`, `WorkDate` (CL only).
  - `CreatedDatetime`.
- **Dedup:**
  - A unique index on (Kind, target) for Bill, Expense and BillCredit replaces relying on the first-submit check. Fixes (d).
  - A unique index on (Kind, ProjectId, WorkDate) for CL replaces `ContractLaborNotification`'s claim, with the same claim/release semantics.
- **`ReviewNotificationTarget`:** (NotificationId → exactly one of BillId / ExpenseId / BillCreditId / ContractLaborId). One row for a per-record email; one per crew member for CL.
- **Who writes it:**
  - The notification path inserts the row (the claim) before enqueueing and stores `OutboxId` after.
  - The MS worker, when it creates the draft (where U-579 already stamps the payload), also stamps `ConversationId` / `InternetMessageId` / `DraftGraphMessageId` on the row, keyed by `OutboxId`. Fixes (a) and (b) without a sent-items poll.
  - The payload stamp stays, so old readers keep working.

### 2. One binder: `bind_reply(reply_email_message) → ReviewNotification + targets`
- **Primary key:** the reply's ConversationId equals `ReviewNotification.ConversationId`, compared as BIN. Fixes (c).
- **Then the existing checks, unchanged:**
  - inbox folder;
  - sender in the PM/Owner envelope of the notification's targets;
  - exactly one notification.
- **Shape:** a single sproc, `FindReviewNotificationForReply(@ConversationId)`, plus one Python binder in `shared/authz/provenance.py`. Bill, Expense and CL `apply_*` call it instead of their own finders. `FindBillForReviewerReply`, `FindExpenseForReviewerReply` and `FindContractLaborForReviewerReply` become thin wrappers, then are retired.
- **Legacy fallbacks:**
  - pre-2026-09-30 subject matching for Bill/Expense;
  - the CL outbound-subject join for rows predating the cutover.

  Both stay **only** for notifications created before the cutover, behind a dated constant, and are retired after a set window (decision D4).

### 3. One composer for the email itself (behaviour-preserving refactor)
- **Composer:** `ReviewEmailComposer` owns the subject prefix, recipients (one `ReviewRecipientService`, with the CL per-project resolver added to it), BCC, greeting, the ask text and the enqueue + `ReviewNotification` write.
- **Per type:** each type supplies a small spec, `{subject_parts, body_blocks(), attachment(), targets, grouping}`. Bill and Expense already have `_NotificationSpec`; CL and BillCredit get one.
- **Recipient sprocs:** the three near-identical ones collapse into one over a target-line view. The CL per-project variant keeps its per-project dedup.
- **Bar:** byte-identical subjects, recipients and bodies for Bill, Expense and CL. Pinned by golden tests rendered from today's code before the refactor.

### 4. Scheduled reply ingestion (copy only, never apply)
- **What it adds:** a scheduler timer every 10 minutes. For each `ReviewNotification` with a ConversationId and any target still awaiting review, it runs the existing `copy_review_thread_replies([conversation_id])` (Graph `conversationId eq`, idempotent on InternetMessageId).
- **Effect:** the reply is in the database when someone, or U-797b, applies it. No more hand-run poll or per-document sync. Applying a decision stays exactly as today; U-797b decides when "Approved" applies automatically.
- **Bound:** only open notifications are polled; Graph calls scale with open reviews, not mailbox size.

### 5. BillCredit joins
- **Pieces:** a BillCredit spec, a recipient resolver and an initial-submit hook branch in `ReviewService.create`, then `apply_emailed` for BillCredit through the shared binder.
- **Scope:** this is the first piece of the "Expense and BillCredit next" roadmap. Coding prediction for BillCredit is a later unit.

## Migration of in-flight threads
- **Backfill from history:**
  - **CL:** backfill `ReviewNotification` from every `ContractLaborNotification` row. Targets = the crew for (project, date). ConversationId is taken from the matching sent-items EmailMessage, else from the CL outbox payload stamp matched on subject.
  - **Bill/Expense:** backfill from completed `send_mail` outbox rows whose payload carries `conversation_id`.

  Every backfilled row is checked for exactly one candidate. Rows with none or several stay on the legacy path and are listed for Chris.
- **Proof:** re-bind the 435 replies already applied since 10/1 through the new binder, read-only. Every one must resolve to the same document it was applied to. **Zero disagreements is the ship bar.**
- **Rollout:** new emails write the table from day one. Old threads bind through the backfilled rows or the legacy fallback until the window closes.

## Slices (each with its own Gate 1 / Gate 2)
| Slice | Content | Tier |
|---|---|---|
| **c1** | `ReviewNotification` + targets; notification paths write it; worker stamps it; backfill; shared binder + sproc; the three apply paths switch over; BIN compare; prove the 435 re-binds | **P0-surface** (decides which record a reply approves) |
| **c2** | Composer refactor, golden-test identical output; one recipient service; stale notifications doc fixed | non-P0 (behaviour-preserving) |
| **c3** | Scheduled reply ingestion timer (copy only) | non-P0 (read-and-copy, no decision) |
| **c4** | BillCredit review email + reply apply through the shared binder | P0-surface (approval path) |

**Order:** c1 → c3 → U-797b (CL suggestions + "Approved" applies, built on c1's targets) → c2 → c4. c2 can run any time after c1. U-797b depends only on c1, plus c3 if replies should arrive without a manual sync.

## Decisions for Chris (Gate 1)
- **D1.** Approve the record-of-every-email table as the one binding (recommended), or keep binding on the outbox payload JSON plus the CL subject join?
- **D2.** Scheduled reply *ingestion* every 10 minutes, copy only (recommended)? Without it, every reply still needs a manual sync before it can be applied.
- **D3.** BillCredit review email in this unit (c4), or as its own unit with the BillCredit prediction work?
- **D4.** Legacy fallback window: keep the subject-based paths for 60 days after c1 ships, then remove them?
- **D5.** The re-bind proof bar: all 435 historical replies must bind to the same document, zero disagreements, before c1's apply paths switch?

## Builder deviations (c1)

Recorded by the c1 builder, 2026-11-20. Each one is a deliberate choice against the approved text above.

1. **Class name.** The record service is `ReviewNotificationRecordService` (module `entities/review/business/review_notification_service.py`). `notification_service.py` already defines `ReviewNotificationService`, so reusing the name would shadow it.
2. **Lookup returns every match.** `ReviewNotificationRecordService.lookup_for_reply()` returns every notification on the conversation, with its target pairs. `find_for_reply()` is the `None unless exactly one` wrapper from the brief. The binder needs three outcomes (no row → legacy, ambiguous → refuse, row excluding the document → refuse), and `Optional` cannot tell "none" from "two".
3. **Binder contract.** `bind_reply_via_notification(conv, (kind, id))` returns the notification when exactly one matches and covers the target. It returns `None` only when no row exists. It raises `ReviewerProvenanceRefused` when the conversation is ambiguous or the row does not cover the document. A blank ConversationId returns `None` with no lookup.
4. **Shared flag gate.** `bind_reviewer_reply_with_notification(...)` in `shared/authz/provenance.py` holds the flag logic for all three writers. Flag OFF calls the legacy closure only. Flag ON runs the generic checks, then the record, then legacy only when no row exists. Contract Labor has a matching `ContractLaborService._bind_cl_decision_reply`. Both CL call sites (crew loop and single member) use it. The brief did not name either helper; it is the smallest way to keep flag OFF byte-identical while the crew loop and the member write path share one rule.
5. **Crew claim is two writes.** `claim_for_crew` inserts the claim row, then one target per crew member. If a target write fails, it deletes the claim row and re-raises. The legacy `ContractLaborNotification` claim is unchanged, and the writer treats any failure of the new claim as "no record" (the legacy claim still gates the send).
6. **Attach-after-enqueue race (open).** The writers attach `OutboxId` after `enqueue_send_mail` returns. If the MS worker drains that outbox row and stamps the draft before `attach_outbox` runs, the stamp finds no row and is a no-op. The row then carries NULL ConversationId, and the reply falls to NO_ROW and the legacy path. This degrades to legacy; it cannot bind a reply to the wrong document. The drain runs on a 60 s timer and attach follows within the same request, so the window is small. Not closed in c1. Possible fix: a stamp keyed by ReviewId or public outbox key recorded before enqueue.
7. **Backfill ambiguity.** For CL, if the outbound-payload candidate has 2+ distinct conversation ids, the row is NULL and listed as ambiguous. It does not fall through to the sent-items candidate.
8. **Proof harness.** `scripts/u797c1_rebind_proof.py` reads the applied-replies list with one ad hoc read-only SELECT (`SWEEP_SQL`). That breaks the sproc-only rule for this one ops script. The harness has no write path, so it has no rollback transaction. It does not run the backfill: run it after the backfill has been applied.
9. **Worker stamp values.** The worker passes the payload's stripped `conversation_id` and `internet_message_id`, the same values it persists, and `graph_message_id` as the draft id. It is wrapped in a broad try and logs the exception class only.

### Fix round 1 (c1, EM review)
- **CL rule (item 1 and 2).** A Contract Labor reply binds on exactly one notification with Kind `contract_labor`, the same ProjectId and the same WorkDate as the request. Target membership is not used for CL, so a late-added crew member binds. Crew membership stays the live check. A mismatch refuses with no legacy fallback. The decision is `shared.authz.provenance.select_notification`, the same function the live binder and the proof call.
- **Delete behaviour (item 3).** Target rows CASCADE from the notification and from each document they name. `ReviewNotification.ReviewId` SET NULLs. This is a deliberate exception to the no-CASCADE convention, documented in the SQL header. No existing delete sproc changed. A guarded repair block rebuilds FKs created with the old action.
- **Backfill (item 4 and 6).** Claims with NULL WorkDate are skipped. One claim per (ProjectId, WorkDate) is kept, the lowest Id. The report moved to `scripts/u797c1_rebind_proof.py --report` (read-only).
- **Proof (item 5).** Labels AGREE, NO_ROW (reason: null_conversation, pre_u579_thread, no_row), AMBIGUOUS, DISAGREE, UNRESOLVED, SKIPPED. Exit 0 only if every row is AGREE, or NO_ROW pre_u579_thread (listed for Chris).
- **Outbox fill (item 7).** `SetReviewNotificationOutbox` is one UPDATE ... FROM ... LEFT JOIN ms.Outbox, with COALESCE per identity column. This replaces the earlier two-statement shape.
- **Release order (item 8).** The record row is released before the legacy claim. The record-release log text was corrected. It said the draft "will not retry", which was wrong. The legacy claim log text was accurate and is unchanged.

### c1 as reviewed (EM, 2026-11-20): what the code actually does now

**Contract Labor binding rule (supersedes target membership for CL).** With the flag ON, a CL reply binds
when exactly one notification matches its ConversationId (BIN) **and** that notification's Kind, ProjectId
and WorkDate equal the request's. Crew membership is the live `ReadReviewableContractLaborByProjectAndDate`
check, as today, so a late-added laborer is covered. Any mismatch refuses with no legacy fallback.
Bill/Expense keep target membership. Work dates must parse as `date`, `datetime` or `YYYY-MM-DD`;
anything else refuses.

**Deletes.** The target FKs cascade and `ReviewId` is SET NULL. This is a deliberate exception to the
"no CASCADE" convention, so the sidecar never changes how an existing record deletes. Filtered indexes on
the four document columns keep cascaded deletes seeking, not scanning.

**Proof labels and pass bar (D5).** `scripts/u797c1_rebind_proof.py --run` labels every applied reply since
2026-10-01:

| Label | Meaning | Passes? |
|---|---|---|
| AGREE | the live selector binds it to the applied record | yes |
| SKIPPED | out of scope (BillCredit, Invoice) | yes |
| NO_ROW `pre_u579_thread` | the thread predates the U-579 stamp; legacy binds it | yes, listed |
| LISTED | CL, no notification row, and today's resolver cannot scope it: both paths refuse | yes, listed |
| NO_ROW `no_row` / `null_conversation` | no record for a post-cutover reply, or no conversation id | **no** |
| DISAGREE | the record does not cover the applied record (`wrong_document`, `wrong_project_or_date`, `wrong_kind`, `applied_cl_outside_scope`, `legacy_unresolved_binder_refuses`) | **no** |
| AMBIGUOUS | two or more records on one conversation | **no** |
| NEW_BIND | today's resolver cannot scope it but the new binder would bind it (a behaviour change) | **no** |
| UNRESOLVED `applied_cl_missing` | the applied CL has no work date or no line project | **no** |

For CL, scope comes from today's legacy resolver, and the applied record must sit inside it (its date,
and a line on the scope project). The backfill's sent-mail fallback uses the same rule as that resolver
(`ProcessingStatus = 'outbound'`, subject equality), so the proof compares like with like.

**Retirement (D4): on or after 2027-01-19** (`LEGACY_BINDING_RETIRE_AFTER`, a documentation constant that
nothing enforces in c1):
- remove the legacy binding closures (`bind_reviewer_reply_to_review_thread` / `_to_thread`) and
  `_expected_cl_reviewer_reply_conversation_id`;
- retire `FindBillForReviewerReply`, `FindExpenseForReviewerReply`, `FindContractLaborForReviewerReply`;
- stop writing `ContractLaborNotification`: the new crew claim becomes the gate (c2, which owns the claim),
  then drop the table;
- remove the `REVIEW_REPLY_BINDER_V2_ENABLED` flag.

⚠️ **NO_ROW is not date-gated in c1.** With the flag ON, a post-cutover reply whose record is missing (a
record write failure, a NULL conversation id) still falls back to legacy, which applies today's rules.
Refusing NO_ROW after the retirement date is a P0 behaviour change, planned as a follow-up, not done here.

**Owned by c2** (Pass 2, deferred): write the record before the enqueue and key the worker stamp by a
payload `review_notification_id`, which removes `SetReviewNotificationOutbox` and the attach race; one
never-raise wrapper in `ReviewNotificationRecordService` instead of four; bind once per CL decision instead
of once per crew member; skip the stamp call for non-review mail; replace the CL branch in
`select_notification` with a per-kind coverage rule (the composer's `grouping`).

### c1a (U-797c1a): the three causes of the 11 proof failures

The live proof (424 AGREE, 11 failing) had three causes, each now handled in `scripts/u797c1_rebind_proof.py`:

- **(a) pre_u579 by send time.** `pre_u579` was decided from the Review's `CreatedDatetime` (when the reply was applied). It is now decided from the send time: the latest `done` `send_mail` outbox row for the document (`EntityPublicId`) at or before the Review's `CreatedDatetime` (`EmailSentDatetime`). A send before `LEGACY_THREAD_FALLBACK_CUTOFF` is pre-U-579. No send row fails closed.
- **(b) applied record outside the legacy scope.** Line `ModifiedDatetime` is NOT evidence: on prod every one of the 185 CL approvals has later line writes, and nothing records project history. So the proof asks what matters for the flag: does the new binder land on the same (project, date) as today's resolver? If yes (BOUND, or NO_ROW which falls back to legacy) the row is `LISTED scope_agrees_record_changed`, printed with the scope and the record's current projects, for Chris to judge (a later re-projection vs a historical mis-apply cannot be told apart from the data). If the binder lands elsewhere it fails (`DISAGREE applied_cl_outside_scope` / `AMBIGUOUS`).
- **(c) repurposed draft.** One row is corrected by `scripts/migrations/2026_11_21_u797c1a_clear_repurposed_draft_conversation.sql`. The MR2-MAIN 10/7 draft (ReviewNotification 1089) was retitled and sent as HP2 10/7. Its `ConversationId`, `InternetMessageId` and `DraftGraphMessageId` are cleared, guarded to that one row. The HP2 replies then bind through the legacy resolver.

New labels:

- `LEGACY_BIND` (`legacy_binds`) passes. It is a CL NO_ROW where today's legacy resolver scopes the reply and the applied record sits inside that scope. With the flag on, NO_ROW falls back to legacy, which binds it. Listed and counted.
- `LISTED` (`scope_agrees_record_changed`) passes and is listed for Chris. See (b).

Known hazard, fails closed and booked for c2/c3: a draft stamped with a conversation and later retitled into another email carries the wrong stamp. The stamp should be verified against the sent copy's subject before it is trusted. Until then, a mismatch refuses (DISAGREE or NO_ROW) rather than binding.

**Precondition for D4 retirement (U-797c1a review S1).** For Contract Labor the stop-2 proof passes any
NO_ROW inside today's resolver scope (LEGACY_BIND): with the flag on, NO_ROW falls back to legacy, so the
decision is identical. That proves equivalence, NOT coverage. A CL claim the backfill skipped (NULL
WorkDate, duplicate project/date) also passes. Before the legacy fallback is removed, CL record coverage
must be re-proved: every post-cutover CL reply must AGREE on a record, with no LEGACY_BIND left.

