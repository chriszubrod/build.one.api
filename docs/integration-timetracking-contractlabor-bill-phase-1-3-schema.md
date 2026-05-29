# Phase 1–3 Schema Sketch — TimeTracking → ContractLabor → Bill Integration

**Status:** Review draft. Drafted 2026-05-27 in response to the locked 8 decisions in [`integration-timetracking-contractlabor-bill.md`](integration-timetracking-contractlabor-bill.md). **No SQL has been run.** Sign off here before any migrations are written.

CREATE TABLE shapes + sproc signatures only. Sproc bodies are omitted — they follow existing conventions (IF NOT EXISTS guards, GO separators, ROWVERSION optimistic concurrency, SYSUTCDATETIME() for timestamps).

---

## Phase 1 — Employee entity + `User.EmployeeId` / `User.VendorId`

### New table: `dbo.Employee`

Paired symmetrically with `Vendor`. Internal billable identity. v1 carries only what billing needs; HR fields land when a feature demands.

```sql
CREATE TABLE dbo.Employee (
    Id                  BIGINT             NOT NULL IDENTITY(1,1) PRIMARY KEY,
    PublicId            UNIQUEIDENTIFIER   NOT NULL DEFAULT NEWID(),
    RowVersion          ROWVERSION         NOT NULL,
    CreatedDatetime     DATETIME2(3)       NOT NULL DEFAULT SYSUTCDATETIME(),
    ModifiedDatetime    DATETIME2(3)       NULL,
    CompanyId           BIGINT             NOT NULL DEFAULT (1),   -- Phase 5-thin pattern
    CreatedByUserId     BIGINT             NOT NULL DEFAULT (17),  -- Gap 2 pattern (17 = Christopher)

    Firstname           NVARCHAR(50)       NOT NULL,
    Lastname            NVARCHAR(255)      NOT NULL,
    Email               NVARCHAR(255)      NULL,    -- optional, may differ from User.Auth.Username
    HourlyRate          DECIMAL(18,4)      NULL,    -- Phase 2 populates; nullable while rate UI rolls out
    Markup              DECIMAL(18,4)      NULL,    -- e.g., 0.50 = 50% markup
    IsActive            BIT                NOT NULL DEFAULT 1,
    IsDeleted           BIT                NOT NULL DEFAULT 0,
    Notes               NVARCHAR(MAX)      NULL,    -- mirrors Vendor.Notes

    CONSTRAINT UQ_Employee_PublicId UNIQUE (PublicId),
    CONSTRAINT FK_Employee_Company  FOREIGN KEY (CompanyId)       REFERENCES dbo.Company(Id),
    CONSTRAINT FK_Employee_CreatedBy FOREIGN KEY (CreatedByUserId) REFERENCES dbo.[User](Id)
);
CREATE INDEX IX_Employee_Lastname_Firstname ON dbo.Employee(Lastname, Firstname);
```

**Sprocs** (full module CRUD set, mirrors Vendor/ContractLabor):

```sql
CREATE OR ALTER PROCEDURE dbo.CreateEmployee
    @Firstname NVARCHAR(50), @Lastname NVARCHAR(255),
    @Email NVARCHAR(255) = NULL, @HourlyRate DECIMAL(18,4) = NULL,
    @Markup DECIMAL(18,4) = NULL, @IsActive BIT = 1, @Notes NVARCHAR(MAX) = NULL,
    @CreatedByUserId BIGINT = NULL  -- COALESCE(@CreatedByUserId, 17) inside body, per Gap 2

CREATE OR ALTER PROCEDURE dbo.ReadEmployees
CREATE OR ALTER PROCEDURE dbo.ReadEmployeesPaginated
    @PageNumber INT = 1, @PageSize INT = 50, @SearchTerm NVARCHAR(255) = NULL,
    @IsActive BIT = NULL, @SortBy NVARCHAR(50) = 'Lastname', @SortDirection NVARCHAR(4) = 'ASC'
CREATE OR ALTER PROCEDURE dbo.CountEmployees  -- same filters as Paginated
CREATE OR ALTER PROCEDURE dbo.ReadEmployeeById @Id BIGINT
CREATE OR ALTER PROCEDURE dbo.ReadEmployeeByPublicId @PublicId UNIQUEIDENTIFIER
CREATE OR ALTER PROCEDURE dbo.UpdateEmployeeById
    @Id BIGINT, @RowVersion BINARY(8),
    @Firstname NVARCHAR(50), @Lastname NVARCHAR(255),
    @Email NVARCHAR(255) = NULL, @HourlyRate DECIMAL(18,4) = NULL,
    @Markup DECIMAL(18,4) = NULL, @IsActive BIT = 1, @Notes NVARCHAR(MAX) = NULL
CREATE OR ALTER PROCEDURE dbo.SoftDeleteEmployeeByPublicId @PublicId UNIQUEIDENTIFIER
```

