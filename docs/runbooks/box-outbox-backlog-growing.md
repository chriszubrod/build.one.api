# Runbook: Box Outbox Backlog Growing

`[box].[Outbox]` holds rows faster than the worker drains them. Box copies of
attachments/packets fall behind their SharePoint/QBO counterparts. Nothing is
lost — rows drain in order once the cause is fixed — but drift compounds.

## Symptom

- Backlog count growing across drain ticks, OR
- Oldest pending row older than ~10 minutes, OR
- A completed bill/expense/invoice's document never appears in the project's
  Box folder.

## Severity

| Condition | Severity | Response |
|---|---|---|
| Backlog >10 rows, oldest <10 min | Warning | Investigate within the hour |
| Backlog >10 rows, oldest >10 min | Critical | Investigate immediately |
| `dead_letter` count growing | High | Triage via retry script below |

## Diagnosis

### Step 1 — Depth / oldest-pending / status counts

```sql
-- Status counts + age range
SELECT Status, COUNT(*) AS n,
       MIN(CONVERT(VARCHAR(19), CreatedDatetime, 120)) AS oldest,
       MAX(CONVERT(VARCHAR(19), ModifiedDatetime, 120)) AS most_recent
FROM box.Outbox
WHERE Status IN ('pending','failed','in_progress','dead_letter')
GROUP BY Status;

-- Backlog depth + oldest-pending age in seconds
SELECT COUNT(*) AS backlog_depth,
       DATEDIFF(SECOND, MIN(CreatedDatetime), SYSUTCDATETIME()) AS oldest_age_seconds
FROM box.Outbox
WHERE Status IN ('pending','failed');

-- Inspect the head of the queue
SELECT TOP 5 Id, Kind, EntityType, EntityPublicId, Status, Attempts,
       LEFT(LastError, 200) AS LastError,
       CreatedDatetime, ReadyAfter, NextRetryAt
FROM box.Outbox
WHERE Status IN ('pending','failed')
ORDER BY Id;
```

- `ReadyAfter` / `NextRetryAt` in the future → expected backoff/debounce,
  not a stall.
- One `Id` with climbing `Attempts` → poison row (Recovery C).
- Everything `pending`, `Attempts = 0`, nothing moving → drain isn't running
  or is paused (Step 2).

### Step 2 — Is the drain running / paused?

1. Check App Service Application Settings for **`PAUSE_BOX_DRAIN`**. When
   `true`, `POST /api/v1/admin/box/drain` returns `{paused: true}` and does
   nothing — by design (investigation lever). Remove the setting + restart
   to resume.
2. Confirm the scheduler Function App's box drain timer is firing
   (`build.one.scheduler`), or that the in-process fallback is enabled
   (`ENABLE_SCHEDULER=true` registers `box_outbox_drain` every 5s).
3. Force a manual pass and read the result:

   ```bash
   curl -s -X POST "https://<api-host>/api/v1/admin/box/drain" \
        -H "X-Drain-Secret: $DRAIN_SECRET" | python3 -m json.tool
   ```

### Step 3 — Interpret the drain result

The `result` dict tells you which circuit (if any) is open:

- `{"skipped": "auth_unavailable", ...}` — the pre-pass **auth circuit
  breaker**: a CCG token couldn't be minted (transient or rejected). No rows
  were claimed. → [box-auth-reauthorization.md](box-auth-reauthorization.md).
- `box.outbox.visibility_circuit_open` in logs / a pass that aborts early —
  the **visibility-lost circuit**: 3 consecutive rows failed with
  `BoxNotFoundError` / `BoxPermissionError`. Meaning: the service account
  almost certainly lost sight of the target folder(s) — a collaboration was
  removed, a mapped folder was deleted/moved, or the app was reauthorized
  with narrower access. The circuit stops the worker from burning all 5
  attempts on every row in the queue. Fix visibility (re-add the service
  account as collaborator / remap via `POST /api/v1/box/map-project`), then
  reset any dead letters.
- `{"error": "..."}` — the worker import or pass itself blew up (e.g. the
  `[box]` schema isn't deployed). The endpoint isolates this instead of
  500-ing; fix the named error.
- Normal: `{"claimed": N, "done": N, "failed": 0, "dead_lettered": 0}`.
- Also check `ALLOW_BOX_WRITES=true` is set — with the gate closed, enqueue
  refuses new rows and the client refuses uploads, so existing rows fail.

## Common causes

1. **Drain paused / not ticking** — `PAUSE_BOX_DRAIN` left on after an
   investigation, or the scheduler timer isn't firing.
2. **Auth broken** — CCG mint failing; every pass skips. See the auth runbook.
3. **Visibility lost** — folder collaboration removed; circuit opens.
4. **429 storm** — see [box-rate-limit.md](box-rate-limit.md).
5. **Poison row** — one row fails repeatedly; `READPAST` lets others drain,
   but the head-of-queue noise hides real throughput. Dead-letter it.
6. **Worker crashed mid-row** — row stranded `in_progress`.

## Recovery

### Recovery A — Resume a paused / dead drain

Remove `PAUSE_BOX_DRAIN` (or set `false`) in App Service settings + restart.
Verify the Function App timer; force manual passes (Step 2.3) to burn the
backlog faster than the natural cadence.

### Recovery B — Dead-letter triage

```sql
-- What dead-lettered and why (grouped)
SELECT LEFT(LastError, 120) AS error_head, Kind, COUNT(*) AS n
FROM box.Outbox
WHERE Status = 'dead_letter'
GROUP BY LEFT(LastError, 120), Kind
ORDER BY n DESC;
```

Fix the underlying cause, then reset:

```bash
# Dry-run first — always
.venv/bin/python scripts/retry_box_outbox_dead_letters.py

# Apply, optionally filtered by kind
.venv/bin/python scripts/retry_box_outbox_dead_letters.py --apply
.venv/bin/python scripts/retry_box_outbox_dead_letters.py --kind upload_box_file --apply
```

Note: `name collision with foreign file <id>` dead letters are deliberate —
the upload found an existing Box file with the same name that our registry
says belongs to a *different* entity. Do NOT blind-retry these; resolve the
collision in Box (rename/move the foreign file) first.

### Recovery C — Poison row blocking attention

```sql
UPDATE box.Outbox
SET Status = 'dead_letter',
    DeadLetteredAt = SYSUTCDATETIME(),
    LastError = 'Manual dead-letter: blocking backlog, see runbook'
WHERE Id = <the_stuck_id>;
```

### Recovery D — Stranded `in_progress` row

```sql
UPDATE box.Outbox
SET Status = 'failed',
    NextRetryAt = SYSUTCDATETIME(),
    LastError = 'Manual recovery: row was stranded in_progress'
WHERE Status = 'in_progress'
  AND StartedAt < DATEADD(MINUTE, -5, SYSUTCDATETIME());
```

## Verification

```sql
SELECT Status, COUNT(*) AS n FROM box.Outbox GROUP BY Status;
```

`pending`/`failed` trending to 0, `done` rising, no new `dead_letter`. Then
spot-check a recent document actually landed in the mapped project folder
(`GET /api/v1/box/project-folders` lists the mappings).

## Prevention

- Alert on backlog depth (>10) and oldest-pending age (>10 min) *before*
  users notice missing files.
- After any Box Admin Console change (reauthorization, collaboration
  cleanup), run one manual drain and watch the result for the visibility
  circuit.
- Always clear `PAUSE_BOX_DRAIN` at the end of an investigation — paused
  drains are silent by design.
