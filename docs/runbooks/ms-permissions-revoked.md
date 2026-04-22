# Runbook: MS Permissions Revoked

Azure AD admin or automated policy has revoked the build.one app's ability
to call Microsoft Graph APIs. All MS sync is down.

## Symptom

Any of:

- Every Graph call returns HTTP 403 (Forbidden) — not 401 (which triggers
  a token refresh; 403 is "authentication is fine, you're not allowed").
- Repeated `MsAuthError` events in logs with 403 status.
- `ms.retry.non_retryable` events — 403 is correctly classified as not
  retryable (retry won't help), so rows dead-letter after one attempt.
- User reports: completed a bill, no PDF in SharePoint, no row in Excel.
- Token refresh succeeds but ALL subsequent Graph calls 403.
- Graph error messages containing one of:
  - `Forbidden` / `Access is denied`
  - `AADSTS65001` (user has not consented)
  - `AADSTS90094` (admin consent required)
  - `AADSTS50105` (signed-in user not assigned to a role)
  - `AADSTS50020` (user account doesn't exist in tenant)
  - `invalid_grant` with reason `AADSTS700016` (app not found in tenant)

## Severity

| Condition | Severity | Expected response |
|---|---|---|
| All calls 403 | Critical | Immediate; all MS sync is down |
| Single operation 403 (e.g., only SendMail) | High | Specific permission revoked; follow Recovery C |
| Intermittent 403 (some tenants/users) | Warning | Conditional access policy likely; check filtering |

## Background

Microsoft 365 delegated OAuth has three layers of permission that can block
Graph calls independently:

1. **API permissions on the app registration** (Azure portal → Entra ID →
   App registrations → build.one → API permissions). These define what
   scopes the app CAN request. If removed, every call for that scope 403s.
2. **User / admin consent** on those scopes (Azure portal → Entra ID →
   Enterprise applications → build.one → Permissions). Consent can be
   revoked without touching the registration.
3. **Conditional Access policies** (Azure portal → Entra ID → Security →
   Conditional Access). Can block access based on user, location, device
   compliance, or risk level — even if permissions are granted.

The three are independent failure points. A 403 could be any one of them.

Our required permissions (minimum set):
- `Mail.Read` — list inbox (retained for future inbox rebuild)
- `Mail.Send` — email send (Phase 4, deferred)
- `Files.ReadWrite.All` — SharePoint file upload/download
- `Sites.ReadWrite.All` — SharePoint site + drive access
- `User.Read` — the test endpoint (`/me`)

## Immediate action

1. **Confirm the symptom is app-wide.** Hit the test endpoint:

   ```bash
   .venv/bin/python -c "
   from integrations.ms.auth.external.client import test_ms_graph_connection
   print(test_ms_graph_connection())
   "
   ```

   - `status_code=200` → permissions are fine for `/me`; specific scope
     is the issue (Recovery C).
   - `status_code=403` → app-wide revocation (Recovery A or B).
   - `status_code=401` → different runbook ([ms-token-expiration](ms-token-expiration.md)).

2. **Check Azure AD sign-in logs** to see what's being rejected:
   - Azure portal → Entra ID → **Sign-in logs** → filter by app = build.one
   - Last 1 hour. Look for `Result=Failure` rows and read the `Failure reason`.

## Diagnosis

### Step 1 — Is the app registration intact?

Azure portal → Entra ID → **App registrations** → build.one →
**API permissions**. Confirm all required scopes are listed (see Background).

If any are MISSING: permission was removed. Follow Recovery A.

### Step 2 — Is admin consent still granted?

Same page, **API permissions** tab. For each listed permission, check the
**Status** column:
- Green checkmark ("Granted for \<tenant\>") = good.
- Red X or warning = consent revoked. Follow Recovery B.

### Step 3 — Is a conditional access policy blocking?

Azure portal → Entra ID → **Security** → **Conditional Access** → **Policies**.
Look for policies that target:
- **Users**: the build.one service account
- **Cloud apps**: the build.one app registration, or "All cloud apps"
- **Conditions**: especially "Locations" (e.g., blocking non-US regions —
  Azure App Service East US should be allowed) or "Device state"

Common recent changes:
- New MFA requirement on the service account (service accounts can't do MFA
  → everything 403s)
- Block-by-location policy hitting App Service's outbound IP range
- Session control requiring managed browser (breaks for non-browser clients)

### Step 4 — Did the client secret expire?

Different from refresh-token expiry. Azure portal → Entra ID →
**App registrations** → build.one → **Certificates & secrets**. Check the
active client secret's expiry date.

If expired: Graph won't reject with 403 — it'll reject at the token endpoint
(looks like Recovery C in [ms-token-expiration.md](ms-token-expiration.md)).
But it's worth confirming as part of this runbook.

## Common causes

1. **Admin consent revoked** — most common. An admin removes the app from
   Enterprise Applications, or uses PowerShell to revoke grants. Usually
   intentional (security review, compliance audit).
2. **API permissions edited** — a scope was removed from the app registration
   (e.g., `Mail.Send` removed before Phase 4 was ready).
3. **New Conditional Access policy** — the IT team rolled out a policy that
   inadvertently blocks the app.
4. **Service account disabled** — the user account used for delegated auth
   was disabled in Azure AD (e.g., offboarding).
5. **Tenant migration** — rare, but if the tenant was renamed or federated
   differently, existing consent can invalidate.

## Recovery

### Recovery A — Re-add missing API permissions

1. Azure portal → Entra ID → App registrations → build.one → **API permissions**.
2. Click **Add a permission** → **Microsoft Graph** → **Delegated permissions**.
3. Add the missing scopes (see Background for the list).
4. Click **Grant admin consent for \<tenant\>**. Requires tenant admin.
5. Confirm each permission shows a green checkmark under Status.
6. Force a new token refresh so the app picks up the changed scopes:

   ```bash
   .venv/bin/python -c "
   from integrations.ms.auth.business.service import MsAuthService
   MsAuthService().ensure_valid_token(force_refresh=True)
   "
   ```

7. Re-test with the `test_ms_graph_connection` probe from Immediate Action.

### Recovery B — Re-grant admin consent

If Step 2 shows consent was revoked but permissions are still defined:

1. Azure portal → Entra ID → Enterprise applications → build.one.
2. Left nav → **Permissions** → click **Grant admin consent for \<tenant\>**.
3. Browser redirects through Microsoft consent flow. Sign in with a tenant
   admin account.
4. Force-refresh + re-test as in Recovery A.

### Recovery C — Adjust the conditional access policy

If Step 3 identifies a blocking policy:

1. **Ask IT before modifying.** Policies exist for a reason; "fix" may not
   be to disable the policy but to exempt the app or service account.
2. Options in order of preference:
   - **Exempt the build.one app** from the policy (add to "Exclude" list on
     the policy's Cloud apps).
   - **Exempt the service account** from the policy.
   - **Relax the condition** (e.g., allow the App Service outbound IP range
     as a trusted location).
3. Wait 5-10 minutes for policy changes to propagate across Azure AD.
4. Force-refresh + re-test.

### Recovery D — Re-enable the service account

If Step 3 shows the service account is disabled:

1. Azure portal → Entra ID → Users → the build.one service account.
2. Set **Sign-in blocked** to **No**.
3. If the account was deleted, you'll need a full re-OAuth flow with a
   replacement account (see `ms-token-expiration.md` Recovery A).
4. Force-refresh + re-test.

## Verification

After any recovery:

1. Test endpoint succeeds:

   ```bash
   .venv/bin/python -c "
   from integrations.ms.auth.external.client import test_ms_graph_connection
   print(test_ms_graph_connection())
   "
   ```

   Expected: `{"message": "Microsoft Graph API connection successful!", ...}`.

2. Retry dead-letters that failed during the outage:

   ```bash
   .venv/bin/python scripts/retry_ms_outbox_dead_letters.py --apply
   ```

3. Complete a test bill end-to-end. Should drain within ~30s.

4. If any operations remain failing after recovery, log pattern of remaining
   403s and return to Diagnosis.

## Prevention

- **Document the required permission set** with IT. When app permissions
  are audited or CA policies updated, our app should be on the review list.
- **Set a calendar reminder for client secret expiry.** 30 days before
  each expiry, rotate the secret (different runbook: `ms-token-expiration`
  Recovery C).
- **Use a dedicated service account.** Don't use a human's personal
  account for the delegated auth — offboarding that human breaks the
  integration. The service account should have a clear owner, a password
  stored in the team vault, and explicit documentation.
- **Monitor the service account in Azure AD sign-in logs.** Consistent
  Failure rows on the build.one app should trigger an alert — catching
  policy issues within hours, not days.
