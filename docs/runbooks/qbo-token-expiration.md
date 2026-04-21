# Runbook: QBO Token Expiration

Recovery for expired or failing-to-refresh QuickBooks Online OAuth tokens.

## Symptom

Any of:

- App Insights alert fired: "QBO refresh token <7 days to expiry" (critical) or
  "<14 days to expiry" (warning).
- Repeated log events: `qbo.auth.token.refresh.failed`.
- Users report QBO sync isn't working; UI shows stale data; Complete Bill returns errors.
- Scheduled sync scripts (`scripts/sync_qbo_*.py`) fail with `QboAuthError`
  ("No valid QBO auth token available" or "Token refresh after 401 did not
  yield a new token").

## Severity

| Condition | Severity | Expected response |
|---|---|---|
| Refresh token <14 days to expiry, not yet refreshed | Warning | Handle within 48 hours |
| Refresh token <7 days to expiry, not yet refreshed | Critical | Handle same day |
| Refresh token already expired | Critical | Handle ASAP; sync is down |
| Access token refresh is failing repeatedly | Critical | Handle ASAP |

## Background

QBO OAuth 2.0 uses two tokens:

- **Access token** — ~1 hour lifetime. Refreshed automatically by
  `QboAuthService.ensure_valid_token` on every request when within 5 minutes
  of expiry (Chapter 4 proactive refresh).
- **Refresh token** — ~100 days lifetime. Used to mint new access tokens via
  Intuit's refresh endpoint. QBO rotates the refresh token ~every time it's
  used. If unused for 100 days, it expires — the app cannot renew unattended
  and a full OAuth re-authorization is required.

The refresh token is the one that typically causes extended outages. The
100-day clock resets every time it's used, so any active sync workflow keeps
it fresh indefinitely. Expiration usually indicates a long quiet period or
a failure chain that stopped refreshes from happening.

## Immediate action

If sync is actively failing right now and the refresh token hasn't yet expired,
force a refresh immediately:

```bash
.venv/bin/python scripts/sync_qbo_term.py
```

Any sync script triggers the refresh path via `ensure_valid_token`. A successful
run will log:

```
Token refreshed successfully for realm_id: <realm>
```

If that message appears, sync is restored. Skip to **Verification**.

If that command itself fails with `QboAuthError` about the refresh token, proceed
to **Diagnosis**.

## Diagnosis

Run these in order until one identifies the problem.

### Step 1 — Inspect current auth state

```sql
SELECT
    RealmId,
    ModifiedDatetime,
    ExpiresIn,
    XRefreshTokenExpiresIn,
    DATEADD(second, ExpiresIn, ModifiedDatetime) AS access_token_expires_at,
    DATEADD(second, XRefreshTokenExpiresIn, ModifiedDatetime) AS refresh_token_expires_at,
    DATEDIFF(minute, GETUTCDATE(), DATEADD(second, ExpiresIn, ModifiedDatetime)) AS access_minutes_remaining,
    DATEDIFF(day, GETUTCDATE(), DATEADD(second, XRefreshTokenExpiresIn, ModifiedDatetime)) AS refresh_days_remaining
FROM qbo.Auth;
```

Read:
- `access_minutes_remaining` — if negative, access token is expired (normal
  if not recently refreshed; automatic).
- `refresh_days_remaining` — if <0, refresh token has expired; full re-OAuth
  is required (see Recovery A). If 0–14, refresh soon (Recovery B). If >14,
  a different failure is happening (see Steps 2-3).

### Step 2 — Check recent refresh attempts in App Insights

```kusto
traces
| where timestamp > ago(2h)
| where customDimensions.event_name startswith "qbo.auth.token.refresh"
| project timestamp, customDimensions.event_name, customDimensions.realm_id, message
| order by timestamp desc
```

Look for `qbo.auth.token.refresh.failed`. The log's `message` field carries the
error text from Intuit's response. Common messages:

- `"invalid_grant"` → refresh token was revoked or expired (see Recovery A).
- `"invalid_client"` → ClientId/ClientSecret in `qbo.Client` is wrong or
  rotated (see Recovery C).
- Network timeout / 5xx → Intuit was temporarily unavailable. Retry after a
  few minutes; no runbook action needed unless it persists.

### Step 3 — Verify ClientSecret still matches Intuit dev portal

1. Open https://developer.intuit.com/ → sign in → App dashboard.
2. Open the build.one app → Keys & credentials.
3. Compare the **Production** ClientId and ClientSecret against the local DB:

```sql
SELECT ClientId FROM qbo.Client;
-- ClientSecret is encrypted at rest since task #5; for visual comparison
-- fetch it via the app layer:
--   .venv/bin/python -c "from integrations.intuit.qbo.client.persistence.repo import QboClientRepository; print(QboClientRepository().read_all()[0].client_secret)"
```

If they differ → ClientSecret was rotated. See Recovery C.

### Step 4 — Check that OAuth state signing secret is set

Only relevant if you're about to re-OAuth (Recovery A). The callback requires
a valid `OAUTH_STATE_SECRET` env var — otherwise the callback returns 401.

```bash
# Prod App Service → Configuration → Application Settings → confirm OAUTH_STATE_SECRET is set.
# Local: confirm .env has OAUTH_STATE_SECRET set.
```

## Common causes

1. **Refresh token naturally approaching expiry** — most common. A period of
   no QBO activity let the 100-day window run down.
2. **Refresh token already expired** — longer quiet period; requires
   re-authorization (Recovery A).
3. **ClientSecret rotated in Intuit dev portal** — someone rotated the secret
   without updating the `qbo.Client` row (Recovery C).
4. **Intuit temporarily unavailable** — 5xx from Intuit's auth endpoint.
   Retry; no permanent action needed.
5. **ENCRYPTION_KEY misconfigured** — tokens were written with one key and
   the app is now trying to read with another. Symptom: tokens read as
   encrypted-looking garbage that fails at Intuit. Recovery: set the correct
   ENCRYPTION_KEY and redo Recovery A to mint fresh tokens.

## Recovery

### Recovery A — Full re-OAuth (required if refresh token expired or revoked)

Required when Step 1 shows `refresh_days_remaining <= 0` or Step 2 showed
`invalid_grant`.

1. Open in a browser (prod): `https://buildone-esgaducjg4d3eucf.eastus-01.azurewebsites.net/api/v1/intuit/qbo/auth/request/connect`
2. Intuit's OAuth consent page opens. Sign in with the QBO account for the
   realm. Click **Connect**.
3. Intuit redirects to the app's callback endpoint. Success message:
   "Oauth 2 Token Endpoint Successful."
4. New tokens are now stored in `qbo.Auth` (encrypted at rest per task #5).
5. Proceed to **Verification**.

### Recovery B — Proactively refresh before expiry (warning severity)

If `refresh_days_remaining` is 7-14 and you just want to extend the window:

```bash
.venv/bin/python scripts/sync_qbo_term.py
```

Any sync call triggers `ensure_valid_token`, which calls Intuit's refresh
endpoint. Intuit issues a new access+refresh token pair. `qbo.Auth.ModifiedDatetime`
updates, resetting the 100-day window.

Confirm via:

```sql
SELECT ModifiedDatetime, DATEDIFF(day, GETUTCDATE(), DATEADD(second, XRefreshTokenExpiresIn, ModifiedDatetime)) AS refresh_days_remaining FROM qbo.Auth;
```

`refresh_days_remaining` should now read ~100.

### Recovery C — ClientId/ClientSecret rotated in Intuit portal

1. Copy the current Production ClientId and ClientSecret from the Intuit dev
   portal.
2. Update `qbo.Client`:

```bash
.venv/bin/python -c "
from integrations.intuit.qbo.client.persistence.repo import QboClientRepository
repo = QboClientRepository()
# Use the app identifier you used originally (likely a fixed string)
repo.update_by_app(app='build.one', client_id='<NEW_CLIENT_ID>', client_secret='<NEW_CLIENT_SECRET>')
"
```

3. Do a full re-OAuth (Recovery A) — new ClientSecret means existing refresh
   tokens were minted by the old secret and Intuit will reject them.

## Verification

After any recovery step:

1. Run a sync call:
   ```bash
   .venv/bin/python scripts/sync_qbo_term.py
   ```
   Expected: completes without `QboAuthError`.

2. Verify auth row is fresh:
   ```sql
   SELECT ModifiedDatetime, DATEDIFF(minute, ModifiedDatetime, GETUTCDATE()) AS minutes_since_refresh FROM qbo.Auth;
   ```
   Expected: `minutes_since_refresh` < 5.

3. Confirm encryption is active (tokens not stored in plaintext):
   ```sql
   SELECT LEFT(AccessToken, 10) AS token_preview FROM qbo.Auth;
   ```
   Expected: starts with `gAAAAAB` (Fernet ciphertext prefix).

4. Confirm App Insights shows a recent success:
   ```kusto
   traces
   | where timestamp > ago(10m)
   | where customDimensions.event_name == "qbo.auth.token.refresh.completed"
   ```

## Prevention

- **Active polling keeps the refresh window from closing.** Once the scheduled
  QBO pull (task #15) is running, refreshes happen automatically every 15
  minutes and the refresh window effectively never closes. This runbook
  should become rare after Phase 3.
- **Monitor the 14-day warning alert.** Don't wait for the 7-day critical —
  the 14-day fires with enough runway to handle during business hours.
- **Never store ClientSecret copies outside the DB and Intuit dev portal.**
  Rotation must always update both in lockstep.
- **Back up the `ENCRYPTION_KEY`** to a password manager. Losing it means
  Recovery A (full re-OAuth + ClientSecret reset) every time until you
  restore or regenerate.
