# U-467 (LS-03c) — Expense stored `Status` column — /em build packet

> **Gate 1 APPROVED by Chris, 2026-09-16.** Nothing was built in the approving session — it was mapped
> only. This document IS the dispatch packet: hand the "Builder instruction" section below to the builder
> verbatim. Written because the approving session's scratchpad is wiped between sessions
> (`feedback_tmp_worktree_wiped_commit_wip_early`).
>
> **Board row:** `build.one.team/BOARD.md` → In flight → U-467.
> **Design:** `docs/design/u357-unified-status-review-status.md` Phase 3 (LS-03c) + §9a/§9b.
> **Template commits:** `be66c676` (U-445) and `c75ed59a` (U-446) — Bill's two halves, shipped here as ONE slice.

## 1. Why this unit exists

Chris's assignment: bring Expense to the `status` / `review_status` functionality Bill has, and to the Bills
web layout. The map found Bill's lifecycle took nine units across two repos and Expense has exactly one of
them (U-457, the derived read block) plus the entity-agnostic shared pieces (U-444 flags, U-454 inbox/review
lock, U-455 frozen kind, U-458 completion gate + `is_draft` closure). Everything else is missing.

Scoped by `/em` as **four serialized units**. U-467 is the first and the only one approved:

| Unit | Repo | Scope | State |
|---|---|---|---|
| **U-467** | api | `Status` column + backfill + CKs + index + computed `IsDraft` + `FinalizeExpenseById` + `TransitionExpenseStatus` + `?status=`/date-range + U-447 single-snapshot + `CreateReview` mirror + Purchase-connector origin | **Gate 1 APPROVED — build this** |
| U-468 | api | Terminal lock (422 `status_locked`) + atomic delete cascade + `DeleteReviewsByExpenseId` (U-446b/U-446c slice; also closes 3 open U-433 findings) | Ready, not opened |
| U-469 | api | Agent/MCP lifecycle vocabulary for Expense (U-446d slice) | Ready, not opened |
| U-470 | web | Bills card layout + six status tabs + filters + pagination + badges + View attachment section | Ready, blocked on U-467 |

Bundling U-467 through U-469 into one unit is how U-458 earned three Codex rejections. Keep them serialized.

## 2. Tier, builder, reviewer

- **Tier: P0-surface** — money document, ROWVERSION transition sproc, cascade-adjacent, QBO connector create path.
- **Builder:** `cursor-grok-4.6-xhigh` (**non-fast, deliberate** — the P0 rule).
- **Reviewer:** Codex was **out of credits** on 2026-09-16 (F0 probe: `ERROR: Your workspace is out of credits`).
  Re-probe first (`codex exec --model gpt-5.6-terra ... "Reply with exactly the word: HEALTHY"`). If still down:
  **F2 is the floor**, and **F3 is indicated** because this unit changes `CreateReview`, a shared primitive with
  five parents. F3 needs ultracode on. Slice the hunt by: backfill correctness · SQL apply-safety/ordering ·
  ROWVERSION + the new `CreateReview` RowVersion bump · money/`Decimal` · RBAC + actor params on the list sprocs ·
  external-write gate (Purchase connector) · API contract (`?status=` 422, `{"data":…}` envelope) · test adequacy.
  Then an adversarial `Verify` phase that tries to refute each finding.
- **Then:** `/simplify` (Pass 2), and `/security-review` (Pass 3, P0-surface) — ⛔ `git add -N` the unit's paths
  first and confirm `git diff HEAD --stat` is non-empty, or the security pass silently reviews nothing.

## 3. Read-only prod census — run 2026-09-16, the numbers the backfill is written against

| Fact | Value |
|---|---|
| `dbo.Expense` rows | **11,753** |
| `IsDraft = 0` | **11,743** — every one has both `QboId` and `RealmId` |
| `IsDraft = 1` | **10** — none has a `QboId` |
| `dbo.Review` rows with `ExpenseId` | **0** |
| `dbo.CompletionJob` rows for Expense | **0** (only Bill 416, BillCredit 1) |
| `MAX(LEN(CONCAT('qbo:',RealmId,'/',QboId)))` | **26** (fits `NVARCHAR(64)`); zero QboId-without-RealmId |
| `IsDraft` default | **`DF__Expense__IsDraft__367CE370`** — system-named; resolve via `sys.default_constraints`, never hardcode |
| Blockers on `DROP COLUMN IsDraft` | **none** — no index, no expression dependency, no CHECK, no user-created statistic (only auto `_WA_Sys_0000000B_33A076C5`) |
| Live `dbo.Expense` columns | 18, incl. `CompanyId` + `CreatedByUserId` that the base `CREATE TABLE` never declares — **leave both alone** |

