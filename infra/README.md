# build.one — Azure infrastructure (capture / DR)

This directory codifies the **live Azure topology** as one reviewable, idempotent
`az` script — the single source of truth for "what's deployed and how," and the
DR rebuild path. It replaces the resource-provisioning commands previously
scattered across `DEPLOY.md`, `build.one.web/docs/deploy.md`, and
`build.one.scheduler/README.md`.

> Captured from the live environment on **2026-06-15**. Reflects what exists; not
> yet re-run end-to-end as a from-scratch deploy.

## Files

| File | Purpose |
|------|---------|
| `provision.sh` | Idempotent `az` script that provisions/configures the whole topology |
| `.secrets.env.example` | Template of secret env-var names `provision.sh` needs (copy → `.secrets.env`, gitignored) |
| `README.md` | This file |

## Usage

```bash
cp infra/.secrets.env.example infra/.secrets.env   # fill in real values
bash infra/provision.sh                            # safe to re-run; idempotent
```

`provision.sh` does **infrastructure only** — it does not create the SQL schema
(use `scripts/run_sql.py`) or migrate data.

## Current topology (subscription `bchristopher_subscription`, region East US)

| Resource | Name | Key facts |
|---|---|---|
| Resource group | `buildone_group` | all but SQL |
| App Service plan | `ASP-node16ltsgroup-9063` | **B2 Basic** linux — hosts API **and** MCP |
| App Service plan | `ASP-buildonegroup-b542` | **FC1** Flex Consumption — scheduler |
| Container registry | `buildone` | Basic, admin-enabled; `buildone-esgaducjg4d3eucf.azurecr.io` |
| App Service (API) | `buildone` | container `buildone:latest`, AlwaysOn, https-only, TLS1.2 |
| App Service (MCP) | `buildone-mcp` | container `buildone-mcp:latest`, port 8001, AlwaysOn |
| Function App | `build-one-scheduler` | Flex, Python 3.11, MI storage, always-ready=`drain_qbo_outbox`:1 |
| Static Web App | `buildone-web` | Free, East US 2, custom domain `app.bld-one.com` |
| Storage | `stbuildonedevdocs` | StorageV2, **Standard_LRS**, soft-delete 7d, versioning **off** |
| Key Vault | `kv-prod-build-one-east` | RBAC mode, **empty/unused** |
| User MI | `buildone-id-91d2` | Function App → Storage (identity-based AzureWebJobsStorage) |
| User MI | `oidc-msi-a7e7` | purpose unconfirmed — audit |
| **SQL (in RG `owner`)** | server `bchristopher` / db `buildone` | **Basic tier, 5 DTU, 2 GB cap, LRS backup, 7d PITR** |

## Findings surfaced by the capture (not visible from code)

Ordered roughly by impact:

1. **SQL DB is Basic tier (5 DTU / 2 GB max size).** For the financial dataset
   (~18K bills + line items + agent/workflow/email audit tables), the **2 GB
   ceiling is a hard risk** — at the cap the DB goes read-only. 5 DTU also
   throttles easily, compounding the missing transient-retry on the CRUD path.
   Backup redundancy is **Local** (lost in a regional disaster). → Consider S0/S1
   (or vCore serverless) + geo-redundant backup; check current size now.
2. **App connects as the SQL server admin** (`zubrodcb@bchristopher`), and the
   server has **public network access on** with a sprawling ad-hoc firewall
   allowlist (incl. transient `claude-*` rules + `AllowAllWindowsAzureIps`).
   → Create a least-privilege app login; prune firewall rules; consider Entra auth.
3. **SQL server lives in a different resource group (`owner`), shared/personal.**
   Cross-RG dependency the runbooks never noted. → At minimum document the
   ownership/backup contract.
4. **MCP web app has `https-only = false`** (API is true). → Enable https-only
   (`provision.sh` sets it; comment out that line if intentional).
5. **Key Vault exists but is unused** — every secret is a plaintext app setting.
   See migration plan below.
6. **Likely-orphaned resources (cost/cleanup):**
   - `buildonesearch` (AI Search, **Free** SKU) — code removed AI Search.
   - `buildonefoundry` Azure OpenAI deployments: `gpt-4o-mini` (likely unused —
     intelligence layer uses Anthropic) and `text-embedding-3-small` (**may still
     be live** — the API Dockerfile comment says "Azure embeddings used"; verify
     before deleting). Stale `AZURE_OPENAI_*` settings remain on the API.
   - `build-one-scheduler202604222224` App Insights component + its "Failure
     Anomalies" rule — leftover from a redeploy.
7. **Storage blob versioning is off** (soft-delete IS on, 7d) — versioning would
   cover the known orphan-blob / accidental-overwrite exposure.

## Key Vault migration (when ready)

KV `kv-prod-build-one-east` is already provisioned in RBAC mode. Lowest-effort
path, zero code change: store the highest-blast-radius secrets and reference them
from app settings via the existing managed identity:

```
ENCRYPTION_KEY = @Microsoft.KeyVault(SecretUri=https://kv-prod-build-one-east.vault.azure.net/secrets/encryption-key/)
```

Start with `ENCRYPTION_KEY` (the Fernet master key), then `DB_PASSWORD`,
`DRAIN_SECRET`, `OAUTH_STATE_SECRET`, `ANTHROPIC_API_KEY`. Secrets then stop
appearing in `az webapp config appsettings list` and gain access auditing.

## If you later want drift detection (Bicep)

This script is imperative (matches the current `az` workflow) and gives you a
single source of truth + DR path, but it can't *detect* portal drift. If that
becomes valuable, the same resources map 1:1 to a Bicep module, and
`what-if` / `az deployment group what-if` would then report drift before applying.
