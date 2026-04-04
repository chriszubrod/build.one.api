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

# Restart Web App to pull new image
az webapp restart --name <webapp-name> --resource-group <resource-group>
```

## VS Code workflow

1. Open terminal in project root
2. Run the commands above (or add a script)
3. VS Code Azure extension can restart the app; the image push is done via CLI

## Notes

- **Mac ARM (M1/M2/M3):** The Dockerfile targets `linux/amd64` for Azure (and for Microsoft ODBC driver). Builds will run under emulation; first build may take longer.
- First build: ~3–5 min (pip installs, ODBC driver setup)
- Subsequent builds (code-only changes): ~1–2 min (Docker layer cache)
- Ensure all env vars (DB_*, AZURE_*, etc.) are set in Web App configuration
