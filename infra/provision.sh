#!/usr/bin/env bash
# =============================================================================
# build.one — Azure topology provisioning (single source of truth)
# =============================================================================
# Captures the CURRENT production topology as one idempotent `az` script, so the
# environment can be reviewed, diffed, and rebuilt (DR) from one file instead of
# scattered runbook commands.
#
#   STATUS:  Captured from live resources on 2026-06-15. NOT a from-scratch
#            deploy that has been re-run end-to-end — it reflects what exists.
#   SAFETY:  Idempotent. Every block guards on existence, so re-running against
#            the live environment is a no-op for resources that already match.
#   SECRETS: NEVER inlined. Secret values are read from ./infra/.secrets.env
#            (gitignored). See .secrets.env.example for the required var names.
#            Long-term: migrate these to Key Vault references (see README.md).
#   DATA:    This script provisions infrastructure only. It does NOT create the
#            SQL schema (stored procs) or migrate data — see scripts/run_sql.py.
#
# Usage:
#   cp infra/.secrets.env.example infra/.secrets.env   # fill in real values
#   bash infra/provision.sh                            # review output; safe to re-run
#
# What is intentionally NOT managed here:
#   - Secret VALUES (sourced from .secrets.env)
#   - The Azure SQL server itself (lives in a DIFFERENT resource group `owner`,
#     is shared/personal — documented as a reference block, creates commented out)
#   - DNS for the custom domain (app.bld-one.com — registrar-side)
# =============================================================================
set -euo pipefail

# ---- Identity / location ----------------------------------------------------
SUBSCRIPTION="c1767ed0-339f-4d5c-a0da-0d578bdb9972"   # bchristopher_subscription
RG="buildone_group"
LOCATION="eastus"
LOCATION_SWA="eastus2"                                  # Static Web App region

# ---- Resource names (current) ----------------------------------------------
PLAN_B2="ASP-node16ltsgroup-9063"        # B2 Basic linux — hosts API + MCP
PLAN_FLEX="ASP-buildonegroup-b542"       # FC1 Flex Consumption — hosts scheduler
ACR_NAME="buildone"                      # Basic; login server below
ACR_LOGIN="buildone-esgaducjg4d3eucf.azurecr.io"
API_APP="buildone"
MCP_APP="buildone-mcp"
FUNC_APP="build-one-scheduler"
SWA_APP="buildone-web"
STORAGE="stbuildonedevdocs"              # StorageV2, Standard_LRS
KEYVAULT="kv-prod-build-one-east"        # RBAC-mode; currently EMPTY/unused
UAMI_STORAGE="buildone-id-91d2"          # user-assigned MI: Function App -> Storage
API_IMAGE="buildone:latest"
MCP_IMAGE="buildone-mcp:latest"

# ---- Load secrets (names only listed in .secrets.env.example) ---------------
SECRETS_FILE="$(dirname "$0")/.secrets.env"
if [[ -f "$SECRETS_FILE" ]]; then
  set -a; source "$SECRETS_FILE"; set +a
else
  echo "WARN: $SECRETS_FILE not found — app-settings steps that need secrets will be skipped." >&2
  SKIP_SECRETS=1
fi

az account set --subscription "$SUBSCRIPTION"
echo ">> subscription: $SUBSCRIPTION"

# ============================================================================ #
# 1. Resource group
# ============================================================================ #
az group show -n "$RG" >/dev/null 2>&1 \
  || az group create -n "$RG" -l "$LOCATION" -o none
echo ">> resource group: $RG"

# ============================================================================ #
# 2. App Service plans
# ============================================================================ #
# B2 Basic (linux) — shared by the API and MCP web apps.
az appservice plan show -g "$RG" -n "$PLAN_B2" >/dev/null 2>&1 \
  || az appservice plan create -g "$RG" -n "$PLAN_B2" --is-linux --sku B2 -o none
echo ">> plan (B2): $PLAN_B2"

# Flex Consumption (FC1) — the scheduler Function App's plan.
# NOTE: Flex plans are usually created implicitly by `az functionapp create
# --flexconsumption-location`; this guard documents it. (See step 5.)
echo ">> plan (Flex): $PLAN_FLEX  (managed by the Function App in step 5)"

# ============================================================================ #
# 3. Azure Container Registry
# ============================================================================ #
az acr show -n "$ACR_NAME" >/dev/null 2>&1 \
  || az acr create -g "$RG" -n "$ACR_NAME" --sku Basic --admin-enabled true -o none
echo ">> ACR: $ACR_NAME ($ACR_LOGIN)"
# Container images are built/pushed by the deploy flow, not here:
#   az acr build --registry $ACR_NAME --image $API_IMAGE --file Dockerfile .

