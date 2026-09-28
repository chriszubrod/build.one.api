# U-556 — UserProject coverage for new projects

**Status:** Part 1 (backfill) **APPLIED to prod 2026-09-28** — narrowed to top-up only, 66 rows.
Part 2 (recurrence) DESIGN — gated, not built.
**Date:** 2026-09-28

## Problem

`dbo.UserProject` is populated by two one-off scripts that only ever covered the
projects existing on the day they ran:

| Script | Covers | Last run |
|---|---|---|
| `scripts/migrations/gap1_agent_user_project_backfill.sql` | agents × projects | before 2026-07-17 |
| `intelligence/persistence/sql/patch.austin_user_projects.sql` | Austin = Owner | before 2026-07-17 |

Every project created afterwards starts with **zero** coverage. Measured live 2026-09-28:

```
 204 | HE15 - 9205 Liza Court       | created 2026-09-08 | agents=0
 203 | WR - 5224 Williamsburg Road  | created 2026-09-03 | agents=0
 202 | CRS - 425 Craighead St       | created 2026-08-26 | agents=0
 201 | MFW - 1802 Morgan Farms Way  | created 2026-07-17 | agents=0
```

Austin was absent from 7 projects. This is how bill 40638 (G&M Plumbing, project
203) produced a review email with an **empty To and Cc line** — unsendable, and
the `Review` row never advanced past "Submitted".

## Why it is load-bearing (not cosmetic)

The tempting read is that agents are service accounts and these rows are
decorative. That is **wrong**, and worth stating precisely because the task
framing raised it as a possibility:

- `intelligence/run.py:59` logs each agent in with its own credentials and
  carries that JWT through `ToolContext.auth_token`. Agent tools call real HTTP
  routes (e.g. `flag_time_entry_for_human_review → POST /api/v1/time-entries/{id}/review-flag`).
- `shared/access.py::_should_bypass()` keys on `IsSystemAdmin` **alone**. Of 14
  agent users only `claude_agent` (33) carries it. The other 13 are fully subject
  to scoping.
- Every route calling `assert_can_access_*` (13 entity services) resolves through
  `dbo.UserCanAccess{Project,Bill,BillCredit,Expense,TimeEntry}` → `dbo.UserProject`.

Measured, non-sysadmin agents on a new project: `UserCanAccessProject(27, 0, 204) = False`.

### The apparent counter-example — CORRECTED 2026-09-28

An earlier revision of this doc claimed `time_tracking_agent` (50) "stamps
`ReviewPriority` successfully despite zero rows, because its routes are
module-gated only". **That was wrong, and the conclusion it supported was wrong.**
The agent is not doing the stamping at all:

- `dbo.AgentSession` records **4** `time_tracking_specialist` runs ever, the last
  on **2026-05-27**. No agent of any kind has run since **2026-06-30**.
- The `ReviewPriority` stamps (189 rows, latest 2026-09-28 17:16) come from the
  **deterministic** sweep in `entities/time_entry/business/auto_submit_service.py`,
  which runs under drain-secret system authz and bypasses scoping entirely.
- `dbo.TimeTrackingOutbox` holds **878 rows, all `pending`**, oldest 2026-06-02 —
  nothing has ever drained. `PAUSE_TIME_TRACKING_AGENT` was specified ON for first
  deploy and is still on, so `process_one` returns `{paused: true}` before
  claiming anything while `submit()` keeps enqueueing. Already tracked as a P2 in
  `TODO.md` (measured 815 rows on 2026-09-09; 878 now).

`contract_labor_agent` (49) likewise carries zero rows and has **zero
`AgentSession` rows, ever** — it is registered and reachable as an orchestrator
delegate (`intelligence/agents/buildone/__init__.py:176`) but has never executed.

The corrected reading does not weaken the case for the backfill — the 12 agents
that *were* topped up are still genuinely scoped, and
`UserCanAccessProject(bill_agent, 204)` was measurably `False`. It changes the
answer for the two deferred agents (below).

### Recommendation on the deferred 278: do not grant

Both agents are dormant — one deliberately paused behind a kill switch since
first deploy, the other never executed. Granting them tenant-wide project access
buys nothing today and widens the blast radius if either is switched on
unexpectedly. When one is genuinely turned on, the Part 2 capability (Option C
below) is the right mechanism anyway: one flag per service account rather than
139 rows each that then need maintaining forever.

## Part 1 — the backfill (BUILT)

`intelligence/persistence/sql/patch.userproject_coverage_gap.sql`.

Reuses `gap1`'s exact `CROSS JOIN` + `WHERE NOT EXISTS` shape rather than
inventing a second pattern — that script already declares itself "re-runnable as
new Projects come online" and simply was never re-run. Adds an Owner section that
**drops the 12-month activity filter** from `patch.austin_user_projects.sql`:
the Owner Cc must exist *before* a project's first bill, and projects 202/204
carry zero activity, so the old filter would leave exactly the gap that caused
the empty Cc.

### Narrowed to a top-up (Chris's call, 2026-09-28)

The unnarrowed script would have written **344** rows, of which **278** were
`contract_labor_agent` (49) and `time_tracking_agent` (50) going 0 → 139. That is
a real access expansion, not a top-up — both are locked out of *every* project,
not just the new ones — so it was split out and deferred.