### `User` additions (ALTER)

```sql
ALTER TABLE dbo.[User] ADD EmployeeId BIGINT NULL
    CONSTRAINT FK_User_Employee FOREIGN KEY REFERENCES dbo.Employee(Id);
ALTER TABLE dbo.[User] ADD VendorId   BIGINT NULL
    CONSTRAINT FK_User_Vendor   FOREIGN KEY REFERENCES dbo.Vendor(Id);

CREATE UNIQUE INDEX UX_User_EmployeeId ON dbo.[User](EmployeeId) WHERE EmployeeId IS NOT NULL;
CREATE UNIQUE INDEX UX_User_VendorId   ON dbo.[User](VendorId)   WHERE VendorId   IS NOT NULL;
```

**XOR rule:** `EmployeeId IS NULL OR VendorId IS NULL` (a User is at most one of the two; Users with neither are non-billable — agents, admins, customers). Enforced in **service layer** (`UserService.update`/`set_worker_link`), not as a CHECK constraint, so admins can flip a row through a neutral state without an atomic transaction. **Open call:** want a CHECK constraint instead? Spell it out and we add `CK_User_WorkerXor`.

The two unique filtered indexes prevent two Users from claiming the same Employee or Vendor.

### Sproc additions to `User`

```sql
CREATE OR ALTER PROCEDURE dbo.UpdateUserWorkerLink
    @UserId BIGINT, @RowVersion BINARY(8),
    @EmployeeId BIGINT = NULL, @VendorId BIGINT = NULL
    -- raises if both non-NULL; allows both NULL (clears link)
```

Existing `ReadUserById` / paginated sprocs grow `EmployeeId` + `VendorId` in their SELECT list (no new params).

### Service surface (Python)

- `entities/employee/` — new package: `api/`, `business/`, `persistence/`, `sql/`.
- Modules row: `Modules.EMPLOYEES` (new). Role grants: Tenant Admin + Controller get full CRUD; everyone else read-only at most.
- `UserService.set_worker_link(user_public_id, *, employee_id=None, vendor_id=None)` — raises if both passed.

### Backfill (one-shot SQL after deploy)

For each entry in `VENDOR_CONFIG` (7 vendor-contractors today):
1. Lookup `Vendor.Id` by name match.
2. Lookup `User.Id` by `Firstname + Lastname` match (manual mapping table for ambiguous cases).
3. `UPDATE [User] SET VendorId = @vendor_id WHERE Id = @user_id`.