# ============================================================================ #
# 4. App Services (containers)
# ============================================================================ #
# ---- 4a. API (buildone) ----
az webapp show -g "$RG" -n "$API_APP" >/dev/null 2>&1 \
  || az webapp create -g "$RG" -p "$PLAN_B2" -n "$API_APP" \
       --deployment-container-image-name "$ACR_LOGIN/$API_IMAGE" -o none
az webapp config set -g "$RG" -n "$API_APP" \
  --always-on true --min-tls-version 1.2 --ftps-state FtpsOnly -o none
az webapp update -g "$RG" -n "$API_APP" --https-only true -o none
az webapp config container set -g "$RG" -n "$API_APP" \
  --container-image-name "$ACR_LOGIN/$API_IMAGE" \
  --container-registry-url "https://$ACR_LOGIN" \
  ${SKIP_SECRETS:+} ${DOCKER_REGISTRY_SERVER_PASSWORD:+--container-registry-password "$DOCKER_REGISTRY_SERVER_PASSWORD" --container-registry-user "$ACR_NAME"} -o none
echo ">> webapp: $API_APP (container, alwaysOn, httpsOnly, TLS1.2)"

# API app settings — non-secret values inline; secrets from .secrets.env.
az webapp config appsettings set -g "$RG" -n "$API_APP" -o none --settings \
  ENV=production DEBUG=false HOST=0.0.0.0 PORT=8000 \
  ENABLE_SCHEDULER=false \
  ALLOW_QBO_WRITES=true ALLOW_MS_WRITES=true \
  DB_DRIVER="ODBC Driver 18 for SQL Server" DB_ENCRYPT=yes \
  DB_NAME=buildone DB_SERVER="tcp:bchristopher.database.windows.net,1433" \
  DB_USER="zubrodcb@bchristopher" \
  ALGORITHM=HS256 ITERATIONS=320_000 \
  ACCESS_TOKEN_EXPIRE_SECONDS=3600 REFRESH_TOKEN_EXPIRE_SECONDS=2592000 \
  AZURE_STORAGE_ACCOUNT_NAME="$STORAGE" AZURE_STORAGE_CONTAINER_NAME=attachments \
  azure_document_intelligence_endpoint="https://buildonedocintel.cognitiveservices.azure.com/" \
  invoice_inbox_email="invoice@rogersbuild.com" \
  PAUSE_EMAIL_AGENT=true PAUSE_TIME_TRACKING_AGENT=true \
  CORS_ALLOW_ORIGINS="https://app.bld-one.com,https://witty-plant-04f092d0f.7.azurestaticapps.net,http://localhost:3000,http://127.0.0.1:3000" \
  WEBSITES_ENABLE_APP_SERVICE_STORAGE=false WEBSITES_CONTAINER_START_TIME_LIMIT=600 \
  WEBSITE_HTTPLOGGING_RETENTION_DAYS=3 \
  DOCKER_REGISTRY_SERVER_URL="https://$ACR_LOGIN" DOCKER_REGISTRY_SERVER_USERNAME="$ACR_NAME"
if [[ -z "${SKIP_SECRETS:-}" ]]; then
  az webapp config appsettings set -g "$RG" -n "$API_APP" -o none --settings \
    SECRET_KEY="$SECRET_KEY" ENCRYPTION_KEY="$ENCRYPTION_KEY" \
    OAUTH_STATE_SECRET="$OAUTH_STATE_SECRET" DRAIN_SECRET="$DRAIN_SECRET" \
    DB_PASSWORD="$DB_PASSWORD" AZURE_STORAGE_ACCOUNT_KEY="$AZURE_STORAGE_ACCOUNT_KEY" \
    ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
    azure_document_intelligence_key="$AZURE_DOCUMENT_INTELLIGENCE_KEY" \
    SIGNUP_REGISTRATION_CODE="$SIGNUP_REGISTRATION_CODE" \
    DOCKER_REGISTRY_SERVER_PASSWORD="$DOCKER_REGISTRY_SERVER_PASSWORD" \
    SCOUT_AGENT_USERNAME="$SCOUT_AGENT_USERNAME"   SCOUT_AGENT_PASSWORD="$SCOUT_AGENT_PASSWORD" \
    SUB_COST_CODE_AGENT_USERNAME="$SUB_COST_CODE_AGENT_USERNAME" SUB_COST_CODE_AGENT_PASSWORD="$SUB_COST_CODE_AGENT_PASSWORD" \
    COST_CODE_AGENT_USERNAME="$COST_CODE_AGENT_USERNAME" COST_CODE_AGENT_PASSWORD="$COST_CODE_AGENT_PASSWORD" \
    CUSTOMER_AGENT_USERNAME="$CUSTOMER_AGENT_USERNAME" CUSTOMER_AGENT_PASSWORD="$CUSTOMER_AGENT_PASSWORD" \
    PROJECT_AGENT_USERNAME="$PROJECT_AGENT_USERNAME"   PROJECT_AGENT_PASSWORD="$PROJECT_AGENT_PASSWORD" \
    VENDOR_AGENT_USERNAME="$VENDOR_AGENT_USERNAME"     VENDOR_AGENT_PASSWORD="$VENDOR_AGENT_PASSWORD" \
    BILL_AGENT_USERNAME="$BILL_AGENT_USERNAME"         BILL_AGENT_PASSWORD="$BILL_AGENT_PASSWORD" \
    BILL_CREDIT_AGENT_USERNAME="$BILL_CREDIT_AGENT_USERNAME" BILL_CREDIT_AGENT_PASSWORD="$BILL_CREDIT_AGENT_PASSWORD" \
    EXPENSE_AGENT_USERNAME="$EXPENSE_AGENT_USERNAME"   EXPENSE_AGENT_PASSWORD="$EXPENSE_AGENT_PASSWORD" \
    INVOICE_AGENT_USERNAME="$INVOICE_AGENT_USERNAME"   INVOICE_AGENT_PASSWORD="$INVOICE_AGENT_PASSWORD" \
    EMAIL_AGENT_USERNAME="$EMAIL_AGENT_USERNAME"       EMAIL_AGENT_PASSWORD="$EMAIL_AGENT_PASSWORD" \
    CLAUDE_AGENT_USERNAME="$CLAUDE_AGENT_USERNAME"     CLAUDE_AGENT_PASSWORD="$CLAUDE_AGENT_PASSWORD" \
    TIME_TRACKING_AGENT_USERNAME="$TIME_TRACKING_AGENT_USERNAME" TIME_TRACKING_AGENT_PASSWORD="$TIME_TRACKING_AGENT_PASSWORD"
  echo ">> $API_APP secret settings applied"
