# Integration: TimeTracking → ContractLabor → Bill

Handoff doc for a multi-day integration project. Drafted 2026-05-27 by a prior research session. Read this first; the goal, current-state map, gap list, decisions-needed list, and proposed phase order are all here so a fresh session can pick up without re-exploring.

## Goal

iOS workers clock in/out (TimeEntry + TimeLog rows). Office reviews and approves. System then aggregates approved TimeEntries into ContractLabor records, which generate Bills (with reportlab PDF + Attachment + BillLineItemAttachment) via the existing bill-generation path.

End-to-end flow:

```
iOS clock in/out
    → TimeEntry + TimeLog rows (status=draft)
    → web review: Submit (draft→submitted)
    → web review: Approve (submitted→approved)
    → aggregation step  ◄── new code, the missing piece
    → ContractLabor + ContractLaborLineItem rows (status=ready)
    → existing bill_service.generate_bills_for_vendor()
    → Bill + BillLineItem + BillLineItemAttachment + Attachment (PDF)
    → ContractLabor.Status=billed + TimeEntryStatus row (approved→billed)
```

## Current state (verified 2026-05-27)

### ContractLabor — feature-complete as Excel-import-to-bill pipeline

- Schema + sprocs in `build.one.api/entities/contract_labor/sql/`.
- Bill generation in `entities/contract_labor/business/bill_service.py:134-432` produces Bill + BillLineItems (one per SubCostCode) + reportlab PDF + Attachment + BillLineItemAttachment, then flips `Status` `pending_review → ready → billed`.
- Schema carries ~10 Excel-import-specific fields meaningless for TimeTracking-sourced rows: `EmployeeName` raw, `JobName`, `TimeIn`/`TimeOut` as NVARCHAR, `BreakTime`, `RegularHours`/`OvertimeHours`, `ImportBatchId`, `SourceFile`, `SourceRow`.
- `VENDOR_CONFIG` is a hardcoded dict in `bill_service.py:33-76` keyed by raw `EmployeeName` string with `rate` + `markup` + address.

### TimeTracking — ~50% implemented

- Schema: `dbo.TimeEntry` (parent, `UserId` + `WorkDate`) + `dbo.TimeLog` (child, `ClockIn`/`ClockOut`/`Duration` + `ProjectId` + GPS) + `dbo.TimeEntryStatus` (insert-only audit, current state = MAX(CreatedDatetime)).
- `ProjectId` was migrated from TimeEntry to TimeLog so a worker can switch projects within a day.
- Status workflow: `draft → submitted → approved → [billed]`. The `approved → billed` transition is in `VALID_TRANSITIONS` (`entities/time_entry/business/service.py:18-24`) but has **NO service method, NO API endpoint, NO caller**. Submit/approve/reject all work.
- iOS clocks in/out only — UI never produces a status other than `draft`. Web review surface at `/time-entry/:id` exposes Submit / Approve / Reject buttons.
- No `Worker → Vendor` binding anywhere. `User` has no `VendorId` column. `ContractLabor.EmployeeName` matches a raw string against `VENDOR_CONFIG`.
- No aggregation sproc for `TimeLog → ContractLaborLineItem` shape.
- No billing-period concept on TimeEntry.

### Bill — universal create requirement

- Every `POST /api/v1/create/bill` requires `attachment_public_id` referencing an existing Attachment row. Today the ContractLabor bill_service uploads the reportlab PDF first, creates the Attachment, then passes its public_id into the Bill create. Same pattern stays for TimeTracking-sourced bills.

## The hard gaps

1. **Worker → Vendor mapping** — nothing bridges `TimeEntry.UserId → Vendor.Id`.
2. **Aggregation sproc** — no code aggregates TimeLogs by Worker × Project × Date (or × BillingPeriod) into the ContractLaborLineItem shape.
3. **`approved → billed` invocation** — even with aggregation, there's no trigger that creates ContractLabor from approved TimeEntries.
4. **Schema overlap** — ContractLabor has ~10 Excel-import fields that are meaningless for TimeTracking-sourced rows. Decision: leave nullable + skip on TT-source rows, or split tables.
5. **`VENDOR_CONFIG` hardcoded dict** — needs to become DB-driven so rates change without a deploy.
6. **Backfill** — scope for old ContractLabor rows + external data (TBD source).

## Decisions needed before code

Ask the user these 8 before sketching schema:

