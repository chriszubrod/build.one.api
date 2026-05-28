# External Timesheet Sample (CSV)

`external_timesheet_sample.csv` is a representative input for Phase 6c — the third-party timesheet backfill.

**Adapter:** `entities/time_entry/business/external_timesheet_import_service.py` — `ExternalTimesheetImportService.import_csv(file_content, filename)`. Sits on top of `TimeEntryBulkImportService` (Phase 6a primitive).

**Endpoint:** `POST /api/v1/time-entries/import/external-csv` (multipart `file=@…`), gated on `Time Tracking can_approve`. Example:

```bash
curl -X POST "$API_BASE_URL/api/v1/time-entries/import/external-csv" \
  -H "Authorization: Bearer $JWT" \
  -F "file=@scripts/samples/external_timesheet_sample.csv"
```

## Column contract

| Column | Required | Format | Notes |
|---|---|---|---|
| `worker_firstname` | yes | string | First word of the worker's name. Must match `dbo.[User].Firstname` exactly (case-insensitive equal). |
| `worker_lastname` | yes | string | Remaining words of the worker's name (can contain spaces — e.g. "Cordova Tercero", "Marcia Salina"). Must match `dbo.[User].Lastname` exactly. |
| `project_name` | no | string | Project name as it appears in `dbo.[Project].Name`. Empty value = no project assigned (aggregation creates a row with `ProjectId IS NULL`). |
| `work_date` | yes | `YYYY-MM-DD` | ISO date. Drives both the TimeEntry.WorkDate AND the billing-period bucket (≤15 → 1st–15th; ≥16 → 16th–EOM). |
| `hours` | yes | decimal | Total billable hours that worker put in on that project that day. **Use one row per (Worker × Project × Day)** — see "Important constraint" below. |
| `note` | no | string | Free-text per-row note. Stored on both TimeEntry.Note and TimeLog.Note. |

## What the adapter does per row

For each row, the adapter:

1. Resolves `worker_firstname` + `worker_lastname` → `dbo.[User]` row. Fails the row if no exact match.
2. Resolves `project_name` → `dbo.[Project].PublicId` (case-insensitive equal). Empty / unmatched → NULL project.
3. Builds a dict for `TimeEntryBulkImportService.import_rows()`:
   ```python
   {
       "worker_firstname": "Selvin",
       "worker_lastname": "Cordova",
       "project_public_id": "<resolved>",
       "work_date": "2026-05-04",
       "hours": "8.00",
       "note": "Framing - second floor walls",
       "submit": True,
   }
   ```
4. Bulk service creates a `TimeEntry` (status=draft) + a single `TimeLog` (ClockIn = workDate 09:00; ClockOut = ClockIn + hours), then `submit()` fires Phase 4 aggregation.
5. Aggregation routes to `EmployeeLabor` (if User.EmployeeId is set, e.g. Selvin) or `ContractLabor` (if User.VendorId is set, e.g. the 6 contractors).

## What this sample exercises

| Scenario | Rows |
|---|---|
| Employee path (Selvin → `EmployeeLabor`) | rows 1–5 |
| Vendor path (Wilmer/Elmer/etc. → `ContractLabor`) | rows 6–16 |
| Multi-word lastname matching | rows 12–13 ("Cordova Tercero"), 15–16 ("Marcia Salina") |
| Same worker × multiple projects same week | Selvin on HP + TB3 |
| Same worker × same project × multiple days (aggregates into per-day ContractLabor rows) | Wilmer @ HP |
| Billing-period split (1st–15th vs 16th–EOM) | rows 8–9, 12–13, 15–16 land in the second half |
| Empty `project_name` (general/unassigned work) | row 14 |
| Empty `note` | rows 3, 7, 9, 10, 11, 16 |

## Important constraint — one row per (Worker × Project × Day)

The Phase 4 aggregation sproc uses `(EmployeeId|VendorId, ProjectId, WorkDate, BillingPeriodStart)` as its natural key and upserts on that key. Each TimeEntry's aggregation SUMs only its own TimeLogs.

**Implication:** if the CSV has two rows for the same worker + project + day:

```csv
Wilmer,Diaz,HP - 6135 Hillsboro Pike,2026-05-04,4.00,Morning
Wilmer,Diaz,HP - 6135 Hillsboro Pike,2026-05-04,4.00,Afternoon
```

…the second row's aggregation will **overwrite** the first's `TotalHours` on the same ContractLabor row, not sum them. The CSV would end up reporting 4 hours, not 8.

**Rule:** bundle multi-session days into one row's `hours` field. If the source data is per-session, the adapter should pre-aggregate per (worker, project, day) before handing rows to the bulk service.

(This is a real limitation of the current Phase 4 sproc — a future enhancement would have aggregation SUM across all TimeEntries that share the natural key, not just within one TimeEntry. Tracked as a follow-on.)

## Expected outcome after running this sample

Assuming the Phase 1g backfill linked the workers correctly and the projects "HP - 6135 Hillsboro Pike" + "TB3 - 917 Tyne Blvd" exist in `dbo.[Project]`:

- **15 TimeEntry rows created**, all in `submitted` status.
- **15 TimeLog rows**, one per TimeEntry, ClockIn = `{work_date}T09:00:00`, ClockOut = ClockIn + hours.
- **Selvin's 5 rows** → 5 `EmployeeLabor` rows (split by project + day).
- **Wilmer's 4 rows** → 4 `ContractLabor` rows; 2 in billing period `2026-05-01`, 2 in `2026-05-16`.
- **Elmer's 2 rows** → 2 `ContractLabor` rows.
- **Emilson's 2 rows** → 1 in each billing period.
- **Michael's 1 row (no project)** → 1 `ContractLabor` row with `ProjectId IS NULL`.
- **Brayan's 2 rows** → 2 `ContractLabor` rows in billing period `2026-05-16`.

If any row fails (worker not matched, hours invalid), the bulk service returns a per-row result with `status='failed'` + `error` message — the adapter should aggregate those into the import response.