**So the backfill maps: 11,743 → `completed`/`qbo_pull`, 10 → `draft`/`backfill`.** The `completion` origin
branch matches zero rows today but is still written, because the design names it and CompletionJob will
accumulate Expense rows going forward.

**base==live proof (run 2026-09-16, normalised per `reference_base_vs_live_sproc_diff.md`):**
`dbo.expense.sql` **11/12 identical**, `dbo.review.sql` **21/21 identical**. The single difference is
`ReadExpenseByPublicId`, where **the base file is AHEAD of prod** — it projects `[QboId]`/`[RealmId]` (U-354)
and live does not. Keep the base version; the apply brings prod forward. **No reconcile is needed.**
⚠️ Re-run this proof before applying — it is a point-in-time fact.

**Live parameter lists that MUST be preserved verbatim and in order** (dropping or reordering one is SQL 8145
on every request — the U-037 class):

```
CreateExpense          @VendorId @ExpenseDate @ReferenceNumber @TotalAmount @Memo @IsDraft @IsCredit @SourceEmailMessageId @CreatedByUserId
UpdateExpenseById      @Id @RowVersion @VendorId @ExpenseDate @ReferenceNumber @TotalAmount @Memo @IsDraft @IsCredit
ReadExpensesPaginated  @PageNumber @PageSize @SearchTerm @VendorId @StartDate @EndDate @IsDraft @IsCredit @SortBy @SortDirection @ActorUserId @ActorIsSystemAdmin
CountExpenses          @SearchTerm @VendorId @StartDate @EndDate @IsDraft @IsCredit @ActorUserId @ActorIsSystemAdmin
```

Note `@StartDate`/`@EndDate` **already exist** on both list sprocs — the date-range work is router-side only.

## 4. The eight contested calls, and how /em resolved them

The map was produced by seven parallel read-only scouts plus a completeness critic; they disagreed on eight
points. These are settled — a reviewer re-opening one should be shown this section.

1. **`FinalizeExpenseById` writes `Status='completed'`** (origin `completion`, guard `Status <> 'completed'`),
   the post-U-446 form. One scout mapped a Status-less Expense and proposed U-434's original
   `SET IsDraft = 0 WHERE IsDraft = 1`; that **fails at execution** once `IsDraft` is computed in this same unit.
