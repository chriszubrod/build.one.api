# Design — U-549 · Ramp receipt/memo chaser

> **DESIGN unit. Nothing is built until `/em` approves** ([[feedback_two_phase_dispatch_design_gated]] — a new
> external integration is foundational).
> Repos: `build.one.api` (primary) · `build.one.scheduler` · `build.one.web`.
> Origin: this is U-005's explicitly deferred v2 — *"Auto-emailing cardholders for missing info (v2 — the
> exception path is flag only in v1)"* ([`expense-coding-cockpit.md`](../../../build.one.team/product/specs/expense-coding-cockpit.md)).
> Author: session 2026-09-25 with Chris.
> **Status: Phase A + C1 SHIPPED and DEPLOYED-TO-DB 2026-09-28** (pushed; both SQL files applied; code
> still inert — no caller outside the units). **C2 REVISED 2026-09-28 and awaiting its own Gate 1.**
> Phases B, C2, D, E are unbuilt and each takes its own Gate 1.

---

## 1. Problem

Card transactions land in Ramp missing a **memo**, a **receipt**, or both. Until the cardholder supplies them the
transaction can't be completed, and today the chase is **periodic and manual** — Chris notices, Chris emails.
The delay between swipe and completion is large and unmeasured.

Two distinct failures, per Chris (2026-09-25):

1. **The crew ignores Ramp's own reminders.** Ramp does nag natively — weekly Friday emails, SMS for in-person
   swipes, push notifications, policy-deadline reminders, manager escalation. It isn't landing. The crew responds
   to a message from *the office*; sender identity is the active ingredient, not message content.
2. **Chris has no standing visibility into who is late.** There is no worklist. Delinquency is discovered by
   remembering to look.

So the deliverable is **two things, co-equal**: an automated chase loop *and* a visibility surface. A chase loop
alone would fix (1) and leave (2) exactly where it is.

### Why not just configure Ramp

Considered and rejected on Chris's answer above, not on ignorance of the feature. Ramp's reminders are already
reaching cardholders and being ignored. Turning up their cadence amplifies a channel that demonstrably doesn't
work on this crew.

✅ **Checked, not assumed (Chris, 2026-09-25): Ramp's policy-deadline reminder is switched ON.** So the native
channel is configured and still failing — we are not comparing against a strawman, and "just turn Ramp's
reminders on" is closed as an alternative. This is the strongest single piece of evidence for the premise.

---

## 2. Scope

**In (v1).**
- Read-only Ramp integration: `transactions:read`, `users:read`.
- Rolling sweep selecting open items by **Ramp's own `all_requirements_met_and_approved == False`** (§4.4), and
  reading `memo` / `receipts` only to say *what* is missing.