fi

# ---- 4b. MCP (buildone-mcp) ----
az webapp show -g "$RG" -n "$MCP_APP" >/dev/null 2>&1 \
  || az webapp create -g "$RG" -p "$PLAN_B2" -n "$MCP_APP" \
       --deployment-container-image-name "$ACR_LOGIN/$MCP_IMAGE" -o none
az webapp config set -g "$RG" -n "$MCP_APP" \
  --always-on true --min-tls-version 1.2 --ftps-state FtpsOnly --http20-enabled true -o none
# DRIFT NOTE: live MCP has https-only = FALSE. The line below ENFORCES https
# (the secure setting). Comment it out if you must preserve current behavior.
az webapp update -g "$RG" -n "$MCP_APP" --https-only true -o none
echo ">> webapp: $MCP_APP (container, alwaysOn, port 8001) [https-only set true — was false in prod]"
az webapp config appsettings set -g "$RG" -n "$MCP_APP" -o none --settings \
  PORT=8001 WEBSITES_PORT=8001 LOG_FORMAT=json LOG_LEVEL=INFO \
  API_BASE_URL="https://buildone-esgaducjg4d3eucf.eastus-01.azurewebsites.net" \
  MCP_ALLOWED_HOSTS="buildone-mcp.azurewebsites.net" \
  WEBSITES_ENABLE_APP_SERVICE_STORAGE=false \
  DOCKER_REGISTRY_SERVER_URL="https://$ACR_LOGIN" DOCKER_REGISTRY_SERVER_USERNAME="$ACR_NAME"
if [[ -z "${SKIP_SECRETS:-}" ]]; then
  az webapp config appsettings set -g "$RG" -n "$MCP_APP" -o none --settings \
    MCP_AUTH_TOKEN="$MCP_AUTH_TOKEN" \
    CLAUDE_AGENT_USERNAME="$CLAUDE_AGENT_USERNAME" CLAUDE_AGENT_PASSWORD="$CLAUDE_AGENT_PASSWORD" \
    DOCKER_REGISTRY_SERVER_PASSWORD="$DOCKER_REGISTRY_SERVER_PASSWORD"
  echo ">> $MCP_APP secret settings applied"
fi

# ============================================================================ #
# 5. Function App (Flex Consumption) — scheduler
# ============================================================================ #
# Storage auth is identity-based (no connection-string secret): the user-assigned
# MI buildone-id-91d2 holds Storage Blob/Queue/Table Data roles on $STORAGE.
az identity show -g "$RG" -n "$UAMI_STORAGE" >/dev/null 2>&1 \
  || az identity create -g "$RG" -n "$UAMI_STORAGE" -o none
UAMI_ID=$(az identity show -g "$RG" -n "$UAMI_STORAGE" --query id -o tsv)
UAMI_CLIENT_ID=$(az identity show -g "$RG" -n "$UAMI_STORAGE" --query clientId -o tsv)

if ! az functionapp show -g "$RG" -n "$FUNC_APP" >/dev/null 2>&1; then
  az functionapp create -g "$RG" -n "$FUNC_APP" \
    --flexconsumption-location "$LOCATION" \
    --runtime python --runtime-version 3.11 --functions-version 4 \
    --storage-account "$STORAGE" -o none
