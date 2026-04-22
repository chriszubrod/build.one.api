# Runbook: MS Token Expiration

Recovery for expired or failing-to-refresh Microsoft 365 delegated OAuth tokens
that back the SharePoint, Excel, and Mail integration paths.

## Symptom

Any of:

- App Insights alert fired: "MS refresh token <7 days since last refresh" (critical)
  or "<14 days warning" (warning). (Alert wiring deferred until App Insights
  verification lands — see `project_ms_integration_plan.md`.)
- Repeated log events: `ms.auth.token.refresh.failed`.
- Users report Bill completions succeed in QBO but no PDF lands in SharePoint
  and no row is added to the project Excel workbook.
- Any Graph call returns a `MsAuthError` with "No valid MS auth token available"
  or "Token refresh after 401 did not yield a new token".
- Error log: `Error during token refresh for tenant_id ...: <Microsoft error>`.

## Severity

| Condition | Severity | Expected response |
|---|---|---|
| `days_since_refresh` ≥ 76 (≤14d of the 90d window remaining) | Warning | Handle within 48 hours |
| `days_since_refresh` ≥ 83 (≤7d remaining) | Critical | Handle same day |
| Refresh failing with `invalid_grant` | Critical | Handle ASAP; MS sync is down |
| Access token refresh failing repeatedly for other reasons | Critical | Handle ASAP |

## Background

Microsoft 365 delegated OAuth 2.0 uses two tokens:

- **Access token** — 1 hour lifetime. Refreshed automatically by
  `MsAuthService.ensure_valid_token()` on every Graph call when within 60
  seconds of expiry (`buffer_seconds=60`, Phase 1 default; may move to 300s
  in a Chapter 4 decision to match QBO).
- **Refresh token** — Azure AD default is **90 days of inactivity** before
  the refresh token is invalidated. Every successful refresh rotates the
  refresh token and resets the 90-day clock. Conditional access policies
  can shorten this window; the `ms.Client` configuration does not override
  the default.

The refresh token is the one that typically causes extended outages. Any
active sync workflow — a bill completion, a scheduled pull — resets the
window. Expiration usually indicates a long quiet period or a failure chain
that stopped refreshes from happening.

Token refresh is serialized across processes via `sp_getapplock` keyed on
`ms_auth_refresh:<tenant_id>` (Phase 1, task 1.2). The first caller actually
hits Microsoft's token endpoint; concurrent callers re-read the refreshed
row inside the lock and skip the remote call.

Tokens are encrypted at rest (Fernet, `shared/encryption.py`) using the
shared `ENCRYPTION_KEY` — the same env var QBO uses. `decrypt_if_encrypted`
self-heals legacy plaintext rows on next write.

## Immediate action

If MS sync is actively failing right now and the refresh token hasn't yet
expired, force a refresh immediately:

```bash
curl -sS -o /dev/null -w "%{http_code}\n" https://buildone-esgaducjg4d3eucf.eastus-01.azurewebsites.net/api/v1/ms/auth/refresh/request
```

Or from a shell with app context:

```bash
.venv/bin/python -c "
from integrations.ms.auth.business.service import MsAuthService
auth = MsAuthService().ensure_valid_token(force_refresh=True)
print('OK' if auth and auth.access_token else 'FAILED')
"
```

A successful refresh writes a new row to `ms.Auth` with updated
`ModifiedDatetime`, `AccessToken`, and `RefreshToken`. Skip to **Verification**.

If either command fails with `MsAuthError` about the refresh token (or returns
"FAILED"), proceed to **Diagnosis**.

## Diagnosis

Run these in order until one identifies the problem.

### Step 1 — Inspect current auth state

```sql
SELECT
    TenantId,
    UserId,
    ModifiedDatetime,
    ExpiresIn,
    DATEADD(second, ExpiresIn, ModifiedDatetime) AS access_token_expires_at,
    DATEDIFF(minute, GETUTCDATE(), DATEADD(second, ExpiresIn, ModifiedDatetime)) AS access_minutes_remaining,
    DATEDIFF(day, ModifiedDatetime, GETUTCDATE()) AS days_since_refresh,
    90 - DATEDIFF(day, ModifiedDatetime, GETUTCDATE()) AS days_remaining_estimate
FROM ms.Auth;
```

Read:
- `access_minutes_remaining` — if negative, access token is expired (normal
  if not recently refreshed; automatic on next Graph call).
