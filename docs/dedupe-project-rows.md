# Project Row Deduplication — Investigation + Cleanup

Handoff doc for a one-shot cleanup project. Drafted 2026-05-28 by a prior session that hit the issue while creating TimeLog rows for Michael Jacobson (the project picker returned 2-3 identical "HP2 - 4406 Harding Pike" / "OL - 925 Overton Lea" rows, forcing a "pick latest Id by default" workaround).

## Problem

`dbo.Project` has **11 name groups with duplicate rows — 25 rows total in dup sets out of 150 Project rows**. Every group follows the same pattern: one "original" row (created 2026-01-08 to 2026-04-18, with historical Bill / Expense / Invoice line items + UserProject grants) and 1-2 newer rows (created mostly 2026-05-15 to 2026-05-28, mostly empty except for a `UserProject` count of exactly 2 — explained below).

Duplicates cause:
- iOS project picker shows the same project 2-3x → workers pick the wrong Id; Michael's TimeLog 173 today went to HP2 Id 140 instead of the canonical 13.
- New transactional data (TimeLogs, fresh ExpenseLineItems) lands on the dup Ids, fragmenting per-project reporting.
- Bill review notifications / UserProject filtering can resolve to the empty-history Id and miss the actual reviewer chain.

## Verified state (2026-05-28)

```
Id    TL   UP   CL   BLI  ELI  IV   PA   Created     Name
 64    0   27   0    554  86   32   0    2026-01-08  BR-MAIN - 7550C Buffalo Road
157    0    0   0      0   7    0   0    2026-05-28  BR-MAIN - 7550C Buffalo Road
128    2   25   0    119  21   12   0    2026-02-10  HA - 206 Haverford Ave
155    0    0   0      0   0    0   0    2026-05-27  HA - 206 Haverford Ave
 13    8   25   0    215  37   18   0    2026-01-08  HP2 - 4406 Harding Pike
138    0    2   0      0   0    0   0    2026-05-15  HP2 - 4406 Harding Pike
140    1    2   0      0   2    0   0    2026-05-18  HP2 - 4406 Harding Pike
 55    0   25   0    127  41   11   0    2026-01-08  KA2 - 827 Kirkwood Ave
156    0    0   0      0   0    0   0    2026-05-27  KA2 - 827 Kirkwood Ave
 93   18   25   1    244 109   15   0    2026-01-08  MR2-MAIN - 1577 Moran Rd
147    0    2   0      0  10    0   0    2026-05-22  MR2-MAIN - 1577 Moran Rd
 23    0   25   0    278  61   18   0    2026-01-08  OL - 925 Overton Lea
148    1    2   0      0   5    0   0    2026-05-22  OL - 925 Overton Lea
132    0   25   0     10   0    1   2    2026-04-18  OL-PH - 925 Overton Lea
146    0    2   0      0   0    0   0    2026-05-22  OL-PH - 925 Overton Lea
149    0    2   0      0   0    0   0    2026-05-22  OL-PH - 925 Overton Lea
145    4   28   0      0   0    0   0    2026-05-22  OVH - 2031 Overhill Drive  ← anomaly
154    0    2   0      0   1    0   0    2026-05-26  OVH - 2031 Overhill Drive
 28    2   25   0    665 119   28   0    2026-01-08  SHT - 2012 Sunset Hills
141    0    2   0      0   0    0   0    2026-05-18  SHT - 2012 Sunset Hills
151    0    2   0      0   2    0   0    2026-05-23  SHT - 2012 Sunset Hills
129    0   25   0      1   0    0   2    2026-03-03  SJC - 1102 Stonewall Jackson
153    0    2   0      0   0    0   0    2026-05-26  SJC - 1102 Stonewall Jackson
 79    0   25   0     69  34    7   0    2026-01-08  SSC2 - 5620 S Stanford Ct
150    0    2   0      0   3    0   0    2026-05-22  SSC2 - 5620 S Stanford Ct
```

Cols: TL=TimeLog, UP=UserProject, CL=ContractLabor, BLI=BillLineItem, ELI=ExpenseLineItem, IV=Invoice, PA=ProjectAddress.

**The recurring `UP=2` on every new-Id row is the system-admin UserProject backfill ran on 2026-05-27** (Christopher id=17 + Claude Agent id=33 — see `build.one.api/TODO.md` ReadProjectsByUserId TODO). It is **not** organic UserProject data and should be deleted along with the dup row.

## FK tables to handle on merge

