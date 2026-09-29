# Runbook: Ramp Receipt/Memo Chaser

The chaser is a **backend scheduled task only — there is no web page.** Two
moving parts, separately gated:

1. **Daily sweep** — `ramp_chaser_sweep` timer (`0 0 11 * * *`, 11:00 UTC) POSTs
   `/api/v1/admin/ramp-chaser/sweep`. Reads Ramp card transactions over a rolling
   `RAMP_CHASER_WINDOW_DAYS` (90) window and upserts one row per still-open item
   into `dbo.RampTransactionFollowUp`. Read-only against Ramp. **No mode gate** —
   a sweep that no-ops leaves the table empty, which is the defect it exists to fix.
2. **Weekly digest** — `POST /api/v1/admin/ramp-chaser/digest`, one Outlook
   **draft** per (cardholder, week) through the MS outbox. Gated by
   `RAMP_CHASER_MODE` **and** `ALLOW_MS_WRITES`. A human reviews and sends every
   draft; `mode="draft"` is hardcoded at the enqueue call site and there is no
   send rung.

**Current state (2026-09-29):** 25 open items across 6 cardholders.
`RAMP_CHASER_MODE=off`, so **zero digests have ever been drafted and no cardholder
has been emailed**; `dbo.RampChaserDigest` has 0 rows. Both timers now exist — the
sweep daily at 11:00 UTC, the digest Tuesdays at 12:03 UTC — but the digest timer
is inert while the mode is `off`, so it costs one no-op HTTP call a week.

> **Completeness is Ramp's own `all_requirements_met_and_approved` flag, never a
> local memo/receipt test.** `NeedsMemo` / `NeedsReceipt` are *descriptive* — they
> say what to ask for, they do not decide membership. A local predicate was measured
> at **101 flagged vs Ramp's 37**, with **64** items Ramp does not require a receipt
> for (median $75, refunds included). Chasing those teaches the crew to ignore our
> mail too, which is the exact failure this feature exists to escape. Any
> "improvement" that reintroduces a local predicate reintroduces that.

Design: [`docs/design/u549-ramp-receipt-memo-chaser.md`](../design/u549-ramp-receipt-memo-chaser.md).

## Setup for every command below

```bash
export API="https://buildone-esgaducjg4d3eucf.eastus-01.azurewebsites.net"
export DRAIN_SECRET='<paste from App Service settings — never commit or echo this>'
```

## Symptom

- The sweep returns `200` but `dbo.RampTransactionFollowUp` is empty, or its open
  row count drops without any cardholder having cleared anything.
- Sweep fails fast with `Ramp credentials are not configured`.
- A cardholder you know is delinquent never appears in the table or in a digest.
- `recipient_changed` is non-zero in a digest summary.
- `stragglers_gone_from_ramp` rises every sweep and never falls.

## Severity

| Condition | Severity | Response |
|---|---|---|
| Sweep erroring (credentials, auth, Ramp outage) | Warning | Within a day — the sweep is idempotent and the next tick recovers; nothing is lost |
| Table empty / row count collapsed | High | Investigate same day — a silently empty table is what U-567 was |
| `recipient_changed` fired | High | Verify the address before the draft is sent |
| A cardholder silently uncounted for weeks | High | Work the decision tree below; a cardholder who vanishes with no trace is the failure mode |
| `stragglers_gone_from_ramp` rising and never falling | Warning | Book work — see Prevention |

## Immediate action

None is needed for a failing sweep: it **fails closed**, writes nothing partial,
and the next daily tick re-runs the whole window. Do not hand-edit
`dbo.RampTransactionFollowUp` to "fix" a count — the next sweep re-derives it.

## Diagnosis

### Step 1 — Run a sweep by hand and read the counters

```bash
curl -s -X POST "$API/api/v1/admin/ramp-chaser/sweep" \
     -H "X-Drain-Secret: $DRAIN_SECRET" | python3 -m json.tool
```