- `days_since_refresh` — how long since the refresh token last rotated.
  - `>90` means the refresh window has closed; full re-OAuth required (Recovery A).
  - `76-90` → refresh soon (Recovery B).
  - `<76` and refresh is still failing → a different problem (Steps 2–3).

### Step 2 — Check recent refresh attempts in App Insights

(Requires App Insights verification, deferred in Phase 2. Skip if not yet
wired; fall back to App Service log stream for `ms.auth.token.refresh.*`
events.)

```kusto
traces
| where timestamp > ago(2h)
| where customDimensions.event_name startswith "ms.auth.token.refresh"
| project timestamp, customDimensions.event_name, customDimensions.tenant_id, message
| order by timestamp desc
```

Look for `ms.auth.token.refresh.failed`. Common error-message patterns:

- `"invalid_grant"` → the refresh token was revoked, expired, or the user's
  Azure AD password was changed and forced re-consent. See Recovery A.
- `"invalid_client"` → `client_id` / `client_secret` in `ms.Client` is wrong
  or was rotated in Azure AD. See Recovery C.
- `"AADSTS50173"` / `"AADSTS50076"` → conditional access / MFA required.
  The admin needs to re-consent interactively. See Recovery A.
- Network timeout / 5xx → Microsoft was temporarily unavailable. Retry after
  a few minutes; no runbook action needed unless it persists.

### Step 3 — Verify client credentials still match Azure AD

1. Open https://portal.azure.com → Microsoft Entra ID → App registrations.
2. Open the build.one app registration → Certificates & secrets.
3. Compare `client_id` and the active client-secret value against the local DB:

```sql
SELECT ClientId, TenantId, RedirectUri FROM ms.Client;
-- ClientSecret is encrypted; fetch via the app layer for visual comparison:
--   .venv/bin/python -c "from integrations.ms.client.persistence.repo import MsClientRepository; c = MsClientRepository().read_all()[0]; print('client_id:', c.client_id); print('secret (first 8):', (c.client_secret or '')[:8])"
```

If they differ → the secret was rotated. See Recovery C. Secrets in Azure AD
have explicit expiry dates set at creation — check the "Expires" column on
the secrets page in Azure portal.

### Step 4 — Check that ENCRYPTION_KEY is set correctly

Required when Recovery A is about to run. The callback that writes new tokens
uses `encrypt_sensitive_data` from `shared/encryption.py`, which requires
`ENCRYPTION_KEY` in production (the `.env` in local dev falls back to an
ephemeral key).

```bash
# Prod App Service → Configuration → Application Settings → confirm ENCRYPTION_KEY is set.
# Same key that QBO uses; do not set a different value for MS.
```

Absence of the key in prod will surface as `EncryptionKeyError: ENCRYPTION_KEY
is required in production.` in logs during the OAuth callback.

## Common causes

1. **Refresh token window closed from inactivity** — most common at the
   90-day mark. No bill completions, no scheduled pulls → no refresh calls →
   refresh token invalidated. Once the scheduled MS pull (Phase 3) runs,
   this will become rare.
2. **ClientSecret rotated in Azure AD** — someone rotated or regenerated the
   client secret without updating `ms.Client` (Recovery C).
3. **Azure AD Conditional Access / MFA re-prompt** — the policy requires
   interactive re-consent (Recovery A).
4. **User's Azure AD credentials changed** — password change or MFA device
   change can invalidate refresh tokens (Recovery A).
5. **ENCRYPTION_KEY misconfigured** — tokens were written with one key and
   the app is now reading with another. Symptom: base64-looking garbage in
   access_token that Microsoft rejects. Recovery: set the correct
   `ENCRYPTION_KEY` and redo Recovery A to mint fresh tokens.
6. **Microsoft temporarily unavailable** — 5xx from Azure AD token endpoint.
   Retry; no permanent action needed.

## Recovery

### Recovery A — Full re-OAuth (required if refresh token expired or revoked)

Required when Step 1 shows `days_since_refresh > 90`, or Step 2 showed
`invalid_grant`, or an Azure AD policy requires interactive re-consent.

1. Open in a browser (prod):
   `https://buildone-esgaducjg4d3eucf.eastus-01.azurewebsites.net/api/v1/ms/auth/request`
   (This endpoint is RBAC-gated — sign in with a user that has the QBO_SYNC
   module permission, then hit it.)
2. The endpoint redirects to Microsoft's OAuth consent page with PKCE.
   Sign in with the service account for the tenant. Grant consent.
