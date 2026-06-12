# Runbook: Box Auth Reauthorization / CCG Mint Failures

The Box integration authenticates via Client Credentials Grant (CCG): no
refresh tokens, no DB rows. Sixty-minute access tokens are minted on demand
from `box_client_id` / `box_client_secret` / `box_enterprise_id` and cached
in process memory (~59 min with the safety buffer). When the mint POST to
`/oauth2/token` is rejected, **every Box call stops** until the underlying
config/authorization problem is fixed — there is nothing to "refresh".

## Symptom

Any of:

- App Insights: `box.auth.token.mint.failed` events with `outcome=rejected`
  (HTTP 400/401 from the token endpoint).
- `GET /api/v1/box/auth/test` returns an error instead of 200.
- The drain endpoint's result repeatedly carries
  `{"skipped": "auth_unavailable"}` (transient mint failure — timeout/5xx/429)
  or an `error` mentioning `BoxAuthError` (hard rejection).
- `box.Outbox` backlog grows: rows sit in `pending` because the worker's
  pre-pass auth circuit breaker aborts every pass before claiming anything.

## Severity

| Condition | Severity | Expected response |
|---|---|---|
| Transient mint failures (timeout / 5xx / 429) | Informational | Self-heals; the drain skips the pass and retries on the next tick |
| Hard rejection (400/401) sustained >15 min | High | Box pushes are fully stopped; fix same day |
| Backlog also growing past ~50 rows | Critical | Fix now — the outbox absorbs, but Box copies of documents drift behind |

No data is lost in any case — uploads queue in `[box].[Outbox]` and drain in
order once auth recovers.

## Diagnosis

1. **Check config + cache state** (no secrets exposed):

   ```bash
   curl -s "https://<api-host>/api/v1/box/auth/status" | python3 -m json.tool
   ```

   `configured.{client_id,client_secret,enterprise_id}` must all be `true`.
   A `false` means the App Service Application Setting is missing/blank.

2. **Force a live mint:**

   ```bash
   curl -s "https://<api-host>/api/v1/box/auth/test" | python3 -m json.tool
   ```

3. **Read the rejection reason** in App Insights:

   ```kusto
   traces
   | where timestamp > ago(1h)
   | where customDimensions.event_name == "box.auth.token.mint.failed"
   | project timestamp, tostring(customDimensions.outcome), tostring(customDimensions.http_status)
   | order by timestamp desc
   ```

   `outcome=rejected` → credential/authorization problem (this runbook).
   `outcome=timeout/transport/server_error/rate_limited` → transient; wait.

4. The `BoxAuthError` detail carries Box's `error_description` — it usually
   names the cause directly (`invalid client credentials`, `unauthorized`,
   etc.).

## Common causes

1. **Client secret rotated but not propagated.** Box client secrets can be
   reset in the Dev Console; the old secret dies immediately. **We control
   secret rotation** — it only happens when someone resets it in *our* Dev
   Console, so check recent admin activity first.
2. **App reauthorization required after a scope change.** Any change to the
   app's scopes or App Access level in the Dev Console flips the app's
   enterprise authorization to "pending reauthorization". Mints fail (or
   succeed with crippled tokens that 403 everywhere) until an enterprise
   admin reauthorizes the app in the Admin Console.
3. **Enterprise deauthorized the app.** An admin revoked the custom app in
   the Admin Console. Same fix as #2 — reauthorize.
4. **Wrong `box_enterprise_id` or App Access level.** CCG with
   `box_subject_type=enterprise` requires the app's App Access set to
   **App + Enterprise Access** and the correct enterprise id.
5. **Transient** (token endpoint timeout / 5xx / 429) — not this runbook;
   the retry layer + outbox absorb it.

## Recovery

### Recovery A — Secret rotated / wrong secret

We own both sides of this rotation:

1. Box Dev Console (https://app.box.com/developers/console) → our app →
   **Configuration** → *OAuth 2.0 Credentials* → **Fetch Client Secret**
   (or **Reset** if compromised — note reset kills the old secret instantly).
2. Azure Portal → App Service → Configuration → Application settings →
   update `box_client_secret` → Save → restart the app.
3. Do both steps in one sitting — the window between reset and App Service
   update is a full Box outage (absorbed by the outbox).

### Recovery B — Reauthorization required / enterprise deauthorized

1. Box **Admin Console** (https://app.box.com/master) → **Apps** →
   **Custom Apps Manager** → find the app by its Client ID.
2. Open the app's `…` menu → **Authorize App** / **Reauthorize App** →
   confirm the scope list → submit.
3. No code or config change needed — the next mint succeeds immediately.
4. Remember: **every** scope / App Access change in the Dev Console requires
   a fresh Admin Console reauthorization. Both consoles are ours, so this is
   a 2-minute self-service fix, but it is easy to forget the second half.

### Recovery C — Wrong enterprise id / App Access level

1. Dev Console → app → **Configuration** → confirm App Access =
   **App + Enterprise Access** and note the Enterprise ID shown under
   *General Settings*.
2. Confirm App Service setting `box_enterprise_id` matches. Fix + restart.

## Verification

1. `GET /api/v1/box/auth/test` returns 200.
2. `box.auth.token.mint.completed` appears in App Insights.
3. The backlog drains:

   ```sql
   SELECT Status, COUNT(*) AS n FROM box.Outbox GROUP BY Status;
   ```

   `pending` should fall to 0 within a few drain ticks. If any rows
   dead-lettered during the outage, reset them:

   ```bash
   .venv/bin/python scripts/retry_box_outbox_dead_letters.py --apply
   ```

## Prevention

- Treat Dev Console scope edits as a two-step change: edit scopes →
  immediately reauthorize in Admin Console. Never leave it overnight.
- Rotate the client secret only with the App Service settings page already
  open in another tab.
- The CCG model means there is no token to "expire" at rest — if auth broke,
  someone changed something in the Box consoles or in App Service settings.
  Check change history before suspecting Box itself.