For employees (you'll need to identify which current Users are employees — likely an explicit list):
1. `INSERT INTO Employee (Firstname, Lastname, HourlyRate, Markup) VALUES (...)`.
2. `UPDATE [User] SET EmployeeId = SCOPE_IDENTITY() WHERE Id = @user_id`.

Backfill script lives at `scripts/backfill_user_worker_links.py`.

---

## Phase 2 — Rate storage in DB

### `Vendor` additions (ALTER)

```sql
ALTER TABLE dbo.Vendor ADD HourlyRate DECIMAL(18,4) NULL;
ALTER TABLE dbo.Vendor ADD Markup     DECIMAL(18,4) NULL;
```

`Employee` already declared these in Phase 1 — no further change.

### New table: `dbo.VendorProjectRate`

Per-(Vendor × Project) override. NULL row = use Vendor default.

```sql
CREATE TABLE dbo.VendorProjectRate (
    Id                  BIGINT           NOT NULL IDENTITY(1,1) PRIMARY KEY,
    PublicId            UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    RowVersion          ROWVERSION       NOT NULL,
    CreatedDatetime     DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),
    ModifiedDatetime    DATETIME2(3)     NULL,
    CompanyId           BIGINT           NOT NULL DEFAULT (1),
    CreatedByUserId     BIGINT           NOT NULL DEFAULT (17),

    VendorId            BIGINT           NOT NULL,
    ProjectId           BIGINT           NOT NULL,
    HourlyRate          DECIMAL(18,4)    NULL,    -- NULL = inherit default
    Markup              DECIMAL(18,4)    NULL,    -- NULL = inherit default
    Notes               NVARCHAR(MAX)    NULL,

    CONSTRAINT UQ_VendorProjectRate UNIQUE (VendorId, ProjectId),
    CONSTRAINT FK_VendorProjectRate_Vendor  FOREIGN KEY (VendorId)  REFERENCES dbo.Vendor(Id),
    CONSTRAINT FK_VendorProjectRate_Project FOREIGN KEY (ProjectId) REFERENCES dbo.Project(Id),
    CONSTRAINT FK_VendorProjectRate_Company FOREIGN KEY (CompanyId) REFERENCES dbo.Company(Id)
);
```

### New table: `dbo.EmployeeProjectRate`

Mirror of `VendorProjectRate`. Same columns, `EmployeeId` swap for `VendorId`. Same UNIQUE constraint on `(EmployeeId, ProjectId)`. Skipped for brevity.

### Sprocs

```sql
-- Lookup (called by Phase 4 aggregation)
CREATE OR ALTER PROCEDURE dbo.ReadEffectiveRateForVendorProject
    @VendorId BIGINT, @ProjectId BIGINT
    -- Returns: HourlyRate, Markup, RateSource (NVARCHAR(20): 'override' | 'default' | 'none')
    -- COALESCE(override.HourlyRate, vendor.HourlyRate) etc.

CREATE OR ALTER PROCEDURE dbo.ReadEffectiveRateForEmployeeProject
    @EmployeeId BIGINT, @ProjectId BIGINT
    -- Same shape.

-- Admin UI CRUD (mirrors join-table sproc shape used by UserRole / RoleModule)
CREATE OR ALTER PROCEDURE dbo.CreateVendorProjectRate
    @VendorId BIGINT, @ProjectId BIGINT,
    @HourlyRate DECIMAL(18,4) = NULL, @Markup DECIMAL(18,4) = NULL,
    @Notes NVARCHAR(MAX) = NULL, @CreatedByUserId BIGINT = NULL

CREATE OR ALTER PROCEDURE dbo.ReadVendorProjectRatesByVendorId   @VendorId  BIGINT
CREATE OR ALTER PROCEDURE dbo.ReadVendorProjectRatesByProjectId  @ProjectId BIGINT
CREATE OR ALTER PROCEDURE dbo.UpdateVendorProjectRateById
    @Id BIGINT, @RowVersion BINARY(8),
    @HourlyRate DECIMAL(18,4) = NULL, @Markup DECIMAL(18,4) = NULL,
    @Notes NVARCHAR(MAX) = NULL
CREATE OR ALTER PROCEDURE dbo.DeleteVendorProjectRateById @Id BIGINT
-- + mirror Employee variants
```

### Rate lookup precedence (locked)

```
1. (Worker × Project) override row    — VendorProjectRate.HourlyRate non-NULL
2. (Worker × Project) override row    — VendorProjectRate exists with NULL rate → inherit
3. Worker default                     — Vendor.HourlyRate non-NULL
4. ERROR                              — aggregation refuses to write a $0 line silently
```

The error in step 4 surfaces as `ContractLabor.Status = 'pending_review'` with a `Description` annotation like `"rate not configured for Vendor X on Project Y"` so the office reviewer sees it on the Bills page. No bill generated.

### Service surface

- `entities/vendor_project_rate/` — new sub-package (or live under `entities/vendor/` — preference?). **Open call.** Lean: standalone `entities/vendor_project_rate/` + `entities/employee_project_rate/` since they're independently routed in React.
- `bill_service._get_rate_for_vendor()` rewrite — reads `ReadEffectiveRateForVendorProject`, drops `VENDOR_CONFIG` dict. The address fields currently in `VENDOR_CONFIG` move onto `Vendor` (or rely on existing `Address` entity if Vendor already links to one — verify in implementation).

### Backfill

INSERT one row per `VENDOR_CONFIG` entry into `Vendor.HourlyRate` + `Vendor.Markup`. No `VendorProjectRate` rows needed at v1 — overrides created on demand via admin UI.

---

## Phase 3 — EmployeeLabor entity + Invoice source

### Naming + scope rationale

Two parallel labor entities:
- **ContractLabor** (existing) — vendor-contractor labor; generates a Bill. Untouched in shape.
- **EmployeeLabor** (new) — internal employee labor; never generates a Bill. Both feed `InvoiceLineItem` so the customer is billed for either kind.

### New table: `dbo.EmployeeLabor`

Mirrors `ContractLabor` shape minus Bill-generation fields (`BillVendorId`, `BillNumber`, `BillDate`, `DueDate`, `BillLineItemId`) and Excel-import fields (`ImportBatchId`, `SourceFile`, `SourceRow`, `EmployeeName` raw, `JobName`, `TimeIn`/`TimeOut`/`BreakTime`).

```sql
CREATE TABLE dbo.EmployeeLabor (
    Id                  BIGINT           NOT NULL IDENTITY(1,1) PRIMARY KEY,
    PublicId            UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    RowVersion          ROWVERSION       NOT NULL,
    CreatedDatetime     DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),
    ModifiedDatetime    DATETIME2(3)     NULL,
    CompanyId           BIGINT           NOT NULL DEFAULT (1),
    CreatedByUserId     BIGINT           NOT NULL DEFAULT (17),

    EmployeeId          BIGINT           NOT NULL FK Employee.Id,
    ProjectId           BIGINT           NULL     FK Project.Id,
    WorkDate            DATE             NOT NULL,
    BillingPeriodStart  DATE             NOT NULL,   -- 1st or 16th
    BillingPeriodEnd    DATE             NOT NULL,   -- 15th or EOM

    TotalHours          DECIMAL(6,2)     NOT NULL DEFAULT 0,
    HourlyRate          DECIMAL(18,4)    NULL,
    Markup              DECIMAL(18,4)    NULL,
    TotalAmount         DECIMAL(18,2)    NULL,       -- hours * rate * (1+markup); locked at invoice-finalize per decision #4
    SubCostCodeId       BIGINT           NULL FK SubCostCode.Id,
    Description         NVARCHAR(MAX)    NULL,

    Status              NVARCHAR(20)     NOT NULL DEFAULT 'pending_review',
        -- pending_review → ready → invoiced
    SourceTimeEntryId   BIGINT           NULL FK TimeEntry.Id,  -- lineage when TT-sourced

    CONSTRAINT UQ_EmployeeLabor_NaturalKey UNIQUE (EmployeeId, ProjectId, WorkDate, BillingPeriodStart)
);
CREATE INDEX IX_EmployeeLabor_BillingPeriod ON dbo.EmployeeLabor(BillingPeriodStart, Status);
CREATE INDEX IX_EmployeeLabor_Employee      ON dbo.EmployeeLabor(EmployeeId, WorkDate);
```

**Status workflow:** `pending_review → ready → invoiced`. Terminal state is `invoiced` (not `billed`) since the Bill path doesn't exist for employees. Set when an Invoice line referencing this row is finalized (`complete_invoice`).

**Open call:** the UNIQUE constraint enforces decision #2 (one row per Worker × Project × Day × Period). Reject if you want Worker × Project × Period only (no per-day granularity).

### New table: `dbo.EmployeeLaborLineItem`

```sql
CREATE TABLE dbo.EmployeeLaborLineItem (
    Id                  BIGINT           NOT NULL IDENTITY(1,1) PRIMARY KEY,
    PublicId            UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    RowVersion          ROWVERSION       NOT NULL,
    CreatedDatetime     DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),
    ModifiedDatetime    DATETIME2(3)     NULL,
    CreatedByUserId     BIGINT           NOT NULL DEFAULT (17),

    EmployeeLaborId     BIGINT           NOT NULL FK EmployeeLabor.Id ON DELETE CASCADE,
    LineDate            DATE             NULL,
    ProjectId           BIGINT           NULL FK Project.Id,
    SubCostCodeId       BIGINT           NULL FK SubCostCode.Id,
    Description         NVARCHAR(MAX)    NULL,

    Hours               DECIMAL(6,2)     NULL,
    Rate                DECIMAL(18,4)    NULL,
    Markup              DECIMAL(18,4)    NULL,
    Price               DECIMAL(18,2)    NULL,
    IsBillable          BIT              NOT NULL DEFAULT 1,
    IsOverhead          BIT              NOT NULL DEFAULT 0,

    InvoiceLineItemId   BIGINT           NULL FK InvoiceLineItem.Id  -- back-ref; mirrors ContractLaborLineItem.BillLineItemId
);
```

**`InvoiceLineItemId` FK update semantics:** UPDATE sproc must use `CASE WHEN` to preserve when NULL is passed — same gotcha as `ContractLaborLineItem.BillLineItemId`. See [`feedback_contract_labor.md`](../../../.claude/projects/-Users-chris-Applications-build-one/memory/contract_labor.md).

### Sprocs (EmployeeLabor + EmployeeLaborLineItem)

Mirror the ContractLabor sproc set 1:1, renamed:

```
CreateEmployeeLabor / ReadEmployeeLaborById / ReadEmployeeLaborByPublicId
ReadEmployeeLaborsPaginated (filters: EmployeeId, ProjectId, Status, BillingPeriodStart, StartDate, EndDate)
CountEmployeeLabors
UpdateEmployeeLaborById
DeleteEmployeeLaborById
ReadEmployeeLaborByNaturalKey (@EmployeeId, @ProjectId, @WorkDate, @BillingPeriodStart)
ReadEmployeeLaborsByEmployeeId / …ByBillingPeriod / …ByStatus
ReadEmployeeLaborDailySummary (@EmployeeId, @WorkDate)
UpdateEmployeeLaborStatusByIds (@Ids, @Status, @InvoiceLineItemId=NULL)
UpdateEmployeeLaborStatusAndLink (@Id, @RowVersion, @Status, @InvoiceLineItemId=NULL)

CreateEmployeeLaborLineItem / ReadEmployeeLaborLineItemsByEmployeeLaborId
ReadEmployeeLaborLineItemById / …ByPublicId
UpdateEmployeeLaborLineItemById
DeleteEmployeeLaborLineItemById / DeleteEmployeeLaborLineItemsByEmployeeLaborId
```

### `InvoiceLineItem` additions

```sql
ALTER TABLE dbo.InvoiceLineItem ADD EmployeeLaborLineItemId BIGINT NULL
    CONSTRAINT FK_InvoiceLineItem_EmployeeLaborLineItem
    FOREIGN KEY REFERENCES dbo.EmployeeLaborLineItem(Id);
```

`SourceType` discriminator gains a new value `'employee_labor'`. **No CHECK constraint update needed** (the column has none today).

Existing service code touched:
- `entities/invoice/business/enrichment.py::enrich_line_items()` — add an `employee_labor` branch fetching `Employee.Firstname/Lastname`, `Project`, `SubCostCode`, `EmployeeLabor.WorkDate`. (No attachment — employee labor has no PDF source like ContractLabor's generated bill PDF does. Open call: do we want a stub PDF per EmployeeLabor row anyway for review consistency? Lean: no, omit.)
- `entities/invoice/api/router.py` — packet TOC "Type" column shows `'EmpLabor'` for the new source.
- React invoice edit page sort: extend `(type_order, vendor_name.lower())` — add EmpLabor as type_order = 3 (after Bill/Credit/Expense).

### Naming nit (one to resolve)

`'employee_labor'` vs `'EmployeeLabor'` vs `'employeelabor'` for the SourceType string. Existing values from `enrichment.py` use `'Bill'` / `'Expense'` / `'BillCredit'` / `'Manual'` — PascalCase. I'll use **`'EmployeeLabor'`** to match unless you say otherwise.

---

## Cross-cutting: what's deliberately NOT in this sketch

- **`ContractLabor.SourceTimeEntryId` + `ContractLaborLineItemTimeLog` join table** — that's Phase 5 (schema rationalization). Phase 3 ContractLabor stays untouched; only EmployeeLabor is added.
- **`UserService.set_worker_link` XOR enforcement** — service code, not schema.
- **Aggregation sproc (`AggregateTimeEntryOnSubmit`)** — that's Phase 4.
- **Excel importer rewrite** — Phase 6.
- **React UI pages** — Phase 7.
- **CompanyId / CreatedByUserId service-layer threading** — follows existing Phase 5 + Gap 2 pattern automatically via the DEFAULT trick; sprocs may grow `@CreatedByUserId BIGINT = NULL` params in a later threading sweep (Gap 2 already covered ~30 entities; new entities follow suit).

## Open sub-decisions surfaced inline (recap)

1. **`User` XOR rule via CHECK constraint vs service layer** — leaning service layer.
2. **Address fields from `VENDOR_CONFIG`** — move to `Vendor` columns, or use existing `Address` entity if linked. To verify in Phase 2 implementation.
3. **`vendor_project_rate` package location** — standalone `entities/vendor_project_rate/` vs sub-package under `entities/vendor/`. Lean standalone.
4. **EmployeeLabor natural-key grain** — `(EmployeeId, ProjectId, WorkDate, BillingPeriodStart)` per decision #2. Reject if you want Period-level instead of Day-level.
5. **EmployeeLaborLineItem → InvoiceLineItem back-ref** — added as a nullable FK. Update sproc must use CASE WHEN preserve-on-NULL.
6. **Stub PDF for EmployeeLabor rows** — leaning no.
7. **`SourceType` string value** — `'EmployeeLabor'` PascalCase to match existing convention.

## Files this will produce (when approved + Phase 1 starts)

| Phase | New file |
|---|---|
| 1 | `entities/employee/sql/dbo.employee.sql` |
| 1 | `entities/employee/{api,business,persistence}/…` |
| 1 | `entities/user/sql/migrations/2026_05_27_add_worker_links.sql` |
| 2 | `entities/vendor/sql/migrations/2026_05_27_add_rate_columns.sql` |
| 2 | `entities/employee/sql/migrations/2026_05_27_add_rate_columns.sql` (rate cols already in Phase 1) |
| 2 | `entities/vendor_project_rate/sql/dbo.vendor_project_rate.sql` |
| 2 | `entities/employee_project_rate/sql/dbo.employee_project_rate.sql` |
| 3 | `entities/employee_labor/sql/dbo.employee_labor.sql` |
| 3 | `entities/employee_labor/{api,business,persistence}/…` |
| 3 | `entities/invoice/sql/migrations/2026_05_27_add_employee_labor_source.sql` |

---

**Reviewer asks:**

1. Sign off on the Employee entity shape (column list + sproc set).
2. Confirm `User.EmployeeId`/`VendorId` + service-layer XOR (or push for CHECK constraint).
3. Sign off on `VendorProjectRate` / `EmployeeProjectRate` shape + rate-lookup precedence (incl. step 4 = error, not silent $0).
4. Sign off on EmployeeLabor + EmployeeLaborLineItem shape + Invoice source addition.
5. Resolve the 7 open sub-decisions in the recap above.

When all 5 are green, Phase 1 implementation kicks off — schema migrations first, then service/API/React/backfill in that order.