The envelope is `{"status", "job", "duration_ms", "result"}`; `result` is the
sweep's counters. Every one of them is a distinct decision the sweep made:

| counter | meaning |
|---|---|
| `transactions_fetched` | rows returned for the 90-day window |
| `upserted` | open items written/refreshed — this is the population that gets chased |
| `resolved` | items Ramp now reports complete; `ResolvedAt` stamped, chase stops |
| `skipped_approval_only` | flagged by Ramp but **neither** memo nor receipt is actually missing — an approval is pending, which no amount of nagging fixes. Deliberately never chased |
| `refreshed_tracked_approval_only` | an already-tracked row that has decayed into the approval-only shape |
| `skipped_inactive_cardholder` | the Ramp `/users` roster says this cardholder is not active |
| `unroutable_persisted` | the `card_holder.user_id` does not resolve on the roster, or resolves to a user with **no email**. Still persisted, logged and counted — never silently dropped |
| `flag_unknown` | Ramp returned neither true nor false for `all_requirements_met_and_approved` |
| `stragglers_refetched` / `stragglers_gone_from_ramp` | open rows outside the window, re-fetched one GET each; `gone` means the Ramp transaction 404s |

A `duration_ms` in the low hundreds with an error message is a fail-closed, not a
successful empty sweep. Read the message.

### Step 2 — Compare against the table

```sql
-- Open items by cardholder
SELECT CardHolderRampUserId, CardHolderName, COUNT(*) AS open_items,
       MIN(FirstSeenAt) AS chasing_since,
       SUM(CASE WHEN NeedsMemo = 1 AND NeedsReceipt = 1 THEN 1 ELSE 0 END) AS both,
       SUM(CASE WHEN NeedsReceipt = 1 AND NeedsMemo = 0 THEN 1 ELSE 0 END) AS receipt_only,
       SUM(CASE WHEN NeedsMemo = 1 AND NeedsReceipt = 0 THEN 1 ELSE 0 END) AS memo_only
FROM dbo.RampTransactionFollowUp
WHERE ResolvedAt IS NULL
GROUP BY CardHolderRampUserId, CardHolderName
ORDER BY open_items DESC;

-- Total open vs resolved, and how fresh the sweep is
SELECT SUM(CASE WHEN ResolvedAt IS NULL THEN 1 ELSE 0 END) AS open_items,
       SUM(CASE WHEN ResolvedAt IS NOT NULL THEN 1 ELSE 0 END) AS resolved_items,
       MAX(UpdatedAt) AS last_sweep_touch
FROM dbo.RampTransactionFollowUp;
```

`last_sweep_touch` older than ~25 hours means the timer is not firing, not that
the data is quiet.

### Step 3 — Digest state (only relevant once mode is on)

```sql
SELECT WeekOf, CardHolderRampUserId, Outcome, LastDraftedAt,
       LastNotifiedAt, NotifyCount,
       CASE WHEN RecipientHash IS NULL THEN 'unbaselined' ELSE 'baselined' END AS recipient_baseline
FROM dbo.RampChaserDigest
ORDER BY WeekOf DESC, CardHolderRampUserId;
```

Zero rows plus `RAMP_CHASER_MODE=off` is the expected shipped-dark state, **not**
a fault.

## Common causes

1. **`RAMP_CLIENT_ID` / `RAMP_CLIENT_SECRET` not set on App Service** — the most
   common, and what happened on the first deploy. Fails closed in a fraction of a
   second with a clear message (Recovery A).
2. **The sweep has no caller** — U-567: the endpoint existed, the timer did not, so
   the table was never populated. Everything looked healthy at every layer.
3. **Cardholder is legitimately not chaseable** — outside the window, complete per
   Ramp, inactive in Ramp, or has no email on their Ramp user (Recovery C).
4. **`RAMP_CHASER_MODE` still `off`** — the sweep populates the table, the digest
   does nothing. This is the shipped default.
