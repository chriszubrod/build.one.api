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
# Login to ACR
az acr login --name <acr-name>

# Build and tag
docker build -t <acr-name>.azurecr.io/buildone:latest .

# Push
docker push <acr-name>.azurecr.io/buildone:latest

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
