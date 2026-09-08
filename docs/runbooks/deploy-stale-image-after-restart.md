# Runbook: Deploy succeeded but old code still runs (`az webapp restart` served a cached image)

> **Sibling failure mode:** [deploy-tag-pinned.md](deploy-tag-pinned.md) covers the case where
> `DOCKER_CUSTOM_IMAGE_NAME` is pinned to a commit tag. **This runbook is the harder one** — it
> happens when the tag config is *perfectly correct* and the deploy still ships nothing.

## Symptom

`az acr build` succeeds, `:latest` resolves to the new digest in ACR,
`DOCKER_CUSTOM_IMAGE_NAME` reads `…/buildone:latest`, `az webapp restart` returns clean, and
the health endpoint returns 200 — **but the running code is the previous image.**

Every check in the old two-command runbook passes. Nothing looks wrong.

## The tell

**A suspiciously fast startup.** On this Basic-tier single-instance app a genuine image pull
takes appreciably longer than a cached relaunch. On the 2026-09-08 U-410 deploy the container
answered `/openapi.json` with 200 **28 seconds** after `restart` — that was the cache hit.
Treat a sub-30s "up" as evidence the image was NOT pulled.

## Diagnosis

Do not trust status codes. Confirm with a **behavioral sentinel**: a request whose response
differs between the old and new code. Capture its old value *before* deploying.

```sh
API=https://buildone-esgaducjg4d3eucf.eastus-01.azurewebsites.net
# U-410's sentinel: a 7-char password was 422 on the old image, 400 on the new one
curl -s -o /dev/null -w "%{http_code}\n" -X POST "$API/api/v1/mobile/auth/login" \
  -H "Content-Type: application/json" -d '{"username":"zz_probe","password":"abc1234"}'
```

Use a **bogus username** — login attempts consume the per-username and per-IP rate-limit
buckets, and ~6 probes on one username start returning 429 (correct behavior, not a failure).

If the sentinel still reports the old value, the image did not roll.

## Fix

```sh
az webapp stop  --name buildone --resource-group buildone_group
az webapp start --name buildone --resource-group buildone_group
```

This forces the pull without touching any configuration. Re-run the sentinel to confirm the flip.
**This is now the standing deploy recipe** — see `DEPLOY.md`; `restart` is no longer used for
code deploys.

## What NOT to do

**Do not force the pull by repointing the image with `az webapp config container set`.** The
instinct is to pin the sha tag so the config change triggers a pull — but
`DOCKER_REGISTRY_SERVER_PASSWORD` **cannot be read back** via
`az webapp config appsettings list` (returns empty). Rewriting the container config without it
risks writing a blank credential and leaving the app unable to pull *any* image, converting a
stale-code problem into an outage. `stop`+`start` achieves the same re-pull with zero config risk.

If a config rewrite is ever genuinely required, source the password from
`az acr credential show -n buildone` first and pass all four `--docker-registry-server-*` args
together in one command.

## History

- **2026-09-08 (U-410)** — first observed. The login-422 field-user lockout fix built correctly
  (ACR run `caap`, digest `45c15618`→`b00ba671`) and `restart` reported success while prod kept
  serving the old schema. Caught only because a pre-deploy 422 baseline had been captured and
  re-probed. `stop`+`start` resolved it. Chris changed the standing recipe the same morning.