5. **`ALLOW_MS_WRITES` not `true`** — mode is `draft`, but the outbox refuses;
   shows as `refused_ms_writes_gate` in the digest summary.
6. **Scopeless Ramp token** — `scope` is REQUIRED on the token request; omit it and
   Ramp issues a token with no scopes that 403s on everything without erroring.
   The auth client asserts the issued scopes and refuses a scopeless token
   (`Ramp issued a scopeless token — refusing to use it`).
7. **Ramp rate limit / timeout** — 200 requests per 10-second rolling window **per
   source IP**, shared with every other outbound call from that address; a request
   over 60s returns 504. Negligible at ~25 open items, but it is the ceiling.

## Recovery

### Recovery A — `Ramp credentials are not configured`

The full message is
`Ramp credentials are not configured (need ramp_client_id + ramp_client_secret)`.
Two App Service Application Settings are missing:

```bash
az webapp config appsettings list --name buildone --resource-group buildone_group \
  --query "[?starts_with(name,'RAMP_')].{name:name}" -o table

az webapp config appsettings set --name buildone --resource-group buildone_group \
  --settings RAMP_CLIENT_ID="<client id>" RAMP_CLIENT_SECRET="<client secret>"
```

Provision the pair in the **Ramp dashboard → Company → Developer → Create New App**
(requires Ramp admin): grant type **Client Credentials**, scopes **`transactions:read`
+ `users:read`** only. Sandbox is a separate app registered on `demo.ramp.com` and a
separate credential pair; point at it with `RAMP_API_BASE_URL=https://demo-api.ramp.com`.

Setting changes recycle the app. Wait for it to come back, then re-run Step 1.

### Recovery B — Sweep ran but the table is empty / the count dropped

Work these in order; each is a counter from Step 1, so the sweep already told you
which one applies.

1. `transactions_fetched = 0` → the window returned nothing. Auth or connectivity,
   not classification. Re-read the error; check Recovery A.
2. `transactions_fetched > 0`, `upserted = 0` → everything classified out. Sum
   `resolved` + `skipped_approval_only` + `skipped_inactive_cardholder` +
   `flag_unknown`; that sum accounts for the drop. A genuine mass-`resolved` is
   good news, not a defect.
3. A **drop in open rows with `resolved` climbing** is the designed behaviour —
   resolution is observed from Ramp on a later sweep, never inferred from having
   sent an email.
4. Rows never disappear from the table on their own. If a row is gone entirely,
   something deleted it; the sweep only ever upserts and stamps `ResolvedAt`.
5. `flag_unknown > 0` → Ramp returned a non-boolean for
   `all_requirements_met_and_approved` on those transactions. Logged per item as
   `ramp.chaser.flag.unknown` with the transaction id; take it to Ramp, do not
   guess locally.

### Recovery C — A cardholder is not being chased

The decision tree, in the order the sweep applies it. Each branch is a named
counter, so a single sweep response usually identifies the branch outright.

1. **Not in the 90-day window?** Discovery is windowed by
   `RAMP_CHASER_WINDOW_DAYS`. An item already tracked is re-fetched individually
   and never dropped for age (`stragglers_refetched`), but an item that was *never*
   seen and is older than the window will not be discovered. Widen the window and
   re-sweep to pick it up.
2. **Ramp says complete?** `resolved`, or simply never flagged. Check the
   transaction in Ramp: if `all_requirements_met_and_approved` is true, we do not
   chase, by design — even when a receipt looks missing to you. That is the
   over-chase guard, working.
3. **Flagged but nothing actually missing?** `skipped_approval_only` — an approval
   is pending, not a document. Nagging the cardholder cannot clear it. Logged as
   `ramp.chaser.skip.approval_only` with the transaction id.
4. **Inactive in Ramp?** `skipped_inactive_cardholder` — the `/users` roster
   reports a non-active status, so we do not chase someone who has left. Re-activate
   in Ramp if that is wrong.
