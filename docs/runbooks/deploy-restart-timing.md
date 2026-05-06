# Runbook: Deploy-Restart Timing Race

A new container image is pushed + an `az webapp restart` is issued, then
work that depends on the new code is triggered before App Service has
finished swapping containers. The work runs against the OLD code while
the database is already in the NEW state — sproc renames, schema adds,
or contract changes manifest as runtime errors.

## Symptom

Any of:

- Within ~2 minutes of `az webapp restart`, errors like:
  - `@<ParamName> is not a parameter for procedure <SprocName>` (8145) —
    code calling a sproc with an old parameter name after the sproc was
    renamed.
  - `Invalid column name '<ColumnName>'` (207) — code reading from a
    column that was added in the same deploy but with a Read sproc that
    hasn't been updated yet, or vice-versa.
  - 500 errors on endpoints whose underlying repo/sproc contract changed.
- `AgentSession` rows stuck in `Status='running'` with `Tokens=0`
  on the final turn — the run was killed mid-LLM-call when the worker
  cycled, leaving `turn_repo.complete` and `session_repo.complete`
  unfired.
- Tool calls in the transcript returning `HTTP 500: Internal Server Error`
  in the first ~60s after a deploy, then succeeding on a retry minutes later.

## Severity

| Condition | Severity | Expected response |
|---|---|---|
| Single agent run died mid-flight after a deploy | Warning | Cosmetic; finish manually. Any work that's already persisted (mark_email_outcome, record_extracted_fields, etc.) is intact — only the AgentSession finalization is missing. |
| Multiple agent runs hung within the deploy window | High | Pause the scheduler immediately. Investigate which sprocs/columns changed; verify which version of code is now running before un-pausing. |
| Cron-triggered work (outbox drain, QBO sync) erroring during the window | High | Errors are typically transient and clear after the next tick. Check that the next tick succeeds; only escalate if errors continue past 2 minutes. |

## Background

App Service container deployment from ACR is **not atomic from the caller's
perspective**:

1. `az acr build` pushes the new image to ACR (~60–90s).
2. `az webapp restart` instructs App Service to recycle workers.
3. App Service pulls the new image (cached if same digest, fresh otherwise).
4. New worker(s) start; old worker(s) drain in-flight requests, then exit.
5. The "API is back" probe (`GET /` returning 200) signals that **at least
   one** worker is responsive — but doesn't guarantee that worker is the
   new image, nor that all workers have transitioned.

Result: there's a 30–120s window after `az webapp restart` returns where
the API is reachable but may be serving requests from the OLD image.

When a deploy includes BOTH a SQL migration (sproc rename, column add)
AND a code change (repo/service to match the new sproc/column), the
migration runs ATOMICALLY against the database; the App Service code
change rolls out probabilistically over the swap window. So during the
window, OLD code talks to NEW database — if any contract changed,
that's a runtime error.