- Persisted delinquency state per transaction (what's missing, since when, notified how often, resolved when).
- **Weekly per-cardholder digest email** from build.one, created as a **draft** for human review — v1
  never auto-sends (§6.1). *(Was twice-weekly; reduced to once per week by Chris, 2026-09-28.)*
- ⛔ **One draft per (cardholder, week); NEVER patched.** A PATCH of an open draft silently reverts
  when the reviewer's Outlook autosaves over it (§6.1) — and the reviewer having it open is the
  normal state here. C2 does **not** use C1's `update_draft` Kind.
- **Send detection via `conversation_id` + SentItems/DeletedItems**, never from a 404 (§6.1).
- ~~**Escalation at 14 days**~~ — **DEFERRED** (Chris, 2026-09-28). Meaningless in draft mode: he sends every
  draft, so he already sees every message. Aged items carry an inline marker instead (§6.2). A **standing CC to
  the company owner** replaces it, from `RAMP_CHASER_CC_EMAIL` (§6.2).
- **Web worklist**: open items by cardholder, age-bucketed — the visibility half of the ask.

**Out (v1), deliberately.**
- **Any write to Ramp.** No memo push, no receipt upload, no coding push. Read-only scopes only.
- **Pulling receipt images.** Detection of absence only. Fetching the images (→ Box project folder → invoice
  packet, the `U-099` ~$22K billing-leak angle) is the obvious v2 and should be booked as one.
- **Capture-at-source.** Ramp is on a **native QBO integration** (confirmed with Chris), so it *imports* fields
  from QBO rather than accepting pushed field options; `/accounting/fields` is for Ramp's custom/API accounting
  mode. Pushing Project/SubCostCode dropdowns into Ramp would mean owning the Ramp→QBO sync. Not v1, possibly
  not ever.
- **Non-Ramp cardholders.** The 1–2 non-Ramp cards named in the U-005 spec have no Ramp transactions and are
  therefore invisible to a Ramp-sourced sweep. **Named gap, not an oversight** — they keep today's manual chase.
- Changing anything about the 58999 coding cockpit or `recode_purchase_line`.

---

## 3. Why this does not touch the QBO write path

Worth stating once, because it bounds the blast radius. [`recode_purchase_line`](../../integrations/intuit/qbo/purchase/connector/expense/business/service.py:417)
converts an `AccountBasedExpenseLineDetail` into an `ItemBasedExpenseLineDetail` (`ItemRef` ← SubCostCode,
`CustomerRef` ← Project). Ramp's native QBO sync only ever writes **account-based** lines carrying
Category / Customer / Class / Billable — it has **no vehicle for a QBO `ItemRef`**. No amount of Ramp work
removes or alters build.one's recode step.

U-549 is therefore **purely additive**: a new read-only source, a new table, a new email, a new page. It cannot
regress the money path because it never touches it.

---

## 4. Architecture

```
Ramp API ──(read-only, cursor-paged)──> RampTransactionService
                                              │
                                    classify: memo? receipt?
                                              │
                                     dbo.RampTransactionFollowUp  ◄── the state
                                              │
                        ┌─────────────────────┴──────────────────────┐
                        │                                            │
              RampChaserDigestService                      GET /ramp-follow-up
              (WEEKLY, Tuesday)                            (web worklist)
                        │
              dbo.RampChaserDigest  ◄── per-cardholder draft state (§5.2)
                        │
                 MS outbox `send_mail`  ──> Graph ──> DRAFT to cardholder, CC owner
                                                      (never patched — §6.1)
```

### 4.1 Integration layer — `integrations/ramp/`

Mirrors the hardened pattern already used by `integrations/box/` and `integrations/ms/`, file-for-file:

```
integrations/ramp/
  auth/          OAuth2 client-credentials; ~10-day token, cached + refreshed
  base/          client.py · errors.py · retry.py · logger.py · correlation.py · idempotency.py
  transaction/   external/client.py · business/service.py · business/model.py
  user/          external/client.py · business/service.py   (roster; see §4.3)
```

No new outbox — this integration **never writes to Ramp**, so it needs no outbox of its own. Email delivery rides
the **existing MS outbox**.

#### 4.1.1 Platform constraints (from Ramp's docs, verified 2026-09-27)

| | |
|---|---|
| **Environments** | Production API `https://api.ramp.com` (app `https://app.ramp.com`) · Sandbox API `https://demo-api.ramp.com` (app `https://demo.ramp.com`). ⚠️ The API host is **not** the app host. |
| **Rate limit** | **200 requests / 10-second rolling window, per _source IP_** — not per client or per endpoint. |
| **Timeout** | A request exceeding **60s** returns 504. |
| **Backoff** | Docs prescribe exponential backoff (1s → 2s → 4s); explicitly warn against immediate retry. |

Three things follow that the build must not rediscover the hard way:

- ⛔ **The `scope` parameter is REQUIRED on the token request. Omit it and Ramp issues a token with _no scopes_** —
  not an error, just a token that 403s on everything. This is a silent-failure trap of exactly the kind that
  costs an afternoon. The auth client must always send `scope` explicitly **and assert the returned token
  actually carries the scopes requested**, failing loudly at startup rather than at first use.
- **Rate limiting is per source IP, so the budget is _shared_** with every other outbound call from the same
  egress address — this sweep does not get 200/10s to itself. Ample for our volume either way (the U-005 probe
  put target purchases at ~1.2/day), but `base/retry.py` gets the prescribed exponential backoff on 429, not a
  tight retry.
- **A real sandbox exists** — a genuine advantage over the QBO work, where U-005's Gate-P2 had no sandbox realm
  wired and had to be proven on one controlled live Purchase. Use `demo-api.ramp.com` to build and test the auth
  + pagination plumbing in Phase A. ⚠️ **But Phase 0's probe must run against PRODUCTION (read-only)** — its
  entire purpose is to measure *real* memo/receipt population, which sandbox data cannot answer.

### 4.2 Detection window — and the watermark trap

`GET /developer/v1/transactions` has **no server-side "missing receipt" filter**, so classification is
client-side. The sweep re-scans a **rolling window** (proposed: 90 days by `user_transaction_time`) on every run.

⚠️ **Do not watermark-advance the pull.** Per [[feedback_widened_pull_filter_needs_full_pull]], a watermark hides
pre-existing rows the moment the "incomplete" definition widens. The window governs **discovery only**; once a
transaction is in `RampTransactionFollowUp` it is tracked until resolved regardless of age, so an item that ages
out of the 90-day window is never dropped.

### 4.3 Cardholder identity — ⚠️ **CORRECTED AT GATE 1 (2026-09-27)**

> **An earlier draft of this section claimed the cardholder's email rides on the transaction. It does not.
> Measured: `email` appears on `card_holder` in 0 of 420 transactions.** The claim came from the API docs; the
> live payload disagrees. Left uncorrected it would have produced a digest with no way to address it.

`card_holder` carries **`user_id`**, `employee_id`, `first_name`, `last_name`, `department_{id,name}`,
`location_{id,name}` — **no email, ever** (all eight keys present on 420/420; the object is never empty).

**So the `/users` join is REQUIRED for delivery, not optional.** `GET /developer/v1/users` returns `id`, `email`,
`first_name`, `last_name`, `status`, `department_id`, `manager_id`, `is_manager`, `role`. Join
`card_holder.user_id` → `users.id` → `email`.

Measured feasibility (90-day window): roster is **14 users total** — fetch once per sweep and cache; this is a
single call, never an N+1 per transaction. All **6/6** open-item cardholders resolve to an email and all are
`USER_ACTIVE`.

**Failure mode the build must handle:** a `user_id` that does not resolve, or resolves to a user with no email,
**cannot be chased**. Log it and surface it on the worklist as unroutable — never silently drop the item, or a
cardholder disappears from the chase with no trace. Skip non-active statuses (don't chase someone who left);
`status` values observed are `USER_ACTIVE`, and the API documents `active | deleted | draft | inactive | pending`.

### 4.4 What counts as incomplete — **SETTLED BY THE PHASE 0 PROBE (2026-09-27)**

> **Both candidate predicates were wrong. Ramp already computes this, and its answer is authoritative.**
>
> ### `needs_followup` ⟺ `all_requirements_met_and_approved == False`

**The measurement** (live production, 420 transactions over 90 days, read-only):

| | count |
|---|---|
| Ramp says **NOT** complete (`all_requirements_met_and_approved == False`) | **37** |
| The hand-rolled blank-memo-or-no-receipt test | 101 |
| — Ramp flags it, my test does not | **0** |
| — My test flags it, Ramp does not | **64** |

Ramp's verdict is a **strict subset** of the hand-rolled test, with **zero** disagreement in the other
direction. The 64 extras are transactions missing a receipt that **Ramp's own policy does not require one for** —
the missing-receipt population has a median of **exactly $75.00** with 29 under that line, and includes refunds
(one at **−$3,362.47**). ⛔ **Chasing those 64 would nag people for receipts they do not owe.** That is the fastest
imaginable way to teach the crew to ignore *our* emails too — self-inflicting the exact failure this unit exists
to escape.

**Consequences, stated plainly:**

- ❌ **My "~75% blank memo" projection was wrong.** Measured: **6.7%** (28 of 420). The inference that a blank
  Ramp `memo` maps to U-005's bare-merchant-string population does not hold. The design carried that number as a
  premise in two places; both are corrected.
- ❌ **The blank-memo vs hint-extractor debate is moot.** Chris chose the literal blank test on 2026-09-25 from
  the two options I offered — neither was the right predicate, and the better one existed the whole time in a
  field I hadn't read. Not a decision to revisit; a decision that no longer applies.
- ✅ **Using Ramp's flag inherits the policy Chris already configured** (§1: the policy deadline is on). Thresholds,
  category exemptions and refund handling all come for free and stay correct when he changes them in Ramp — we
  never re-implement a policy engine, and we can never drift from it.
- **`memo` / `receipts` are still read** — not to decide *whether* to chase, but to say *what is missing* in the
  digest body. Of the 37: **12** receipt only, **6** memo only, **19** both, **0** neither.
- **`policy_violations` is empty on all 420** — the field exists but is unpopulated for this account. Do not
  build on it.

#### ⚠️ Required guard — the field name says `..._and_approved`

The selector **conflates "requirements met" with "approved."** A transaction could read `False` because an
*approval* is pending — something no amount of nagging the cardholder can fix. Chasing those would be the
over-chase failure in a new costume.

**Verified clean at Gate 1 over a widened 180-day window: 928 transactions, 45 flagged, `0` with neither a
missing memo nor a missing receipt.** So the selector is safe against today's Ramp configuration — but that is a
property of the *config*, not of the field, and nobody will remember this the day approvals get switched on.

⛔ **Build the guard: flagged, but neither memo nor receipt is actually missing → log it and SKIP. Never chase
it, and never let it into a digest** (there would be nothing to ask for). Surface the count so a rising number
is visible rather than silent.

### 4.5 Resolution

A follow-up row resolves when a **later sweep observes** Ramp showing the missing field(s) present. Resolution is
**observed from Ramp, never inferred from having sent an email.** `resolved_at` stamped; row stops being chased
and drops off the worklist.

---

## 5. Data model — `dbo.RampTransactionFollowUp`

| Column | Notes |
|---|---|
| `Id`, `PublicId` | house convention |
| `RampTransactionId` | Ramp's id — **UNIQUE**, the idempotency anchor |
| `CardHolderRampUserId`, `CardHolderName` | off the transaction's `card_holder` |
| ~~`CardHolderEmail`~~ | ⛔ **NOT STORED — removed at Gate 2 (Chris, 2026-09-27).** See §5.1. |
| `MerchantName`, `Amount`, `TransactionDate` | for the digest body and the worklist |
| `NeedsMemo`, `NeedsReceipt` | **descriptive, not selective** — they populate the digest body ("what's missing"). Membership is decided solely by Ramp's `all_requirements_met_and_approved` (§4.4). |
| `FirstSeenAt` | drives the age buckets and the 14-day escalation |
| `LastDraftedAt`, `DraftMessageId` | the draft created for this item's cardholder — see §6.1 |
| `LastNotifiedAt`, `NotifyCount` | ⚠️ stamped on **observed send**, never on draft creation (§6.1) |
| `EscalatedAt` | set once when it first crosses 14d |
| `ResolvedAt` | null while open |
| `CreatedAt`, `UpdatedAt`, `RowVersion` | house convention ([[project_access_control_rebuild]] scoping) |

`Amount` is `DECIMAL` and read via `Decimal(str(...))` end-to-end — display-only here, but the house money rule
applies regardless ([[feedback_money_falsy_zero_coercion]]).

### 5.1 What this table is — and the one column that was cut

The columns fall into two groups, and it is worth being explicit about which is which:

- **Irreducible chase state, which Ramp cannot know:** `RampTransactionId` (the join key), `FirstSeenAt`,
  `LastDraftedAt`, `DraftMessageId`, `LastNotifiedAt`, `NotifyCount`, `EscalatedAt`, `ResolvedAt`.
- **A denormalised display cache, re-fetched from Ramp every sweep:** `CardHolderRampUserId`, `CardHolderName`,
  `MerchantName`, `Amount`, `TransactionDate`, `NeedsMemo`, `NeedsReceipt`. These exist so the Phase-B worklist
  can render without calling Ramp on every page load.

**This is not a `qbo.*`-style raw mirror, and must not become one.** It holds only *open* items (~37 measured)
and stops being written once an item resolves. U-294 found that **none** of the 39 `qbo.*` staging tables are
drop-ready and that retiring them is ~6 prerequisite waves out; a second raw mirror is a mistake this codebase
is actively paying for elsewhere.

⛔ **`CardHolderEmail` was cut at Gate 2 (Chris's call, 2026-09-27) and must not come back.** It is employee PII,
the `/users` roster is already fetched once per sweep so the address is always available fresh, and a stored copy
can drift from Ramp. Persisting it bought nothing for the digest (which runs inside a sweep) and served only the
worklist. **Routability is now DERIVED at the moment it is needed rather than stored** — which is strictly better,
because a stored flag could go stale against the roster. The unroutable *handling* is unchanged: such a row is
still persisted, still logged, still counted, and never silently dropped.

Cutting it also keeps the tier honest: PII at rest is a `/security-review` surface, and without this column
Phase A has none.

---

### 5.2 `dbo.RampChaserDigest` — **NEW, added at the C2 redesign (2026-09-28)**

⛔ **The single highest-leverage change in the C2 revision.** Draft state was specced onto
`dbo.RampTransactionFollowUp`, which is **one row per transaction** — but §6 and §6.1 treat that state as
**per cardholder** ("*the stored `DraftMessageId`*", singular). That mismatch is the root of four separate P0s
found by the pre-build red-team.

| column | purpose |
|---|---|
| `Id`, `PublicId` | house convention |
| `CardHolderRampUserId` | the cardholder this digest is for |
| `WeekOf` | the Tuesday this digest belongs to — **computed in `business_timezone`, never UTC** (§6) |
| `DraftMessageId` | the Graph draft created for this (cardholder, week) |
| `ConversationId` | ⛔ the send-detection key (§6.1) — a 404 alone proves nothing |
| `InternetMessageId` | secondary identity for the same lookup |
| `LastDraftedAt` | when the draft was created (distinct from notified) |
| `LastNotifiedAt`, `NotifyCount` | stamped on an **observed send only** |
| `Outcome` | `drafted` / `sent` / `discarded_unsent` / `unsent_carryover` / `unroutable` |
| `CreatedAt`, `UpdatedAt`, `RowVersion` | house convention |

**UNIQUE on `(CardHolderRampUserId, WeekOf)`** — that pair is the idempotency anchor, and it is what makes a
re-run safe without depending on a `uuid5` of a date string.

**What this closes, and why one table is worth it:**

| pre-mortem P0 | how the digest row closes it |
|---|---|
| **No edge back to "draft again"** — each cardholder chased exactly once, forever, while `NotifyCount` climbed | Each week is its own row. Last week being `sent` says nothing about this week; there is no stale `DraftMessageId` to re-resolve. |
| **Full resolution orphans a draft** — a cardholder who clears *all* items drops out of a `ResolvedAt IS NULL` query, so their outstanding draft is never revisited and gets sent demanding completed work | The digest row survives independently of the follow-up rows, so an outstanding draft is still findable after every item resolves. |
| **`LastNotifiedAt` misses every *successful* chase** — the cardholder fixes everything, rows resolve, the send is never observed, and a chase that *worked* records as `NotifyCount = 0` | The counter lives on the digest row, which `ResolvedAt` does not touch. |
| **Torn writes** — stamping one draft id across N transaction rows; a crash after 3 of 10 leaves 3 pointing at the draft and 7 NULL, and the next cycle's behaviour depends on an unstated read | One row, one write. No fan-out, no partial state. |

`dbo.RampTransactionFollowUp` keeps exactly what is genuinely per-transaction: `NeedsMemo`, `NeedsReceipt`,
`FirstSeenAt`, `ResolvedAt`, and the display cache. Its `LastDraftedAt` / `DraftMessageId` / `LastNotifiedAt` /
`NotifyCount` / `EscalatedAt` columns become **vestigial** — they were never written by anything (confirmed:
`UpsertRampTransactionFollowUp` sets none of them). ⚠️ **Leave them in place for now** rather than shipping a
DROP against a just-applied table; book the cleanup and do it once C2 is proven.

## 6. Cadence, escalation, delivery

**Chris's call: ONCE PER WEEK** (2026-09-28, reduced from twice). Day is a Phase-D scheduler concern, not a
Phase-C one — the digest's idempotency key is `(cardholder, sweep_date)`, so cadence changes cost nothing in
code. ✅ **Day: TUESDAY** (Chris, 2026-09-28), morning in `business_timezone` (`America/Chicago`). Chosen over
Friday deliberately: Friday would overlap Ramp's own Friday nag so the office message lands beside it, but
Friday afternoon is when field crew are least likely to act — and at weekly cadence there is no second
bite that week. Tuesday gives them the week to clear it.

- **One digest per cardholder per run**, listing every open item with merchant, amount, date, age, and exactly
  what's missing. Never one email per transaction.
- **Escalation**: any cardholder holding an item ≥14 days gets Chris CC'd on that digest; those items are
  flagged in the body and stamped `EscalatedAt`. *(Alternative considered — a separate consolidated escalation
  digest to Chris. Rejected for v1: the web worklist already provides the consolidated view, and a CC keeps the
  escalation visible to the cardholder, which is the point.)*
- ⚠️ **Two different clocks — do not collapse them.** Escalation keys off `FirstSeenAt` (**how long we have been
  chasing**), never off transaction age. Phase 0 measured **22 of 37 open items already past 14 days** (oldest
  82d), so an age-based clock would CC Chris on 5 of 6 cardholders on the very first run and make escalation
  meaningless before it ever meant anything. Backfilled items get `FirstSeenAt = go-live`, so nothing escalates
  for the first two weeks — correct, and deliberate. The **worklist** (Phase B) sorts by transaction age instead,
  so Chris still sees the 82-day-old item on day one; it just doesn't manufacture an escalation.
- **Never CC the escalation recipient onto their own digest.** Chris is himself the #2 cardholder (10 open items,
  $3,884.98 at stake — Phase 0). CC'ing him on his own reminder is pure noise; suppress it.
- **Delivery rides the MS outbox `send_mail` Kind** — never an inline Graph call, exactly as
  [`digest_service.py`](../../entities/time_entry/business/digest_service.py:36) does.
- **Per-cardholder `try/except`**; one bad recipient cannot sink the batch. Cardholders with no resolvable email
  are logged and skipped. The sweep never raises back to its caller.

**Sender: `invoice@rogersbuild.com`** (Chris, 2026-09-25) — the established chase mailbox
([[reference_vendor_document_request_email]]). Lives in config, not in code. Accepted side effect: employee
replies land in the AP mail flow alongside vendor correspondence.

### 6.1 Drafts, not sends — **REVISED 2026-09-28 after two pieces of hard evidence**

**Chris's call (2026-09-25): every chaser email is created as a DRAFT for human review. v1 never auto-sends.**
That stands. What changed is *how* a draft is maintained across cycles, and how a send is detected.

#### ⛔ Evidence 1 — a PATCH of an open draft SILENTLY REVERTS

[[feedback_graph_draft_patch_reverts_if_client_open]], observed live 2026-09-28 on the G&M Plumbing bill-40638
review draft: a `PATCH me/messages/{id}` landed and read back correct **immediately**, then Chris's Outlook —
which had the draft open — autosaved its stale copy over it at 18:26:34Z and blanked it. Last-writer-wins, and
the human's client writes last.

**This is not an edge case for this feature; it is the normal state.** Chris reviews and sends every draft, so
"a human has this draft open" is the expected condition, not a rare one. The failure it produces is the exact
one the unit exists to prevent:

1. Tuesday — draft created for a cardholder listing 5 open items.
2. Chris opens it to review.
3. A later run PATCHes it to the current 3 items. Graph returns 2xx; C1 truthfully stamps `update_draft_patched`.
4. His still-open client autosaves → the draft reverts to the **5-item** body.
5. He sends it. **The cardholder is chased for two items they already cleared.**

Note the outcome stamp was *true when written and false a minute later* —
[[feedback_live_state_readings_are_perishable]] in its purest form.

#### ⛔ Evidence 2 — 404 is not proof of a send

A vanished draft id means sent **or** deleted **or moved** (Graph message ids change on move). Three review
rounds on C1 established this; C1 was narrowed to report `not_found` as a fact and infer nothing.

#### → The revision: ONE DRAFT PER (CARDHOLDER, WEEK). NEVER PATCH.

**Do not maintain a draft across cycles.** Each weekly run creates that week's draft from current data and
leaves any earlier unsent draft alone.

- ⛔ **C2 does NOT call C1's `update_draft` Kind.** Patching is unsafe for exactly the reviewer this feature
  has. *(Honest note: C1 was built before this evidence existed. It is a correct and useful outbox capability,
  but it is not what C2 v1 needs — a sequencing mistake, recorded rather than hidden. Do not reach for it here
  because it exists.)*
- **A leftover unsent draft is a SIGNAL, not a defect to patch away.** It means the previous week's draft was
  never sent. Surface it on the Phase-B worklist as `unsent_carryover`; do not delete it (deletion needs a Graph
  write Kind that does not exist, and deleting a human's mail is not ours to do).
- **Two drafts in the folder is acceptable and self-correcting.** Chris sends the newer and discards the older;
  both are visible and dated. Visible untidiness beats one silently-wrong draft.
- At weekly cadence a week-old draft's contents are stale anyway, so recreating is *also* the more correct body.

#### Send detection — `conversation_id`, never absence

Store **`conversation_id`** (and `internet_message_id`) alongside the draft id at creation; `_format_message`
already returns both. On a later run, for a draft whose id no longer resolves:

| observed | reading | action |
|---|---|---|
| a message in that conversation in **SentItems** | genuinely sent | stamp `LastNotifiedAt`, bump `NotifyCount` |
| the draft in **DeletedItems** | discarded unsent | do **NOT** stamp; surface as `discarded_unsent` |
| neither | unknown | do **NOT** stamp; log and surface |

All three are id/conversation-keyed lookups, so this respects
[[feedback_never_patch_email_by_subject_match]]. ⛔ **Never infer a send from absence.**

#### Still true from the original

- One digest per cardholder per run, never one per transaction.
- Delivery rides the existing MS outbox `send_mail` Kind — never an inline Graph call.
- Per-cardholder `try/except`; one bad recipient cannot sink the batch; a cardholder with no resolvable email is
  logged, counted and surfaced, never silently dropped.
- `LastNotifiedAt` / `NotifyCount` stamp on an **observed send only** — never on draft creation. A draft nobody
  sent is a cardholder nobody chased.


### 6.2 The message — specified by Chris, 2026-09-28

⛔ **This is the active ingredient, not decoration.** The entire premise of the unit is that the crew ignores
Ramp's automated mail but responds to the office. A message that pattern-matches to another automated nag fails
in the one way the architecture cannot compensate for.

**Subject:** `Ramp Action Needed - <yyyy-mm-dd> - <card holder>`

**Body:**

```
<First name>,

When you have a moment, will you please jump into Ramp and complete the following items?

Receipt & Memo
  Sep 18   Lowe's              $92.50
  Aug 04   Amazon              $47.10   ← 54 days

Receipt Only
  Sep 12   Home Depot         $184.22

Memo Only
  Sep 24   Tractor Supply     $310.00

Thanks,
Chris
```

**Rules the build must hold:**

- **Three sections, in this order: `Receipt & Memo`, `Receipt Only`, `Memo Only`.** They partition the
  cardholder's open items by `NeedsMemo` / `NeedsReceipt`. An item appears in exactly one section.
- **Omit any section that is empty** — never render a heading with nothing under it.
- **Aged items carry a marker** (`← N days`) computed from `FirstSeenAt`, i.e. how long *we* have been chasing,
  consistent with §6's two-clocks rule. Not transaction age.
- **CC: the company owner**, from config (`RAMP_CHASER_CC_EMAIL`), on every digest — a standing CC, not
  escalation-triggered. ⚠️ If the owner is also the sender/signer, the CC is a no-op and should be left unset.
- **No "easiest way to submit" paragraph and no "this holds up billing" paragraph.** Cut deliberately — the
  opening line already tells them to do it in Ramp, and the rest reads as filler. Do not reintroduce them.
- **Closing is exactly `Thanks,` / `Chris`.**
- Amounts are `Decimal`-formatted currency; dates render in `business_timezone`.

**The 14-day escalation is DEFERRED, not built** (Chris, 2026-09-28). It was specced as "CC Chris on aged
items," which is meaningless in draft mode — he sends every draft, so he already sees every message. It only
becomes meaningful if `send` mode is ever enabled. The aged-item marker carries the signal in the meantime.
`EscalatedAt` stays in the schema unused rather than being dropped and re-added.


## 7. Config and gates

Mirrors the proven [`digest_service.py`](../../entities/time_entry/business/digest_service.py:36) ladder —
ships dark, flips without a redeploy:

| Setting | Default | Purpose |
|---|---|---|
| `RAMP_CHASER_MODE` | `off` | `off` = sweep is a no-op (kill switch) · `draft` = draft per cardholder for human review — **the v1 terminal rung** · `send` = deliver directly, **kept in the ladder for shape-parity with the time-entry digest but deliberately unused in v1**; enabling it is a separate decision, not a config tweak |
| `RAMP_CHASER_ESCALATE_DAYS` | `14` | |
| `RAMP_CHASER_WINDOW_DAYS` | `90` | discovery window (§4.2) |
| `RAMP_CHASER_SENDER` | `invoice@rogersbuild.com` | decided 2026-09-25 (§6) |
| `RAMP_CLIENT_ID` / `RAMP_CLIENT_SECRET` | — | Azure app settings, never committed. **Separate pairs for sandbox and production** — they are different Ramp apps registered in different dashboards. |
| `RAMP_API_BASE_URL` | `https://api.ramp.com` | `https://demo-api.ramp.com` for sandbox. Explicit URL rather than an `RAMP_ENV` enum the code maps — one fewer indirection between config and the host actually called. |

Still gated by **`ALLOW_MS_WRITES`** at the outbox layer — two independent switches, same as the time-entry
digest.

---

## 8. Security, RBAC, secrets

- Ramp scopes **read-only** (`transactions:read`, `users:read`). No write scope is requested, so a credential
  leak cannot mutate Ramp.
- Credentials live in Azure app settings alongside the Box/QBO secrets. Never in the repo, never in the board.
- Sweep runs with `set_authz_context(is_system_admin=True)` on the outbox write path
  ([[feedback_outbox_authz_boundary]]).
- The worklist endpoint + page are gated by the **Expense module**, matching the coding cockpit.
- Cardholder emails and card metadata are employee PII: they belong in the follow-up table and the digest, and
  **must not** be logged at INFO or echoed into board/TODO entries.
- **P0-surface?** No money mutation and no external *write* to a financial system — but it does send outward-facing
  email on the company's behalf and handles employee PII. `/em`'s call, but I'd **run Pass 3 `/security-review`**
  on the strength of the outbound-email surface alone.

---

## 9. Phasing

Each phase is independently shippable and independently useful. **Phase 0 gates everything.**

| Phase | Deliverable | Repo |
|---|---|---|
| ✅ **0 — Probe (read-only)** | **DONE 2026-09-27.** 420 transactions / 90d, live production, read-only. Settled §4.4's classifier (Ramp's own flag, not a hand-rolled test), sized the queue at **37 open items across 6 cardholders**, and answered the backfill question. Ran inline per [[feedback_no_script_files]] — no script file committed. | api |
| **A — Integration + state** | `integrations/ramp/`, `dbo.RampTransactionFollowUp`, classify + upsert sweep. No email, no UI. | api |
| **B — Worklist** | `GET` endpoint + web page: open items by cardholder, age buckets. **Closes Chris's visibility gap on its own, before a single email is sent.** | api + web |
| **C — Digest** | `RampChaserDigestService` + MS outbox enqueue, **draft-only** (§6.1), including the update-in-place and vanished-draft-means-sent handling. | api |
| **D — Schedule** | Tue/Fri timer. | scheduler |
| **E — Docs** | `/docs` section + operator guide, per the per-unit pipeline. | web + team |

**Phase B before C is deliberate.** Visibility is half the ask, it carries zero outbound-email risk, and the
worklist is the instrument we use to sanity-check the classifier before any of it reaches the crew.

---

## 10. Acceptance criteria

- [x] **Phase 0 reports real counts from the live Ramp account.** Done 2026-09-27: 420 txns / 90d → **37 open**,
      6 cardholders, oldest 82d. Classifier settled (§4.4).
- [ ] The sweep selects **exactly** the transactions with `all_requirements_met_and_approved == False` — no local
      memo/receipt predicate decides membership. Mutation-prove it: flip one item's flag in a fixture and assert
      membership follows the flag, not the fields.
- [ ] **A transaction missing a receipt that Ramp considers complete is NOT chased.** (Guards the 64-item
      over-chase directly — the single worst outcome this design can produce.)
- [ ] **A flagged item with neither memo nor receipt missing is logged and SKIPPED**, never chased and never
      placed in a digest (§4.4 guard — the `..._and_approved` conflation).
- [ ] **The cardholder email is resolved via the `/users` join** and **never persisted** (§5.1). Assert the
      roster is fetched **once per sweep**, not once per transaction, and that no column holds an address.
- [ ] **An unresolvable `user_id`, or one resolving to a user with no email, is still PERSISTED, logged and
      counted** — never silently dropped. Routability is derived at use, not stored.
- [ ] A cardholder whose `/users` status is not active is not chased.
- [ ] An open item appears exactly once in `RampTransactionFollowUp`; a repeat sweep updates it rather than
      inserting a duplicate (UNIQUE on `RampTransactionId`).
- [ ] Ramp flipping the item to complete → next sweep stamps `ResolvedAt` and it leaves the worklist.
- [ ] A backfilled item carries `FirstSeenAt = go-live`, not its transaction date, and therefore does **not**
      escalate on the first run despite being 82 days old.
- [ ] The escalation recipient is never CC'd onto their own digest.
- [ ] The token request always sends `scope`, and the client **asserts the issued token carries the scopes
      requested** — a scopeless token fails loudly at startup, not with mystery 403s at first use (§4.1.1).
- [ ] A 429 triggers exponential backoff (1s/2s/4s), never an immediate retry.
- [ ] `RAMP_CHASER_MODE=off` performs **no** Graph call and enqueues **no** outbox row.
- [ ] `draft` deposits one draft per cardholder listing all their open items — never one per transaction.
- [ ] **v1 never sends.** No path through the code reaches Graph `sendMail` / `send_draft`; only draft creation
      and draft update. (Mutation-prove it: force `mode="send"` and assert the send path is unreachable/refused.)
- [ ] A second sweep while a draft is still outstanding **updates that draft in place** (resolved by stored
      `DraftMessageId` with `is_draft == True`) — the mailbox holds exactly one chaser draft per cardholder.
- [ ] A draft that no longer resolves as a draft is treated as **sent**: `LastNotifiedAt` stamped, `NotifyCount`
      bumped, and **no replacement draft created**.
- [ ] `LastNotifiedAt` is **never** stamped by draft creation alone.
- [ ] Re-running the sweep the same day drafts nothing further (deterministic `EntityPublicId`).
- [ ] An item at 14 days CCs Chris exactly once and stamps `EscalatedAt`.
- [ ] A cardholder with an unroutable email is logged and skipped; the rest of the batch still goes.
- [ ] A Ramp API outage fails the sweep closed — no partial digests, no rows marked notified that weren't.
- [ ] Worklist is Expense-module gated; an unauthorized caller gets 403.

**Revised C2 draft-model criteria (2026-09-28):**
- [ ] ⛔ **No code path PATCHes a chaser draft.** C2 must not call `enqueue_update_draft`. Mutation-prove it:
      grep-assert the absence in a test, so a future contributor cannot quietly reach for C1's Kind.
- [ ] **One draft per `(CardHolderRampUserId, WeekOf)`** — enforced by the UNIQUE index, not by application logic.
      A re-run in the same week creates nothing new.
- [ ] `WeekOf` is computed in **`business_timezone`**, not UTC. Prove it with a test at a boundary hour: a run at
      23:00 Central Monday must NOT collide with the scheduled Tuesday-morning run.
- [ ] **An unsent draft from a previous week does not block this week's draft.** It is surfaced as
      `unsent_carryover`, never patched, never deleted.
- [ ] **A send is stamped only on positive evidence** — a message in that `ConversationId` found in SentItems.
      A 404 alone stamps nothing. A draft found in DeletedItems is recorded `discarded_unsent` and does NOT
      stamp `LastNotifiedAt`.
- [ ] **A cardholder who clears every item still has their outstanding draft findable** (the digest row survives
      independently of `ResolvedAt`), and it is surfaced rather than silently sent.
- [ ] `LastNotifiedAt` / `NotifyCount` survive their transactions resolving — so a chase that WORKED is
      distinguishable from one that never happened.
- [ ] Only items with at least one of `NeedsMemo` / `NeedsReceipt` actually set appear in a digest body.

---

## 11. Risks

| Risk | Handling |
|---|---|
| ~~Ramp auto-populates `memo` → blank test is inert~~ | ✅ **CLOSED by Phase 0.** Not inert (6.7% blank) — but moot anyway: the classifier is now Ramp's own flag (§4.4). |
| ~~Blank-memo test selects ~75% of transactions~~ | ✅ **CLOSED by Phase 0 — the projection was wrong.** Real queue is **37 of 420 (8.8%)**, worst cardholder 11 items. The "demoralizing first digest" fear does not materialize. |
| **Over-chasing — the risk that replaced them** | The hand-rolled test would have chased **64 items Ramp does not require a receipt for** (sub-threshold spend, refunds). Using Ramp's flag structurally prevents it; a future "improvement" that reintroduces a local predicate reintroduces this. Do not. |
| **Duplicate nagging** — our digest lands beside Ramp's | Accepted by design: the whole premise is that our sender lands where Ramp's doesn't. Revisit if the crew complains. |
| **Crew ignores us too** | `NotifyCount` / `FirstSeenAt` make this *measurable* within two weeks instead of anecdotal. If the loop doesn't move time-to-completion, we learn it from the data. |
| **Chasing a departed employee** | `/users` status check skips `deleted` / `inactive`. |
| **Drafts pile up unsent** | Draft-only keeps a human in the loop, so the loop is only as fast as the sending. `LastDraftedAt` vs `LastNotifiedAt` makes the gap visible rather than invisible — if drafts sit, we'll see it in the data and revisit the `send` rung (§12 #4). |
| **Duplicate drafts / duplicate chasing** | §6.1: update-in-place by verified `DraftMessageId`, and a vanished draft is read as *sent*, never as *missing*. Both are explicit acceptance criteria. |
| **Employee PII in logs** | Explicit §8 rule; check in Pass 1 review. |

---

## 12. Open questions for Gate-1

### ⛔ C2 BLOCKER — which mailbox is `/me`?

`get_message` and `list_messages` accept a `mailbox` parameter. **`create_draft` and `update_draft` do not —
both hardcode `me/messages`.** So §7's `RAMP_CHASER_SENDER = invoice@rogersbuild.com` **cannot be honoured by
the draft path as built**; the draft lands wherever `/me` resolves for the MS token.

The failure shape is what makes this a blocker rather than a detail. If C2 reads with
`get_message(mailbox=ramp_chaser_sender)` while drafts are created under `/me`, **every GET 404s** — and a 404
that gets read as "gone" would stamp `LastNotifiedAt` across the whole population while having sent nothing.
§6.1's revision removes that specific trap (a 404 no longer implies a send), but the drafts would still be
invisible to every subsequent run, silently, forever.

**Must be settled before C2 starts:** what mailbox does the MS token's `/me` resolve to, and does
`create_draft` need a `mailbox` parameter? ⚠️ Related and already recorded:
[[feedback_graph_draft_patch_reverts_if_client_open]] — whichever mailbox it is, a human reviewing in it will
autosave over a PATCH, which is why §6.1 no longer patches.

### Resolved earlier



**Resolved 2026-09-25 (Chris):** sender = `invoice@rogersbuild.com` · Ramp's policy deadline **is** switched on
· every chaser email is a **draft**, v1 never auto-sends.

1. ~~**Ramp API credentials** — how to provision them.~~ **RESOLVED 2026-09-27**, path below. Still **blocks
   Phase 0**, which blocks everything else, until the app is actually created.
   **Ramp dashboard → Company → Developer → "Create New App"** (requires **admin** access to the Ramp
   dashboard). Name it, accept the ToS, then: *Grant types* → "Add new grant type" → **Client Credentials**;
   *Scopes* → "Configure allowed scopes" → **`transactions:read` + `users:read`** only. Client ID and Client
   Secret are then shown to copy. Register the app separately on `demo.ramp.com` for sandbox.
2. ~~**Backfill depth at go-live**~~ — ✅ **ANSWERED by Phase 0: chase the full 90 days.** 22 of 37 open items
   (59%) are already older than 14 days and the oldest is 82d, so a go-live-forward-only seed would ignore the
   actual backlog — which *is* the problem Chris described. At 37 items total there is no volume argument against
   it. Items are backfilled with `FirstSeenAt = go-live` so the aged history does not trigger instant escalation
   (§6).
3. **P0-surface classification** — `/em`'s call; §8 recommends running Pass 3 regardless. Note the draft-only
   decision *lowers* the outbound risk materially: nothing leaves the building without a human pressing send.
4. **Does the draft-only gate undercut the goal?** The stated pain is *"the reminder is periodic and manual —
   large delay."* Draft-only keeps a manual step in the loop; what it removes is the *composition* and the
   *remembering*. Worth an explicit revisit after a few weeks: if drafts sit unsent, the bottleneck simply moved
   and the `send` rung becomes the conversation. Flagged, not argued — the metrics in §5 will settle it.