5. **No email on the Ramp user?** `unroutable_persisted`, logged as
   `ramp.chaser.unroutable.no_email` (or `...missing_user` when the `user_id` does
   not resolve at all). The row **is** persisted — fix the address on the Ramp user
   and the next sweep routes it. Note the address is never stored locally; it is
   resolved from the roster at the moment it is needed.

```sql
-- Is the cardholder tracked at all?
SELECT RampTransactionId, MerchantName, Amount, TransactionDate,
       NeedsMemo, NeedsReceipt, FirstSeenAt, ResolvedAt
FROM dbo.RampTransactionFollowUp
WHERE CardHolderName LIKE '%<name>%'
ORDER BY ResolvedAt, FirstSeenAt;
```

### Recovery D — `recipient_changed` fired

**What it means.** The recipient address comes from the Ramp `/users` API — an
external trust boundary. Each digest row stores an **HMAC fingerprint** of the
address (`shared/encryption.py::blind_index`, keyed off `ENCRYPTION_KEY`); the
address itself is **never persisted**. `recipient_changed` means this cardholder's
address differs from the fingerprint stored at their previous digest. A warning is
raised in the draft body so the reviewer sees it before sending.

Two related outcomes, and the difference matters:

- `recipient_changed` — there was a baseline and it no longer matches. Someone
  changed the address in Ramp.
- `recipient_unverified` — there was **no** baseline to compare against (first
  digest for that cardholder, or a row written before the fingerprint column
  existed). The honest signal, not an alarm.

**What to actually do.** Open the cardholder in Ramp and confirm the current
address is one you recognise for that person; check who changed it and when. If it
is legitimate, nothing to do — the fingerprint re-baselines on this week's row, so
the advisory fires **once**, not every week. If it is not legitimate, do not send
the draft: fix the Ramp user first, then re-run the digest.

⛔ **Do not "fix" this with a domain allow-list.** It was considered and
deliberately rejected: **4 of 10 active cardholders are legitimately off-domain**
(3 gmail, 1 other), so blocking off-domain addresses would silently stop chasing
them — trading a visible warning for an invisible gap.

### Recovery E — Turning the feature on

The sweep is already on. Only the digest is gated.

```bash
# 1. Confirm both gates
az webapp config appsettings list --name buildone --resource-group buildone_group \
  --query "[?name=='RAMP_CHASER_MODE' || name=='ALLOW_MS_WRITES'].{name:name,value:value}" -o table

# 2. Flip the digest on (ALLOW_MS_WRITES must already be true)
az webapp config appsettings set --name buildone --resource-group buildone_group \
  --settings RAMP_CHASER_MODE=draft

# 3. Sweep first so the table is current, THEN draft
curl -s -X POST "$API/api/v1/admin/ramp-chaser/sweep" \
     -H "X-Drain-Secret: $DRAIN_SECRET" | python3 -m json.tool
curl -s -X POST "$API/api/v1/admin/ramp-chaser/digest" \
     -H "X-Drain-Secret: $DRAIN_SECRET" | python3 -m json.tool

# Target a specific week instead (ISO date, interpreted in business_timezone):
curl -s -X POST "$API/api/v1/admin/ramp-chaser/digest?week_of=2026-09-29" \
     -H "X-Drain-Secret: $DRAIN_SECRET" | python3 -m json.tool
```

**What to expect on the first run.** From a cold start (`dbo.RampChaserDigest` at
0 rows):

- One draft per cardholder with open items — today that is up to 5 drafts, never
  one per transaction. Drafts land in the invoice mailbox's Drafts folder
  (`/me` resolves to `invoice@rogersbuild.com`).
- `recipient_unverified` for **every** cardholder, because nobody has a stored
  fingerprint yet. Expected. Not `recipient_changed`.
- Every draft body partitions the cardholder's items into `Receipt & Memo`,
  `Receipt Only`, `Memo Only`; empty sections are omitted.