2. **One slice, straight to the end state.** U-445 + U-446 together; `IsDraft` ends PERSISTED COMPUTED. No
   dual-write image is ever deployed, so the three-step swap the design describes does not apply (same reasoning
   the design's own Phase-3 rebase block gives for Bill).
3. **Keep `@IsDraft` declared** on `CreateExpense`/`UpdateExpenseById` as the compat fallback, and keep the repo
   sending it. Dropping the param while the current image is live is SQL 8145 on every create and update.
   Verified harmless: a stored `@IsDraft = 0` on a completed row hits `Status <> 'completed'` = false; `@IsDraft = 1` is neutralised.
4. **Reproduce the `CK_Expense_Status_IsDraft` create-then-drop pair.** It is the ONLY in-file proof the backfill
   mapped every row before `IsDraft` is dropped — `run_sql.py` never surfaces `PRINT`, so a print-based check is
   invisible, and the whole file is one transaction. (Two scouts wanted it skipped as "tautological once computed";
   that reasoning applies to the end state, not to the apply.)
5. **Backfill origin keeps the `CompletionJob` branch** the design names (`§4.3`), guarded on
   `OBJECT_ID('dbo.CompletionJob','U') IS NOT NULL` for from-scratch builds. Census says it maps 0 rows today.
6. **The Purchase connector passes the status triple**, not `is_draft=False` — the design's Phase-3 Expense note.
   ⚠️ **This exposed a latent Bill gap:** `BillService.create` / `BillRepository.create` / the Bill connector never
   bind `CreateBill`'s `@Status`/`@StatusOrigin`/`@StatusSourceRef`, so **every QBO-born Bill since U-445 carries
   `StatusOrigin='user'` and a NULL `StatusSourceRef`**, and `docs/design/u357…:64`'s claim that the Bill connector
   writes `qbo_pull` is **false against code**. Booked as a follow-on (§6 below); correct the doc line in this unit.
7. **Port U-447's single-snapshot page+total** into `ReadExpensesPaginated`. Without it `count` is a second
   snapshot and the U-470 web tabs inherit a count that can disagree with its own rows. This also makes the U-445
   test clone (which asserts the U-447 shape) applicable as written.
8. **Two-file atomic apply.** The `CreateReview` Expense mirror couples `dbo.review.sql` to `dbo.expense.sql`.
   `run_sql.py` commits **per invocation**, so two invocations are two transactions with a live window in which
   Expense stores `Status` but `CreateReview` does not mirror it — any review created in that window is stamped
   `draft` **forever**, because the backfill's `NOT EXISTS (StatusDatetime IS NOT NULL)` guard is already satisfied
   and no re-apply repairs it. The reverse order fails outright (error 207: the mirror names a column that does not
   exist yet). **Apply both files on ONE connection with ONE commit.** The mirror is placed BEFORE the Bill block
   so `test_u445`'s slice-from-`UPDATE b`-to-EOF assertion stays green.

## 5. Apply / deploy order (Gate-2 asks, each separately, `/em` runs them — builders never touch prod)

**SQL FIRST is the only safe order, and it is safe.** With the schema applied ahead of the image, the *current*
container keeps working: `CreateExpense` keeps `@IsDraft` and lands `Status` via the `COALESCE` fallback;
`UpdateExpenseById` translates `@IsDraft = 0` into `Status='completed'`, so the old `complete_expense` still
completes and reads `IsDraft = 0` back off the computed column; the new `@Status` params default NULL and the old
repo never binds them; the old `_from_db` ignores the new projected columns.

**Code first is unsafe three ways:** the new repo binds `Status` on the list sprocs (8145 on every
`GET /get/expenses`), calls `FinalizeExpenseById` before it exists (hard 500 on every completion → job marked
failed → watchdog re-drives to dead-letter), and raises on the missing second result set from U-447.

1. Re-run the base==live proof (12 expense + 21 review sprocs) and the census above.
2. **Rehearse** both SQL files against prod inside a **rolled-back** transaction on one connection: confirm
   `CreateExpense`'s `OUTPUT INSERTED.[IsDraft]` works on the computed column, `FinalizeExpenseById` is idempotent,
   `UpdateExpenseById @IsDraft=0` still completes a draft, `TransitionExpenseStatus` refuses an illegal move,
   `UPDATE dbo.Expense SET IsDraft = 1` is refused, and `COUNT(IsDraft=0) == COUNT(Status='completed')` with zero
   disagreements. Record the schema-modification-lock stall (Bill's 20k rows blocked reads 3.5–10s and dropped one
   connection; Expense has 11.7k). Apply in a quiet window, outside a QBO purchase pull tick.
3. **Apply for real** — `dbo.expense.sql` + `dbo.review.sql`, ONE connection, ONE commit.
4. Post-apply: `sys.columns` shows the four Status columns and `IsDraft` with `is_computed=1, is_nullable=0`;
   `sys.check_constraints` lists `CK_Expense_Status` + `CK_Expense_StatusOrigin` (and NOT `CK_Expense_Status_IsDraft`);
   `sys.parameters` shows the preserved lists plus the new params; the 8 untouched expense sprocs and all 21 review
   sprocs still base==live.
5. **Then** `az acr build` + `az webapp stop`/`start` (NOT `restart` — serves a stale image), tag MUST be `:latest`,
   verify the ACR digest moved. Behavioral sentinel, not a 200: `GET /api/v1/get/expenses?status=completed` returns
   a count near 11,743, an unknown status 422s naming the six values, and one QBO purchase pull tick creates a row
   with `Status='completed'` / `StatusOrigin='qbo_pull'`.

## 6. Booked, NOT built here

- **Bill connector origin parity** — `_create_bill` / `BillService.create` / `BillRepository.create` never bind
  `CreateBill`'s `@Status`/`@StatusOrigin`/`@StatusSourceRef`; QBO-born Bills since U-445 carry `StatusOrigin='user'`.
  Same three-layer fix, plus an `/em`-applied one-off `UPDATE` for rows with
  `QboId IS NOT NULL AND StatusOrigin='user' AND StatusDatetime >= <U-445 deploy>`.
- **Expense completion-result is process-local** (`_EXPENSE_COMPLETION_RESULT_CACHE`, `entities/expense/api/router.py`)
  while Bill persists to `dbo.BillCompletionResult`. With `-w 2` workers the poll can miss the worker that ran the job.
  Port = new table + Upsert/Get sprocs + repo + route + `_run_complete_expense` write; changes 404 semantics from
  TTL-expiry to never-ran.
- **`POST /ensure-expense-from-qbo-purchase/{id}` authz gap** — a `QBO_SYNC.can_create` human reaches a
  create-as-completed path without entering `system_authz`. Inherits this unit's connector change; the authz decision
  (wrap in `system_authz()` vs refuse) is its own unit.
- **U-433 open findings** untouched here: delete-cascade blob/FK inversion, child-entity row-scoping bypass,
  stranded finalized parent on a line-item ROWVERSION conflict, non-atomic `SetExpenseQboIdentity`. Most land in U-468.
- **`ExpenseLineItem.IsDraft`** stays a real, writable column (design §5). Do not sweep it in with a broad regex.

---

## 7. Builder instruction — hand the rest of this file to the builder verbatim

Dispatch:

```bash
cd /Users/chris/Applications/build.one/build.one.api && cursor-agent -p --model cursor-grok-4.6-xhigh --force --trust "$(sed -n '/^### BEGIN BUILDER INSTRUCTION/,/^### END BUILDER INSTRUCTION/p' docs/handoff-u467-em.md)" </dev/null
```

### BEGIN BUILDER INSTRUCTION

You are the Backend Engineer for build.one.api (FastAPI + SQL Server stored procedures; router -> service -> repo -> sproc). Read CLAUDE.md first and follow it. Unit: U-467 (LS-03c). Work ONLY in this repo. Do NOT commit, do NOT run anything against a database, do NOT run scripts/run_sql.py, do NOT deploy. Do not touch build.one.web or any sibling repo.

GOAL: give dbo.Expense a stored lifecycle `Status` column exactly as Bill got in U-445 (commit be66c676) and U-446 (commit c75ed59a), shipped as ONE slice straight to the end state (IsDraft ends as a PERSISTED NOT NULL computed column over Status), plus FinalizeExpenseById replacing the is_draft=False PUT in complete_expense, and `?status=` / `?start_date=` / `?end_date=` on GET /get/expenses with page+total from ONE materialized set (the U-447 shape). Read both Bill commits in full (`git show be66c676`, `git show c75ed59a`) and use dbo.bill.sql + dbo.bill_create_source_email.sql as the byte-level template; rename Bill->Expense. The six statuses are in shared/lifecycle/resolver.py LIFECYCLE_STATUSES.

PROD FACTS (census 2026-09-16, read-only): dbo.Expense has 11,753 rows: 11,743 IsDraft=0 (ALL have QboId and RealmId), 10 IsDraft=1 (no QboId). dbo.Review has 0 Expense rows. dbo.CompletionJob has 0 Expense rows. MAX LEN of CONCAT('qbo:',RealmId,'/',QboId) = 26; no NULL RealmId. Live IsDraft default is system-named DF__Expense__IsDraft__367CE370 (never hardcode it). No index, dependency, CHECK, or user statistic on IsDraft. Live dbo.Expense has 18 columns incl. CompanyId and CreatedByUserId that the base CREATE TABLE does not declare - never touch them. Base==live proof: 11/12 expense sprocs identical, 21/21 review sprocs identical; ReadExpenseByPublicId in the base is AHEAD of live (it projects QboId/RealmId from U-354) - keep the base version. Live param lists you MUST preserve verbatim and in order: CreateExpense(@VendorId,@ExpenseDate,@ReferenceNumber,@TotalAmount,@Memo,@IsDraft,@IsCredit,@SourceEmailMessageId,@CreatedByUserId); UpdateExpenseById(@Id,@RowVersion,@VendorId,@ExpenseDate,@ReferenceNumber,@TotalAmount,@Memo,@IsDraft,@IsCredit); ReadExpensesPaginated(@PageNumber,@PageSize,@SearchTerm,@VendorId,@StartDate,@EndDate,@IsDraft,@IsCredit,@SortBy,@SortDirection,@ActorUserId,@ActorIsSystemAdmin); CountExpenses(@SearchTerm,@VendorId,@StartDate,@EndDate,@IsDraft,@IsCredit,@ActorUserId,@ActorIsSystemAdmin). New params are APPENDED or inserted where Bill inserted them, all defaulting NULL, so the currently deployed image keeps working after the SQL is applied (SQL is applied before the image).

=== SQL: entities/expense/sql/dbo.expense.sql (sole home of its 12 sprocs - tests/test_sproc_single_source.py; every GO batch runs on every apply, so every DDL batch must be self-guarded; terminate every batch with GO; every new sproc starts with SET NOCOUNT ON) ===
1. Rewrite the header (lines 1-14): keep the bullet that the CREATE TABLE omits IsCredit/SourceEmailMessageId/CompanyId/CreatedByUserId; DELETE the bullet claiming the list sprocs lack actor params (false since U-089/U-100); add, within the first 2000 characters, the atomic-apply banner: this file and entities/review/sql/dbo.review.sql must be applied in **ONE** TRANSACTION on one connection (schema first leaves a window where Expense stores Status but CreateReview does not mirror it; review first fails with 207 because the mirror names a column that does not exist yet); name SQL error 271 as the failure mode of the old CreateExpense against a computed IsDraft. Mirror the wording of dbo.bill.sql lines 1-19.
2. After the QboId/RealmId/SyncToken guarded block (~lines 81-108), add four guarded ALTER TABLE batches (template dbo.bill.sql 147-176): [Status] NVARCHAR(20) NOT NULL CONSTRAINT [DF_Expense_Status] DEFAULT ('draft'); [StatusDatetime] DATETIME2(3) NULL; [StatusOrigin] NVARCHAR(24) NOT NULL CONSTRAINT [DF_Expense_StatusOrigin] DEFAULT ('user'); [StatusSourceRef] NVARCHAR(64) NULL. Each guarded on OBJECT_ID('dbo.Expense','U') IS NOT NULL AND NOT EXISTS (sys.columns ...).
3. Backfill batch (template dbo.bill.sql 176-253): body inside EXEC sp_executesql N'...' (dbo.Review carries FK_Review_Expense, so an ad-hoc batch fails at compile time on a from-scratch build); guard on OBJECT_ID of dbo.Expense, dbo.Review, dbo.ReviewStatus AND dbo.CompletionJob all not null, AND EXISTS rows, AND NOT EXISTS (StatusDatetime IS NOT NULL) so it runs once. Set-based UPDATE TOP (5000) loop. Status CASE: IsDraft=0 -> 'completed'; latest review IsDeclined -> 'declined'; IsFinal -> 'approved'; IsInitial -> 'submitted'; a review exists -> 'in_review'; else 'draft' - the latest review chosen with EXACTLY the ordering ReadCurrentReviewByExpenseId uses (dbo.review.sql). StatusOrigin CASE: IsDraft=0 AND EXISTS (dbo.CompletionJob cj WHERE cj.EntityType='Expense' AND cj.EntityPublicId=e.PublicId AND cj.Status='completed') -> 'completion'; QboId IS NOT NULL -> 'qbo_pull'; else 'backfill'. StatusSourceRef = CONCAT('qbo:',RealmId,'/',QboId) when QboId IS NOT NULL AND RealmId IS NOT NULL else NULL. StatusDatetime = SYSUTCDATETIME().
4. AFTER the backfill, three WITH CHECK constraints (template dbo.bill.sql 255-292): [CK_Expense_Status] over the six canonical values; [CK_Expense_StatusOrigin] IN ('user','completion','qbo_pull','fast_path','backfill'); [CK_Expense_Status_IsDraft] ((CASE WHEN [Status]='completed' THEN 0 ELSE 1 END) = [IsDraft]) created ONLY when sys.columns says IsDraft is_computed = 0 - it is the in-file parity proof of the backfill and is dropped again in step 6.
5. Filtered index [IX_Expense_Status] ON dbo.[Expense]([Status]) INCLUDE ([VendorId],[ExpenseDate]) WHERE [Status] <> 'completed' (template 294-305). Never INCLUDE IsDraft.
6. The computed-column swap (template dbo.bill.sql 307-397), one batch guarded on IsDraft is_computed = 0: DROP INDEX [IX_Expense_Status]; DROP CONSTRAINT [CK_Expense_Status_IsDraft]; find the IsDraft default via sys.default_constraints JOIN sys.columns and drop it through sp_executesql with QUOTENAME; ALTER TABLE DROP COLUMN [IsDraft]; ALTER TABLE ADD [IsDraft] AS (CASE WHEN [Status] = 'completed' THEN CAST(0 AS BIT) ELSE CAST(1 AS BIT) END) PERSISTED NOT NULL  (NOT NULL is explicit and load-bearing); recreate [IX_Expense_Status]. Place all DDL (steps 2-6) BEFORE the sproc redefinitions, as dbo.bill.sql does.
7. CreateExpense (~124-167): keep all 9 params verbatim; APPEND @Status NVARCHAR(20) = NULL, @StatusOrigin NVARCHAR(24) = NULL, @StatusSourceRef NVARCHAR(64) = NULL; REMOVE [IsDraft] from the INSERT column list and @IsDraft from VALUES (error 271 otherwise); INSERT [Status],[StatusDatetime],[StatusOrigin],[StatusSourceRef] = COALESCE(@Status, CASE WHEN @IsDraft = 0 THEN 'completed' ELSE 'draft' END), @Now, COALESCE(@StatusOrigin,'user'), @StatusSourceRef; keep OUTPUT INSERTED.[IsDraft] (computed, readable) and add the four Status columns to the OUTPUT; keep COALESCE(@CreatedByUserId,17); SET NOCOUNT ON. Template dbo.bill_create_source_email.sql.
8. UpdateExpenseById (~373-418): keep all 9 params verbatim; DELETE the `[IsDraft] = CASE ...` SET line; add the compat translation from dbo.bill.sql 777-797 (@IsDraft = 0 AND [Status] <> 'completed' -> Status 'completed', StatusDatetime @Now, StatusOrigin 'completion'; @IsDraft = 1 is neutralised - never un-complete); keep the IsCredit CASE; OUTPUT the computed IsDraft plus the four Status columns; SET NOCOUNT ON.
9. Project e.[Status], e.[StatusDatetime], e.[StatusOrigin], e.[StatusSourceRef] in EVERY read path: ReadExpenses, ReadExpenseById, ReadExpenseByQboIdAndRealmId, ReadExpenseByPublicId (keep its QboId/RealmId), ReadExpenseByReferenceNumberAndVendorId, ReadExpensesPaginated, and the OUTPUT of DeleteExpenseById. Parameter lists byte-identical.
10. ReadExpensesPaginated + CountExpenses: add `@Status NVARCHAR(20) = NULL` in the position Bill used (after @IsCredit; before @SortBy / before @ActorUserId) and the predicate `AND (@Status IS NULL OR e.[Status] = @Status)` in BOTH; keep @IsDraft/@IsCredit/@StartDate/@EndDate predicates and the dbo.UserCanAccessExpense(@ActorUserId,@ActorIsSystemAdmin,e.[Id]) = 1 predicate (tests/test_list_sproc_scoping.py). Port the U-447 single-snapshot shape into ReadExpensesPaginated (template dbo.bill.sql 1051-1190): SET NOCOUNT ON; filter ONCE into #FilteredExpenses; page from it (keep the existing @SortBy/@SortDirection behaviour and the existing tiebreaker); then SELECT COUNT(*) AS [TotalCount] FROM #FilteredExpenses; DROP TABLE #FilteredExpenses. CountExpenses stays (other callers) and gains @Status.
11. NEW FinalizeExpenseById (@Id BIGINT) after UpdateExpenseById (template dbo.bill.sql 1322-1372, the POST-U-446 form): SET NOCOUNT ON; BEGIN TRANSACTION; UPDATE dbo.[Expense] SET [Status]='completed', [StatusDatetime]=SYSUTCDATETIME(), [StatusOrigin]='completion', [ModifiedDatetime]=SYSUTCDATETIME() WHERE [Id] = @Id AND [Status] <> 'completed'; then an UNCONDITIONAL SELECT of the ReadExpenseById projection plus the four Status columns plus QboId/RealmId WHERE [Id] = @Id (presence-not-rowcount contract); COMMIT. No @RowVersion. Never assign [IsDraft].
12. NEW TransitionExpenseStatus cloned from dbo.bill.sql 1461-1544 (params @Id,@RowVersion,@FromStatuses,@ToStatus,@ActorUserId,@Origin,@SourceRef; STRING_SPLIT guard; AND [Status] <> @ToStatus; the `AND [Status] <> 'completed'` reopen fence from U-446c; SELECT WHERE [Id] = @Id AND [Status] = @ToStatus). Sproc only, no Python caller, matching Bill.

=== SQL: entities/review/sql/dbo.review.sql, CreateReview ===
Insert an Expense mirror block BEFORE the existing Bill mirror (`IF @BillId IS NOT NULL` ~line 577): `IF @ExpenseId IS NOT NULL BEGIN UPDATE e SET e.[Status] = @ReviewKind, e.[StatusDatetime] = @Now, e.[StatusOrigin] = 'user', e.[ModifiedDatetime] = @Now FROM dbo.[Expense] e WHERE e.[Id] = @ExpenseId AND e.[IsDraft] = 1; END`. Assign @ReviewKind (already computed once, U-455) - never re-read ReviewStatus flags. Alias `e.`. No additional UPDLOCK (the pre-INSERT guard at ~483-485 already holds the row; tests/test_u454 pins exactly 5 UPDLOCK reads and exactly four `AND [IsDraft] = 0;` guards - leave that guard untouched). The block, INCLUDING its comment, must not contain the literal 'completed' (tests/test_u445 slices the body from `UPDATE b` to end). Add a short comment that this mirror couples dbo.review.sql to dbo.expense.sql for a one-transaction apply.

=== PYTHON ===
- entities/expense/business/model.py: add status, status_datetime, status_origin, status_source_ref (Optional[str] = None) after is_credit; keep is_draft.
- entities/expense/persistence/repo.py: _from_db hydrates the four Status fields getattr-guarded (template entities/bill/persistence/repo.py 67-70); create() gains status/status_origin/status_source_ref kwargs sent as "Status"/"StatusOrigin"/"StatusSourceRef" while KEEPING the "IsDraft" key; read_paginated and count gain `status: Optional[str] = None` -> "Status"; read_paginated returns (rows, total) reading the second result set via cursor.nextset() and raises RuntimeError naming dbo.expense.sql if it is missing (template bill repo 511-571); new finalize_by_id(id) calling FinalizeExpenseById, returning _from_db(row) or None (template bill repo 474-500).
- entities/expense/business/service.py: create() gains the three status kwargs threaded to the repo (KEEP the is_draft parameter - tests/test_u458_completion_gate.py pins it); read_paginated/count gain status and read_paginated returns (rows,total); complete_expense: replace the 3-attempt update_by_public_id(is_draft=False) loop (~614-680) with the U-434 shape from entities/bill/business/service.py ~1560-1620: re-read -> 404 dict; vendor missing -> 400 dict; finalized = self.repo.finalize_by_id(id=expense.id); None -> 404 dict with 'Expense deleted during finalization'; exception -> 500 dict with message f'Error finalizing expense: {e}'; all of those keep 'expense_finalized': False; success path continues into the unchanged line-item loop and Steps 3-5. Delete `import time` (its only use was the dead sleep). Do NOT add assert_editable / any terminal lock (that is U-468) - adding it would refuse the QBO purchase pull.
- entities/expense/api/router.py: add `from fastapi import status as http_status` and re-point the two existing `status.HTTP_*` references to it (the new query param would shadow the module); GET /get/expenses gains, spelled EXACTLY `status: Optional[str] = Query(default=None, description=...)`, plus `start_date: Optional[date] = Query(default=None, ...)` and `end_date: Optional[date] = Query(default=None, ...)`; validate `isinstance(status, str) and status not in LIFECYCLE_STATUSES` -> ApiError 422 whose detail contains 'Expected one of:' (template entities/bill/api/router.py 193-290); forward status/start_date/end_date to service.read_paginated, take total from the (rows,total) tuple and DROP the separate service.count call; keep the U-457 one-batch review lookup and attach_lifecycle (stored status now wins); refresh the _expense_dict_with_lifecycle docstring; keep `"is_draft": True` on POST create and `"is_draft": None` on PUT update exactly as they are.
- integrations/intuit/qbo/purchase/connector/expense/business/service.py `_create_expense` (~203-212): replace `is_draft=False` with status='completed', status_origin='qbo_pull', status_source_ref=f'qbo:{realm_id}/{qbo_id}' (None if either half is missing); pass by keyword, no **splat; do NOT also pass is_draft. Leave the HIT-path update and the expense_line_item connector untouched (ExpenseLineItem.IsDraft is a real, separate column - out of scope).
- integrations/intuit/qbo/base/field_ownership.py PURCHASE app_owned block: add status, status_datetime, status_origin, status_source_ref; re-comment is_draft as derived from status / unwritable (template the BILL block).
- scripts/verify_propose_invoice_source_links_tier0.py new_expense(): INSERT Status 'completed' instead of IsDraft, exactly as new_bill() in the same file was changed by U-446. Leave new_expense_line_item() alone.
- Comment/docstring refreshes only (no logic): shared/lifecycle/resolver.py ~97-98 (remove expense from the column-less list); shared/lifecycle/terminal_lock.py ~172-186; entities/review/sql/dbo.inbox_tasks.sql ~283-287 (say Expense now has Status; do NOT introduce the bracketed string `[Status] <> 'completed'` anywhere in that file - test_u454 forbids it; the inbox arm stays on IsDraft); scripts/sync_qbo_purchase.py ~333; TODO.md ~4310-4313 (ensure-expense route now lands status='completed'/origin qbo_pull; the authz gap stays booked).
- docs/design/u357-unified-status-review-status.md: in the Phase-3 rebase block add an "LS-03c Expense - BUILT as U-467" note (what shipped, one-transaction apply of expense+review, backfill census numbers above); in §9b record the Expense IsDraft writer sweep (3 physical writers: CreateExpense INSERT, UpdateExpenseById SET, the verify script; service call sites: create x3, complete_expense x1); correct line ~64's claim that the Bill connector writes StatusOrigin='qbo_pull' (it does not - it never binds CreateBill's @Status params; note Bill parity as a booked follow-on).