3. Microsoft redirects to `/api/v1/ms/auth/request/callback`, which exchanges
   the auth code for tokens and writes them encrypted to `ms.Auth`.
4. Browser lands on `/integration/list` with a success message.
5. Proceed to **Verification**.

### Recovery B — Proactively refresh before expiry (warning severity)

If `days_since_refresh` is 76-90 and you just want to extend the window:

```bash
curl -sS https://buildone-esgaducjg4d3eucf.eastus-01.azurewebsites.net/api/v1/ms/auth/refresh/request
```

Or via the service directly:

```bash
.venv/bin/python -c "
from integrations.ms.auth.business.service import MsAuthService
MsAuthService().ensure_valid_token(force_refresh=True)
"
```

Both paths call Microsoft's token endpoint with the current refresh token;
Microsoft issues a new access+refresh token pair; `ms.Auth.ModifiedDatetime`
updates, resetting the 90-day window.

Confirm via:

```sql
SELECT ModifiedDatetime, DATEDIFF(day, ModifiedDatetime, GETUTCDATE()) AS days_since_refresh FROM ms.Auth;
```

`days_since_refresh` should now read `0`.

### Recovery C — ClientId / ClientSecret rotated in Azure AD

1. Open Azure portal → Entra ID → App registrations → build.one app →
   Certificates & secrets.
2. Either copy the existing valid client-secret value (if you still have it
   from when it was created — Azure shows it only once), or generate a new
   client secret. Record the new expiry date.
3. Update `ms.Client`:

```bash
.venv/bin/python -c "
from integrations.ms.client.persistence.repo import MsClientRepository
repo = MsClientRepository()
# Use whatever identifier your app uses (inspect repo.read_all() to see schema)
existing = repo.read_all()[0]
repo.update_by_public_id(
    public_id=existing.public_id,
    client_id='<NEW_OR_SAME_CLIENT_ID>',
    tenant_id=existing.tenant_id,
    client_secret='<NEW_CLIENT_SECRET>',
    redirect_uri=existing.redirect_uri,
)
"
```

4. Do a full re-OAuth (Recovery A) — a new client secret invalidates any
   refresh tokens minted under the old secret.

## Verification

After any recovery step:

1. Force a refresh and confirm it succeeds:
   ```bash
   .venv/bin/python -c "
   from integrations.ms.auth.business.service import MsAuthService
   auth = MsAuthService().ensure_valid_token(force_refresh=True)
   print('OK' if auth and auth.access_token else 'FAILED')
   "
   ```
   Expected: `OK`.

2. Verify auth row is fresh:
   ```sql
   SELECT ModifiedDatetime, DATEDIFF(minute, ModifiedDatetime, GETUTCDATE()) AS minutes_since_refresh FROM ms.Auth;
   ```
   Expected: `minutes_since_refresh` < 5.

3. Confirm encryption is active (tokens not stored in plaintext):
   ```sql
   SELECT LEFT(AccessToken, 10) AS token_preview FROM ms.Auth;
   ```
   Expected: starts with `gAAAAAB` (Fernet ciphertext prefix).

4. Do a lightweight Graph call to exercise the full path:
   ```bash
   curl -sS https://buildone-esgaducjg4d3eucf.eastus-01.azurewebsites.net/api/v1/ms/auth/test
   ```
   Expected: `{"message": "Microsoft Graph API connection successful!", ...}`.

5. (When App Insights is verified) confirm recent success:
   ```kusto
   traces
   | where timestamp > ago(10m)
   | where customDimensions.event_name == "ms.auth.token.refresh.completed"
   ```

## Prevention

- **Active polling keeps the refresh window from closing.** Once the
  scheduled MS pull (Phase 3, outbox worker) is running, refresh calls
  happen at least every 30 minutes and the 90-day window never elapses.
  This runbook should become rare after Phase 3.
- **Monitor the 14-day warning.** Don't wait for the 7-day critical — the
  14-day fires with enough runway to handle during business hours.
- **Track Azure AD client-secret expiry dates.** Set a calendar reminder 30
  days before each secret's expiry to generate a replacement and follow
  Recovery C. Secrets have 6–24 month lifetimes in Azure AD; forgetting
  causes Recovery A + Recovery C combined outages.
- **Back up the `ENCRYPTION_KEY`** to the team password manager. Losing it
  means Recovery A for every integration (QBO and MS) every time until
  you restore or regenerate.
- **Never set a different `ENCRYPTION_KEY` for MS vs QBO.** The same key
  encrypts both token stores; splitting them causes one to break silently.