```
BillCreditLineItem.ProjectId
BillLineItem.ProjectId
ContractLabor.ProjectId
ContractLaborLineItem.ProjectId
EmployeeLabor.ProjectId          ← exists in schema, may be unused
EmployeeLaborLineItem.ProjectId  ← exists in schema, may be unused
EmployeeProjectRate.ProjectId    ← exists in schema, may be unused
ExpenseLineItem.ProjectId
Invoice.ProjectId
MsMessageProject.ProjectId
ProjectAddress.ProjectId
TimeLog.ProjectId
VendorProjectRate.ProjectId
```

## Goal

1. **Investigate** how the duplicates got created (root cause — see "Hypothesis to test" below).
2. **Define keep-Id per group** (mostly obvious — keep the original; OVH is the only ambiguous case).
3. **Merge** child row references from dup Ids → keep Id across all 13 FK tables.
4. **Delete** the now-empty dup Project rows + their UserProject backfill rows.
5. **Add a uniqueness constraint** on Project so this can't recur (subject to a decision call — see #6 below).
6. **Re-run** the system-admin UserProject backfill against the cleaned Project set if Claude Agent / Christopher lose grants on the kept Ids during the merge.

## Hypothesis to test (root cause)

Three of the dup-name dates correlate suspiciously:
- 2026-05-22 (5 new rows: 145, 146, 147, 148, 149, 150) — possibly a bulk create event.
- 2026-05-27 / 28 (5 new rows: 153, 154, 155, 156, 157) — possibly user/script activity.
- 2026-05-15 / 18 (138, 140, 141) — possibly iOS test sessions.

**Look at**:
- `dbo.Project.CreatedByUserId` on the dup rows — who created them? An agent (likely `project_specialist`)? A human? The scheduler?
- `dbo.Workflow` rows for `WorkflowType='project_create'` on those dates — the agent's `Context` payload should explain the `name` that was passed and whether `FindProjectForInvoice` was used or skipped before the create.
- `intelligence/agents/project_specialist/` — does the agent have a duplicate-check guard before calling `create_project`? Does it call `FindProjectForInvoice` first? The bill_specialist memory says **"Use `FindVendorForInvoice` — not `search_vendors` — for invoice-driven vendor binding"**. There may be an equivalent rule for projects that's not being followed.
- Recent React Project create form: does it warn on close-name match before submit?
- iOS: does anything in `build.one.ios/BuildOne/Services/Project/` accidentally POST a project create?

Likely culprit: **the `project_specialist` agent creating a fresh Project when it should be matching an existing one via `FindProjectForInvoice`**. The Wave 3 reviewer-reply automation (2026-05-07 per memory) added the bill_specialist's `find_sub_cost_code_for_reply` helper but the project_specialist's match-or-create discipline may be weaker.

## Decisions needed before code

1. **Keep-Id per dup group** — confirm the originals are the keepers in all 11 groups. **Special case**: OVH has Id 145 (4 TL, 28 UP, 2026-05-22) vs Id 154 (1 ELI, 2026-05-26). Neither is "the original" since the project itself was added in May. Recommend **keep 145** (more references, earlier created); merge 154 into 145.
2. **Workflow / agent audit before unique constraint** — should the unique constraint be added FIRST (force the agent to fail fast on future duplicates while we investigate) or LAST (after the agent fix lands)?
3. **Case-sensitive vs case-insensitive uniqueness** — `UNIQUE(Name, CompanyId)`? Or normalize to lower(Name)? SQL Server default collation is case-insensitive so plain `UNIQUE(Name, CompanyId)` already catches `"HP2 - …"` vs `"hp2 - …"`. Confirm with user.
4. **Active-only uniqueness?** If a project is ever soft-deleted (status='archived' or similar), do we allow the name to be reused? Recommend YES — filtered unique index `WHERE Status='active'`.
5. **Re-do system-admin UserProject backfill?** After merging, the kept Ids will have the original ~25 UserProject grants; the deleted dup Ids' system-admin grants disappear with them. Confirm Christopher + Claude Agent still resolve to the kept Ids post-merge (since the backfill already gave them grants on the originals too).
6. **Communicate to users?** Any pinned/bookmarked Project links to dup Ids (e.g., `/projects/138`) will 404 after merge. Probably negligible — confirm.

## Proposed phases

### Phase 0 — Audit (read-only, no DB writes)

- Query `Project.CreatedByUserId` for all 14 dup rows.
- Query `dbo.Workflow` for `WorkflowType LIKE '%project%'` between 2026-05-15 and today, ordered by CreatedDatetime. Extract who/what created each dup.
- Read `intelligence/agents/project_specialist/prompt.md` + `entities/project/intelligence/tools.py` — is there a match-first discipline?
- Read `entities/project/business/service.py::create` — is there any duplicate-detection guard at the service layer? Recommendation: probably should add one even after agent prompt fix.
- Output: brief report identifying the source(s).

