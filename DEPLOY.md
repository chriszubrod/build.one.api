# Docker + ACR Deployment

Deploy to Azure App Service using a container image. Build happens locally; Azure runs the pre-built image (no Oryx build).

## Prerequisites

- Docker installed locally
- Azure CLI (`az`) logged in
- Azure Container Registry (ACR)
- Web App configured for containers

## One-time setup

### 1. Create Azure Container Registry (if needed)

```bash
az acr create --resource-group <resource-group> --name <acr-name> --sku Basic
```

### 2. Enable admin user (or use managed identity)

```bash
az acr update -n <acr-name> --admin-enabled true
az acr credential show -n <acr-name>  # Get username/password
```

### 3. Configure Web App to use container

Azure Portal → Web App → **Deployment Center** → **Container settings**:
- Source: Azure Container Registry
- Registry: your ACR
- Image: `buildone` (or your image name)
- Tag: `latest`

Or via CLI:

```bash
az webapp config container set \
  --name <webapp-name> \
  --resource-group <resource-group> \
  --docker-custom-image-name <acr-name>.azurecr.io/buildone:latest \
  --docker-registry-server-url https://<acr-name>.azurecr.io \
  --docker-registry-server-user <acr-username> \
  --docker-registry-server-password <acr-password>
```

## Deploy (each release)

```bash
# ACR builds server-side — no local Docker, no `az acr login`, no `docker push`.
# Name the build context explicitly; never a bare `.`. The positional argument is
# tarred and uploaded, so a wrong CWD silently ships the wrong tree (and, once the
# repos are consolidated, the whole monorepo). ⛔ Pass NO `--file`: az joins
# `Dockerfile` to the named context itself (azure-cli `acr/build.py:57`), so the
# two can never disagree. A `--file` you DO pass is resolved against the CURRENT
# DIRECTORY and checked there (`build.py:60`), which puts back the exact CWD
# dependence the explicit context removes — it failed outright on the 2026-11-12
# deploy. `--help`'s "relative to the context" holds only for a REMOTE (git-URL)
# source location. ALWAYS pass both tags — :latest and the sha
# — including on a rollback build, or :latest matches no sha and traceability dies.
REPO=/Users/chris/Applications/build.one/build.one.api   # or .worktrees/<unit>
git -C "$REPO" fetch origin && git -C "$REPO" merge --ff-only origin/master
SHA=$(git -C "$REPO" rev-parse --short=8 HEAD)
echo "shipping $SHA"   # assert this is the commit you mean BEFORE building
az acr build --registry buildone \
  --image buildone:latest --image "buildone:$SHA" "$REPO"

# Force the Web App to pull the new image.
# Use stop + start, NOT `az webapp restart` — restart can relaunch the CACHED
# image and still report success (it did on the 2026-09-08 U-410 deploy: correct
# :latest tag, digest flipped in ACR, container answered 200 in 28s, old code
# still serving). A sub-30s "up" is a cache-hit tell, not a fast deploy.
az webapp stop  --name <webapp-name> --resource-group <resource-group>
az webapp start --name <webapp-name> --resource-group <resource-group>
```

**Verify with a behavioral sentinel, never a bare 200** — the old image returns 200 too.
Capture a request whose response differs between old and new code *before* deploying, and
re-run it after. Also confirm `:latest` and the short-sha tag resolve to the same digest.

**Do not** try to force a pull with `az webapp config container set`:
`DOCKER_REGISTRY_SERVER_PASSWORD` cannot be read back via `az webapp config appsettings list`,
so repointing the image risks writing a blank credential and leaving the app unable to pull.

## VS Code workflow

1. Open terminal in project root
2. Run the commands above (or add a script)
3. VS Code Azure extension's "restart" is NOT sufficient to pick up a new image — use the stop/start above; the image push is done via CLI

## Notes

- **Mac ARM (M1/M2/M3):** The Dockerfile targets `linux/amd64` for Azure (and for Microsoft ODBC driver). Builds will run under emulation; first build may take longer.
- First build: ~3–5 min (pip installs, ODBC driver setup)
- Subsequent builds (code-only changes): ~1–2 min (Docker layer cache)
- Ensure all env vars (DB_*, AZURE_*, etc.) are set in Web App configuration