The narrowing is a **predicate, not a name list**:

```sql
AND EXISTS (SELECT 1 FROM dbo.[UserProject] held WHERE held.[UserId] = u.[Id])
```

Only agents that already hold coverage get topped up. This matters beyond the two
agents at hand: `gap1` grants any `IsAgent = 1` row the whole estate, so an agent
provisioned tomorrow would silently inherit tenant-wide reach the next time
anyone re-ran a backfill to fix an unrelated gap. Coverage should widen by an
explicit decision. A predicate stays true for agents that do not exist yet; a
list of usernames would rot.

The post-flight assertion was changed to match — `NOT IN (0, @Projects)` — since
"every agent covers every project" would otherwise fail the script on exactly the
rows we chose not to write. Zero-coverage agents are listed under a `DEFERRED`
heading so a zero never reads as "done".

### Applied to prod 2026-09-28

```
UserProject 4670 -> 4736   (delta 66 = 59 agent + 7 Owner)
12 agents at 139/139; agents 49 + 50 deferred at 0
Austin 139/139, NULL RoleId = 0
UserCanAccessProject(bill_agent,           201..204) = Y Y Y Y
UserCanAccessProject(orchestrator,         201..204) = Y Y Y Y
UserCanAccessProject(Austin/Owner,         201..204) = Y Y Y Y
UserCanAccessProject(contract_labor_agent, 201..204) = N N N N   (deferred, as intended)
duplicate (UserId, ProjectId) pairs: 0
re-ran the script against prod: count unchanged at 4736 (idempotent)
```

**To grant the deferred 278 later:** drop the `EXISTS` clause and re-run. The
whole script is idempotent, so the rows already applied are a no-op.

## Part 2 — stopping the recurrence (DESIGN, gated)

Three options considered.

### A. Grant at project creation

Hook `ProjectService.create`, which self-documents as the choke point for "ALL
callers (UI, agents, connectors)" and is enforced by
`UQ_Project_Name_CustomerId_Active`. On insert, write the agent rows + the Owner row.

*For:* one place; no schema change; no new timer; correct at t=0 for the review
routing that motivated this.
*Against:* does not self-heal a project that arrives by any path bypassing the
service (a restore, a direct SQL insert, a future bulk import). Grows the matrix
forever — today 14 agents × 139 projects = 1,946 rows whose only information
content is "yes".

### B. Reconcile timer in `build.one.scheduler`

A periodic job re-running the idempotent backfill.

*For:* self-heals regardless of creation path; reuses the Part 1 artifact exactly.
*Against:* a window between project creation and the next tick, during which a
first bill can land and route to nobody — the exact failure already observed.
Still grows the matrix.

### C. Tenant-wide project reach as a capability (recommended)

Give service accounts a narrow flag — e.g. `User.HasTenantWideProjectAccess` —
checked inside the `UserCanAccess*` family alongside the `UserProject` lookup.

*For:* O(1) per agent instead of O(agents × projects); correct for every project
the instant it exists, by construction; no backfill can ever drift again. Strictly
narrower than `IsSystemAdmin`, which also bypasses module checks **and** tenant
membership — making every agent a system admin would be a large over-grant
(`bill_agent` would escape RBAC entirely).
*Against:* touches an authz primitive. Five UDFs change, and `dbo.access_udfs.sql`
is `WITH SCHEMABINDING`. Needs its own Pass 3.

**Recommendation: C for agents, A for the Owner row.** They are different
problems wearing the same symptom. Agent reach is a *capability* of a service
account and belongs in the authz model; the Owner Cc is a *per-project human
assignment* that genuinely belongs in `UserProject` and should be written when the
project is born.

### Why this is gated rather than built

Per `feedback_two_phase_dispatch_design_gated` and
`feedback_adversarial_verify_foundational_primitives`, a change to an
access-control primitive is foundational and takes DESIGN → approval → build, not
a builder's unilateral call. Option C rewrites five security UDFs; shipping that
inside a unit booked as "backfill some rows" is exactly the altitude error the
rule exists to prevent.

## Follow-ups surfaced, not fixed here

- **`time_tracking_agent`'s routes are module-gated only.** `review-flag` and
  `validate-completeness` call no `assert_can_access_*`. Whether that is correct
  for a flag-only observability surface is a real question independent of
  whether the agent runs.
- **The whole agent fleet is dormant** — no `AgentSession` row since 2026-06-30.
  Worth knowing before designing anything that assumes agents are live; a
  scoping gap on an agent path is latent, not active.
- **`update_draft` is inert in prod.** `dbo.CountMsOutboxByEntityAndKind` — the
  idempotency-guard sproc `enqueue_update_draft` calls — does not exist in the
  live database, so the guard throws and the enqueue fails **closed**
  (`idempotency_guard_failed_enqueue_refused`). It is the **only** missing sproc
  of the 13 in `integrations/ms/outbox/sql/ms.outbox.sql`; the other 12 are live.
  The definition is purely additive — `CREATE OR ALTER`, a read-only `COUNT`,
  three params, no table or index change. Belongs to U-549 C1's pending Gate-2,
  not to this unit. Found by hitting it: the bill-40638 review draft had to be
  patched through the Graph client directly because the sanctioned outbox path
  could not enqueue.