fi
# Identity-based AzureWebJobsStorage + always-ready pin (timer reliability).
az functionapp identity assign -g "$RG" -n "$FUNC_APP" --identities "$UAMI_ID" -o none
az functionapp config appsettings set -g "$RG" -n "$FUNC_APP" -o none --settings \
  API_BASE_URL="https://buildone-esgaducjg4d3eucf.eastus-01.azurewebsites.net" \
  PYTHON_THREADPOOL_THREAD_COUNT=4 REQUEST_TIMEOUT_SECONDS=60 \
  AzureWebJobsStorage__blobServiceUri="https://$STORAGE.blob.core.windows.net" \
  AzureWebJobsStorage__queueServiceUri="https://$STORAGE.queue.core.windows.net" \
  AzureWebJobsStorage__tableServiceUri="https://$STORAGE.table.core.windows.net" \
  AzureWebJobsStorage__credential=managedidentity \
  AzureWebJobsStorage__clientId="$UAMI_CLIENT_ID"
[[ -z "${SKIP_SECRETS:-}" ]] && az functionapp config appsettings set -g "$RG" -n "$FUNC_APP" -o none \
  --settings DRAIN_SECRET="$DRAIN_SECRET"
# always-ready: keep one worker hot so timers never miss ticks (~$5-15/mo).
# Must reference a CURRENT function name (drain_qbo_outbox); the old drain_outbox is gone.
az functionapp scale config always-ready set -g "$RG" -n "$FUNC_APP" \
  --settings function:drain_qbo_outbox=1 -o none || true
echo ">> functionapp: $FUNC_APP (Flex, MI storage, always-ready=1)"

# ============================================================================ #
# 6. Static Web App (React frontend)
# ============================================================================ #
az staticwebapp show -g "$RG" -n "$SWA_APP" >/dev/null 2>&1 \
  || az staticwebapp create -g "$RG" -n "$SWA_APP" -l "$LOCATION_SWA" --sku Free -o none
echo ">> static web app: $SWA_APP (Free, $LOCATION_SWA, custom domain app.bld-one.com)"
# Build + deploy is manual (no CI): npm run build && npx @azure/static-web-apps-cli deploy

# ============================================================================ #
# 7. Storage account (attachments)
# ============================================================================ #
az storage account show -g "$RG" -n "$STORAGE" >/dev/null 2>&1 \
  || az storage account create -g "$RG" -n "$STORAGE" --sku Standard_LRS --kind StorageV2 \
       --https-only true --min-tls-version TLS1_2 --allow-blob-public-access false -o none
# data-protection: blob soft-delete (7d) + container soft-delete ON; versioning OFF.
az storage account blob-service-properties update -g "$RG" --account-name "$STORAGE" \
  --enable-delete-retention true --delete-retention-days 7 \
  --enable-container-delete-retention true --container-delete-retention-days 7 -o none
echo ">> storage: $STORAGE (Standard_LRS, soft-delete 7d) [versioning OFF — consider enabling]"

# ============================================================================ #
# 8. Key Vault (provisioned; currently UNUSED — see README migration plan)
# ============================================================================ #
az keyvault show -n "$KEYVAULT" >/dev/null 2>&1 \
  || az keyvault create -g "$RG" -n "$KEYVAULT" -l "$LOCATION" \
       --enable-rbac-authorization true -o none
echo ">> key vault: $KEYVAULT (RBAC mode; not yet referenced by any app)"

# ============================================================================ #
# 9. Azure SQL — REFERENCE ONLY (lives in resource group `owner`, shared server)
# ============================================================================ #
# The production DB is NOT in $RG. Documented here so the topology is complete.
# Creates are commented out deliberately — do not recreate a shared server.
#
#   server : bchristopher  (rg: owner, region: eastus, v12.0)
#   db     : buildone      (tier Basic, 5 DTU, 2 GB max, LRS backup, 7d PITR)
#   auth   : SQL auth as server admin `zubrodcb` (NO managed identity)
#   network: public network access ENABLED; firewall has many ad-hoc IP rules
#
# REVIEW (see README "Findings"): Basic tier + 2 GB cap is undersized for the
# financial dataset; backup is Local (not geo); the app connects as the server
# admin rather than a least-privilege login.
#
# az sql server create -g owner -n bchristopher -l eastus \
#   --admin-user zubrodcb --admin-password "$DB_PASSWORD"
# az sql db create -g owner -s bchristopher -n buildone --service-objective Basic \
#   --backup-storage-redundancy Local
echo ">> SQL: bchristopher/buildone (rg 'owner') — reference only, not provisioned here"

echo ""
echo "DONE. Review output above. This script is idempotent; re-running is safe."
