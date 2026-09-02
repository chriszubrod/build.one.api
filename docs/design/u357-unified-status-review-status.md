# U-357 — Unified lifecycle `status` + `review_status` on every workflow entity (DESIGN / SCOPING)

**Status:** design/scoping, awaiting Chris's decisions (§9) and `/em` dispatch. **STOP — nothing is built,
no SQL is applied, no deploy is sequenced by this document.** Every unit below is a proposal until `/em`
promotes it; every SQL step is `/em`-applied (builders never touch prod).
**Unit id:** **U-357 — confirmed by `/em` 2026-09-01.** The U-349 Wave-C session had booked its line-item design
as U-357 (`45b8f278`) and then renumbered its block to U-361–364 (`75c62d23`) to cede U-357 to this program.
Phase-0 ids: `LS-00a` = **U-357a**, `LS-00b` = **U-357b**, `LS-00d` = **U-357d**; later `LS-xx` units get ids at dispatch.
**Citation freshness:** file:line references are as of 2026-09-01 after U-356 (`5c28a268`); `BOARD.md`/`TODO.md`
line numbers drift daily — prefer the unit id / anchor text when generating unit prompts from this doc.
**Class:** cross-repo lifecycle/vocabulary program (schema + sprocs + API + web + iOS + MCP + scheduler) →
design-gated, multi-unit, phased; one atomic multi-repo cutover (ContractLabor) inside it.
**Origin:** Chris (2026-09-01) — every workflow entity should carry one consistent `status` and one consistent
`review_status`, replacing today's mix (`IsDraft` bits on four financial documents, free-text `Status` on
labor, a history table on TimeEntry, review state stitched from `dbo.Review` on some endpoints only, a
ContractLabor-only one-way mirror, a canonical vocab in `shared/labor_status.py` with zero importers, and a
2026-07-02 rename migration that was never applied). Product direction: **Ground it, internal** — incremental,
low-risk units; web deploy ask-first; installed iOS clients must keep working.
**Inputs:** the U-357 survey (`survey_profiles_digest.txt`, `survey_consumers_critic.txt`, `survey_result.json`),
three proposals and three judgements. Tally: Proposal 1 (persisted Status + denormalized ReviewStatusId)
36+38+37 = **111**; Proposal 2 (read-model first) 35+37+38 = 110; Proposal 3 (one ledger) 33+32+31 = 96.
This design **builds on Proposal 1**, grafts every non-conflicting judge graft (read-model as the first value
unit, env-flagged completion gate, three-step column swap, StatusOrigin/SourceRef, `Outcome` on transitions,
never-422 on inbound `is_draft`, `status_source`, telemetry, CL dual-field + coordinated web window, guard-rail
extensions, TE `completed` write with wire alias, explicit delete/backfill decisions), and **drops** the
judge-listed fatal flaws (SQL-before-API completion window, two-sproc review write, 422 on create, bare-409
terminal lock, in-place `rejected` rename, `approved→declined` contradiction, web-trailing CL cutover).

---

## 1. Summary

Target model, in plain English: every workflow entity carries a **persisted, CHECK-guarded `Status`** column
holding the six canonical values (`draft / submitted / in_review / approved / declined / completed`) plus
`StatusDatetime / StatusOrigin / StatusSourceRef`, written only by one transition sproc per entity (completion)
and by `CreateReview` (review-caused values) inside the same transaction that inserts the insert-only
`dbo.Review` row. Every Review parent also carries a **denormalized `ReviewStatusId`** pointer (single writer:
`CreateReview`) so the RBAC-scoped list/GET sprocs project review state with one `LEFT JOIN` — no second call.
`is_draft` survives forever as a **derived** field (`status != 'completed'`), and on the four financial
documents `IsDraft` becomes a **persisted computed column** so every raw-SQL/sproc/MCP/iOS consumer keeps
working unchanged and the column can no longer be written (the swap redefines the four `Create*` sprocs first —
they INSERT `[IsDraft]` explicitly today). `review_status` keeps today's meaning (admin
display Name); the canonical, flag-derived `review_status_kind` is the value clients branch on. TimeEntry keeps
`dbo.TimeEntryStatus` as its ledger with a denormalized column and a wire alias for installed phones.
**First shippable step:** `LS-00b` — reconcile the half-migrated `entities/review/sql/dbo.review.sql` to the
live 5-parent shape and put it under the whole-file single-source guard (prerequisite to every later Review
touch); in parallel `/em` runs the read-only prod census (`LS-00a`) and iOS ships the forward-compat decoder
(`LS-00d`). First *user-visible* value is `LS-01a`: canonical `status` + `review_status_kind` on every
list/GET with **zero schema change**, using the same resolver expression that later becomes the backfill
specification and the daily drift oracle.

## 2. Glossary