### Phase 1 — Root-cause fix (so we don't have to repeat this cleanup)

Whichever combination of:
- Tighten `project_specialist` prompt to require `FindProjectForInvoice` (or build it if it doesn't exist for the project_specialist's tool surface — `FindProjectForInvoice` is mentioned in memory but verify it's accessible).
- Add a server-side duplicate check in `ProjectService.create()` — if name matches an existing active project under the same Company, return the existing PublicId rather than creating new. Or raise + force the caller to pass an `allow_duplicate=True` flag.
- Add a filtered unique index `UQ_Project_Name_CompanyId_Active` on `(Name, CompanyId) WHERE Status='active'`.
- React: warn on close-name match in the Create Project form.

### Phase 2 — Merge

For each dup group, in a single transaction per group:

1. UPDATE all 13 FK-referencing tables: `SET ProjectId = @keepId WHERE ProjectId IN (@dupIds)`.
2. DELETE any `UserProject` rows where `(UserId, ProjectId)` would now duplicate against the keep Id (system-admin backfill collision).
3. DELETE the dup Project rows.
4. Verify final state — each kept Id carries the union of children.

Script template: `scripts/migrations/dedupe_project_rows.sql`. Wrap in BEGIN TRANSACTION / COMMIT; build the keep-Id → dup-Id mapping at the top as a table variable / CTE; verify the count of affected rows per FK table before COMMIT. Optionally print before / after counts.

### Phase 3 — Add uniqueness constraint

`CREATE UNIQUE INDEX UQ_Project_Name_CompanyId_Active ON dbo.Project(CompanyId, Name) WHERE Status = 'active';` (or whatever the live filter ends up being per decision #4).

### Phase 4 — Re-run system-admin UserProject backfill

The 2026-05-27 backfill set `IsSystemAdmin=1 × every Project`. After merge, the deleted dup Ids vanish; the kept Ids already have the system-admin grant (since they were ALL projects from the backfill's perspective). No re-run needed unless the merge accidentally drops a grant.

### Phase 5 — Verify

- `SELECT Name, COUNT(*) FROM dbo.Project GROUP BY Name HAVING COUNT(*) > 1;` → 0 rows.
- iOS project picker shows 137 entries (148 active rows today minus ~11 dup deletions), no duplicate names.
- Michael's TimeLog 173 (created 2026-05-28 19:39, on ProjectId 140 = HP2 dup) and TimeLog 174 (on ProjectId 148 = OL dup) should be re-pointed to ProjectId 13 / 23 respectively during the merge.

## What NOT to do

- Do not delete dup Project rows before merging children — every FK is enforced.
- Do not add the unique constraint BEFORE merge — it'll fail on the existing dups.
- Do not run the merge against prod without backups + a dry-run COUNT(*) verification pass first.
- Do not assume the "kept" Id is the lowest Id — verify by the data-carrying counts above (OVH proves it).
- Do not change Project schema columns / sproc signatures as part of this — out of scope; if a column needs to change, file a separate ticket.

## Known landmines

- `dbo.UserProject` has a `(UserId, ProjectId)` natural-key likely already enforced — if it isn't, the merge will create duplicate rows for users who had grants on both the dup AND original Ids. Drop those duplicates as part of step 2.
- `dbo.ProjectAddress` has rows on the original SJC (id=129, 2 rows). Verify the merge doesn't create a duplicate address row if a dup Id somehow accumulated one (none do today, but worth defending).
- `dbo.MsMessageProject.ProjectId` may carry email-message → project bindings from the email_specialist pipeline. Re-pointing these to the kept Id is fine; verify the keep Id makes sense as the "real" project the message was about.
- iOS CoreData has `CDProject` rows cached client-side. After merge, iOS clients will still see the deleted Ids until they pull fresh. Force a re-sync hint in the next iOS release notes, or just let it self-correct on next `ReadProjectsByUserId` refresh.
- The 2026-05-27 ReadProjectsByUserId admin-bypass workaround backfill is still in place (see `build.one.api/TODO.md`). When that sproc is patched to honor `IsSystemAdmin`, the backfilled rows on the kept Ids become redundant but harmless.

## References

- This file: `build.one.api/docs/dedupe-project-rows.md`
- Related TODO: `build.one.api/TODO.md` — "ReadProjectsByUserId doesn't honor IsSystemAdmin bypass"
- Schema: `build.one.api/entities/project/sql/dbo.project.sql`
- Service: `build.one.api/entities/project/business/service.py`
- Agent: `build.one.api/intelligence/agents/project_specialist/`
- Project lookup tool (per memory, presumed to exist): `entities/project/intelligence/tools.py::find_project_for_invoice`