1. **Worker → Vendor mapping shape**: `User.VendorId` column, separate `User.ContractLaborVendorId`, or `UserVendor` join? Most workers map 1:1 to a Vendor, so probably the first.
2. **Aggregation granularity**: one ContractLaborLineItem per TimeLog, per (Worker × Project × Day), or per (Worker × Project × Period)?
3. **Billing period**: weekly Mon-Sun? bi-weekly? Defined in Settings, per-Vendor, or hardcoded?
4. **Rate/markup lock-in point**: at TimeLog creation (locked forever to that hour), ContractLabor aggregation (locked at week's end), or Bill generation (latest rate wins)?
5. **Trigger style**: scheduler ("every Friday 5pm aggregate the week"), manual web button on Contract Labor Bills page, or per-entry on `approved` transition?
6. **Excel import path**: keep parallel (some vendors still arrive that way?), deprecate, or migrate the importer to write TimeEntry/TimeLog instead?
7. **VENDOR_CONFIG migration**: rates onto `Vendor` table directly, or new `VendorContractRate` history table with effective dates?
8. **Backfill scope**: how many years of existing ContractLabor rows? What is "external data" the user mentioned — QBO BillableTime entries, third-party timesheets, paper records?

## Probable order of work (after decisions are locked)

### Phase 1 — Worker→Vendor binding

- Schema migration: `User.VendorId BIGINT NULL FK Vendor.Id` (or per decision #1).
- Populate via SQL for existing ~5-7 workers using current `VENDOR_CONFIG` keys.
- Expose on User Profile React page (admin-only edit).
- Surface on `/auth/me` payload if needed by iOS.

### Phase 2 — Move VENDOR_CONFIG to DB

- Add `Vendor.HourlyRate` + `Vendor.Markup` columns, OR new `VendorContractRate` table with `(VendorId, EffectiveFrom, EffectiveTo, Rate, Markup)` if rates change over time (decision #7).
- Backfill from current `VENDOR_CONFIG` dict.
- Update `bill_service._get_rate_for_vendor()` to read from DB.
- Keep dict as fallback or delete entirely.

### Phase 3 — Aggregation

- New sproc: `AggregateApprovedTimeEntriesIntoContractLabor(@vendor_id, @billing_period_start, @billing_period_end)`.
- Service method: `TimeEntryService.aggregate_into_contract_labor(...)` or `ContractLaborService.aggregate_from_time_entries(...)` (whichever owns the boundary).
- Walks approved TimeEntries × their TimeLogs, groups by chosen granularity (decision #2), creates ContractLabor + ContractLaborLineItem rows with `SourceTimeEntryId` / `SourceTimeLogId` FK.
- Inserts TimeEntryStatus row `billed` on success — closes the unreachable transition.

### Phase 4 — Wire the trigger

- Per decision #5:
  - **Scheduler**: add timer to `build.one.scheduler` (e.g., Friday 5pm) + admin endpoint `POST /api/v1/admin/contract-labor/aggregate-period`.
  - **Manual**: button on React `ContractLaborBills.tsx` page that calls the endpoint for the chosen `(vendor_id, billing_period_start)`.
  - **Per-entry**: hook the aggregation in `TimeEntryService.approve()` (probably async via outbox so the API stays fast).

### Phase 5 — Schema rationalization

- Add `ContractLabor.SourceTimeEntryId BIGINT NULL FK` + `ContractLaborLineItem.SourceTimeLogId BIGINT NULL FK` so lineage is queryable.
- Decide whether Excel-import-specific ContractLabor columns stay nullable forever (cheap, ugly) or split (expensive, clean).

### Phase 6 — Backfill

- Old ContractLabor rows: probably no-op since they keep working.
- External data import: plan + script TBD (depends on decision #8).
- One-time pass to set `User.VendorId` for all current workers.

### Phase 7 — UI updates

- React Contract Labor pages handle TT-sourced rows (show SourceTimeLog references; hide Excel-only fields when row is TT-sourced).
- React TimeEntry pages show "Billed via ContractLabor #X" once status = billed.
- iOS optional: surface "Billed" state on TodayScreen / past-day list (decision call — may add visual clutter).

## Key files to touch (in approximate order)

| Phase | File |
|---|---|
| 1 | `build.one.api/entities/user/sql/dbo.user.sql` — add VendorId column |
| 1 | `build.one.web/src/pages/users/UserProfile.tsx` — Vendor picker |
| 2 | `build.one.api/entities/vendor/sql/dbo.vendor.sql` — rate columns OR new VendorContractRate table |
| 2 | `build.one.api/entities/contract_labor/business/bill_service.py` — read rates from DB |
| 3 | `build.one.api/entities/contract_labor/sql/dbo.contract_labor.sql` — SourceTimeEntryId FK |
| 3 | `build.one.api/entities/time_entry/sql/dbo.time_entry.sql` — new aggregation sproc |
| 3 | `build.one.api/entities/time_entry/business/service.py` — billed transition |
| 4 | `build.one.scheduler/` if scheduler trigger chosen |
| 4 | `build.one.api/shared/api/admin.py` — new admin endpoint if needed |
| 7 | `build.one.web/src/pages/contract-labor/` |
| 7 | `build.one.web/src/pages/time-entry/` |

## What NOT to do

- Do not start coding before the 8 decisions are answered. Sketch schema + sproc shapes for review first.
- Do not modify the existing Excel import path until decision #6 is locked in.
- Do not delete the Excel-import-specific columns until decision #5 is locked in — they may be load-bearing for in-flight imports.
- Do not change `bill_service.py`'s reportlab PDF format without explicit ask; the PDF is what reviewers / QBO see.
- Do not introduce a new `cost_code_id` write path — every BillLineItem hangs off SubCostCode; CostCode is reachable via SubCostCode (per `entities/bill/CLAUDE.md`).
- Do not use `float()` for any rate / amount / markup calculations — `Decimal(str(value))` only (memory: financial precision rule).

## Known landmines (from memory + prior incidents)

- `bill_service.py` inner-loop variable shadowing: don't name accumulators `total_amount` — use `scc_amount` / `scc_price` to avoid corrupting the outer bill total.
- ContractLabor import `_parse_row()` returns `(dict, skip_reason)` tuple — always unpack at every call site or `.get()` crashes.
- `IsBillable` on ContractLaborLineItem: use `is_billable is not False` (not `if is_billable`) — None means default-billable.
- `BillLineItemId` FK on ContractLaborLineItem: the UPDATE sproc uses `CASE WHEN` to preserve when NULL is passed. Always read + re-pass existing value on updates.
- TimeEntry row scoping (Phase 3 RBAC): non-admin users see only their own TimeEntry rows via sproc-layer filter. Aggregation runs from outbox / scheduler context — must `set_authz_context(is_system_admin=True)` (see `feedback_outbox_authz_boundary.md`).
- `ReadProjectsByUserId` does NOT honor `IsSystemAdmin` bypass — uses INNER JOIN on UserProject. Workaround backfill applied 2026-05-27; permanent fix tracked in `build.one.api/TODO.md`.

## References

- ContractLabor architecture: `entities/contract_labor/business/bill_service.py`, `import_service.py`, `bill_summary.py`
- TimeTracking architecture: `entities/time_entry/business/service.py` (lines 18-24 = VALID_TRANSITIONS), `time_log_service.py`
- Bill universal attachment requirement: `build.one.api/CLAUDE.md` "Bill.attachment_public_id" section
- Memory: `~/.claude/projects/-Users-chris-Applications-build-one/memory/` — has Contract Labor gotchas + TimeTracking session history

---

## Decisions locked + plan executed (2026-05-27)

After the 8 decisions were locked, the 7-phase plan was reshaped + executed in one session. Schema sketch reviewed + signed off; SQL written and ready to apply; Python entity packages + service hooks + minimal React surface delivered. Excel-importer migration and parts of the React polish are deliberately deferred.

### Locked decisions

1. **Worker model:** New `Employee` entity paired with `Vendor`. `User.EmployeeId` XOR `User.VendorId` enforced in the service layer (`UserService.set_worker_link`) + defense-in-depth XOR check in the `UpdateUserWorkerLink` sproc. Users with neither link are non-billable (admin/agent).
2. **Aggregation grain:** per (Worker × Project × Day). Multi-TimeLog same combo collapses via `SUM(Duration)`. Multi-project days produce multiple parent rows.
3. **Billing period:** semi-monthly, hardcoded — 1st–15th, 16th–EOM. Aggregation sproc computes from `WorkDate`.
4. **Rate lock:** captured at aggregation time as a snapshot. Re-aggregation (post-edit-resubmit) refreshes the snapshot. Once downstream Bill/Invoice is posted, the row freezes (sproc skip).
5. **Trigger:** `submit_for_review` (`draft → submitted`) calls `AggregateTimeEntryOnSubmit`. Failure-isolated — aggregation errors log + flag but don't roll back the submit.
6. **Excel importer:** migration to TimeEntry/TimeLog **DEFERRED**. Existing Excel path stays running as-is to avoid regressing in-production import. New `TimeEntryBulkImportService` primitive shipped — both the Excel importer migration AND the external CSV adapter will sit on top of it.
7. **Rate storage:** `HourlyRate` + `Markup` columns on `Vendor` + `Employee`. `VendorProjectRate` + `EmployeeProjectRate` override tables for per-project overrides. Lookup precedence: override → default → ERROR (aggregation flags row `pending_review` with annotation). `VENDOR_CONFIG` dict still resolved as a final fallback during the cut-over window; backfill script populates Vendor rate columns.
8. **Backfill:** Phase 1g script (`scripts/backfill_user_worker_links.sql`) links existing 6 contractor Users → Vendor and creates Selvin's Employee row. Phase 2f script (`scripts/backfill_vendor_rates.sql`) populates Vendor rate columns from `VENDOR_CONFIG`. External third-party CSV import is **BLOCKED on sample file** from the user.

### Key entities + tables created

| Phase | Artifact |
|---|---|
| 1 | `dbo.Employee` table + 7 sprocs (`entities/employee/sql/dbo.employee.sql`) |
| 1 | `User.EmployeeId` + `User.VendorId` + filtered unique indexes + `UpdateUserWorkerLink` sproc (`entities/user/sql/migrations/005_*.sql`) |
| 1 | `Modules.EMPLOYEES` seed |
| 2 | `Vendor.HourlyRate` + `Vendor.Markup` columns + extended sprocs (`entities/vendor/sql/migrations/002_*.sql`) |
| 2 | `dbo.VendorProjectRate` + `dbo.EmployeeProjectRate` tables + `ReadEffectiveRateFor{Vendor,Employee}Project` sprocs |
| 3 | `dbo.EmployeeLabor` + `dbo.EmployeeLaborLineItem` tables (`entities/employee_labor/sql/dbo.employee_labor.sql`) |
| 3 | `InvoiceLineItem.EmployeeLaborLineItemId` + `SourceType='EmployeeLaborLineItem'` (`entities/invoice_line_item/sql/migrations/001_*.sql`) |
| 3 | `Modules.EMPLOYEE_LABOR` seed |
| 4 | `dbo.AggregateTimeEntryOnSubmit` sproc (`entities/time_entry/sql/migrations/001_*.sql`) |
| 5 | `ContractLabor.SourceTimeEntryId` + re-issued aggregation sproc to stamp it (`002_*.sql`) |
| 5 | `dbo.IsTimeEntryDownstreamLocked` sproc + service-layer edit-lock (`003_*.sql`) |

### Python service surface

- `entities/employee/` — full entity package.
- `entities/employee_labor/` + `entities/employee_labor_line_item/` — full entity packages, status workflow `pending_review → ready → invoiced`.
- `entities/vendor_project_rate/` + `entities/employee_project_rate/` — rate-override entity packages.
- `UserService.set_worker_link` — XOR-enforced worker linkage mutation.
- `TimeEntryRepository.aggregate_for_billing` + `is_downstream_locked` — sproc wrappers.
- `TimeEntryService.submit` hooks aggregation (failure-isolated).
- `TimeEntryService.update_by_public_id` + `reject` enforce the downstream-lock guard.
- `TimeEntryBulkImportService` — format-agnostic primitive for historical data imports.
- `ContractLaborImportService._get_rate_for_vendor` reads from Vendor table first; falls back to historical + VENDOR_CONFIG.
- `enrichment.enrich_line_items` handles the new `EmployeeLaborLineItem` source.
- Invoice packet TOC + sort include `EmpLabor` type at type_order=3.

### React surface

- `pages/employees/{List,View,Create,Edit}.tsx` — full Employee CRUD.
- `pages/employee-labor/EmployeeLaborList.tsx` — read-only list filtered by billing period.
- `pages/users/UserProfile.tsx` — new "Worker" section (None / Employee / Vendor picker, admin-only).
- `pages/vendors/{Create,Edit,View}.tsx` — extended with HourlyRate + Markup fields.
- `pages/invoices/InvoiceEdit.tsx` — `EmployeeLaborLineItem` added to the source_type dropdown.
- Lookups extended with `employees` + `contract_labor_vendors` for filtered pickers.

### Backfill scripts (apply after migrations)

- `scripts/backfill_user_worker_links.sql` — links 6 contractor Users + creates Selvin's Employee row.
- `scripts/backfill_vendor_rates.sql` — populates Vendor.HourlyRate/Markup from VENDOR_CONFIG.

### Apply order

1. SQL — in this order so FKs resolve cleanly:
   ```
   python scripts/run_sql.py entities/employee/sql/dbo.employee.sql
   python scripts/run_sql.py entities/user/sql/dbo.user.sql
   python scripts/run_sql.py entities/user/sql/migrations/005_2026_05_27_worker_links.sql
   python scripts/run_sql.py entities/vendor/sql/migrations/002_2026_05_27_rate_columns.sql
   python scripts/run_sql.py entities/vendor_project_rate/sql/dbo.vendor_project_rate.sql
   python scripts/run_sql.py entities/employee_project_rate/sql/dbo.employee_project_rate.sql
   python scripts/run_sql.py entities/employee_labor/sql/dbo.employee_labor.sql
   python scripts/run_sql.py entities/invoice_line_item/sql/migrations/001_2026_05_27_employee_labor_source.sql
   python scripts/run_sql.py entities/time_entry/sql/migrations/001_2026_05_27_aggregate_on_submit.sql
   python scripts/run_sql.py entities/contract_labor/sql/migrations/2026_05_27_source_time_entry_id.sql
   python scripts/run_sql.py entities/time_entry/sql/migrations/002_2026_05_27_aggregate_contract_labor_lineage.sql
   python scripts/run_sql.py entities/time_entry/sql/migrations/003_2026_05_27_downstream_lock_check.sql
   python scripts/run_sql.py entities/module/sql/seed.AllModules.sql
   ```
2. **API restart** — picks up new entity packages + routers + lookup fetchers.
3. **Backfills**:
   ```
   python scripts/run_sql.py scripts/backfill_user_worker_links.sql
   python scripts/run_sql.py scripts/backfill_vendor_rates.sql
   ```
4. **React rebuild** — `npx tsc --noEmit` then `npm run dev`.
5. **RoleModule grants for `Modules.EMPLOYEES` + `Modules.EMPLOYEE_LABOR`** — Christopher's `IsSystemAdmin=1` bypass means the pages work for him immediately; other roles need explicit `RoleModule` rows before the sidebar entries appear.

### Deferred follow-ons (tracked)

- **Excel importer migration** (Phase 6b) — write a thin wrapper on top of `TimeEntryBulkImportService` and gate the existing Excel path behind a feature flag.
- **External CSV backfill** (Phase 6c) — BLOCKED on sample file from user (format, headers, date format, worker identification).
- **EmployeeLabor View/Edit/Create React pages** (Phase 7b) — only the List page shipped. Edits via API for now.
- **ContractLabor source-mixed-row badge + TimeEntry "Billed via X" badge** (Phase 7c) — cosmetic.
- **iOS Submitted/Billed badges** (Phase 7d) — separate sub-repo, separate cycle.
- **`dbo.user.sql` divergence cleanup** — surfaced during Phase 1; canonical file's sproc bodies are stale vs migrations 002/003/004. New migration 005 follows the live shape; canonical file still lags. Worth a tracked cleanup task in `build.one.api/TODO.md`.
- **Pre-existing VendorEdit bug** — picker fields (`vendor_type_public_id`, `taxpayer_public_id`) hydrate as empty even when the loaded vendor has them set, so re-saving could null the FKs. Not introduced by this work; worth a separate fix.
- **`UserProfile.tsx` Worker section + new Vendor rate fields TypeScript verification** — run `npx tsc --noEmit` to confirm no type drift.

### Address fields from VENDOR_CONFIG

VENDOR_CONFIG still carries `address` + `city_state_zip` (used for the ContractLabor bill PDF header). Phase 2 left this in place — rate/markup moved to DB, address-half stays in `VENDOR_CONFIG` dict for now. Long-term migration to the existing `VendorAddress` entity is a separate follow-on.

---

## 2026-06-04 — Legacy ContractLabor → TimeEntry data migration (in scoping)

Separate from the integration project above. Goal: backfill TimeEntry / TimeLog / TimeEntryStatus rows from the 421 Excel-imported legacy ContractLabor rows so workers and reviewers see a complete history in the new system, and so the entire labor pipeline lives under one model going forward.

### Source

`dbo.ContractLabor` rows where `ImportBatchId IS NOT NULL`:

- **421 rows total** spanning WorkDate 2026-01-02 → 2026-03-13 (~10 weeks)
- 6 Excel import batches (`TimeClock.xls` family) created 2026-01-21 → 2026-03-23
- 8 distinct `EmployeeName` values
- Status: 338 `billed` (270 with live `BillLineItemId`), 74 `pending_review`, 9 `ready`

### Hard constraint

**Must not re-trigger `dbo.AggregateTimeEntryOnSubmit`** — 338 of 421 legacy CL rows already have downstream Bills produced. Re-aggregation would create duplicate ContractLabor parents and double-count hours.

Migration writes TimeEntry / TimeLog / TimeEntryStatus rows representing historical data, then **stamps `dbo.ContractLabor.SourceTimeEntryId = <new TE Id>` retroactively** to close the lineage loop. Legacy CL row stays canonical for billing history; nothing in the bill chain moves.

### Collapse ratio (key shape decision)

Legacy CL is one row per `(Worker × Day × Project)`. TimeEntry is one row per `(Worker × Day)` with N TimeLogs hanging off it. So multi-project days collapse:

| CL rows on same worker-day | worker-days | = CL rows |
|---|---|---|
| 1 (single-project, 1:1) | 258 | 258 |
| 2 | 44 | 88 |
| 3 | 17 | 51 |
| 4 | 6 | 24 |
| **Total** | **325 TimeEntries** | **421 TimeLogs (1:1 with CL)** |

**Grouping key**: `(resolved_user_id, work_date)`.

### Decisions locked

**Worker name → User.Id resolution**:

| Legacy EmployeeName | → User |
|---|---|
| `Wilmer  Diaz` | 41 |
| `Emilson  Cordova` | 38 |
| `Elmer Cordova` | 37 |
| `Selvin Cordova` | 40 |
| `Michael Jacobson` | 39 |
| `Parker Hazen` | 42 |
| `Brayan Rafael Marcia Salina` | 36 (name shortened to `Brayan Marcia Salina` later) |
| `Denis Marcia Izaguirre` | **NEW** `User` row — `Firstname='Denis'`, `Lastname='Izaguirre'`, `Email=NULL`. No longer with the company; created for historical records only (no Auth row, no UserRole, no UserProject). |

**TimeEntry field mapping**:

| TimeEntry column | Source |
|---|---|
| `UserId` | resolved from `EmployeeName` per table above |
| `WorkDate` | `CL.WorkDate` |
| `Note` | `NULL` (per-session worker notes ride on TimeLog) |
| `CompanyId` | `CL.CompanyId` |
| `CreatedByUserId` | each worker's own resolved `User.Id` (TE is "self-authored" historically — Wilmer's TEs `CreatedByUserId=41`, Michael's `=39`, etc.) |
| `CreatedDatetime` | `MIN(CL.CreatedDatetime)` across the group (earliest import-timestamp on the day) |
| `ProjectId` | `NULL` (deprecated on TE; project lives on TimeLog) |
| `ReviewPriority`, `ReviewReasons` | `NULL` (agent-stamped fields not back-populated) |

### Still open (next scoping pass)

- **ContractLabor → TimeeLog mapping** — `TimeIn`/`TimeOut` parse strategy, `BreakTime` modeling, ProjectId carry-through, Duration source (`TotalHours` vs computed from clock-in/out).
- **ContractLabor.Status → TimeEntryStatus mapping** — `billed` → `billed`, `ready` → `approved`, `pending_review` → ? (`submitted` or `draft`).
- **Lineage back-stamp** — exact SQL for `UPDATE ContractLabor SET SourceTimeEntryId = ?` post-insert.
- **Idempotency** — re-runnable via `(SourceFile, SourceRow)` natural key, or one-shot?
- **Acting system context** — `set_authz_context(is_system_admin=True)` requirement during the migration since cross-user row writes will hit RBAC.
- **EmployeeLabor backfill** — table is empty; confirm no employee-side historical data in scope.
