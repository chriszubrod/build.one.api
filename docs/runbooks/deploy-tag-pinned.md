# Runbook: Deploy succeeded but old code still runs (App Service tag pin)

A code change is pushed to git, `az acr build` succeeds, `az webapp restart`
succeeds, and the health endpoint returns 200 — but the running container
is still on a previous version. New code never goes live; whatever runtime
bug or regression the deploy was meant to fix continues unchanged.

## Symptom

- Recently-deployed code path doesn't execute. Expected log lines / metrics
  / response shapes from the new code are missing.
- A bug the deploy fixed continues to fire (e.g. the same dup-creation,
  same crash, same wrong-data behavior).
- `az acr build` output ends with `Run ID: <id> was successful`, `az webapp
  restart` exit code is 0, curl on `/` returns 200.
- Image manifest in ACR for `:latest` is newer than the build before the
  deploy; ACR shows the new tag attached to the manifest.

## Severity

| Condition | Severity | Expected response |
|-----------|----------|-------------------|
| Active prod bug that the deploy was meant to fix | High | Repoint container tag + restart, then re-verify |
| Code-only deploy (no runtime impact yet) | Medium | Repoint at next maintenance window |
| First-time setup, no live traffic | Low | Repoint, document |

## Diagnosis

Run:

```sh
az webapp config container show --name buildone --resource-group buildone_group \
  --query "[?name=='DOCKER_CUSTOM_IMAGE_NAME'].value" -o tsv
```

Expected output for a healthy setup:

```
DOCKER|buildone-esgaducjg4d3eucf.azurecr.io/buildone:latest
```

If the output ends with a specific commit tag instead of `:latest`
(e.g. `:0d63b19` or `:a70dea8`), the container is **pinned**. Every
`az webapp restart` will relaunch that exact image regardless of what was
pushed to `:latest`.

Cross-check by inspecting which image the container is actually serving:

```sh
az acr manifest list-metadata --registry buildone --name buildone \
  --orderby time_desc --top 5
```

The `:latest` tag should appear on the manifest with the most recent
`createdTime`. If it does, ACR is healthy — the problem is App Service's
pin, not the build.

## Recovery

```sh
# 1. Repoint to :latest. Once done, future az webapp restart commands
#    will pick up new images automatically.
az webapp config container set --name buildone --resource-group buildone_group \
  --container-image-name buildone-esgaducjg4d3eucf.azurecr.io/buildone:latest

# 2. Restart to pull the new :latest image.
az webapp restart --name buildone --resource-group buildone_group

# 3. Wait for the new container to come up (~30-60s) and verify the new
#    code is running by hitting a sentinel endpoint or checking a log
#    line that only the new code emits.
until curl -s -o /dev/null -w "%{http_code}" \
      https://buildone-esgaducjg4d3eucf.eastus-01.azurewebsites.net/ | grep -q 200; do
  sleep 3
done
```

## Verification

Pick a sentinel that distinguishes new code from old. Examples:

- A response-shape change (a new field on a JSON envelope).
- A new endpoint that returns 200 instead of 404.
- A `qbo.ReconciliationIssue` row of a new `DriftType` that only the new
  connector emits (e.g. `duplicate_qbo_customer` from the 2026-05-29
  CustomerProjectConnector fix).
- An App Insights log line containing a string that only appears in the
  new code.

"Curl returned 200" alone is not verification — the old container also
returns 200.

## Prevention

- Default container tag is `:latest`. Don't pin to a specific commit tag
  unless you have a deliberate rollback scenario in mind.
- After any deploy, run the diagnosis command above as part of the
  post-deploy checklist until you're confident the pin is gone.
- If you ever DO need to pin (e.g. to rollback by holding at a known-good
  tag), unpin afterward — set `--container-image-name ...:latest` and
  restart.

## History

- **2026-06-03** — Connector fix (commit `a70dea8`) shipped 2026-05-29
  was silently ignored for 5 days because App Service was pinned to
  `:0d63b19`. The recurring 4-hourly QBO Customer sync created 6 more
  dup `dbo.Project` rows during the window. Repointed to `:latest`,
  re-cleaned the dups, added `UQ_Project_Name_CustomerId_Active` as
  belt-and-suspenders. See `docs/dedupe-project-rows.md` for the
  full incident report.