- **Aged markers (`← N days`) will be sparse or absent at first, and that is
  correct.** The marker is computed from `FirstSeenAt` — *how long we have been
  chasing* — not from transaction age. Backfilled items carry `FirstSeenAt` =
  go-live precisely so aged history cannot manufacture instant escalations.
  ⚠️ Do not "fix" this by switching the marker to transaction age; that collapses
  two deliberately separate clocks.
- **Nothing is sent.** Read every draft, then send by hand.
- Re-running the same week drafts nothing further — `(CardHolderRampUserId, WeekOf)`
  is UNIQUE, and a re-run reports `already_drafted`.

### Recovery F — Stopping it safely

Two independent stops, for two different situations.

**Stop the digest (leave the sweep running, keep the data current):**

```bash
az webapp config appsettings set --name buildone --resource-group buildone_group \
  --settings RAMP_CHASER_MODE=off
```

The digest then returns `{"status": "disabled", ...}` and enqueues nothing.

**Stop the sweep:** comment out the `ramp_chaser_sweep` function in
`build.one.scheduler/function_app.py` and republish:

```bash
func azure functionapp publish build-one-scheduler --python
```

⛔ **Never use the Flex `AzureWebJobs.<funcName>.Disabled` app-setting.** On Flex
Consumption it **de-registers the function permanently** — setting it back to
`false`, or deleting it, does not restore the timer even after a stop+start.
Recovery is a forced republish. See
[deploy-restart-timing.md](deploy-restart-timing.md).

## Verification

1. A hand-run sweep returns `"status": "ok"` and counters that add up:
   `transactions_fetched` ≈ `upserted` + `resolved` + the skip counters.
2. Open-row count matches expectation:

   ```sql
   SELECT COUNT(*) AS open_items,
          COUNT(DISTINCT CardHolderRampUserId) AS cardholders
   FROM dbo.RampTransactionFollowUp WHERE ResolvedAt IS NULL;
   ```

   Baseline 2026-09-29: **25 open items across 6 cardholders.**
3. `MAX(UpdatedAt)` on the table moves within the last 25 hours — the timer is
   firing, not just the endpoint working when you poke it.
4. After a credentials fix, the sweep no longer returns
   `Ramp credentials are not configured` and `transactions_fetched > 0`.
5. After enabling the digest: one `dbo.RampChaserDigest` row per cardholder for
   this `WeekOf`, `Outcome = 'drafted'`, `LastNotifiedAt` still **NULL** (a draft
   nobody sent is a cardholder nobody chased — the stamp only lands on an observed
   send), and the matching drafts visible in the invoice mailbox.

## Prevention

- **Verify the chain end to end, not layer by layer.** U-549's chain broke in three
  independent places at once — code (the sweep had no caller), data (the table had
  0 rows), and config (Ramp credentials were never added to App Service) — and each
  layer looked fine by its own measure. The only check that catches this is
  "is the feature doing its job in production", asked of the whole chain.
- **`stragglers_gone_from_ramp` rising and never falling is the signal to act.**
  `_process_chaser_window` re-fetches every unresolved id not in the window, one GET
  each, every sweep, forever. Two classes never leave that set: items that aged out
  of the window while still open, and items whose Ramp transaction 404s — and the
  404 class **can never self-heal** (the external client returns `None`, so the row
  never enters the window and `mark_resolved` can never fire). Bounding it is a
  behaviour change and is booked separately (U-573). Watch the counter in the
  scheduler's `ramp_chaser_sweep.done` log line.
- **Never reintroduce a local memo/receipt predicate for membership.** Using Ramp's
  own flag inherits the policy already configured in Ramp — thresholds, category
  exemptions, refund handling — and cannot drift from it.
- **Never chase from transaction age.** `FirstSeenAt` and transaction age are two
  different clocks. Collapsing them CCs everyone on day one and makes escalation
  meaningless before it ever means anything.
- **Always send `scope` on the token request** and assert the issued token carries
  it. A scopeless token is not an error — it is a token that 403s on everything,
  which is the silent-failure trap this integration was built to fail loudly on.
