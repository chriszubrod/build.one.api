# Audit: `can_update` vs `can_submit` on prod `RoleModule` grants

**Status:** INVESTIGATION ONLY — read-only prod query, no code/schema/data changed.
**Origin:** Follow-up tracked by `docs/design/bill-edit-resubmit-review.md` §7 decision 2
(2026-09-06): before migrating the review-route family (`/submit/review/*`,
`/advance/review/*`, `/decline/review/*` in `entities/review/api/router.py`) from
gating on `can_update` to gating on the dedicated `can_submit` column, audit which
prod roles would be affected.

## Method

Read-only query against prod `dbo.RoleModule` (joined to `dbo.Role` / `dbo.Module`)
run locally 2026-09-06, for the six modules the review-route family touches:
Bills, Expenses, Bill Credits, Invoices, Contract Labor, Time Tracking.

```sql
SELECT r.Name AS RoleName, m.Name AS ModuleName, rm.CanUpdate, rm.CanSubmit
FROM dbo.RoleModule rm
JOIN dbo.Role r ON r.Id = rm.RoleId
JOIN dbo.Module m ON m.Id = rm.ModuleId
WHERE m.Name IN ('Bills','Expenses','Bill Credits','Invoices','Contract Labor','Time Tracking')
ORDER BY m.Name, r.Name
```