| Term | Definition |
|---|---|
| `status` | The entity's **lifecycle** position, one of six canonical values below. Persisted (Phase 3+) in `dbo.<Entity>.[Status] NVARCHAR(20) NOT NULL` with `CK_<Table>_Status`; derived at the API boundary before that (Phase 1) by `shared/lifecycle/resolver.py` from `IsDraft` × latest Review row. The two definitions are provably identical because both use the rule `completed ⇔ IsDraft = 0` (every live consumer already treats `IsDraft = 0` as final: `dbo.budget_variance.sql:174,283`; `integrations/intuit/qbo/reconciliation/business/service.py:74,409-411`; `integrations/ms/reconciliation/business/excel_detector.py:148,307`; `entities/invoice/business/push.py:434-438`). |
| `review_status` | **Kept as today's wire field:** the admin-editable `dbo.ReviewStatus.Name` of the entity's current review row, or `null` (`entities/bill/api/router.py:156`; `build.one.web/src/types/api.ts:741`; rendered verbatim at `BillList.tsx:454`). Display only — never branch on it (rows are admin-editable through the LIVE U-155 CRUD — deployed 2026-07-29 per its BOARD row; BOARD line numbers drift daily, cite by unit id — `build.one.web/src/routes.tsx:285`, `entities/review_status/api/router.py:17,75,111`). |
| `review_status_kind` | **New, canonical, flag-derived:** `none / submitted / in_review / approved / declined`. Resolution keys ONLY on `IsDeclined`, `IsFinal`, `SortOrder`, `IsActive` — never on Name: `declined` if `IsDeclined=1`; else `approved` if `IsFinal=1`; else `submitted` if `SortOrder = MIN(SortOrder)` over active non-declined rows (the `ReadFirstReviewStatus` rule, `entities/review_status/sql/dbo.review_status.sql:184-205`); else `in_review` (any number of admin-added intermediate stages collapse here by design). |
| `draft` | Author editing; not in any reviewer inbox; `ReviewStatusId IS NULL`. Default on every non-system create (`DF_<Table>_Status DEFAULT 'draft'`). |
| `submitted` | A Review row whose status is the first active non-declined status was written; reviewer inbox has it. |
| `in_review` | A Review row at an intermediate (non-first, non-final, non-declined) status was written. Per-entity signal — **Bill:** system auto-advance when the review notification is enqueued (`entities/review/business/notification_service.py:271-287`), re-attributed to the system user (User 33), located by `get_next_status` not the `sort_order > 10` literal (:273); **ContractLabor:** the crew-draft claim in `cl_notification_service.enqueue_drafts` writes one intermediate row per CL under the system user (skipped when no intermediate status is active); **Expense/BillCredit/Invoice:** manual `/advance` only; **TimeEntry:** not used (`ReviewPriority/ReviewReasons` stay a triage sidecar, `dbo.time_entry.sql:1974-1990`). |
| `approved` | A Review row with `IsFinal=1 AND IsDeclined=0` was written. **Editable** (`shared/labor_status.py:74-78` — Chris revises after approval); edits never reset review state (§9 #23). |
| `declined` | A Review row with `IsDeclined=1` was written. Resting state; author edits in place and resubmits (`declined→submitted`, already allowed by `build_submit_payload`, `entities/review/business/service.py:260-267`). No `declined→draft` edge (would leave `draft` with a non-null pointer). |
| `completed` | Terminal. **Per entity, `completed` = the LOCAL flip + outbox fan-out ENQUEUED (today's semantics, made explicit); external landing is NOT a status value** — it stays visible via `status_source` (single GET: CompletionJob state, `BillCompletionResult`, `QboId`), the outbox rows and the daily reconciles. No `completing / posted / paid / void` values are introduced (`dbo.CompletionJob.Status` + `CompletionStatusBar` remain the async interstitial; `QboId` + `qbo.Outbox` own "posted"; `qbo.Invoice.Balance` stays in staging). Writers: **Bill** — `BillService.complete_bill` (`entities/bill/business/service.py:1465-1700`: Status flip replaces the `is_draft=False` PUT at :1499-1567, then line finalize :1580-1617, then SharePoint/MS-Excel/Box-Excel/QBO/Box enqueue :1648-1683), or `CompletionJob` reclaim under `system_authz` (`entities/completion_job/business/service.py:73`), or the QBO bill pull creating the row born completed (`integrations/intuit/qbo/bill/connector/bill/business/service.py:282`, `StatusOrigin='qbo_pull'`). **Expense** — `complete_expense` (`entities/expense/business/service.py:670-679` flip, :753-790 enqueue; QBO push disabled :781-782) or reclaim (:77) or the Purchase pull (`purchase/connector/expense/business/service.py:210`, the ~10K-row majority). **BillCredit** — `BillCreditCompleteService.complete_bill_credit` (`entities/bill_credit/business/complete_service.py:60-265`; SharePoint+Box only, no QBO push) or the VendorCredit pull (`vendorcredit/connector/bill_credit/business/service.py:224`). **Invoice** — `complete_invoice` (`entities/invoice/business/service.py:381-547`: lines finalized, source lines `IsBilled=1`, packet, SP/Excel col-H/Box enqueued, QBO push skipped :533-535) or the Invoice pull creating the row born completed (`invoice/connector/invoice/business/service.py:410`, the MISS-path create; the HIT-path update at `:205` stops forcing `is_draft` in LS-01d, and the adopt path already leaves lifecycle alone since U-356 — `_adopt_invoice_identity` :513 → `base/identity_fastpath.py::stamp_dbo_identity_with_lock`). "Draw pushed" stays derived from `ComputeInvoiceDrawMatrix`/`BilledSourceCount` (`dbo.invoice.sql:804-866`). **ContractLabor** — `generate_bills_for_vendor` when every project-anchored line has a `BillLineItemId` (`entities/contract_labor/business/bill_service.py:690-710`, today's `'billed'` at :700); **cross-entity implication stated:** the generated vendor Bill is itself born `draft` (`bill_service.py:437`) — CL completed ≠ vendor paid ≠ Bill completed. **TimeEntry** — written on the downstream CL completion, idempotent per `SourceTimeEntryId` (§9 #13); until then derived from `ReadTimeEntryBilledLineage` (`dbo.time_entry.sql:1888-1940`). **EmployeeLabor** — `invoiced` has no producer (`TODO.md:1473-1474`); mapped to `completed` only at fold-in. |
| `StatusOrigin` | Provenance of the last transition (persisted, `NVARCHAR(24) NOT NULL DEFAULT 'user'`, CHECK): `user / fast_path / system / agent / completion / qbo_pull / downstream / backfill / reopen`. `fast_path` marks the approver fast-path's Approved row (the only `draft|declined → approved` edge, §4.1); `qbo_pull` and `backfill` are the exemptions for the `completed ⇒ approved` invariant (replaces the `QboId IS NOT NULL` proxy — a locally completed then QBO-pushed bill also has a `QboId`). |
| `StatusSourceRef` | Optional pointer for audits: `completion_job:<public_id>`, `qbo:<realm>/<qbo_id>`, `bill:<id>` (CL → generated Bill), `backfill:<unit>`. |
| Terminal lock | `completed` refuses header PUT, line-item create/update/delete and non-admin DELETE with **HTTP 422 + `error_code:"status_locked"`** (not 409 — installed iOS treats 409 as a conflict-reload loop and only 4xx-non-409 as a permanent discard: `BuildOne/Services/BuildOneAPI/APIClient.swift:110-111`, `APIError.swift:49-55`, `BuildOne/Services/Bill/BillService.swift:454-461` vs :469-486). Exempt: `system_authz()` callers (outbox workers, `SetBillQboIdentity` `dbo.bill.sql:681-724`, QBO pull, CLI sync, CompletionJob reclaim `force=True`), idempotent re-completion (`Outcome='noop'`), and `IsBilled` flips on source lines by invoice completion. |

## 3. Current state

| Entity | Representation today | Vocab today | Review parent? | Review exposure | Propagation (review ↔ lifecycle) | Citations |
|---|---|---|---|---|---|---|
| Bill | `dbo.Bill.IsDraft BIT NOT NULL DEFAULT 1` → `is_draft`; no Status, no history | Draft / Finalized (web), Draft / Complete (iOS); filter `draft\|finalized`; review names Submitted 10 / In Review 20 / Approved 30 IsFinal / Declined 100 IsDeclined | Yes | List stitches `review_status/_is_final/_is_declined` via `ReadCurrentReviewsByBillIds`; single GETs carry none | None; auto-submit on draft create; system auto-advance to In Review under the **submitter's** user_id; `apply_reviewer_decision` bypasses `ReviewService.create` and returns `is_draft: True` hard-coded; PUT/POST `is_draft` flips both ways with no side effects | `entities/bill/sql/dbo.bill.sql:17,405,425,492-504,550,569-577`; `entities/bill/business/service.py:521-552,1258-1289,1465-1700`; `entities/bill/api/router.py:102-107,143-158,323-365,394-425`; `notification_service.py:271-287`; `BillList.tsx:390-391,444-454`; `BillModel.swift:21,36` |
| Expense | `dbo.Expense.IsDraft` (base file STALE — CREATE TABLE omits `IsCredit/SourceEmailMessageId`); orthogonal `IsCredit`; child `ExpenseLineItem.IsDraft` mirror | draft / finalized; iOS "Complete" | Yes | submit/advance/decline/list routes; inbox arm; **list/GET omit review state** | None (`ReviewService.create` branches only on `contract_labor_id`/`bill_id`); QBO Purchase pull writes `IsDraft=0` on create AND every re-pull; delete 547s once reviewed (no `DeleteReviewsByExpenseId`) | `dbo.expense.sql:1-14,31,399`; `entities/expense/business/service.py:487-488,670-679,753-790`; `review/api/router.py:172-222`; `purchase/connector/expense/business/service.py:151,210`; `ExpenseEdit.tsx:126,143`; `ExpenseModel.swift:19,43` |
| BillCredit | `dbo.BillCredit.IsDraft`; line mirror `BillCreditLineItem.IsDraft/IsBilled` | Draft / Finalized; router says "completed", agent tool "committed" | Yes | routes + inbox arm; list/GET omit review state; `ReviewTimeline` renders actions on finalized rows | None; PUT `is_draft=true` reopens; VendorCredit pull born `IsDraft=0`; no QBO push (pull-only) | `dbo.bill_credit.sql:17,260`; `complete_service.py:60-265,157-189`; `vendorcredit/connector/bill_credit/business/service.py:224`; `BillCreditEdit.tsx:218`; `types/api.ts:815` |
| Invoice | `dbo.Invoice.IsDraft`; line mirror; majority of rows QBO-pulled | Draft / Finalized; budget variance calls `IsDraft=0` "completed" | Yes | routes + inbox arm; only read-only `InvoiceView` timeline routed (`InvoiceEdit` parked, U-128) | None; QBO pull writes `is_draft=False` on create and HIT-path update (the adopt path no longer does since U-356); `push_draw` halts on `is_draft`; budget variance "Drawn" keys on `IsDraft=0` | `dbo.invoice.sql:17,345,804-866`; `entities/invoice/business/service.py:321-322,381-547`; `push.py:434-438`; `invoice/connector/invoice/business/service.py:205` (update), `:410` (create); `dbo.budget_variance.sql:174,283`; `routes.tsx:64-69` |
| ContractLabor | `dbo.ContractLabor.[Status] NVARCHAR(20) NOT NULL DEFAULT 'pending_review'`, no CHECK; Pydantic `Optional[str]` | `pending_review / submitted (shim ce6cf810) / ready / billed` (581/—/13/338 rows on 2026-07-02, stale) | Yes (live via migrations 003/005; base file 4-parent) | review routes gated `Modules.TIME_TRACKING` while CRUD gates `CONTRACT_LABOR`; **absent from the inbox**; `apply-reviewer-decision` | **One-way partial:** initial submit flips `pending_review→submitted`; Approved flips `→ready` only when every project line is coded (silent defer); Declined/In Review are no-ops; `ready` reachable by 4 non-converging paths (review mirror, `mark_as_ready`, `PUT /{id}/bill status=ready`, `PUT /{id}`) | `dbo.contract_labor.sql:42,232,1810,1843,1876`; `review/business/service.py:91-111,136-187`; `contract_labor/business/service.py:354,542,808-874,925`; `bill_service.py:232,456,700,891`; `dbo.time_entry.sql:1431,1501,1685`; `LaborList.tsx:45-62`; `LaborReviewScreen.tsx:924,949,963,977`; `contractLaborStatus.ts:14-22` |
| EmployeeLabor | `dbo.EmployeeLabor.[Status]`, no CHECK; service transition map | `pending_review → ready → invoiced` (no producer for `invoiced`) | No | none; upstream TimeEntry approve is the de-facto review and never writes EL | None; module `Employee Labor` CanRead-only (`role/sql/migrations/006:6-8,52-54`) and possibly absent from prod `dbo.Module` (U-136 BOARD row); 0 rows last dated 2026-07-02 (`2026_07_02_unify_labor_status_vocab.sql:25-27`) | `dbo.employee_labor.sql:35,108`; `employee_labor/business/model.py:11`; `service.py:14-18`; `dbo.time_entry.sql:1436-1481,1455,1694`; `TODO.md:664,1473-1474` |
| TimeEntry | **No column** — append-only `dbo.TimeEntryStatus` history; current = latest row `(CreatedDatetime DESC, Id DESC)` resolved in 4 places (digest site lacks the Id tiebreak); `current_status` injected by the router | `draft / submitted / approved / rejected (transient) / billed (never written)` | No | `/submit` (can_update), `/approve`, `/reject` (can_approve); `billed-lineage` | Reject writes `rejected` then `draft` in one call; downstream CL `billed` / EL `invoiced` lock edits via `IsTimeEntryDownstreamLocked`; nothing writes TE completion | `dbo.time_entry.sql:214-232,595-600,669-674,1218-1259,1705-1743,1860-1865,1672-1701`; `time_entry/business/service.py:21-27,98-103,239,297,435-447`; `api/router.py:49-99`; `migrations/004_cascade_delete_status.sql:22-29`; `TimeEntryModel.swift:52,248,326-330`; `TimeEntryService.swift:186-200,1597` |
| Budget / BudgetRevision | `dbo.Budget.Status draft\|active\|archived`; `dbo.BudgetRevision.Status draft\|approved`; no CHECK; `archived` baked into `UQ_Budget_ProjectId_Active` | own vocab; approval IS the status flip with `ApprovedByUserId/ApprovedDatetime` | No | `POST /activate/budget`, `/approve/budget-revision` (can_approve — Tenant Admin/Controller AND Owner via `seed.owner_role.sql:26-42`) | n/a | `dbo.budget.sql:31,40-45,266-344`; `dbo.budget_revision.sql:44,327-394`; `role/sql/migrations/005:22-30,47-55` |
| Vendor | `dbo.Vendor.IsDraft BIT DEFAULT 1` | Draft / Active (web) | No | none; nothing outside `entities/vendor` reads it; QBO vendor pull mints `is_draft=False` | n/a | `dbo.vendor.sql:16,162,330,372`; `VendorList.tsx:15-19`; `vendor/connector/vendor/business/service.py:243` |
| Compliance docs (COI / BL / CL) | `VerificationStatus NVARCHAR(20) NOT NULL DEFAULT 'Received'` with **DB CHECK** `IN ('Received','Verified','Rejected')` — the only CHECK precedent in the repo | Received / Verified / Rejected | No | generic PUT from the compliance dashboard (hard-coded option values) | n/a | `dbo.certificate_of_insurance.sql:15,33-36`; `dbo.business_license.sql:16,34-37`; `dbo.contractors_license.sql:17,35-38`; `VendorComplianceDashboard.tsx:558-569` |

Cross-cutting facts the design is built on: `shared/labor_status.py` has **zero importers** (grep); the
2026-07-02 migration is **unapplied** (`contract_labor/sql/README.md:92-101`, `TODO.md:667`); there are **no**
CHECKs/triggers/computed columns on any of these columns; **no index references `IsDraft`** in any base file
(grep); `dbo.review.sql` is **half-migrated** (4-parent table/CK/`vw_Review`/`CreateReview` at :5-31,:120-147,
:153-190 vs. live 5-parent per migrations 003/005; `review` has no whole-file row in `ENTITY_BASE_FILES` (`tests/test_sproc_single_source.py:198-226`) — six review
sprocs are pinned per-object (`ResolveReviewRecipients*` :104-106; `Read/ReadCurrent/DeleteReviews*ByContractLaborId`
:121-123) plus the `IsHumanReviewUser` UDF (:99), so `CreateReview`, `vw_Review`, `CK_Review_OneParent` and
`ReadCurrentReviewsByBillIds` are unguarded); `vw_Review` INNER JOINs
`ReviewStatus` (:144) and `FK_Review_ReviewStatus` sits on `ReviewStatusId` (:19); `ReadInboxTasks` partitions
`vw_Review` by four parents and ignores `IsDraft` (`dbo.inbox_tasks.sql:61-88`); the field-ownership registry
declares a phantom `review_status_id` on Bill and Expense (`field_ownership.py:259-260,318-319`).

### 3a. Prod census — U-357a (read-only, 2026-09-02 00:13Z)

| Fact | Value |
|---|---|
| ContractLabor `Status` | `billed` 1,128 (306 with a Review row, 822 without) · `submitted` 38 (all with) · `pending_review` 23 (none) · `ready` 5 (1 with); **0 stray values** — confirms the 2026-07-02 rename never ran, and a `CK` can be added cleanly |
| EmployeeLabor / Budget / BudgetRevision | EL **0 rows**; Budget `active` 1; BudgetRevision `approved` 2 |
| TimeEntry latest-row status (Id tiebreak) | `approved` 859 · `billed` **260** · `draft` 10 · `submitted` 7; 2,864 history rows. The 260 `billed` rows were all written Feb–Mar 2026 by User 17 with no Note — a one-time backfill; no writer exists in current code (§3 "never written" is true of the code, not the data). `rejected` rows: 28 (May–Aug), all with notes |
| TimeEntry → downstream ContractLabor | approved+CL billed 776 · approved+no CL 24 · approved+CL pending_review 16 · approved+CL ready 5 · approved+CL submitted 38 · billed+CL billed 338 · submitted+CL pending_review 7 |
| `dbo.ReviewStatus` | seed intact (Submitted 10 / In Review 20 / Approved 30 final / Declined 100 declined; all active); invariants hold: 1 active declined, 1 active final-non-declined, MIN row non-final |
| Review rows per parent | Bill 751 · Invoice 49 · ContractLabor 508 · **Expense 0 · BillCredit 0** (1,308 total) |
| `IsDraft` × latest-review kind | **Bill** IsDraft=0: none 19,831 / approved 170 / submitted 39 / in_review 25 / declined 1 (**65 finalized-with-open-review misfits**); IsDraft=1: in_review 69 / submitted 8 / declined 1 / none 1. **Expense** 11,659 finalized + 10 draft, zero reviews. **BillCredit** 441 + 3 draft, zero reviews. **Invoice** IsDraft=0: none 981 / approved 13 / in_review 5; 0 drafts. **ContractLabor** billed: approved 203 / submitted 103 / none 822 · ready: none 4 / submitted 1 · submitted: submitted 38 · pending_review: none 23 |
| Completion evidence on IsDraft=0 | Bill: QboId 20,062 · CompletionResult-only 1 · **no evidence 3** · drafts 79. Expense: QboId 11,659 · none 0 · drafts 10. BillCredit: 438 · 3 · 3. Invoice: 986 · 13 · 0 |
| `IsDraft` dependents (four documents) | **none** — no indexes, no expression dependencies (schemabound or not); DEFAULTs are system-named (`DF__Bill__IsDraft__259C7031`, `DF__Expense__IsDraft__367CE370`, `DF__BillCredi__IsDra__18B7765F`, `DF__Invoice__IsDraft__5E20C076`) → Phase-3 step (c)'s `sys.default_constraints` lookup is required as written |
| Review shape liveness | 5-parent live: `ContractLaborId` column + `FK_Review_ContractLabor` + `IX_Review_ContractLaborId` + 5-way `CK_Review_OneParent`; `CreateReview` has `@ContractLaborId` (param 8 of 10); `vw_Review` projects it; `ReadCurrentReviewsByBillIds` live == base. `dbo.Module 'Employee Labor'` exists (role migration 006 applied) |
| RoleModule grants (7 modules) | `CanApprove`: Bills/Expenses = Controller, Owner, Project Manager, Tenant Admin; Bill Credits/Invoices/Contract Labor = Controller, Owner, Tenant Admin (**no PM on Contract Labor**); Time Tracking = Controller, Owner, PM, Tenant Admin. `CanComplete`: each `*Specialist` agent on its own module + Controller/Owner/TA. `CanSubmit`: AP Specialist (Bills), PM (Time Tracking), Controller/Owner/TA. Employee Labor = CanRead-only (Controller/Owner/TA). Tasks read-only for AP/AR Specialist, Controller, PM, Reviewer |

## 4. Target model

### 4.1 `status` — storage, vocabulary, transitions, guards, `is_draft` compatibility

**Storage.** Bill / Expense / BillCredit / Invoice: NEW `[Status] NVARCHAR(20) NOT NULL CONSTRAINT
DF_<Table>_Status DEFAULT 'draft'`, `[StatusDatetime] DATETIME2(3) NULL`, `[StatusOrigin] NVARCHAR(24) NOT
NULL DEFAULT 'user'`, `[StatusSourceRef] NVARCHAR(64) NULL`, added by guarded idempotent ALTERs in each base
file (pattern `dbo.expense.sql:37-55`; the Expense base CREATE TABLE is never re-run). `IsDraft` is retained as
a real column through the API cutover and then re-created as `AS (CASE WHEN [Status]='completed' THEN CAST(0
AS BIT) ELSE CAST(1 AS BIT) END) PERSISTED` (§9 #22) — every sproc projection, raw-SQL filter, filtered
predicate and the `is_draft` payload keep working and the column becomes physically unwritable.
ContractLabor: the EXISTING `[Status]` column (`dbo.contract_labor.sql:42`) is renamed in place at the cutover
and gains DF/CK + the three companion columns. TimeEntry: NEW denormalized `dbo.TimeEntry.[Status]`
maintained in the same transaction as the `dbo.TimeEntryStatus` history insert; the history table stays the
ledger (not a Review parent). EmployeeLabor: untouched until fold-in. Transition history = existing
ProcessEngine `Workflow/WorkflowEvent` rows (transitions route through `'{entity}_status_transition'`) + the
Review ledger; no new history table.

**Vocabulary.** Exactly the six values in `shared/labor_status.py:35-40` — no additions. Per-value meaning in
§2.

**Transitions** (one verb per edge; system contexts run under `system_authz()`):

| From → To | Trigger | Verb |
|---|---|---|
| draft → submitted | `POST /submit/review/{parent}/{id}`; Bill auto-submit on draft create (`bill/business/service.py:521-552`); TE `POST /time-entries/{id}/submit` + auto-submit sweep | `can_update` (author; `CanSubmit` stays dead and is dropped in LS-06d) |
| declined → submitted | same submit routes (`build_submit_payload` already allows it, `review/business/service.py:260-267`) | `can_update` |
| submitted → in_review | `POST /advance/review/…` (target non-final) or the per-entity system auto-advance (§2) | `can_approve` \| system |
| submitted \| in_review → approved | NEW `POST /approve/review/{parent}/{id}` (targets the single active `IsFinal ∧ ¬IsDeclined` status directly — approval must not require walking every intermediate SortOrder); the last `/advance`; `apply_reviewer_decision('approved')` rerouted through `ReviewService.create`; TE `/approve` | `can_approve` \| system (agent user) |
| draft \| declined → approved | `record_fast_path_approval` ONLY — the approver fast-path (`can_complete ∧ can_approve`, or `IsSystemAdmin`) at completion, and the four converged ContractLabor "ready" paths (§4.2 rule 3); writes ONE Approved Review row (`Comments='Fast-path approval by {user}'`, `StatusOrigin='fast_path'`) inside `CreateReview` — the sole exception to "approval requires a submit" | `can_approve` |
| submitted \| in_review → declined | `POST /decline/review/…`; `apply_reviewer_decision('rejected')`; TE `/reject` (writes declined then draft; resting state stays draft) | `can_approve` |
| approved → completed | `POST /complete/{bill\|expense\|bill-credit\|invoice}/{id}` → `complete_*` calls `Transition<Entity>Status(from ∈ allowed-by-gate, to='completed')` BEFORE the fan-out enqueue; ContractLabor = `generate_bills_for_vendor` (`bill_service.py:690-710`); TE = downstream write on CL completion | `can_complete` \| system |
| draft \| submitted \| in_review → completed | only while the entity's gate is `off` (today's behaviour) or `block_open_review` (draft only) — see §4.2 gate; the approver fast-path (`can_complete ∧ can_approve`) first records an Approved Review row under the actor, then runs approved → completed | `can_complete` (+ `can_approve` for the fast-path) |
| (none) → completed | create-as-completed: `Create<Entity>(@Status='completed', @StatusOrigin='qbo_pull', @StatusSourceRef='qbo:<realm>/<id>')` accepted ONLY under `system_authz` (QBO pull connectors, CLI sync scripts) | system |
| completed → completed | `Outcome='noop'`: CompletionJob reclaim `force=True` (`completion_job/business/service.py:73,77`), web double-click, duplicate PM reply — no transition, proceed to idempotent re-enqueue under the U-221 fan-out guards | — |
| completed → * | **none.** Reopen (with compensations: un-bill sources, clear Excel col-H, delete packet) and author recall (submitted → draft) are separate future units (§9 #6). `approved → draft` is REMOVED from `VALID_TRANSITIONS` (`labor_status.py:63`); `draft → approved` (`:56`) survives ONLY as the fast-path edge above; `approved → declined` is NOT allowed (keeps `build_decline_payload`'s final-status refusal, `review/business/service.py:337-340`; §9 #15). |

**DB guards.** Named idempotent `CK_<Table>_Status CHECK ([Status] IN (six values))` on Bill, Expense,
BillCredit, Invoice, ContractLabor, TimeEntry (guarded `IF NOT EXISTS (SELECT 1 FROM sys.check_constraints
WHERE name=…)`); `CK_TimeEntryStatus_Status` allowing the canonical six PLUS the legacy audit literals
`rejected`/`billed` (history rows are never renamed — installed iOS decodes each history row with
`EntryStatus(rawValue:) ?? .draft`, `TimeEntryModel.swift:248`); `CK_<Table>_StatusOrigin`;
`DF_<Table>_Status 'draft'`; `FK_<Table>_ReviewStatus` + filtered `IX_<Table>_ReviewStatusId WHERE
ReviewStatusId IS NOT NULL`; `IX_<Table>_Status WHERE Status <> 'completed'`. The census (LS-00a) must show
zero stray CL values before `CK_ContractLabor_Status` is added — the ALTER fails loudly otherwise, by design.

**Transition sproc** (one per entity, Budget shape — `SET NOCOUNT ON`, `BEGIN TRAN`, parent row `WITH (UPDLOCK,
HOLDLOCK)`, validate-first, mutate only when valid, `COMMIT` unconditionally, never `ROLLBACK`):
`Transition<Entity>Status(@Id BIGINT, @RowVersion BINARY(8), @FromStatuses NVARCHAR(200) /*csv → STRING_SPLIT*/,
@ToStatus NVARCHAR(20), @ActorUserId BIGINT = NULL, @Origin NVARCHAR(24) = NULL, @SourceRef NVARCHAR(64) =
NULL)`. Result: the updated row + `[Outcome]` ∈ `'transitioned'` (Status/StatusDatetime/Origin/SourceRef
written; during the swap window also the real `IsDraft`) | `'noop'` (already `@ToStatus`; nothing written, no
RowVersion bump) | **empty result set** on RowVersion mismatch or `Status ∉ @FromStatuses` → service maps to
HTTP 409 `error_code:"status_conflict"` with the current status. Completion keeps its 3× re-read/retry only
for genuine conflicts against the 300 ms web auto-save (`bill/business/service.py:1499-1567`).

**Review-caused values are written by `CreateReview`, in the SAME transaction as the ledger insert** (closes
the two-sproc atomicity flaw): `CreateReview` (`dbo.review.sql:153-190`, live 5-parent signature after LS-00b)
takes the parent row `WITH (UPDLOCK)`, refuses (empty result) when the parent is `completed` or when the
requested kind is not a legal edge from the parent's current lifecycle — returning `Outcome='noop'` (not a refusal)
when the requested kind equals the current kind, so a duplicate PM reply or a double-click is idempotent; "current
lifecycle" is the `Status` column once that parent has one, and until then is derived in-sproc from `IsDraft ×
dbo.ReviewKind(ReviewStatusId)` for the four documents and from the legacy literals for ContractLabor — then
`INSERT dbo.Review` and `UPDATE
dbo.<Parent> SET ReviewStatusId=@ReviewStatusId, ReviewStatusDatetime=@Now, Status=dbo.ReviewKind(@ReviewStatusId),
StatusDatetime=@Now, StatusOrigin=@Origin, ModifiedDatetime=@Now` — the Status write is enabled per parent as
each Status unit lands (the CL branch writes canonical only after the cutover). `dbo.ReviewKind(@ReviewStatusId)`
is a scalar UDF used ONLY inside mutation sprocs, the backfill and the reconcile (never in list-sproc WHERE
predicates). This UPDATE bumps the parent's ROWVERSION — a reviewer action can 409 an in-flight web auto-save;
its write set is disjoint from every editor column so no update is lost; the web `ReviewTimeline` must `await
autoSave()` before firing and refetch after (the existing CLAUDE.md rule for state-dependent actions) — §9 #5.

**Python.** `shared/lifecycle/` (`labor_status.py` becomes a re-export shim, deleted in LS-06d):
`LifecycleStatus` (`StrEnum`), corrected `VALID_TRANSITIONS`, `assert_transition()`, `is_terminal()`,
`is_editable()`, `review_kind_from_flags(row, first_sort_order)`, `resolve_document_status(is_draft, review)`,
`resolve_labor_status(disk, review)`, `resolve_time_entry_status(history)`, `canonicalize()` (both directions),
`COMPLETION_GATE` policy reader (env flags), `attach_lifecycle(items, *, parent_type, review_map, …)` replacing
the hand-rolled Bill router stitch (`bill/api/router.py:143-158`). Pydantic schemas replace `Optional[str]`
status fields with the enum at each entity's cutover (`contract_labor/api/schemas.py:74-77,159-162,326`).
Tests: pure-logic `tests/test_lifecycle_guard.py` (transition table, flag→kind incl. a 5-stage admin table,
noop semantics, alias round-trips); a fixture table shared with a SQL characterization script `/em` runs
through `dbo.ReviewKind` + the backfill expression (parity before each backfill); `tests/test_status_literals.py`
grep-guard for legacy literals outside an allowlist after LS-04; the field-ownership registry test asserting
no connector UPDATE path passes `is_draft`/`status` (xfail-tagged with the unit id until LS-01d).

**`is_draft` compatibility.** (1) Payload: `is_draft` is emitted forever on Bill/Expense/BillCredit/Invoice (and
line items) as `status != 'completed'` — iOS decodes a missing `is_draft` as Draft (`BillModel.swift:36`,
`ExpenseModel.swift:43`, `InvoiceModel.swift:40`). (2) DB: `IsDraft` persisted computed (above), so
`WHERE b.IsDraft = 0` in `budget_variance.sql:174,283`, `expense_coding_suggestion.sql:41-42`, the QBO/MS
reconciles, `scripts/backfill_box_workbook.py:62-98`, `scripts/reconcile_project.py:209-276` and the
`getattr(bill,'is_draft')` in `excel_detector.py:148,307` keep exact semantics. (3) Filters: list sprocs keep
`@IsDraft BIT = NULL` (translated `1 → Status <> 'completed'`, `0 → Status = 'completed'`) and gain `@Status
NVARCHAR(20) = NULL` + `@ReviewStatusPublicId UNIQUEIDENTIFIER = NULL`; web `draft|finalized`, MCP
`?is_draft=` (`build.one.mcp/src/build_one_mcp/tools/bill.py:160-161`, `expense.py:131-132`,
`bill_credit.py:164-165`) and agent search tools keep working. (4) Writes: inbound `is_draft` on POST/PUT from
non-system callers is **ignored with a warning log — never 422** (a 422 on a queued create deletes the
offline-created bill: `BillService.swift:283-302`; iOS sends it at `BillEndpoints.swift:54,85`,
`BillLineItemEndpoints.swift:61,94`); the telemetry event `lifecycle.is_draft_false_without_completion`
(intake_source + actor + `X-Client-Build`) counts surviving callers before any path or alias is removed.
`Update<Entity>ById` keeps `@IsDraft` as a no-op param; the `CASE WHEN` writes at `dbo.bill.sql:425`,
`dbo.expense.sql:399`, `dbo.bill_credit.sql:260`, `dbo.invoice.sql:345` are removed in swap step (c) — which
also removes the "IsDraft=0 without completion" path (`bill/api/router.py:360-364`) and the iOS Draft-toggle
side effect (`BillDetailView.swift:243,293`). (5) Line-level `BillLineItem/ExpenseLineItem/BillCreditLineItem/
InvoiceLineItem.IsDraft` mirrors and `IsBilled` are untouched (§5).

### 4.2 `review_status` — representation, exposure, propagation, gate, inbox, ReviewStatus policy

**Representation.** Denormalized, single-writer, ledger retained. Each of the five Review parents gains
`[ReviewStatusId] BIGINT NULL CONSTRAINT FK_<Table>_ReviewStatus REFERENCES dbo.ReviewStatus(Id)` +
`[ReviewStatusDatetime] DATETIME2(3) NULL`, written ONLY by `CreateReview` (all write paths converge there:
`ReviewService.create` via ProcessEngine `review_create`, `apply_reviewer_decision` once rerouted, the
notification auto-advance, `record_fast_path_approval`). The FK points at the ReviewStatus ROW, so admin
renames/colours are reflected without drift. Why not derived-at-read as the end state: only Bill has a batch
sproc today, list filtering/sorting by review state needs the ranked CTE inside every RBAC-scoped list sproc,
and the inbox re-derives the same partition (`dbo.inbox_tasks.sql:61-88`); the Phase-1 read-model is kept as
the transitional shape and as the daily oracle. Why not a separate ledger table: `field_ownership` already
declares `review_status_id` app-owned on Bill/Expense — the column makes that declaration true.

**Exposure — identical flat field set on every parent's paginated list AND single GET**, projected by the
RBAC-scoped sprocs themselves via `LEFT JOIN dbo.ReviewStatus rs ON rs.Id = p.ReviewStatusId` (Phase 2+; via
`ReadCurrentReviewsByParentIds` + `attach_lifecycle` in Phase 1): `status`, `status_datetime`, `status_origin`,
`status_source_ref`, `is_draft` (four documents), `review_status` (Name, kept), `review_status_kind`,
`review_status_public_id`, `review_status_is_final`, `review_status_is_declined` (kept),
`review_status_sort_order`, `review_status_color`, `review_status_datetime`, `review_status_user_id`. Single
GET adds `status_source` `{completion_job: processing|completed|failed|null, completion_result: …|null,
qbo_id: …|null}`. ContractLabor emits `lifecycle_status` (canonical) beside the unchanged legacy `status` until
the LS-04 cutover, after which they are equal and `lifecycle_status` is dropped one release later.
EmployeeLabor emits `lifecycle_status` read-only. TimeEntry emits canonical `status`, keeps `current_status`
and `status_history[].status` as **reverse-aliased legacy strings** (`declined→'rejected'`,
`completed→'billed'`; `draft/submitted/approved` identical) until LS-06d proves no old decoder remains, and
`review_status_kind` derived from its own column/history (`submitted`, `approved`, `declined` when the resting
`draft` row is immediately preceded by a declined row — makes the phone's unreachable "Rejected · talk to your
PM" intent observable on web/MCP — else `none`). Web: `review_status*` optional fields become present on all
five types (`types/api.ts:741-743` shape). MCP: additive output fields; `search_*` gain `status` and
`review_status_kind` filters. iOS: additive keys ignored by `Codable`. Error bodies gain an additive
`error_code` (`status_locked`, `status_conflict`, `review_open`, `review_declined`, `approval_required`,
`lines_uncoded`, `review_status_shape`) without rewording `detail` (iOS substring-matches
`'transition'`, `"not in 'draft'"`, `'submitted'` — `TimeEntryService.swift:186-200`).

**Propagation rules.**
1. Initial submit (kind `submitted`): `draft|declined → submitted` inside `CreateReview` (not failure-isolated —
   a refused edge surfaces as 409 to the caller; today's CL shim swallows it, `review/business/service.py:160-170`).
   The Bill notification enqueue and CL crew-draft enqueue remain failure-isolated side effects AFTER the write.
2. Intermediate (kind `in_review`): `submitted → in_review` (no-op with log if already beyond). Bill's
   auto-advance switches from `sort_order > 10` (`notification_service.py:273`) to `get_next_status(first.sort_order)`
   and writes under User 33 instead of `review.user_id` (:285). CL: crew-draft claim writes one row per CL in
   the claimed `(project, work_date)` crew.
3. Final non-declined (`approved`): `submitted|in_review → approved` for all five parents, plus `draft|declined →
   approved` ONLY via `record_fast_path_approval` (`StatusOrigin='fast_path'`). ContractLabor:
   approval becomes **unconditional** — the coded-SubCostCode invariant leaves `mark_as_ready_via_review_approval`
   (`contract_labor/business/service.py:830-874`, silent defer) and becomes `completion_blockers` evaluated by
   `generate_bills` and surfaced on the CL payload; the four legacy "ready" paths (`mark_as_ready`,
   `/bulk-mark-ready`, `PUT /{id}/bill status=ready`, `PUT /{id} status`) converge on
   `record_fast_path_approval` (an Approved Review row under the operator — the office is the reviewer of
   record) — §9 #11.
4. Declined: `submitted|in_review → declined` for all five parents (today a no-op for CL:
   `contract_labor/business/service.py:509,737-738`); declined CLs drop out of the reviewable crew
   (`dbo.contract_labor.sql:1843,1876` re-keyed on `'submitted','in_review'`).
5. Completion writes NO Review row and never mutates `ReviewStatusId` (no "Completed" ReviewStatus is
   introduced). The approver fast-path FIRST writes an Approved row (`Comments='Fast-path approval by {user} at
   completion'`, `user_id=actor`) so `completed ⇒ approved` holds for every non-QBO-origin row once the gate is
   `require_approved`.
6. Reverse direction (lifecycle → review): none. Edits never reset review state. Delete cascades Review rows
   (`Delete{Reviews}By{Parent}Id` for all five) and nulls `ReviewStatusId` first (children first).
7. Review endpoints are refused when the parent is `completed` (422 `status_locked`) — closes the
   "Finalized + Submitted" misfit (`BillCreditEdit.tsx:218`); `ReviewTimeline` hides actions when
   `status === 'completed'`.
8. TimeEntry: no Review rows. approve → history `approved` + parent `approved`; reject → history `declined` then
   `draft`, parent `draft` (one transaction); submit → `submitted`. ContractLabor keeps its own Review thread
   for the same worker-day (PM coding review ≠ worker-day correctness) — by design.

**Completion gate** (graft from Proposal 2): per-entity env flags `LIFECYCLE_COMPLETION_GATE_{BILL,EXPENSE,
BILL_CREDIT,INVOICE}` ∈ `{off, block_open_review, require_approved}`, shipped `off`, flipped by `/em` without a
deploy. `block_open_review`: `POST /complete/*` returns 409 `review_open` when `review_status_kind ∈
{submitted,in_review}` and 409 `review_declined` when `declined`; completion with `none` (never submitted —
the AP fast path the web labels "bypasses review", `BillCreate.tsx:667`) or `approved` proceeds.
`require_approved`: only `approved` passes, EXCEPT the approver fast-path (`can_complete ∧ can_approve`, or
`IsSystemAdmin`) which records the Approved row then completes — the web button becomes "Approve & Complete";
`ReviewService.is_approved` (`review/business/service.py:238-242`, zero callers) gets its first caller.
Exemptions: `system_authz` (QBO-origin births with `StatusOrigin='qbo_pull'`; reclaim re-drives are `noop`
anyway). Recommended steady state (§9 #1): Bill and BillCredit → `require_approved`; Invoice →
`block_open_review` until U-128 unparks `InvoiceEdit` (`routes.tsx:64-69`); Expense → `off` (QBO-pulled bulk,
no reviewer resolver/notifications, the coding cockpit is the human touchpoint). ContractLabor: `generate_bills`
selects only `approved` rows with zero `completion_blockers` (gate inherently on). TimeEntry: `submitted | approved → completed` for
`StatusOrigin='downstream'` only — most source TimeEntries are still `submitted` when the PM approves the CL by
email, and `generate_bills` never reads TE status (no `TimeEntryStatus` join anywhere in `bill_service.py` or
`dbo.contract_labor.sql`); a TE that is `draft` or `declined` at CL completion is left unchanged with a warning and
stays lineage-locked (`IsTimeEntryDownstreamLocked` keeps the CL/EL lineage `OR`).

**Inbox.** Phase 1: `ReadInboxTasks`/`ReadInboxTaskCounts` add the parent `IsDraft = 1` predicate per branch so
completed parents with a stale open review drop out (the stale-task bug, `dbo.inbox_tasks.sql:81-88`). Phase 6:
rewritten as a `UNION` over the parent tables joined to `dbo.ReviewStatus` on the denormalized column (`WHERE
p.Status IN ('submitted','in_review') AND (@StatusId IS NULL OR p.ReviewStatusId=@StatusId)`) plus a NEW
ContractLabor arm (`@EntityType 'ContractLabor'`, counterparty = Vendor, `'mine'` = PM/Owner `UserProject` on the
CL's ProjectId) and a `ContractLabor` `TaskFeed` registration. RBAC scoping (`@CurrentUserId/@IsSystemAdmin/@Scope`,
`Role.Name IN ('Project Manager','Owner')`, `dbo.inbox_tasks.sql:117,131`) preserved verbatim. TimeEntry is NOT
added (its review surface is the TE approve screen; a TE arm would double-list every worker-day beside its CL
and the inbox is a Review-row feed). The inbox stays API-only (`/get/tasks/inbox` has no web consumer; cross-feed
paging deferred at `entities/task/business/aggregator.py:17-19,38-40`) — a web Tasks page is a separate product unit.

**`dbo.ReviewStatus` policy.** Stays admin-editable through the LIVE U-155 CRUD. The canonical mapping keys ONLY
on flags + position (§2). Guard rails added to `ReviewStatusService.create/update/delete` (422
`review_status_shape`, surfaced by the U-155 form): exactly ONE active `IsDeclined` row (decline auto-resolution
already errors on 0 or >1, `review/business/service.py:452-462`); exactly ONE active `IsFinal ∧ ¬IsDeclined`
row; ≥1 active non-final non-declined row; the MIN-`SortOrder` active non-declined row must be non-final (`ReadFirstReviewStatus` ignores `IsFinal`, so otherwise `/submit` auto-approves); no create/update may change WHICH row is that MIN while any parent `ReviewStatusId` or Review row references the current one — refuse a `SortOrder` at or below the current MIN and refuse deactivating the MIN row — because `review_status_kind = submitted` is position-derived and a new lowest row would silently re-derive every stored `submitted` as `in_review` (breaking invariant I2 table-wide); `IsFinal ∧ IsDeclined` rejected; deactivating (`IsActive=0`) or
deleting a row that any parent's `ReviewStatusId` or any Review row references is refused (FK already blocks
delete); historical rows keep their `ReviewStatusId` regardless of later `IsActive` edits (derivation ignores
`IsActive` for stored rows). Seeds unchanged (`seed_review_statuses.sql:4-26`).

### 4.3 Per-entity mapping (current value/state → target `status`, `review_status_kind`)

| Entity | Current | → `status` | → `review_status_kind` | Notes |
|---|---|---|---|---|
| Bill / Expense / BillCredit / Invoice | `IsDraft=1`, no Review | draft | none | pointer NULL |
| " | `IsDraft=1`, latest Review = first active non-declined | submitted | submitted | |
| " | `IsDraft=1`, latest Review intermediate | in_review | in_review | |
| " | `IsDraft=1`, latest Review `IsFinal ∧ ¬IsDeclined` | approved | approved | |
| " | `IsDraft=1`, latest Review `IsDeclined` | declined | declined | |
| " | `IsDraft=0` via `complete_*` (CompletionJob / `BillCompletionResult` exists) | completed, origin `completion` | latest kind or none | pointer kept as-is |
| " | `IsDraft=0` from QBO pull (`QboId` set, no Review) | completed, origin `qbo_pull`, ref `qbo:<realm>/<id>` | none | exempt from `completed ⇒ approved` |
| " | `IsDraft=0` with no completion evidence and no `QboId` (POST/PUT `is_draft=false`, iOS toggle) | completed, origin `backfill` | latest kind or none | path removed going forward; census counts them |
| " | `IsDraft=0` while latest Review is submitted/in_review (misfit) | completed, origin `backfill` | submitted/in_review (stale, muted in web) | never fabricate Approved rows (§9 #16); inbox suppressed by lifecycle predicate |
| " | line-level `IsDraft` / `IsBilled` | unchanged line flags | — | out of scope (§5) |
| ContractLabor | `pending_review` | draft | none | `AggregateTimeEntryOnSubmit` literal `dbo.time_entry.sql:1431` → `'draft'`; CreateContractLabor default `:232` |
| " | `submitted` (shim) | submitted | submitted | no rename; rows with `submitted` but no Review row → pointer NULL, census-counted |
| " | `ready` via review approval | approved | approved | |
| " | `ready` via legacy paths (no Review row) | approved, origin `backfill` | none | census-counted "approved without review"; no fabricated rows |
| " | `billed` | completed, ref `bill:<ContractLaborLineItem.BillLineItemId>` | latest kind or none | `:1501,1685` → `'completed'` |
| " | (none today) | in_review | in_review | crew-draft claim under system user |
| " | (none today) | declined | declined | PM reply `rejected` / `/decline` now transitions |
| " | `ready`/`billed` with newer Submitted review (submit-from-ready) | census-counted; going forward `/submit` refused unless `draft\|declined` | | |
| " | stray free-text | MUST be 0 before `CK_ContractLabor_Status` | | census gate |
| EmployeeLabor (fold-in only) | `pending_review` / `ready` / `invoiced` | draft / approved / completed | none | applied when rows are copied into ContractLabor; EL block of the 2026-07-02 migration deleted |
| TimeEntry | latest history `draft` | draft | none (or `declined` if the previous row is declined/rejected) | |
| " | `submitted` | submitted | submitted | iOS hardcodes `'submitted'` (`CDTimeEntry.swift:43`, `TimeEntryService.swift:1337`) and `'draft'` (:835,:1046) — unchanged |
| " | `approved` | approved | approved | |
| " | `rejected` (transient) | never a resting value; history literal kept; new writes use `declined` | | |
| " | `billed` (never written) | completed (new write on CL completion from `submitted` OR `approved`, origin `downstream`; history literal `completed`, wire alias `'billed'`) | last kind (`submitted` / `approved`) | `IsTimeEntryDownstreamLocked` reads the column OR the lineage |
| " | `ReviewPriority/ReviewReasons` | not a status | — | triage sidecar |
| Budget / BudgetRevision | `draft\|active\|archived` / `draft\|approved` | unchanged (own vocab) | — | hygiene CHECKs only (§5) |
| Vendor | `IsDraft` 1/0 | unchanged | — | out (§5) |
| Compliance docs | Received / Verified / Rejected | unchanged | — | out (§5) |

## 5. Scope decisions

| Item | Decision | Rationale |
|---|---|---|
| Bill, Expense, BillCredit, Invoice, ContractLabor, TimeEntry | **IN** — full canonical `Status` column + identical review exposure (TimeEntry: derived `review_status_kind`, own ledger) | The entities with an author → reviewer → downstream pipeline; five are Review parents, TimeEntry has a structural twin of the Review ledger. |
| Budget / BudgetRevision | **OUT** of the canonical vocab and the Review system; LS-06d adds named CHECKs with their OWN vocab + Pydantic enums | Container/operational states (`active`; `archived` baked into `UQ_Budget_ProjectId_Active`, `dbo.budget.sql:40-45`), Rev-0 approval atomically coupled to Budget activation (`:304-326`), approved revisions immutable at three layers (contradicts editable `approved`), no downstream `completed` effect, `can_approve` already gates (Tenant Admin/Controller + Owner). A PM → Controller change-order handoff is net-new product work; if requested, BudgetRevision becomes a Review parent whose approval routes THROUGH `ApproveBudgetRevisionById` with `row_version`. |
| Vendor.IsDraft | **OUT**; keep as a reference-record curation flag; optional later rename to `IsVerified`/`IsActive` | No workflow, reviewer or completion; QBO pull sets it; nothing outside `entities/vendor` reads it. |
| COI / BL / CL `VerificationStatus` | **OUT**; keep literals and existing CHECKs; optional display-only alias in a shared badge | A verification axis with the strongest DB guard in the repo; renaming costs three CHECK rebuilds + a web deploy for zero workflow gain. |
| EmployeeLabor | **OUT** of in-place migration; absorbed at the decided fold-in (`TODO.md:664`, Chris 2026-08-05) with the fixed mapping; interim read-only `lifecycle_status`; EL section of the 2026-07-02 migration deleted; the `'invoiced'` literals at `dbo.time_entry.sql:1455,1694` untouched by LS-04 | 0 rows last dated 2026-07-02; module CanRead-only and possibly absent in prod; migrating twice is waste. If the census shows accumulated rows, `/em` may fold EL into the LS-04 transaction (small: `model.py:11`, `service.py:14-18`, `dbo.employee_labor.sql:35/108`, three web files). |
| TimeEntry as a Review parent | **OUT** (§9 #12); denormalized column + `TimeEntryStatus` ledger; `review_status_kind` derived | A 6th parent needs a CK rewrite, `vw_Review`/`CreateReview`/inbox/`ParentType`/`ReviewTimeline` changes and two review threads per worker-day; the history table already IS a latest-row-wins ledger and is cascade-deleted (`migrations/004_cascade_delete_status.sql:22-29`). |
| TimeEntry `declined` as a resting state | **OUT**; keep the two-row reject; `declined` visible only as `review_status_kind` | A resting declined day locks the phone with no resubmit UI and breaks the auto-submit "non-draft sibling" rule (`auto_submit_service.py:179`). |
| Line-level `IsDraft` / `IsBilled` mirrors | **OUT** (flipped by completion exactly as today); converting line `IsDraft` to computed-from-parent is a booked follow-up | Consumed by invoice-candidate SQL (`invoice/business/service.py:1412,1505` (+ bli/eli twins :1330,1371,1448,1477)), `expense_coding_suggestion.sql:42`, reconciliation; `IsBilled` is a per-line post-completion fact (partial billing across invoices) no header status can absorb. |
| Operational statuses (CompletionJob, Outbox, ReconciliationIssue, EmailMessage.ProcessingStatus, FolderRun, Attachment, Integration, ExpenseCodingItem, Project, AgentApprovalRequest decisions) | **OUT**; documented in `/docs` as non-entity lifecycles | Jobs/queues/pipelines, not entity lifecycles; several share words (`approved/rejected`, `pending/done`). |
| Recall (`submitted→draft`) and reopen (`completed→draft`) | **OUT** of v1; Decline is the send-back; admin-only `/reopen` with compensations is a later unit | Both break the insert-only Review invariant or need compensating external actions; neither exists today. |
| Web Tasks/inbox page, cross-feed paging, Expense/BillCredit/Invoice notification resolvers, auto-submit for non-Bill parents | **OUT** (separate product units) | No web consumer of `/get/tasks/inbox`; adding reviewer surfaces is product scope beyond "consistent status". |
| `review_status` wire meaning | **KEEP** = admin Name; canonical under NEW `review_status_kind` | A live consumer renders it verbatim (`BillList.tsx:454`); Proposals 2/3 would show lowercase codes until the ask-first web deploy. |

## 6. Phased plan

Conventions: every SQL step is a targeted, idempotent, `GO`-terminated batch in the entity's base file (or
`entities/*/sql/migrations/` where the base is stale), applied by `/em` after a `sys.parameters` diff of the
live sproc; API = `az acr build … :latest` + `az webapp restart` + container verify; web = commit+push,
`swa deploy` only when Chris says go; MCP deploy after API; scheduler `func azure functionapp publish` after the
API endpoint exists. **iOS forward-compat (LS-00d) ships FIRST** — before any unit that emits a new wire value
to a phone (LS-04, LS-05) and, recommended, before LS-03 so the Draft toggle stops queuing no-op PUTs. Roles:
Backend = api owner; DBA on every sproc/migration; Integrations + Security on any QBO connector change;
Security on RBAC/grants; Frontend (web), iOS, MCP-eng, Scheduler as named; Docs on every unit (`/docs`
CURATED "Lifecycle & review status" page + DERIVED refreshes).

### Phase 0 — Ground truth + prerequisites (zero behaviour change)

| Unit | Repo / layer | Owner → handoffs | Content |
|---|---|---|---|
| **LS-00a Prod census (read-only)** | prod SQL, run by `/em`; results pasted into §3/§4.3 of this doc | Architect writes the query pack → `/em` runs | `DISTINCT Status + COUNT` on `dbo.ContractLabor`, `dbo.EmployeeLabor`, `dbo.Budget`, `dbo.BudgetRevision`; latest-row `dbo.TimeEntryStatus` distribution (with Id tiebreak); `dbo.ReviewStatus` rows vs seed (assert one active `IsDeclined`, one active `IsFinal ∧ ¬IsDeclined`); per parent the `IsDraft × latest-Review-kind` matrix (incl. `IsDraft=0` with open review, `IsDraft=1` with Approved, `IsDraft=0` with `QboId NULL` and no CompletionJob/`BillCompletionResult`); Review row counts per parent (sizes the delete-parity and pointer backfills); indexes/defaults/schemabound objects on each `IsDraft` (expected: none — `UserCanAccess*` bind `CreatedByUserId`); `RoleModule` grants for BILLS/EXPENSES/BILL_CREDITS/INVOICES/CONTRACT_LABOR/TIME_TRACKING/TASKS per role; liveness of review migrations 003/005 (`sys.columns` ContractLaborId on `dbo.Review`; `sys.parameters` on `CreateReview`) and role migration 006 (`dbo.Module 'Employee Labor'`); `X-Client-Build` is not yet logged, so iOS fleet composition comes from TestFlight/App Store data. |
| **LS-00b Reconcile `dbo.review.sql` to the live 5-parent shape + whole-file guard** | api / `entities/review/sql` + `tests/` | Backend → **DBA** (design-gated, two-phase dispatch per `feedback_two_phase_dispatch_design_gated.md`) | Idempotent `ContractLaborId` column-add + `FK_Review_ContractLabor` + filtered index; 5-way `CK_Review_OneParent`; `vw_Review` projecting `[ContractLaborId]`; `CreateReview @ContractLaborId BIGINT = NULL`; `ReadCurrentReviewsByBillIds` projecting it — bodies taken from the LIVE `sys.sql_modules` after the `sys.parameters` diff, never from migration 005 blindly; migrations 003/005 reduced to pointer stubs; `("review", REVIEW_BASE)` added to `ENTITY_BASE_FILES` (`tests/test_sproc_single_source.py:198-226`) — the six existing per-sproc pins (`:104-106`, `:121-123`) and the `IsHumanReviewUser` UDF pin (`:99`) stay beside it (presence pins guard deletion, the whole-file row guards duplication — the test's own comment at `:112-119` keeps both); fix the digest OUTER APPLY Id tiebreak (`dbo.time_entry.sql:1860-1865`) as the pure-bug rider. `/em` applies; verify CL review submit still works. |
| **LS-00c `shared/lifecycle` guard + resolver (pure logic)** | api / `shared/lifecycle/`, `tests/` | Backend → SDET | `LifecycleStatus`, corrected `VALID_TRANSITIONS`, `assert_transition`, `review_kind_from_flags`, the three `resolve_*` functions, `canonicalize` both ways, `COMPLETION_GATE` env reader, `attach_lifecycle`; `labor_status.py` → re-export shim (first importer). Pure-logic tests + the shared fixture table; field-ownership registry test (xfail `LS-01d`); no runtime importers yet. |
| **LS-00d Forward-compat: API `error_code` + iOS decoder** | api (additive 4xx bodies, `X-Client-Build` logging) → ios | Backend → **iOS** → SDET (offline-queue + multi-user logout regression per `build.one.ios/CLAUDE.md`) | API: add `error_code` beside `detail` without rewording any message; log `X-Client-Build`. iOS (TestFlight → App Store): `EntryStatus` gains `.unknown` rendered read-only (no Clock In, no edits, no queue replay) instead of `?? .draft` (`TimeEntryModel.swift:52,248`); optional decode of `status`/`review_status_kind` for display; keep `is_draft ?? true`; offline recovery keys on `error_code` with the substring match as fallback; remove the Draft toggles (`BillDetailView.swift:243,293`); **hard deliverable:** fix or delete `CreateBillEndpoint` — today it sends `vendor_id` as an int and omits the required `attachment_public_id` (`BillEndpoints.swift:46-55` vs `bill/api/schemas.py:12-26`), so every offline-queued create already 422s and is deleted by `BillService.swift:283-302` regardless of `is_draft` (gate the iOS create UI until fixed); regenerate the `/docs` iOS manifest (`gen_docs_manifest.py` + `npm run docs:sync:ios`). |
| **LS-00e `/docs` CURATED "Lifecycle & review status" page** | web / `src/pages/docs/` (`docsSections.ts` `api` section) | **Docs** → Frontend (ask-first deploy) | Vocabulary, per-entity `completed` definitions, the flag-keyed ReviewStatus policy, the out-of-scope table, gate/verb matrix placeholders; refreshed by every later unit. |

**Deploy order:** LS-00a (read-only) → LS-00b (SQL by `/em`, then API so the repo layer matches) → LS-00c (API,
inert) → LS-00d (API additive, then iOS TestFlight) → LS-00e (web ask-first). **State after Phase 0:** prod
behaviour identical; the review base file is canonical and guarded; the resolver exists with tests; phones
have a safe decoder; `/docs` has the page.

### Phase 1 — Read-model exposure + isolated consistency fixes (API additive, zero schema change)

| Unit | Repo / layer | Owner → handoffs | Content |
|---|---|---|---|
| **LS-01a Canonical `status` + `review_status_kind` on every list/GET (derived)** | api / `entities/review/sql` (`ReadCurrentReviewsByParentIds(@ParentType NVARCHAR(20), @ParentIds NVARCHAR(MAX))`, ROW_NUMBER over `vw_Review` per parent, STRING_SPLIT ids — the `ReadCurrentReviewsByBillIds` shape `:355-384`), routers of bill/expense/bill_credit/invoice/contract_labor/employee_labor/time_entry | Backend → DBA → MCP-eng → Frontend (types, ask-first) → Docs | `attach_lifecycle` replaces the Bill stitch (`bill/api/router.py:143-158`); documents get `status`, `review_status` (Name, kept), `review_status_kind` + the `review_status_*` block, `status_source` on single GET; ContractLabor gets `lifecycle_status` (legacy `status` untouched; overlay: disk `submitted` + declined Review → `declined`, + intermediate → `in_review`, + approved-but-deferred → `submitted` with kind `approved`, rendered "Approved — needs coding" until LS-04); EmployeeLabor `lifecycle_status`; TimeEntry `status` canonical beside `current_status`, `review_status_kind` from history (batch sproc `ReadCurrentTimeEntryStatusesByTimeEntryIds` extended with the previous row). `apply_reviewer_decision` returns the resolved block (no more `is_draft: True`, `bill/business/service.py:1289`). MCP: additive output fields, docstrings list both vocabularies. Web: optional fields on all five types; badge from `review_status_kind`. `?status=` filters are NOT added here (post-filtering a page is wrong; they arrive with the column in Phase 3). |
| **LS-01b Inbox lifecycle suppression + review-refused-on-finalized** | api / `dbo.inbox_tasks.sql` (whole-file guarded) + `review/business/service.py` | Backend → DBA | `ReadInboxTasks` adds `B.[IsDraft]=1` (E./BC./I.) per branch (parent joins exist: B :121, E :166, BC :211, I :255); `ReadInboxTaskCounts` first needs NEW parent joins on its Bill and BillCredit arms (today only Expense :356 and Invoice :392 join the parent) or list and badge counts diverge; `build_submit/advance/decline_payload` refuse when the parent is finalized (422 `status_locked`); `ReviewTimeline` hides actions on finalized parents (web, ask-first). |
| **LS-01c Review-service hygiene** | api / `entities/review`, `entities/review_status` | Backend → Frontend (U-155 form 422 copy) | `ReviewStatusService` shape guard rails (§4.2); notification auto-advance via `get_next_status` under User 33 (`notification_service.py:273,285`); Bill + CL `apply_reviewer_decision` routed through `ReviewService.create` (a duplicate reply at the same kind returns `Outcome='noop'` and the caller treats it as success — today's duplicate audit rows, `bill/business/service.py:1265-1268`, stop; a genuinely illegal edge is a 409 the email agent must surface, never retry; hooks uniform). |
| **LS-01d QBO pull stops writing `is_draft` on UPDATE** | api / `integrations/intuit/qbo/{bill,purchase,invoice,vendorcredit}/connector/**` + `base/field_ownership.py` + `reconciliation/business/service.py` | Backend → **Integrations + Security** → Scheduler (observe one pull cycle + one daily reconcile) | Drop `is_draft=False` from every UPDATE kwargs (`bill/connector/bill/business/service.py:208`; `purchase/connector/expense/business/service.py:151`; `invoice/connector/invoice/business/service.py:205` — the HIT-path update ONLY; `:410` is the MISS-path CREATE and stays until Phase 3; line connectors' update branches) — the `CASE WHEN @IsDraft IS NULL` guards then preserve local state (the `preserve_human_edited_ref` precedent applied by omission); CREATE paths unchanged until Phase 3. `field_ownership`: drop the phantom `review_status_id` (:260,:319), keep `is_draft` app-owned with the rule "written by pull on create only". Adopt of a locally in-progress row: stamp `QboId`, leave lifecycle, record non-critical `[qbo].[ReconciliationIssue] qbo_adopted_uncompleted_local` (§9 #7) — the Invoice adopt already routes through the shared `base/identity_fastpath.py::stamp_dbo_identity_with_lock` since U-356 (`_adopt_invoice_identity` :513), so the issue is recorded inside that shared helper: a shared-primitive change → two-phase dispatch. **Behaviour change to state:** a locally drafted invoice later adopted by the pull now stays `IsDraft=1` and drops out of budget-variance "Drawn" (`dbo.budget_variance.sql:174,283`) until completed locally — the `qbo_linked_not_completed` invariant must feed the budget watchdog. Daily QBO reconcile gains warning-severity `qbo_linked_not_completed` (`QboId IS NOT NULL AND IsDraft = 1`). LS-00c's xfail test goes green. |

**Deploy order:** LS-01a SQL (`/em`) → API → MCP → web (ask-first; cosmetic until then) ; LS-01b SQL → API ;
LS-01c API ; LS-01d API (verify one scheduler pull cycle, one reconcile). Units are independent of each other.
**State after Phase 1:** every in-scope entity speaks canonical `status`/`review_status_kind` on the wire; the
15-minute pulls can no longer clobber local lifecycle; stale inbox tasks are gone; review shape is guarded; no
column has changed.

### Phase 2 — Review pointer denormalization (additive; all five parents; no vocabulary change)

| Unit | Repo / layer | Owner → handoffs | Content |
|---|---|---|---|
| **LS-02a `ReviewStatusId` pointer + single-writer `CreateReview`** | api / base files of bill, expense, bill_credit, invoice, contract_labor (guarded ALTERs) + `dbo.review.sql` | Backend → **DBA** (design-gated: touches `CreateReview` and five hot tables) | Columns + FK + filtered index on all five parents; `CreateReview` gains the UPDLOCK parent read, the completed/edge refusal (empty result; same-kind duplicate = `noop`) and the in-transaction parent `UPDATE` of `ReviewStatusId/ReviewStatusDatetime` (Status write branches disabled until each Phase-3 unit) — in this phase the edge predicate reads the parent's lifecycle as `IsDraft × dbo.ReviewKind(ReviewStatusId)` for the four documents and as the legacy `Status` literals for ContractLabor, switching to the `Status` column per Phase-3 unit and at LS-04; `dbo.ReviewKind` UDF; set-based per-batch backfill (`ROW_NUMBER` over `vw_Review` per parent, 1,000/batch, idempotent); parity query (pointer == latest row) must return 0 before the API deploy. |
| **LS-02b In-sproc review projection + `/approve` route** | api / list & GET sprocs of the five parents (`LEFT JOIN dbo.ReviewStatus`; actor params preserved verbatim; base-vs-live diff each), `review/api/router.py` | Backend → DBA → Security (route verb) → Frontend → MCP-eng | Identical `review_status_*` field set projected in-sproc; `attach_lifecycle` switches to the projected columns (batch sproc kept one release, then LS-06d); NEW `POST /api/v1/approve/review/{parent}/{id}`; `record_fast_path_approval()` (no caller until Phase 3 gates); `ReadCurrentReviewsByBillIds` stitch retired from the Bill router. **Web rider (ask-first):** `BillEdit`/`ExpenseEdit`/`BillCreditEdit` auto-save gains refetch-and-rebase on 409 — today `useAutoSave.ts:74` → `BillEdit.tsx:489` keeps the stale `row_version` and strands the editor's changes; needed because an email-reply approval (agent, no browser) now bumps the parent ROWVERSION while AP has the page open. |
| **LS-02c Daily lifecycle reconcile** | api / `shared/api/admin.py` (`POST /api/v1/admin/reconcile/lifecycle`, `X-Drain-Secret`) → scheduler (`reconcile_lifecycle` daily timer) | Backend → **Scheduler** → Docs | Invariants recorded as `ReconciliationIssue`-style rows: I1 pointer == latest Review row; I2 `Status ∈ {submitted,in_review,approved,declined} ⇒ ReviewKind(pointer) == Status` (once Status exists); I3 `Status='draft' ⇒ pointer NULL`; I4 `Status='completed'` with an open (non-final, non-declined) review — warning, expected 0 once a gate ≥ `block_open_review`; I5 derived (Phase-1 resolver expression) == stored Status per row (drift). API endpoint deploys first, then `func azure functionapp publish`. |

**Deploy order:** LS-02a SQL by `/em` (columns → backfill in batches → parity 0) → LS-02b SQL (`/em`) → API →
web ask-first → MCP → LS-02c API → scheduler. **State after Phase 2:** review state is one `LEFT JOIN` away on
every list/GET, written atomically with the ledger row; the pointer is reconciled daily; `IsDraft` still the
lifecycle source of truth. Rollback = stop the pointer write (sproc revert) with the columns left in place.

### Phase 3 — Financial-document `Status` column, one unit per entity, **three-step swap** (Bill pattern-setter)

Each of **LS-03a Bill**, **LS-03b BillCredit**, **LS-03c Expense**, **LS-03d Invoice** (serialized, Bill first —
§9 #21; recompute any shared-registry count on rebase per `feedback_shared_registry_parallel_decrement_collision.md`)
ships as one unit in three `/em` steps around ONE API deploy — this closes the silent-no-op completion window
(an old API image completing via `UpdateXById(@IsDraft=0)` against a sproc that ignores it would leave fan-out
enqueued on a still-draft row; `bill/api/router.py:417-422` marks the job success for any status code):

- **Step (a) SQL (`/em`):** add `Status/StatusDatetime/StatusOrigin/StatusSourceRef` + DF + CKs + indexes;
  set-based per-batch backfill from `IsDraft × ReviewKind(pointer)` with origin per §4.3 (`completion` where
  CompletionJob/`BillCompletionResult` exists, `qbo_pull` where `QboId` is set, else `backfill`); parity:
  derived (LS-00c expression, via the SQL characterization script) == stored for every row, and
  `COUNT(IsDraft=0) == COUNT(Status='completed')`; create `Transition<Entity>Status` (writes Status AND the real
  `IsDraft`); `CreateReview` enables this parent's Status branch; `Create<Entity>` gains `@Status/@StatusOrigin/
  @StatusSourceRef = NULL` (writes both columns; NB `CreateBill` is homed in `dbo.bill_create_source_email.sql:8`,
  outside the `dbo.bill.sql` whole-file guard); `Update<Entity>ById` gains the compat translation
  `@IsDraft = 0 AND Status <> 'completed' → Status='completed', StatusOrigin='completion', IsDraft=0` and
  neutralizes `@IsDraft = 1` (no write — an old image or a queued iOS Draft-toggle PUT must never produce
  `IsDraft=1 ∧ Status='completed'`) (only an old image can hit either); list/count sprocs gain `@Status`/`@ReviewStatusPublicId` (actor params preserved).
  Old and new API images both keep `IsDraft` correct.
- **Step (b) API deploy:** model `status/status_datetime/status_origin/status_source_ref` + derived `is_draft`;
  `complete_*` calls the transition (gate flag `off` at deploy; `noop` short-circuit; 3× retry on true
  conflicts) then the unchanged finalize/enqueue sequence; PUT/POST `is_draft` ignored + logged; create
  `status='completed'` accepted under `system_authz` only; connector CREATE passes `status='completed',
  origin='qbo_pull', source_ref` (Integrations + Security); terminal lock (422 `status_locked`) on header PUT,
  line-item mutations, non-admin DELETE with the `system_authz` exemptions; `field_ownership` app-owned =
  `['status','review_status_id']`; agent tools/prompts (`bill_specialist prompt.md:10,77,142-148,157,208,231`;
  `bill/intelligence/tools.py:27,294,460`) reworded to `status`; MCP `search_*` gains `status` (`is_draft` kept);
  web (ask-first): status badge, `?status=` chips, Complete button gating by gate reason, "Approve & Complete"
  for approvers, `CompletionStatusBar` polling ends on `status==='completed'`; `ExpenseEdit` stops sending
  `is_draft` on save (`ExpenseEdit.tsx:143`). Docs refresh.
- **Step (c) SQL (`/em`, after the new image is verified live and the compat path shows zero hits), in THIS
  order inside one batch:** (1) redefine `Create<Entity>` to omit `[IsDraft]` from its INSERT list (`@IsDraft`
  kept as a no-op param) — every Create sproc names the column today (`dbo.bill_create_source_email.sql:29-32,52`;
  `dbo.expense.sql:145,162`; `dbo.bill_credit.sql:87,100`; `dbo.invoice.sql:131,146`) and an INSERT naming a
  computed column is SQL Server error 271; (2) remove the `@IsDraft` application from `Update<Entity>ById`
  (param kept as a no-op) and make `Transition<Entity>Status` stop writing `IsDraft`; (3) drop the system-named
  `IsDraft` DEFAULT via a `sys.default_constraints` lookup (no `DF_*_IsDraft` exists), DROP `IsDraft`, re-ADD as
  PERSISTED computed, recreate any dependent index (census: none); (4) list sprocs translate `@IsDraft` to Status.

Entity notes: **BillCredit** — `complete_service.py:87-145` finalize loop → transition; no QBO push. **Expense**
— gate `off` by policy; Purchase connector create → `status='completed'`; coding SQL untouched via computed
`IsDraft`; the STALE base file rule (guarded ALTERs only). **Invoice** — `service.py:404-415` (the header `is_draft=False` finalize) → transition;
connector MISS-path create `:410` → `status='completed'` (the `:205` update already stopped writing `is_draft` in
LS-01d; adopt untouched since U-356); `push_draw` unchanged (derived `is_draft`); `budget_variance` and
the QBO reconcile untouched via the computed column; gate `block_open_review` until U-128.

**Deploy order per unit:** (a) SQL `/em` → (b) API `:latest` + restart + verify → web ask-first → MCP → (c) SQL
`/em`. **State after each unit:** that entity has a CHECK-guarded `Status`, an unwritable `IsDraft`, a locked
terminal state and gate flags ready to flip; siblings still `IsDraft`-native; every external contract still
speaks `is_draft`. **After Phase 3:** all four documents on the canonical column; `/em` flips gates per §9 #1
after one clean daily reconcile each.

### Phase 4 — ContractLabor vocabulary cutover (the ONE coordinated multi-repo window)

| Unit | Repo / layer | Owner → handoffs | Content |
|---|---|---|---|
| **LS-04 ContractLabor cutover** | api (`entities/contract_labor/**`, `entities/review/**`, `entities/time_entry/sql/dbo.time_entry.sql`, `intelligence/agents/{contract_labor_specialist,buildone}`, `scripts/`), web (`src/pages/labor/**`, `src/pages/contract-labor/**`), mcp (`tools/labor*`, `time_entry.py` docstrings) | Backend → **DBA** (single `XACT_ABORT` batch) → Security (module unification grants) → **Frontend** (same-window deploy, §9 #10) → MCP-eng → Docs | **SQL batch (`/em`, one transaction):** census gate = zero stray values; in-place `UPDATE` `pending_review→draft`, `ready→approved`, `billed→completed` (explicit allowlists, U-200 pattern; `submitted` unchanged); `DF_ContractLabor_Status 'draft'` + `CK_ContractLabor_Status` + companion columns (`StatusOrigin='backfill'` for renamed rows; `SourceRef='bill:<BillLineItemId>'` where present); `TransitionContractLaborStatus`; `CreateReview` CL Status branch enabled; `CreateContractLabor @Status` default `'draft'` (`:232`); `AggregateTimeEntryOnSubmit` `:1431 → 'draft'`, `:1501 → 'completed'`; `IsTimeEntryDownstreamLocked` `:1685 → 'completed'` (EL `'invoiced'` at `:1455,:1694` untouched); U-210 gate/crew sprocs `:1810 → 'draft'`, `:1843,:1876 → IN ('submitted','in_review')`. **API (deployed FIRST, dual-read, LEGACY-write):** `canonicalize` both ways on `?status=` and on reads; **writes stay on the legacy literals until `/em` flips `CONTRACT_LABOR_CANONICAL_WRITES=true` immediately after the SQL batch** (before the flip `TransitionContractLaborStatus` does not exist, `CreateContractLabor` still defaults `'pending_review'` and the U-210 gate sprocs still count legacy literals — canonical writes in that window would under-count the crew-email gate and hide rows from LaborList/Generate Bills); after the flip every write goes through the guard; legacy `mark_as_ready`/`bulk-mark-ready`/`PUT /{id}/bill status`/`PUT /{id} status` → `record_fast_path_approval` (`can_approve`); `status` removed from CL create/update schemas (ignored + warning one release); `completion_blockers` predicate consumed by `generate_bills` (reads `approved`, writes `completed`, origin `downstream`, ref `bill:<id>`); `/decline` and PM reply `rejected` transition to `declined`; `read_by_status('ready')` → `'approved'` (`bill_service.py:232,891`; `bill_summary.py:36`); delete guards `:354` (`delete_by_public_id`) and `:925` (`bulk_delete`) → `is_terminal` + `DeleteReviewsByContractLaborId` cascade; the `apply_reviewer_decision` precondition `:542` (+ docstring `:538`) → `status ∈ {submitted, in_review}`; `lifecycle_status` now equals `status`; `contract_labor_specialist`/`buildone` prompts + tool descriptions; MCP docstrings; `regenerate/fix/clean_contract_labor_*` scripts rewritten, `verify_*` scripts that `UPDATE dbo.ContractLabor` on prod row 647 deleted. **Web:** `LaborList.tsx:45-62`, `LaborReviewScreen.tsx:924,949,963,977`, `contractLaborStatus.ts:14-22`, `ContractLaborList/Edit/View`; default chip `draft`; `PERSISTER_BUSTER` bump (`src/main.tsx:68`). `tests/test_status_literals.py` added. |

**Deploy order (one off-hours window avoiding the 18:00 UTC auto-submit; ~10 min):** web build green and
staged → API deploy (dual-read) → SQL batch by `/em` → web deploy (pre-authorized, §9 #10) → MCP → verify:
per-bucket counts equal pre-migration, `DISTINCT Status` canonical only, LaborList shows all queues, a
TimeEntry submit aggregates a `draft` CL, the crew-draft gate counts `draft` (not 0 → premature release), one
TE edit on a `completed` CL → 422. Previous API image + reverse-UPDATE script staged as rollback. **State
after Phase 4:** CL speaks canonical everywhere; TE sprocs lock on `completed`; EL untouched; installed iOS
unaffected (1 iOS consumer of CL, read-only).

### Phase 5 — TimeEntry denormalized `Status` + canonical alias

| Unit | Repo / layer | Owner → handoffs | Content |
|---|---|---|---|
| **LS-05 TimeEntry Status column** | api / `entities/time_entry/**` (base file whole-file guarded), `contract_labor/business/bill_service.py` (TE write hook), web `src/pages/time-entry/**`, mcp `time_entry.py` | Backend → **DBA** → Frontend (ask-first) → MCP-eng → iOS (no change; LS-00d already installed) → Docs | SQL (`/em`): `dbo.TimeEntry.[Status]` + DF + CK; `CK_TimeEntryStatus_Status` (six canonical + legacy `rejected`/`billed`); backfill from the latest history row `(CreatedDatetime DESC, Id DESC)` with parity 0; the history-insert sproc behind `TimeEntryService.submit/approve/reject` (`service.py:335,400,437,445`) updates the parent in the same transaction (reject inserts `declined` + `draft`, parent `draft`); `ReadTimeEntriesPaginated/CountTimeEntries` (`:595-600,:669-674`) and the digest filter on the column (OUTER APPLY removed); `IsTimeEntryDownstreamLocked` reads `TimeEntry.Status='completed'` OR the CL/EL lineage (belt until fold-in). API: `status` canonical; `current_status` + `status_history[].status` reverse-aliased; `?status=` accepts both spellings; `VALID_TRANSITIONS` (`service.py:21-27`) re-expressed through the shared guard with TE narrowing; `review_status_kind` derived; `generate_bills` appends the TE `completed` write per `SourceTimeEntryId` (idempotent, origin `downstream`, §9 #13); error `detail` texts frozen + `error_code`. Web: `TimeEntryList.tsx:40-41` chips and `TimeEntryView.tsx:342` `isTerminal` read `status`; banner copy `:704` corrected. MCP docstrings list both. |

**Deploy order:** SQL `/em` (backfill + parity) → API → web ask-first → MCP. Precondition: LS-00d is the
App Store build (or accepted risk: unknown → `.draft`, which this unit never emits thanks to the alias).
**State after Phase 5:** TE list/count/lock read one column; phones see byte-identical resting values.

### Phase 6 — Closure + hygiene (independent units)

| Unit | Repo / layer | Owner → handoffs | Content |
|---|---|---|---|
| **LS-06a Inbox on parent columns + ContractLabor arm** | api / `dbo.inbox_tasks.sql`, `entities/review/business/task_feed.py`, `app.py` | Backend → DBA → Security (scope predicate) | §4.2 inbox rewrite; `@EntityType 'ContractLabor'`; `TaskFeed` registration. |
| **LS-06b Review delete parity + delete policy** | api / `dbo.review.sql` (`DeleteReviewsByExpenseId/ByBillCreditId/ByInvoiceId`), the three `delete_by_public_id` services, `ContractLaborService.delete_by_public_id` | Backend → DBA | Null `ReviewStatusId` first, cascade rows, then parent delete; forbid non-admin delete once a Review row exists (§9 #17). May run any time after LS-00b (independent). |
| **LS-06c Permission unification** | api / `entities/review/api/router.py` (15 routes), `contract_labor/api/router.py`, `time_entry/api/router.py:472`; web `contractLaborPermissions.ts`, `timeEntryPermissions.ts`, `ReviewTimeline.tsx:28-33,90` | **Security** → Backend → Frontend | RoleModule grant-matrix SQL generated from LS-00a's census, applied by `/em` BEFORE the API deploy; advance/approve/decline → `can_approve`; submit stays `can_update`; complete stays `can_complete` (+`can_approve` fast-path); CL review routes → `Modules.CONTRACT_LABOR` (§9 #9); `CanSubmit` documented dead. |
| **LS-06d Shim retirement + hygiene** | api / web / mcp | Backend → DBA → Frontend → MCP-eng → Docs | Drop `@IsDraft` params where no caller remains, `ReadCurrentReviewsByBillIds`, `ReadCurrentReviewsByParentIds`; delete `labor_status.py`; drop `lifecycle_status` on CL; remove the TE aliases once `X-Client-Build` logs show no pre-LS-00d decoder; Budget/BudgetRevision named CHECKs with their own vocab + Pydantic enums; `field_ownership` phantom cleanup; `/docs` final refresh. |
| **LS-06e EmployeeLabor fold-in** | separate program (own design gate) | — | Consumes the EL→CL mapping (§4.3) and the CL guard; retires the EmployeeId branch of `AggregateTimeEntryOnSubmit` (`dbo.time_entry.sql:1436-1481`) and the `'invoiced'` literals. |

## 7. Contract impact

**API payloads (all `{"data": …}` envelope).** Added (additive, optional): `status`, `status_datetime`,
`status_origin`, `status_source_ref`, `review_status_kind`, `review_status_public_id`,
`review_status_sort_order`, `review_status_color`, `review_status_datetime`, `review_status_user_id` on
Bill/Expense/BillCredit/Invoice/ContractLabor/TimeEntry lists + GETs; `status_source` on single GETs;
`lifecycle_status` on ContractLabor (transitional) and EmployeeLabor; `completion_blockers` on ContractLabor;
`error_code` on 4xx bodies. Kept with today's meaning: `is_draft` (derived), `review_status` (Name),
`review_status_is_final`, `review_status_is_declined`, TimeEntry `current_status` + `status_history[].status`
(legacy-aliased). Changed: ContractLabor `status` becomes canonical at LS-04 (same window as web); `apply-
reviewer-decision` responses return the resolved block; `POST /complete/*` may return 409 `review_open /
review_declined / approval_required` once gates are on; header PUT / line mutations / DELETE on `completed`
return 422 `status_locked`; POST/PUT `is_draft` is ignored. New routes: `POST /approve/review/{parent}/{id}`,
`POST /admin/reconcile/lifecycle`. **Consumers flagged:** web `BillList/BillEdit/BillView/BillCreate`,
`ExpenseList/Edit/View`, `BillCreditList/Edit/View`, `InvoiceList/View`, `LaborList`, `LaborReviewScreen`,
`ContractLabor*`, `TimeEntryList/View`, `ReviewTimeline`, `CompletionStatusBar`/`useCompletionPolling`
(`BillEdit.tsx:193`), `types/api.ts:201,736-743,763,777,801,815,834,850,871,1009,1047-1052`; MCP
`tools/{bill,expense,bill_credit,invoice,contract_labor,time_entry}.py` output models (extra=ignore) and
`search_*` params; agents `bill_specialist`, `expense_specialist`, `bill_credit_specialist`,
`contract_labor_specialist`, `buildone`, `time_tracking_specialist` (`cowork_time_agent_prompt.md`); iOS
`BillModel/ExpenseModel/InvoiceModel/TimeEntryModel` (additive only), `BillEndpoints`/`BillLineItemEndpoints`
(`is_draft` still accepted); scripts listed in §8.

**Sproc signatures.** New: `ReadCurrentReviewsByParentIds(@ParentType, @ParentIds)`;
`Transition{Bill,Expense,BillCredit,Invoice,ContractLabor}Status(@Id, @RowVersion, @FromStatuses, @ToStatus,
@ActorUserId=NULL, @Origin=NULL, @SourceRef=NULL)` → row + `Outcome`; `dbo.ReviewKind(@ReviewStatusId)`;
`DeleteReviewsByExpenseId/ByBillCreditId/ByInvoiceId`. Changed (all new params `= NULL`; actor params
preserved): `CreateReview` (+`@ContractLaborId` in LS-00b; +`@Origin`; in-transaction parent UPDATE);
`Create{Bill,Expense,BillCredit,Invoice,ContractLabor}` (+`@Status/@StatusOrigin/@StatusSourceRef`);
`Update{…}ById` (`@IsDraft` → no-op); `Read{…}Paginated`/`Count{…}` (+`@Status`, `+@ReviewStatusPublicId`,
`@IsDraft` translated; `LEFT JOIN ReviewStatus`); `ReadInboxTasks/Counts` (`@EntityType` + `'ContractLabor'`);
`AggregateTimeEntryOnSubmit`, `IsTimeEntryDownstreamLocked`, the U-210 gate sprocs (literals);
`ReadTimeEntriesPaginated/CountTimeEntries` + the TE history-insert sproc (parent update); `CreateContractLabor`
default. The connector CREATE kwargs that pass `is_draft=False` (`bill :282`, `purchase :210`, `invoice :410`,
`vendorcredit :224` — the LIVE pull path, imported by `shared/api/admin.py:355-388`; the `scripts/sync_qbo_*.py`
entry points themselves never pass it) switch to `status='completed', origin='qbo_pull'` in Phase 3.

**MCP tools.** Additive fields on Bill/Expense/BillCredit/Invoice/ContractLabor/TimeEntry summaries;
`search_bills/expenses/bill_credits/invoices` gain `status` + `review_status_kind` (`is_draft` kept);
`time_entry` docstrings (`tools/time_entry.py:7-26,129-135`) list both vocabularies; ContractLabor tool
docstrings switch at LS-04. MCP deploy follows each API deploy that adds fields.

**iOS.** **No CoreData model migration** (`CDBill.isDraft/pendingIsDraft`, `CDTimeEntry` keep their meaning);
LS-00d is the only iOS unit; queued `is_draft` mutations drain cleanly (ignored server-side); the terminal
lock's 422 is discarded as permanent by installed builds (`BillService.swift:469-486`); TE resting values
never change on the wire.

**Web types.** Optional additions on the five entity types + `ReviewStatusKind` union; `TimeEntryStatusValue`
gains the canonical members at LS-05; `LaborStatus` union flips at LS-04 (`PERSISTER_BUSTER` bump then);
`tsc -p tsconfig.app.json` / `npm run build` per `feedback_web_type_check_command.md`.

**Scheduler.** New `reconcile_lifecycle` daily timer (LS-02c); no change to the pull timers (their behaviour
changes API-side in LS-01d/Phase 3). Publish scheduler AFTER the API endpoint exists.

## 8. Blast radius / risk

| Hazard (census / judges) | Kind | How this design closes it |
|---|---|---|
| `excel_detector.py:148,307` `getattr(bill,'is_draft', True)` — a renamed/removed attribute treats EVERY bill/expense as draft (daily MS reconcile silently empties) | fail-silent | `is_draft` stays a real model attribute (derived) forever; LS-06d never removes it. |
| Crew-email date gate counts `Status='pending_review'` (`dbo.contract_labor.sql:1810`) — after a rename the count is 0 → consolidated crew email releases on the FIRST submit | fail-silent | Literal moves inside the same LS-04 transaction; post-window verification "gate counts `draft`". |
| `LaborList.tsx:45-62` defaults to a legacy literal → empty Labor list; `LaborReviewScreen.tsx:924-977` keys actions/disable on legacy literals → broken review screen for the ask-first window | fail-silent (UX) | CL emits legacy `status` + `lifecycle_status` until the cutover; the API canonicalizes `?status=` both ways; the LS-04 web deploy is same-window (pre-authorized, §9 #10). |
| MCP `time_entry.py` docstrings enumerate legacy values → agent asks for `status='draft'`, gets empty pages | fail-silent | TE resting strings never change; `?status=` accepts both spellings; docstrings list both until LS-06d. |
| `IsTimeEntryDownstreamLocked` keys on CL `'billed'` / EL `'invoiced'` (`dbo.time_entry.sql:1685,1694`) → a rename silently UNLOCKS billed entries | fail-silent (payroll) | `:1685` re-keyed in the LS-04 batch; LS-05 adds the TE column as the primary lock source; EL literal untouched until fold-in; post-window smoke: TE edit on a completed CL → 422. |
| `AggregateTimeEntryOnSubmit` frozen-skip `:1501` on `'billed'` → re-aggregation would overwrite completed CL amounts | fail-silent | Same LS-04 batch. |
| Raw-SQL `i.IsDraft` in the daily QBO reconcile (`reconciliation/business/service.py:74,409-411`) and manual scripts | fail-loud | `IsDraft` persists as a computed column — no SQL breaks. |
| Old API image completing during a Phase-3 window (fan-out enqueued, Status not flipped) | silent drift | Three-step swap: transition sproc writes real `IsDraft` in (a); compat translation in `Update*ById`; computed column only in (c) after the image is verified. |
| Two-sproc review write leaving pointer ≠ Status on a refused edge | inconsistency | `CreateReview` writes ledger row + pointer + Status in ONE transaction; refusal = empty result before any write. |
| Terminal lock as bare 409 → installed iOS eternal reload loop (`BillService.swift:454-461`) | client break | 422 + `error_code` (discarded as permanent :469-486); `ConflictResolution` untouched. |
| 422 on POST create `is_draft=false` → offline-created bill deleted (`BillService.swift:283-302`) | data loss | Inbound `is_draft` ignored + logged, never 422 — AND the queued create already 422s today for unrelated reasons (`CreateBillEndpoint` sends `vendor_id` as int and omits `attachment_public_id`), so LS-00d fixes or deletes that endpoint. |
| Unknown TE value decoded as `.draft` re-enables Clock In on a locked day (`TimeEntryModel.swift:52`) | client break | Reverse alias on `current_status` and `status_history`; LS-00d `.unknown` decoder ships first; no in-place history rename. |
| QBO pull re-forcing `is_draft=False` every tick clobbers a review/draft (`bill connector :208`, `purchase :151`, `invoice :205`; `:410` is the create) | silent clobber | LS-01d removes the UPDATE kwarg before any column exists; `qbo_linked_not_completed` warning invariant; `field_ownership` corrected. |
| LS-01d side effect: a locally drafted invoice later adopted by the pull stays `IsDraft=1` and leaves budget-variance "Drawn" (`dbo.budget_variance.sql:174,283`) until completed locally | behaviour change | Stated in LS-01d; `qbo_linked_not_completed` feeds the budget watchdog; the census counts such rows before the flip. |
| `CreateReview` parent UPDATE bumps ROWVERSION → reviewer action 409s a concurrent web auto-save | UX 409 | Write set disjoint from editor columns (no lost update); `ReviewTimeline` awaits `autoSave()` + refetch; the three edit pages gain refetch-and-rebase on 409 (LS-02b rider — an email-reply approval has no browser to await); explicit decision §9 #5. |
| Backfills on hot tables (~10K Expense rows) TCP-drop under load | ops | Set-based, 1,000/batch, idempotent, per-batch commit (`feedback_backfill_setbased_under_load.md`); parity before CHECK. |
| Base-vs-live drift when redefining sprocs (U-037 class) | RBAC regression | `sys.parameters` diff before every redefinition; whole-file guards already cover bill/expense/bill_credit/invoice/contract_labor/time_entry/inbox_tasks; LS-00b adds `review`. |
| ReviewStatus admin edits breaking flag-based resolution (0 or 2 declined/final rows) | fail-loud today | Shape guard rails (422) at write time; historical rows keep their id. |
| Permission tightening (`can_update` → `can_approve`) locking a live reviewer role out | fail-closed | Grant matrix from the LS-00a census applied by `/em` BEFORE the verb flip; `IsSystemAdmin` (17, 33) bypass keeps daily operation intact. |
| Gate turning on while `InvoiceEdit` is unrouted → completion impossible from the UI | UX | Invoice stays `block_open_review` until U-128; flags flip per entity, no deploy. |
| Legacy misfit rows (completed with open review; `ready` without review) | data honesty | Never fabricate approvals (§9 #16): origin `backfill` exempts them; inbox suppressed by lifecycle predicate; census sizes them. |
| Scripts: `regenerate/fix/clean_contract_labor_*`, `verify_contract_labor_*` (prod `UPDATE` of CL 647), `backfill_box_workbook.py`, `reconcile_project.py`, `_clr_*` | manual | Rewritten or annotated in LS-04 (never blind grep-rename); `verify_*` prod mutators deleted. |

## 9. Open decisions for Chris

1. Completion gate: adopt per-entity env flags `LIFECYCLE_COMPLETION_GATE_{BILL,EXPENSE,BILL_CREDIT,INVOICE}` ∈ {off, block_open_review, require_approved}, shipped `off` and flipped by /em, with steady state Bill + BillCredit = require_approved, Invoice = block_open_review until U-128, Expense = off? Recommend YES.
2. Approver fast-path: an actor with can_complete AND can_approve completing an unapproved document auto-records an Approved Review row under their name (web button "Approve & Complete") so `completed ⇒ approved` becomes a real invariant — this is the ONLY `draft|declined → approved` edge (`StatusOrigin='fast_path'`)? Recommend YES.
3. Terminal-lock error contract: A) HTTP 422 + `error_code:"status_locked"` (installed iOS discards the queued edit as permanent) or B) HTTP 409 carrying the server entity (engages the iOS ConflictResolution UI)? Recommend A.
4. Inbound `is_draft` on POST/PUT from non-system callers: ignore with a warning log (never 422)? Recommend YES — a 422 on a queued create deletes an offline-created bill.
5. `CreateReview`'s in-transaction parent UPDATE bumps the parent ROWVERSION, so a reviewer action can 409 a concurrent web auto-save: A) accept it (write set is disjoint, no lost update; web ReviewTimeline awaits autoSave and refetches) or B) move the pointer/status to a 1:1 sidecar table so the parent row is untouched? Recommend A — with the LS-02b web rider (auto-save refetch-and-rebase on 409), since the Bill/CL reviewer is usually an email reply with no browser to `await autoSave()`.
6. Recall (submitted→draft) and reopen (completed→draft): both OUT of v1 — Decline is the send-back, and an admin-only /reopen with compensations is a later unit? Recommend YES.
7. QBO pull adopting a locally in-progress row: A) stamp QboId, leave status, record a `qbo_adopted_uncompleted_local` ReconciliationIssue (complete with origin qbo_pull only when the local row is draft with no review) or B) keep today's force-to-completed on the Bill/Expense HIT-path updates (`bill :208`, `purchase :151`; the Invoice adopt already leaves lifecycle alone since U-356)? Recommend A — noting A makes a locally drafted, QBO-adopted invoice leave budget-variance "Drawn" until it is completed locally.
8. Permission verbs: move review advance/approve/decline from can_update to can_approve (RoleModule grant matrix applied first), keep submit on can_update, keep complete on can_complete, drop the dead CanSubmit flag? Recommend YES.
9. ContractLabor module: A) unify review + CRUD + complete on Modules.CONTRACT_LABOR (grant-parity SQL) or B) keep review routes on Modules.TIME_TRACKING? Recommend A.
10. ContractLabor cutover web deploy: pre-authorize ONE same-window web deploy for LS-04 (the only exception to ask-first), with the API held in dual-field mode (`status` legacy + `lifecycle_status`) until that window? Recommend YES.
11. ContractLabor "ready" convergence: A) all four legacy ready paths converge on an explicit Approved Review row under the operator, approval is unconditional, and coding completeness moves to the completion edge as `completion_blockers` reported by Generate Bills (retiring the three "ready" definitions) or B) keep coding as an approval precondition (409 `lines_uncoded` at approve)? Recommend A.
12. TimeEntry representation: A) keep dbo.TimeEntryStatus as the ledger, add a denormalized TimeEntry.Status column, derive review_status_kind, NOT a Review parent or B) make TimeEntry a 6th Review parent? Recommend A.
13. TimeEntry `completed` write on ContractLabor completion (idempotent per SourceTimeEntryId, from `submitted` OR `approved` — PM approval of the CL never requires TE approval; wire alias `current_status='billed'`) so the downstream lock reads one column? Recommend YES.
14. TimeEntry reject: keep the two-row reject (declined then draft), resting state draft, declined visible only as review_status_kind — no resting declined state? Recommend YES.
15. `approved → declined`: A) not allowed (keeps today's final-status refusal; retraction is a future recall/reopen unit) or B) allowed while not completed? Recommend A.
16. Legacy contradictions (completed rows whose latest Review is open or declined; `ready` rows with no Review): A) never fabricate Approved rows — mark StatusOrigin='backfill', suppress from the inbox via the lifecycle predicate, exempt from the invariant or B) /em writes one system Approved row per row? Recommend A.
17. Delete policy once a Review row exists: A) forbid for non-admins, IsSystemAdmin cascades via Delete{Reviews}By{Parent}Id or B) cascade for everyone (today's Bill behaviour)? Recommend A.
18. EmployeeLabor: A) freeze on the legacy vocab until the fold-in, expose a read-only `lifecycle_status`, delete the EL block of the 2026-07-02 migration or B) migrate EL in place now? Recommend A unless the census shows accumulated rows.
19. Confirm OUT of scope: Budget/BudgetRevision (own-vocab CHECKs only), Vendor.IsDraft, COI/BL/CL VerificationStatus, line-level IsDraft/IsBilled mirrors, operational job/queue statuses, web Tasks page? Recommend confirm all.
20. Bill `in_review`: keep the system auto-advance at review-notification enqueue, attributed to the system user (33) and located via get_next_status? Recommend YES.
21. Phase-3 order: A) Bill first as pattern-setter (largest consumer surface, proves the shape) or B) BillCredit first (smallest blast radius)? Recommend A.
22. IsDraft end state on the four documents: A) PERSISTED COMPUTED over Status (physically unwritable; all raw SQL survives) or B) keep a real IsDraft column mirrored by the transition sproc forever? Recommend A.
23. Editing an approved (not completed) document: A) allowed, review state unchanged or B) a material edit auto-reverts to submitted and re-notifies? Recommend A.
24. Daily lifecycle reconcile as its own admin endpoint + scheduler timer (mirrors reconcile/qbo and reconcile/ms) rather than folded into the MS daily reconcile? Recommend YES.

## 10. Architect map — first unit (LS-00b: reconcile `dbo.review.sql` to the live 5-parent shape)

- **Repo + layer:** `build.one.api` — SQL only: `entities/review/sql/dbo.review.sql` (table ALTER block,
  `CK_Review_OneParent`, `vw_Review`, `CreateReview`, `ReadCurrentReviewsByBillIds`), migrations 003/005 →
  pointer stubs, `tests/test_sproc_single_source.py` (`("review", REVIEW_BASE)` whole-file row, coexisting with the six per-sproc pins at `:104-106,:121-123`); rider: the
  Id-tiebreak fix at `dbo.time_entry.sql:1860-1865`. No Python, no router.
- **Right altitude:** fix the shared base file, not another migration — the base is internally half-migrated
  (4-parent table/view/`CreateReview` vs. CL read sprocs at :267-346); every later Review touch would otherwise
  re-drop `@ContractLaborId` (U-037 class). Bodies come from live `sys.sql_modules` after a `sys.parameters`
  diff, never from migration 005 blindly.
- **Owning role + handoffs:** Backend engineer builds → **DBA** for the sproc/table batch (design-gated, two-phase
  dispatch: /em approves the diff first, then the apply) → **Security** review that the CL review routes still
  gate `Modules.TIME_TRACKING can_update` unchanged → **Docs**: no `/docs` change owed (internal SQL hygiene);
  note in `SESSION_NOTES.md`.
- **Contract impact:** none on the wire. `CreateReview`'s live signature (with `@ContractLaborId`) becomes the
  repo-canonical one; `vw_Review` gains `[ContractLaborId]` (additive); consumers unchanged
  (`ReviewRepository.create`, inbox, `ReviewTimeline`).
- **Verification:** `./.venv/bin/python -m pytest` green (single-source test now covers `review`); after `/em`
  applies: `sys.parameters` of `CreateReview` == base, `sys.columns` on `dbo.Review` includes `ContractLaborId`,
  a CL review submit round-trips (`POST /submit/review/contract-labor/{id}` → row with `ContractLaborId`).
- **Freshness tier:** `/docs` API section — no change (LIVE/DERIVED unaffected); the CURATED lifecycle page
  arrives in LS-00e.

**STOP.** Nothing above is built, applied or deployed. `/em` decides the real unit id(s), the dispatch order
and the SQL applies; Chris decides §9.