=== TESTS (./.venv/bin/python -m pytest -q from the repo root; baseline 4187 tests / 264 files; conftest blocks live DB; no async plugin - write sync tests) ===
UPDATE deliberately: tests/test_u446d_agent_lifecycle_vocabulary.py ~271 `with_status == ["bill"]` -> `["bill", "expense"]` (leave the _SearchArgs pin at ~243-253 in place; fix its docstring - the Expense agent half is U-469); tests/test_u457_lifecycle_for_expense_billcredit_invoice.py: drop expense from test_no_status_filter_is_added_in_this_phase (~365-377), refresh the module docstring, and update _drive_list (~159-180) and the list-binding tests to the (rows,total) shape; tests/test_ls01d_pull_does_not_own_the_lifecycle.py ~115-133: each connector create site must carry EITHER is_draft OR status whose AST value is the Constant 'completed' (per-connector, not total-count), plus Expense-specific pins that the header connector carries status=='completed', status_origin=='qbo_pull', a status_source_ref keyword, and NO is_draft; update its docstrings. Stale docstrings naming Expense as status-less: tests/test_u445_bill_status_column.py ~145, tests/test_u446b_terminal_lock.py ~279, tests/test_u454_inbox_lifecycle_suppression.py ~63,161, tests/test_u458_completion_gate.py ~733-757.
NEW tests/test_u467_expense_status_column.py: clone from tests/test_u445_bill_status_column.py, Bill->Expense, EVERY test in that file except test_both_bill_sql_files_demand_an_atomic_apply (replace it with the Expense+Review pair: dbo.expense.sql head must carry "ONE** TRANSACTION" and "271"; dbo.review.sql's CreateReview Expense block must exist) - including the U-447 block (route reports the sproc total, ONE materialized set, filter evaluated once, repo reads the SECOND result set, missing total RAISES), the date-bound tests, NO_sproc_assigns_IsDraft, swap idempotent + `) PERSISTED NOT NULL;` + literal CASE polarity, nothing-anywhere-writes-Expense-IsDraft (OTHER pattern `Expense(Line|Coding|Folder|Completion)`), second apply does not resurrect the dropped constraint, from-scratch guard listing dbo.CompletionJob where Bill lists dbo.BillCompletionResult, backfill ordering == ReadCurrentReviewByExpenseId, the CreateReview mirror (`AND e.[IsDraft] = 1`, never assigns 'completed'). Add: CK_Expense_Status value list == LIFECYCLE_STATUSES; PURCHASE.app_owned contains the four status fields; a runtime pin using tests/test_u283b_purchase_qbo_identity_repoint.py's connector fixture asserting expense_service.create kwargs status=='completed', status_origin=='qbo_pull', status_source_ref=='qbo:<realm>/<id>' and 'is_draft' not in kwargs; clone tests/test_u434_complete_bill_finalize_and_job_marking.py for complete_expense (finalize_by_id called with id=, no update_by_public_id, no row_version kwarg, None -> 404 + expense_finalized False, vendor missing -> 400 and finalize not called, repo raise -> 500 'Error finalizing expense:', the module has no `time` attribute, sproc text: no @RowVersion, `WHERE [Id] = @Id AND [Status] <> 'completed';` in comment-stripped body, SET NOCOUNT ON first, `[QboId]` projected, and `[IsDraft] = 0` NOT assigned); clone the U-446c transition reopen-fence pin for TransitionExpenseStatus.
Keep green: tests/test_list_sproc_scoping.py, tests/test_sproc_single_source.py, tests/test_repo_sproc_param_contract.py, tests/test_sproc_nocount_shape_guard.py, tests/test_u454_*, tests/test_u455_*, tests/test_u458_*, tests/test_u435_*, tests/test_completion_job_*.
Finish with the full suite green and py_compile on every touched .py file. Report the exact test count, the list of files you changed, and anything you could not do.

OUT OF SCOPE (do not build): terminal lock / assert_editable / 422 status_locked (U-468); DeleteReviewsByExpenseId and atomic delete cascade (U-468); `status` on the Expense agent _SearchArgs / expense_specialist prompt (U-469); any web change (U-470); a persisted Expense completion-result table; the ensure-expense-from-qbo-purchase authz gap; ExpenseLineItem.IsDraft; Bill connector origin parity (book only).

### END BUILDER INSTRUCTION