The build.one email_specialist agent is particularly sensitive because:
- A run takes ~1–10 minutes (delegation can pause indefinitely on
  bill_specialist's approval gate).
- Mid-run worker recycle kills the asyncio task → final `done` event
  never fires → session_runner never calls `session_repo.complete` →
  the row is stuck `running` forever.
- Tool calls during the window return HTTP 500; the agent sees them as
  errors and may degrade to `needs_review` instead of the right
  classification, or it may succeed via fallback paths despite the
  errors (the cassidy@rogersbuild.com run on 2026-05-06 still
  classified correctly as `internal_reply` despite both
  `search_email_sender_history` calls 500'ing — the agent had enough
  email-signal alone).

## Diagnosis

### Confirm the timing matches a recent restart

Check Function App / scheduler invocation timestamps and App Service
restart times:

```bash
az webapp show --name buildone --resource-group buildone_group \
  --query "{lastModifiedTime: lastModifiedTimeUtc, state: state}" -o table
```

If a hung agent run started within ~2 minutes of the last restart, this
is the most likely cause.

### Confirm which sproc/contract changed

Look at recent SQL migration runs (look at git log on
`entities/<entity>/sql/*.sql` and `intelligence/persistence/sql/*.sql`)
and recent code changes to repos/services that call those sprocs. If the
sproc was renamed or a parameter was renamed, that's the smoking gun.

### Confirm the deployed image is correct now

```bash
az acr repository show-tags --name buildone --repository buildone \
  --orderby time_desc --top 3 -o tsv
```

The first tag (newest) should be the one App Service is pulling
(`buildone:latest` resolves to whatever the most recent build tagged
`latest`). If App Service config shows a fixed tag (not `latest`),
verify it matches what you just built.

### Read App Insights for the window

```bash
az monitor app-insights query --app <app-id-or-name> \
  --analytics-query "exceptions | where timestamp > ago(15m) | order by timestamp desc | take 20" \
  -o json
```

`DatabaseOperationError` mentions of "is not a parameter for procedure"
or "Invalid column name" within the deploy window confirm the diagnosis.

## Recovery

### Recovery A — single agent run hung

Most common case. Persisted state (EmailMessage row, EmailAttachment
typed columns, Bill rows, etc.) is intact — only the AgentSession
finalization is missing.

1. Confirm the run hung mid-finalize: query the session.

   ```sql
   SELECT Status, TerminationReason, TotalInputTokens, CompletedAt
   FROM dbo.AgentSession WHERE PublicId = '<uuid>';
   ```

   Status='running' + Tokens=0 + CompletedAt=NULL = stuck.

2. Verify the work-side state is consistent. For email runs, check
   `EmailMessage.AgentClassification` / `AgentDecidedAction` /
   `ProcessingStatus`. If they match what the agent should have stamped,
   the run effectively succeeded.

3. Manually finalize the session row:

   ```sql
   UPDATE dbo.AgentSession
   SET Status = 'completed',
       TerminationReason = 'end_turn (manual cleanup)',
       CompletedAt = SYSUTCDATETIME()
   WHERE PublicId = '<uuid>' AND Status = 'running';
   ```

4. If the EmailMessage row's classification is wrong or missing,
   re-run the agent on it: temp-bump conflicting `pending` rows to
   `processing` so claim picks the target, then `POST /admin/email/process_one`.

### Recovery B — multi-run damage

If multiple agent runs hung during the window:

1. Pause the Function App's `process_email_inbox` timer to stop new
   runs:

   ```bash
   az functionapp config appsettings set \
     --name build-one-scheduler --resource-group buildone_group \
     --settings "AzureWebJobs.process_email_inbox.Disabled=true"
   ```

2. Wait until App Service is fully on the new image. The simplest signal:
   make a curl call to a known endpoint that demonstrates the new
   behavior (e.g., for a sproc rename, hit the endpoint that calls it
   and confirm 200 instead of 500).

3. Run Recovery A on each hung session row.

4. Identify EmailMessage rows whose agent run failed without persisting
   classification — those have `AgentSessionId` linked but
   `AgentClassification IS NULL` (or workflow status is `processing`
   with no progress). Reset them:

   ```sql
   UPDATE dbo.EmailMessage
   SET ProcessingStatus = 'pending',
       AgentSessionId = NULL,
       LastError = 'reset after deploy-restart timing race; see runbook'
   WHERE Id IN (<list>);
   ```

5. Un-pause the timer:

   ```bash
   az functionapp config appsettings set \
     --name build-one-scheduler --resource-group buildone_group \
     --settings "AzureWebJobs.process_email_inbox.Disabled=false"
   ```

   **⚠ Warning — Flex Consumption gotcha (2026-05-06 incident):** Setting
   `AzureWebJobs.<funcName>.Disabled=true` on a Flex Consumption Function
   App **de-registers the function from the host's discovery list**, and
   setting it back to `false` (or deleting the app-setting) does NOT
   cause the host to re-register it on its own — even after a stop+start
   cycle. The other timers will keep firing; only the disabled-then-
   re-enabled one stays missing. Symptoms: the function name vanishes
   from `az functionapp function list` and from the host's "Found the
   following functions" log message in App Insights traces.

   **Recovery is a fresh code redeploy.** `func azure functionapp
   publish <name>` republishes the source via OneDeploy and forces the
   host to rediscover all `@app.timer_trigger` decorators:

   ```bash
   cd /Users/chris/Applications/build.one/build.one.scheduler
   func azure functionapp publish build-one-scheduler --python
   ```

   After redeploy, verify with both:

   ```bash
   # 1. Function shows up in the registered list
   az functionapp function list --name build-one-scheduler \
     --resource-group buildone_group --query "[].name" -o tsv | grep <funcName>

   # 2. App Insights confirms it's actually firing (within ~1 minute)
   az monitor app-insights query --app <id> \
     --analytics-query "requests | where timestamp > ago(5m) | where name == '<funcName>' | count" -o tsv
   ```

   **Prefer NOT using the `AzureWebJobs.<name>.Disabled` flag at all on
   Flex Consumption.** When you need to pause a function for testing,
   either: (a) deploy a code change with `disabled=True` in the
   decorator, or (b) leave the function on but ignore its outputs
   (often safer because the trigger keeps registering and you don't
   end up in this stuck state). The classic Disabled flag works fine
   on the older Consumption + Premium plans; Flex is the outlier.

### Recovery C — outbox / scheduled work

QBO and MS outbox draining is naturally retry-friendly: failed rows
remain `pending` and the next tick (30s later) picks them up. No manual
intervention needed unless errors persist past 2 minutes.

## Verification

1. The hung session row(s) now show `Status = completed`.
2. The associated EmailMessage row(s) show consistent classification +
   workflow status.
3. A fresh agent run kicked off after the deploy window completes
   cleanly with `TotalInputTokens > 0` and a recorded `CompletedAt`.
4. App Insights shows zero `DatabaseOperationError` events with the
   contract-mismatch signature in the last 5 minutes.

## Prevention

### Pre-flight: don't trigger time-sensitive work for ~2 minutes after restart

The 30–120s window is irreducible without VNet integration and a more
sophisticated traffic-cutover mechanism. The simplest mitigation is
operational discipline.

If you're testing a deploy by manually firing an agent run, **wait at
least 90 seconds after `az webapp restart` returns** before kicking off
the run. A useful manual check:

```bash
# Hit the API root; it returning 200 means at least ONE worker is up.
# Wait an extra 60s after this responds before triggering work — that
# leaves time for the second worker to also transition.
until curl -sS -o /dev/null -w "%{http_code}" \
  "https://buildone-esgaducjg4d3eucf.eastus-01.azurewebsites.net/" \
  --max-time 15 | grep -qE "^(200|404)$"; do sleep 5; done
sleep 60   # cushion for second worker swap
```

### Avoid simultaneous breaking sproc/code renames

Where possible, deploy in two steps:

1. **First deploy**: add the new sproc parameter / column / route as
   ADDITIVE, code calls only the OLD form. Ship + verify.
2. **Second deploy**: switch code to the NEW form. Drop the OLD form
   in a third deploy if needed.

This eliminates the breaking window — at every point during deploys,
both old and new code can talk to the database.

For tightly-coupled changes where additive isn't practical (e.g.,
renaming a parameter on a frequently-called sproc), accept the risk
window but: pause schedulers before deploying, and don't trigger
manual work for 2 minutes after restart.

### Never run SQL migrations BEFORE the matching code is deployed

The opposite ordering causes the same race in reverse: NEW code
running against OLD database. Same mitigation applies.

The safest order is:

1. `az acr build` — image is in ACR but not yet pulled.
2. Run any ADDITIVE SQL migrations (new columns, new sprocs).
3. `az webapp restart` — App Service pulls the new image.
4. Wait 90+ seconds.
5. Run any DESTRUCTIVE SQL migrations (drop old columns/sprocs/params)
   only AFTER you've verified the new code is running everywhere.