**Route-to-module correction found during the audit:** the Contract Labor review
routes (`/submit|advance|decline/review/contract-labor/{id}`) do **not** gate on
`Modules.CONTRACT_LABOR` — they gate on `Modules.TIME_TRACKING` (comment in
`entities/review/api/router.py` line 342: "the role that handles
TimeTracking-sourced ContractLabor rows"). So for the migration question, the
Contract Labor **module** grants below are gathered for completeness but the row
that actually governs contract-labor review actions today is **Time Tracking**.

## Result: 31 grants, 10 mismatches, 0 in the "safe" direction

A mismatch = `CanUpdate=True` and `CanSubmit=False` — a role that can act on the
route today (gated on `can_update`) and would silently **lose** submit/advance/decline
access if the route switched to `can_submit`. Across all 31 (Role × Module) rows, there
is **not one** case of the opposite direction (`CanSubmit=True`, `CanUpdate=False`) —
migrating can only take access away, never grant it.

| Module | Role | CanUpdate | CanSubmit | Would lose access? |
|---|---|---|---|---|
| Bills | AP Specialist | ✅ | ✅ | — |
| Bills | **Bill Specialist** | ✅ | ❌ | **YES** |
| Bills | Controller | ✅ | ✅ | — |
| Bills | Email Specialist | ❌ | ❌ | — (no access either way) |
| Bills | Owner | ✅ | ✅ | — |
| Bills | **Project Manager** | ✅ | ❌ | **YES** |
| Bills | Tenant Admin | ✅ | ✅ | — |
| Expenses | Controller | ✅ | ✅ | — |
| Expenses | **Expense Specialist** | ✅ | ❌ | **YES** |
| Expenses | Owner | ✅ | ✅ | — |
| Expenses | **Project Manager** | ✅ | ❌ | **YES** |
| Expenses | Tenant Admin | ✅ | ✅ | — |
| Bill Credits | **Bill Credit Specialist** | ✅ | ❌ | **YES** |
| Bill Credits | Controller | ✅ | ✅ | — |
| Bill Credits | Owner | ✅ | ✅ | — |
| Bill Credits | Tenant Admin | ✅ | ✅ | — |
| Invoices | **Invoice Specialist** | ✅ | ❌ | **YES** |
| Invoices | Controller | ✅ | ✅ | — |
| Invoices | Owner | ✅ | ✅ | — |
| Invoices | Tenant Admin | ✅ | ✅ | — |
| Contract Labor (module; not what the route gates on) | **Contract Labor Specialist** | ✅ | ❌ | (n/a — route uses Time Tracking) |
| Contract Labor | Controller | ✅ | ✅ | — |
| Contract Labor | Owner | ✅ | ✅ | — |
| Contract Labor | Tenant Admin | ✅ | ✅ | — |
| **Time Tracking (governs Contract Labor review routes)** | Controller | ❌ | ❌ | — (Controller already can't touch CL review today) |
| Time Tracking | **Field Crew** | ✅ | ❌ | **YES** |
| Time Tracking | **Intern** | ✅ | ❌ | **YES** |
| Time Tracking | Owner | ✅ | ✅ | — |
| Time Tracking | Project Manager | ✅ | ✅ | — |
| Time Tracking | Tenant Admin | ✅ | ✅ | — |
| Time Tracking | **Time Tracking Specialist** | ✅ | ❌ | **YES** |

**8 distinct roles (9 Role×Module grants) would lose review-route access** if the
whole family migrated to `can_submit` as-is (counting only modules the routes
actually gate on — Bills, Expenses, Bill Credits, Invoices, Time Tracking; excluding
the Contract-Labor-module row that isn't load-bearing for any route). Project
Manager appears twice (Bills and Expenses are separate grants):

- Bill Specialist, Project Manager *(Bills)*
- Expense Specialist, Project Manager *(Expenses)*
- Bill Credit Specialist *(Bill Credits)*
- Invoice Specialist *(Invoices)*
- Field Crew, Intern, Time Tracking Specialist *(Time Tracking → Contract Labor review)*

These are not edge cases — they are **the primary "doer" role for each entity**
(the `*Specialist` roles are literally named after the module they submit), plus
Project Manager on Bills/Expenses and the three Time-Tracking field roles. A blind
migration would break submit/advance/decline for the people who use those actions
most.

One incidental finding, not part of the ask but worth flagging separately:
**Controller already cannot submit/advance/decline Contract Labor reviews today**
(`Time Tracking` row: `CanUpdate=False`) even though Controller has full
`can_update` on Bills/Expenses/Bill Credits/Invoices — likely an oversight rather
than a deliberate scope decision, but out of scope for this audit.

## Conclusion / recommendation

**The sets were not identical — migrating the route family to `can_submit` as-is
would have been an access regression.** Per §7 decision 2's framing, presented to
Chris 2026-09-06: backfill `can_submit=True` on the mismatched grants (matching each
role's existing `can_update` value) as its own small, reversible data change, then
migrate the route family to `can_submit` as a separate unit once the sets are
identical (net-zero behavior change) — vs. leaving the whole family on `can_update`
permanently and treating `can_submit` as dead. **Chris chose backfill-then-migrate.**

## Backfill — APPLIED 2026-09-06

Exact statement reviewed and run against prod, scoped by primary key with a guard
so it only touched rows still in the expected state:

```sql
UPDATE dbo.RoleModule
SET CanSubmit = 1
WHERE Id IN (46, 44, 1, 48, 2, 50, 77, 136, 145)
  AND CanUpdate = 1
  AND CanSubmit = 0;
```

Result: **9 rows affected**, verified by re-query — all 9 now show
`CanUpdate=True, CanSubmit=True`. `can_update`/`can_submit` are now identical across
every (Role, Module) grant for Bills, Expenses, Bill Credits, Invoices, and Time
Tracking. **Not touched (out of scope / not route-relevant):** Contract Labor
Specialist's `can_submit` on the **Contract Labor** module itself — that module's
`can_submit` isn't read by any route (the Contract Labor review routes gate on
**Time Tracking**, already backfilled above), so it was left as-is rather than
bundled into a statement Chris hadn't reviewed.

**Next step (separate unit, not yet built):** migrate
`entities/review/api/router.py`'s `/submit|advance|decline/review/*` routes (and
`entities/time_entry/api/router.py`'s `/submit` route, called out in the router
comment as "the same actor-gate") from `can_update` to `can_submit`. Proposed
separately — see chat / next design note — since this repo's convention is to plan
code changes before writing them.

## Related

- [[feedback_builders_never_mutate_prod_data]] — this audit stayed read-only per that rule.
- `docs/design/bill-edit-resubmit-review.md` §7 decision 2 — origin of this follow-up.
- `shared/rbac.py` — `can_submit`/`can_approve`/`can_complete` permission tuple.
- `entities/review/api/router.py` — the route family in question.
