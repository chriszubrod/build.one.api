# Design — U-549 · Ramp receipt/memo chaser

> **DESIGN unit. Nothing is built until `/em` approves** ([[feedback_two_phase_dispatch_design_gated]] — a new
> external integration is foundational).
> Repos: `build.one.api` (primary) · `build.one.scheduler` · `build.one.web`.
> Origin: this is U-005's explicitly deferred v2 — *"Auto-emailing cardholders for missing info (v2 — the
> exception path is flag only in v1)"* ([`expense-coding-cockpit.md`](../../../build.one.team/product/specs/expense-coding-cockpit.md)).
> Author: session 2026-09-25 with Chris. Status: **awaiting Gate-1.**

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
- Rolling sweep classifying transactions as `needs_memo` / `needs_receipt` / `needs_both` / `complete`.
- Persisted delinquency state per transaction (what's missing, since when, notified how often, resolved when).
- **Twice-weekly per-cardholder digest email** from build.one, created as a **draft** for human review — v1
  never auto-sends (§6.1).
- **Escalation at 14 days**: items open ≥14d CC Chris on that cardholder's digest and are marked escalated.
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
              (twice-weekly sweep)                         (web worklist)
                        │
                 MS outbox `send_mail`  ──> Graph ──> cardholder + CC Chris @14d
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

### 4.3 Cardholder identity

`card_holder` rides on the transaction itself — `{first_name, last_name, user_id, email, department}`. **The
email comes straight off the transaction**; no roster mapping or Contact lookup is required, and no build.one
`User` row needs to exist. `/users` is pulled only to resolve status (skip `deleted` / `inactive` cardholders so
we don't chase someone who left).

### 4.4 What counts as incomplete

**Chris's call (2026-09-25): a literal blank test.**

- `needs_memo` ⟺ `memo` is null or whitespace-only.
- `needs_receipt` ⟺ `receipt_ids` is empty.

Deliberately *not* the richer "hint-extractor can't resolve a project" test — that was offered and declined. Log
it in `TODO.md` as a tagged follow-up rather than re-arguing it: the useless-but-present memo
(`"THE HOME DEPOT #0723 - 3892"`) will pass the blank test and continue to feed the 58999 flag queue.

⚠️ **Gate-1 must probe this empirically before a line is written.** The U-005 probe found text present on
**439/439** QBO `PrivateNote` values. Ramp composes that field as *"{cardholder name} - {memo}"* for memo'd
transactions and falls back to the raw merchant descriptor otherwise — so a blank Ramp `memo` most likely
corresponds to the **~75%** that arrived as bare merchant strings. If instead Ramp auto-populates `memo`, **the
blank test selects zero rows and v1 is inert.** This is a yes/no question one read-only API call answers, and it
is the single highest-value thing Gate-1 can do.

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
| `CardHolderRampUserId`, `CardHolderEmail`, `CardHolderName` | denormalized off the transaction |
| `MerchantName`, `Amount`, `TransactionDate` | for the digest body and the worklist |
| `NeedsMemo`, `NeedsReceipt` | current classification, refreshed each sweep |
| `FirstSeenAt` | drives the age buckets and the 14-day escalation |
| `LastDraftedAt`, `DraftMessageId` | the draft created for this item's cardholder — see §6.1 |
| `LastNotifiedAt`, `NotifyCount` | ⚠️ stamped on **observed send**, never on draft creation (§6.1) |
| `EscalatedAt` | set once when it first crosses 14d |
| `ResolvedAt` | null while open |
| `CreatedAt`, `UpdatedAt`, `RowVersion` | house convention ([[project_access_control_rebuild]] scoping) |

`Amount` is `DECIMAL` and read via `Decimal(str(...))` end-to-end — display-only here, but the house money rule
applies regardless ([[feedback_money_falsy_zero_coercion]]).

---

## 6. Cadence, escalation, delivery

**Chris's call: twice weekly, escalate at 14 days.** Proposed Tue + Fri mornings in `business_timezone`
(`America/Chicago`) — Friday deliberately overlaps Ramp's own Friday nag so the office message is the one in the
inbox alongside it.

- **One digest per cardholder per run**, listing every open item with merchant, amount, date, age, and exactly
  what's missing. Never one email per transaction.
- **Escalation**: any cardholder holding an item ≥14 days gets Chris CC'd on that digest; those items are
  flagged in the body and stamped `EscalatedAt`. *(Alternative considered — a separate consolidated escalation
  digest to Chris. Rejected for v1: the web worklist already provides the consolidated view, and a CC keeps the
  escalation visible to the cardholder, which is the point.)*
- **Delivery rides the MS outbox `send_mail` Kind** — never an inline Graph call, exactly as
  [`digest_service.py`](../../entities/time_entry/business/digest_service.py:36) does.
- **Per-cardholder `try/except`**; one bad recipient cannot sink the batch. Cardholders with no resolvable email
  are logged and skipped. The sweep never raises back to its caller.

**Sender: `invoice@rogersbuild.com`** (Chris, 2026-09-25) — the established chase mailbox
([[reference_vendor_document_request_email]]). Lives in config, not in code. Accepted side effect: employee
replies land in the AP mail flow alongside vendor correspondence.

### 6.1 Drafts, not sends — and the resend trap

**Chris's call (2026-09-25): every chaser email is created as a DRAFT for human review. v1 never auto-sends.**
Consistent with the house convention for all outbound chase mail.

This is not merely "stop at the `draft` rung" — drafts have failure modes a send does not, and the design has to
answer them:

- **`LastNotifiedAt` / `NotifyCount` must not be stamped at draft creation.** A draft that is never sent is a
  cardholder who was never chased. Stamping on enqueue would make the metrics lie and start the escalation clock
  on an email nobody received. Draft creation stamps `LastDraftedAt` + `DraftMessageId`; `LastNotifiedAt` is
  stamped only on an **observed send** (below). The 14-day escalation clock keys off `FirstSeenAt`, which is
  Ramp-observed and therefore always honest.
- **Never create a second draft while an unsent one is outstanding**, or the Drafts folder fills with duplicates
  within two sweeps. Before drafting, resolve the stored `DraftMessageId` and confirm `is_draft == True`
  ([[feedback_never_patch_email_by_subject_match]] — by verified id, never by subject match). Still a draft →
  **update it in place** with the current open items rather than creating another.
- ⚠️ **A vanished draft means SENT, not missing.** [[feedback_remittance_check_sent_before_recreate]] is directly
  on point: if `DraftMessageId` no longer resolves as a draft, the overwhelmingly likely cause is that Chris sent
  it. Treat that as the **observed send** — stamp `LastNotifiedAt` / bump `NotifyCount` — and do **not** recreate
  it. Recreating on a vanished draft is how this design would spam the crew with duplicates of mail they already
  received.
- **Idempotent** via a deterministic outbox `EntityPublicId` keyed on `(cardholder_ramp_user_id, sweep_date)`, so
  a re-run on the same day cannot produce a second draft even before the checks above.

---

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
| **0 — Probe (read-only, no build)** | Authenticate; pull 90 days; report counts: blank-memo, no-receipt, both, by cardholder, age distribution. **Answers §4.4's kill question and sizes the chase population before we commit to cadence.** | api (script) |
| **A — Integration + state** | `integrations/ramp/`, `dbo.RampTransactionFollowUp`, classify + upsert sweep. No email, no UI. | api |
| **B — Worklist** | `GET` endpoint + web page: open items by cardholder, age buckets. **Closes Chris's visibility gap on its own, before a single email is sent.** | api + web |
| **C — Digest** | `RampChaserDigestService` + MS outbox enqueue, **draft-only** (§6.1), including the update-in-place and vanished-draft-means-sent handling. | api |
| **D — Schedule** | Tue/Fri timer. | scheduler |
| **E — Docs** | `/docs` section + operator guide, per the per-unit pipeline. | web + team |

**Phase B before C is deliberate.** Visibility is half the ask, it carries zero outbound-email risk, and the
worklist is the instrument we use to sanity-check the classifier before any of it reaches the crew.

---

## 10. Acceptance criteria

- [ ] Phase 0 reports real counts from the live Ramp account; blank-memo count is **> 0** (if it is 0, §4.4's
      definition is wrong and v1 stops for a re-spec).
- [ ] A transaction with blank memo and no receipt appears exactly once in `RampTransactionFollowUp`; a repeat
      sweep updates it rather than inserting a duplicate (UNIQUE on `RampTransactionId`).
- [ ] Supplying the memo in Ramp → next sweep stamps `ResolvedAt` and the item leaves the worklist.
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

---

## 11. Risks

| Risk | Handling |
|---|---|
| **Ramp auto-populates `memo` → blank test is inert** | Phase 0 probe answers it before any build. Highest-priority unknown. |
| **Duplicate nagging** — our digest lands beside Ramp's | Accepted by design: the whole premise is that our sender lands where Ramp's doesn't. Revisit if the crew complains. |
| **Crew ignores us too** | `NotifyCount` / `FirstSeenAt` make this *measurable* within two weeks instead of anecdotal. If the loop doesn't move time-to-completion, we learn it from the data. |
| **Chasing a departed employee** | `/users` status check skips `deleted` / `inactive`. |
| **Blank-memo test selects ~75% of transactions** | Plausible per the U-005 probe. A first digest listing dozens of items per cardholder is demoralizing and gets filtered. **Phase 0 sizes it**; if large, consider seeding only from a go-live date forward rather than 90 days of history. |
| **Drafts pile up unsent** | Draft-only keeps a human in the loop, so the loop is only as fast as the sending. `LastDraftedAt` vs `LastNotifiedAt` makes the gap visible rather than invisible — if drafts sit, we'll see it in the data and revisit the `send` rung (§12 #4). |
| **Duplicate drafts / duplicate chasing** | §6.1: update-in-place by verified `DraftMessageId`, and a vanished draft is read as *sent*, never as *missing*. Both are explicit acceptance criteria. |
| **Employee PII in logs** | Explicit §8 rule; check in Pass 1 review. |

---

## 12. Open questions for Gate-1

**Resolved 2026-09-25 (Chris):** sender = `invoice@rogersbuild.com` · Ramp's policy deadline **is** switched on
· every chaser email is a **draft**, v1 never auto-sends.

1. ~~**Ramp API credentials** — how to provision them.~~ **RESOLVED 2026-09-27**, path below. Still **blocks
   Phase 0**, which blocks everything else, until the app is actually created.
   **Ramp dashboard → Company → Developer → "Create New App"** (requires **admin** access to the Ramp
   dashboard). Name it, accept the ToS, then: *Grant types* → "Add new grant type" → **Client Credentials**;
   *Scopes* → "Configure allowed scopes" → **`transactions:read` + `users:read`** only. Client ID and Client
   Secret are then shown to copy. Register the app separately on `demo.ramp.com` for sandbox.
2. **Backfill depth at go-live** — chase 90 days of history, or only transactions from go-live forward? Depends
   entirely on Phase 0's numbers. (§11)
3. **P0-surface classification** — `/em`'s call; §8 recommends running Pass 3 regardless. Note the draft-only
   decision *lowers* the outbound risk materially: nothing leaves the building without a human pressing send.
4. **Does the draft-only gate undercut the goal?** The stated pain is *"the reminder is periodic and manual —
   large delay."* Draft-only keeps a manual step in the loop; what it removes is the *composition* and the
   *remembering*. Worth an explicit revisit after a few weeks: if drafts sit unsent, the bottleneck simply moved
   and the `send` rung becomes the conversation. Flagged, not argued — the metrics in §5 will settle it.
