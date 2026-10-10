# U-639 — Cost-code prediction for bill lines: evaluation of the 2026-10-08 design prompt

**Status:** DESIGN unit U-639 (Phase 1), claimed 2026-10-08. Nothing built. Awaiting Chris's D1–D9 decisions (§7).
**Author:** `/em`, 2026-10-08. Every "exists / does not exist" claim below was checked against `build.one.api` on
this date; the live counts are quoted from board rows U-535a / U-535b (measured 2026-09-24) and are perishable.
**Input:** Chris's dictated prompt for an "automated cost-code prediction feature" (two intake adapters → Stage 1
document-type gate → Stage 2 cost-code classifier → deterministic budget cross-check → routing → human review).
**Terminology:** the prompt uses generic construction-finance vocabulary. The build.one names are used here:
a vendor invoice is a **Bill**; an **Invoice** is the customer draw; lines code to a **SubCostCode** (SCC).

---

## 1. Verdict in three sentences

The design's *shape* is sound and its instincts are right: attachment as the unit, strict leakage and temporal
discipline, a deterministic budget check rather than a learned one, everything terminating at a human, and
structured signal before text. Its *premises* describe a different system: three of the five "already exists"
claims are false or dormant, the commitment feature family has no data behind it, and the structured-first
milestone it proposes already shipped for card expenses under U-005. The highest-return first step is therefore
not a classifier — it is wiring the text extraction that is built and never runs (U-535a), then porting the
existing deterministic resolver to bill lines and **measuring** it before any learned model is considered.

---

## 2. Premise check — what the prompt assumes vs what the repo holds

| Prompt assumes | build.one reality (verified) | Consequence for the design |
|---|---|---|
| Commitments (POs / subcontracts) exist | **None.** No PO, subcontract, or commitment entity anywhere. `dbo.VendorProjectRate` carries HourlyRate + Markup only; `dbo.Contract` carries ProjectId + BuildersFeeRate and its README defers contract value / COs / retainage. | Every "commitment linkage" feature and the "budgeted − committed − spent" formula have **no data**. Drop, or book a Contract/commitment entity as a *product* unit first (it is already on the roadmap as a draw-package dependency). |
| "Invoices" are vendor invoices | `dbo.Invoice` is the customer draw (AR). Vendor invoices are `dbo.Bill`. | Vocabulary only. |
| The cost code is a property of the bill | `dbo.Bill` has **no ProjectId**. `ProjectId` and `SubCostCodeId` live on each `dbo.BillLineItem`; lines code to one of **475 SubCostCodes**, never to a CostCode directly. | The **label grain is the line**. A bill can span projects and codes. **Project is itself a prediction** on email intake — and the budget check cannot run without it. |
| Change orders exist as an entity | `dbo.BudgetRevision` with `Type='change_order'` and delta lines. | Fine as-is. |
| A document intake pipeline with provenance exists | `dbo.Attachment` (+ Blob original, SHA-256 `FileHash`) exists. **Provenance is free text**: `Category='email_intake'`, `Description='Bridged from EmailAttachment {id}'` — no source column, no FK. Bill-level `IntakeSource` (`manual|agent|script|bill_folder`) exists. | A real but small gap — columns, not a new entity (§5.1). |
| The email adapter is new work | **Built, then torn down 2026-08-16 (U-218f):** scheduler timers removed, `email_specialist` deleted. `dbo.EmailMessage` / `dbo.EmailAttachment` / the bridge service remain, inert; `/admin/email/poll` is a manual endpoint nothing calls. | A **revival decision**, not a build. The teardown *rationale* is not recorded in `SESSION_NOTES.md` §U-218f — recover it before proposing revival (§7 D3). |
| The SharePoint/Box adapter is new work | **SharePoint bill-folder intake is LIVE** (`process_bill_folder`, 1-min timer → `/admin/bill-folder/tick`). The filename convention `{Project} - {Vendor} - {BillNumber} - {Desc} - {SubCostCode} - {Rate} - {BillDate}.pdf` **is the classifier today — a human types the code.** Box inbound = vendor-compliance docs only; there is no Box → Bill path. | The folder adapter exists. The prompt's "platform metadata" is already captured in `dbo.BillFolderRunItem` (Filename, SharePoint ItemId, Result JSON). |
| Dedup exists | SHA-256 `FileHash` + `ReadAttachmentByHash`, honoured on 4 paths (email bridge, QBO attachable, Box compliance, extraction skip). **No unique index**; the manual-upload prevention is commented out (`entities/attachment/api/router.py` ~L299). | Partial. Not a blocker for this feature. |
| Budget check = budgeted − committed − spent | `ReadBudgetVarianceByProjectId` (`entities/budget/sql/dbo.budget_variance.sql`) returns per-SCC `BudgetAmount`, `ActualCost`, `CostVariance`, `RemainingToDraw`. **No committed term. Drafts count toward ActualCost by policy.** | One sproc call away — but the check **must subtract the bill being scored** or a draft bill counts against itself (§5.4). |
| Line items can be assembled from the document | Document Intelligence runs `prebuilt-layout` + key-value pairs **by decision** (DI is a sensor); typed fields are deliberately NULL. There are no typed line items anywhere. | "Assemble line-item descriptions + vendor + material keywords" is itself an extraction step (an LLM structured task), not a lookup. |

---

## 3. What already exists that the prompt would rebuild

- **The structured-first milestone shipped for expenses — U-005.** `ExpenseCodingSuggestionService`
  (`entities/expense_coding_item/business/suggestion_service.py`) is deterministic: Project from memo
  abbreviation (0.95) else address match; SCC from memo shorthand via `FindSubCostCodeForReply` else the vendor's
  modal SCC over completed expense lines (`ReadVendorDominantSubCostCode`, ≥3 priors, confidence = top ÷ total).
  Persists `Suggested*` / `Confirmed*` / `WasOverridden` / `SuggestionSource` / `SuggestionReason` /
  `SuggestionConfidence`. Metric endpoint `GET /api/v1/expense-coding/metrics` (auto-clear rate). Probe: 76%
  suggested / 24% flagged. **The `/pm`×`/em` decision of 2026-07-14 was: deterministic backbone, explainable, no
  LLM nondeterminism in the book-of-record write, no auto-apply in v1.** Bills have none of this.
- **The "LLM path with rationale" pattern exists — `StructuredTask`** (`intelligence/cascade/core.py`). Returns
  `{label, confidence, reason}`; a rung is accepted **iff** the deterministic validator passes **and** confidence
  ≥ τ (0.85 in the email pilot); optional cross-rung consensus. Ladder cheapest-first: DeepSeek-V4-Flash →
  claude-haiku-5-5 → gpt-5.4-nano → gpt-5.4-mini → claude-sonnet-5-5 (U-641 · U-642b · U-648). Stage 1 and the LLM path are both
  instances of this, not new integrations.
- **Text extraction is built, tested, and never runs — U-535a (booked).** `AttachmentExtractionService`
  (text-layer-first → DI fallback) and `POST /admin/attachment/extract/tick` exist; the tick is not on the
  scheduler; the DI endpoint is unconfigured and VNet-blocked (95 + 102 of the 200 failures). Live:
  **28,459 NULL · 893 pending · 200 failed · 170 completed of 29,722.** `AIExtractedFields` is 100% NULL.
  **Every text-derived feature and the Stage-1 classifier are behind this unit.**
- **82% of bills have no linked document — U-535b (booked, DESIGN).** `dbo.Attachment` starts 2026-01; link
  coverage by BillDate year is 2022 0.0% · 2023 0.7% · 2024 0.3% · 2025 13.2% · 2026 90.1%. **16,699 of 20,367
  bills** are document-less in the database. The **structured labels (SCC + Project per line) exist for all of
  them.** Text can train on ~3,000 bills today; structured history on ~20,000. This is the strongest argument
  for the prompt's own structured-first instinct.
- **Dormant leftovers of exactly this shape:** `dbo.Attachment.AICategory / AICategoryConfidence /
  AICategoryStatus / AICategoryReasoning / AIExtractedFields / CategorizedDatetime` (all NULL, no caller);
  `shared/classification/models.py::ClassificationType` (BILL_DOCUMENT, BILL_CREDIT_DOCUMENT, EXPENSE, INQUIRY,
  STATEMENT, UNKNOWN); `entities/bill/business/extraction_mapper.py::BillExtractionMapper` (DI → vendor /
  project / SCC, never called); the email-level vocabulary in `intelligence/cascade/email_classification.py`
  (`vendor_invoice`, `vendor_credit_memo`, `vendor_statement`, `vendor_expense_receipt`,
  `contract_labor_timesheet`, …) in a read-only pilot.
- **Embeddings:** `text-embedding-3-small` is provisioned in Foundry (2026-06-30); no vector surface exists.
  No embedding, VECTOR, or nearest-neighbour code anywhere in the repo.
- **No ML runtime.** `requirements*.txt` / `pyproject.toml` carry no numpy, pandas, scikit-learn, lightgbm,
  xgboost, or onnx. A gradient-boosted model is a net-new runtime surface: training job, artifact storage,
  versioning, and a retrain cadence on a Flex-consumption scheduler.
- **Azure SQL is Basic tier (5 DTU, 2 GB), consciously HELD** with re-eval triggers at storage > 65% and DTU
  > 80%/1h; bursts already saturate. 30k × 1536 × 4 B ≈ **184 MB** of vectors plus an exact-scan
  `VECTOR_DISTANCE` per prediction. The tier bump is a prerequisite for the embedding milestone, not a footnote.
- **Review:** `BillService.create` auto-submits a `dbo.Review` row; Approved is terminal. The reviewer's
  confirmation point for a bill-line label is the approval transition. **U-357 (unified status /
  review_status), U-486 (expense coding through draft → review), and U-551 (one transactional
  edit-and-decide command) are in flight and reshape exactly this seam** — a suggestion record must sit beside
  them, not pre-empt them.

---

## 4. Design evaluation — keep / change

**Keep (correct as written):** attachment as the classification unit (matches `dbo.Attachment` grain) · the
current label is never a feature · history features aggregated over *other* records available at prediction
time · temporal ordering · budget check deterministic, not learned · every path ends at a human · the
reviewer's confirmation becomes the next label · do not embed raw OCR · do not embed the email body ·
structured signal before text.

**Change:**

1. **Grain.** Predict per `BillLineItem`, not per bill. Define the target as `(ProjectId, SubCostCodeId)`
   on the line. Folder-intake bills have one summary line; email-intake bills may have several.
2. **Project is predicted, not given.** On email intake nothing supplies it. The U-005 resolver already
   resolves Project from text shorthand and address; the vendor×project history (§5.5) is a second source.
   The budget check is gated on a resolved Project with confidence above the same bar.
3. **Commitment linkage: remove from the feature vector.** There is nothing to link. The signal the prompt
   wants from it — "this vendor on this project goes to this code" — is available *today* as **vendor×project
   history** over `BillLineItem`, which is strictly cheaper than a new entity and needs no commitment data.
4. **Gradient-boosted classifier: not first, and possibly not at all.** With ~20k lines, 475 classes, a long
   tail, and vendor-history as the dominant features, a GBM will mostly learn the vendor's modal code — which
   the deterministic resolver already yields with an *honest* confidence and a human-readable reason. The
   house decision (U-005) was deterministic-first and measure. Add a **backtest gate**: port the resolver,
   replay it over the ~20k historical lines with an as-of date, and let the measured auto-clear rate decide
   whether a learned model earns its new runtime surface. Expected residual after vendor×project history:
   multi-code vendors on one project (lumber yards, Ferguson-class suppliers) — that residual is the LLM
   path's job, and text-separable only once U-535a/U-535b land.
5. **Stage-1 vocabulary: inherit, don't invent.** Adopt the existing email set plus the two missing types:
   `bill · bill_credit · receipt · statement · quote · lien_waiver · timesheet · other`. Note `bill_credit`
   → `BillCredit` (its own entity), `statement` → reconciliation input
   (`reference_vendor_statement_reconciliation`), `timesheet` → ContractLabor — all have owning workflows the
   prompt's five-class list omits. **`lien_waiver` has zero hits in the repo and no workflow**, so "route to
   its own workflow" means route to a human until one exists. Statements are also the multi-invoice-PDF
   source (`feedback_multi_invoice_pdf_per_page_vendor`), so Stage 1 is where that split belongs.
6. **Pre-fill is P0-surface.** Writing `SubCostCodeId` onto a draft `BillLineItem` feeds client draw packets
   (`feedback_invoice_attachment_specificity`). Suggestion-only columns (§5.3) are non-P0 and mirror U-005's
   v1; promote to pre-fill only after the auto-clear metric is read on real volume.
7. **Budget check double-count.** `ActualCost` includes drafts. Subtract the scored bill's own lines before
   computing fit, or every draft bill reads as "already spent."
8. **Email adapter = revival, with the teardown reason in hand first.**
9. **Vectors on Basic tier.** Defer embeddings until (a) the backtest shows a text-separable residual and
   (b) the SQL tier is bumped (Standard S1 is the HOLD memory's named step).

---

## 5. Proposed data model — minimal, on the real DDL

Five new entities were requested. **Two** are needed (5.1 columns, 5.3 table); two reuse dormant columns
(5.2) or existing tables (5.6); one should not be persisted as an entity (5.4); one is deferred (5.5).

### 5.1 Provenance — columns on `dbo.Attachment`, not a new table
```
SourceKind              NVARCHAR(20)  NULL   -- 'upload' | 'email' | 'sharepoint_folder' | 'box' | 'qbo_attachable' | 'agent'
SourceEmailAttachmentId BIGINT        NULL   FK → dbo.EmailAttachment(Id)      -- carries sender/subject/body via EmailMessage
SourceFolderRunItemId   BIGINT        NULL   FK → dbo.BillFolderRunItem(Id)    -- carries Filename, SharePoint ItemId, Result JSON
SourceExpenseFolderRunItemId BIGINT   NULL   FK → dbo.ExpenseFolderRunItem(Id)
SourceExternalId        NVARCHAR(255) NULL   -- Graph attachment id / SharePoint ItemId / Box file id, for idempotent re-ingest
CHECK: at most one Source*Id non-NULL; SourceKind='upload' ⇒ all NULL
```
`CreatedByUserId` / `CreatedDatetime` already give uploader and timestamp. The email body is **not** copied —
it is one join away on `EmailMessage` (`Subject`, `BodyContent`, `FromAddress`, `ReceivedDatetime`), which is
the prompt's "secondary signal via lookup" exactly. **Backfill:** `Category='email_intake'` + the bridged
`Description` → `SourceEmailAttachmentId`; `BillFolderRunItem.Result.bill_public_id` → Bill →
`BillLineItemAttachment` → Attachment → `SourceFolderRunItemId`. Same nullable-one-parent pattern as
`dbo.Review`. Migration under `entities/attachment/sql/migrations/`, never a whole-file re-apply.

### 5.2 Document-type classification — reuse the dormant columns on `dbo.Attachment`
`AICategory` (the §4.5 vocabulary), `AICategoryConfidence DECIMAL(5,4)`, `AICategoryStatus`
(`pending|classified|confirmed|failed`), `AICategoryReasoning NVARCHAR(500)`, `CategorizedDatetime` already
exist and are 100% NULL. Add only:
```
AICategoryRung  NVARCHAR(100) NULL   -- which cascade rung produced it (cost + audit)
```
Human confirmation of the *type* is implicit in routing (a Bill created from it = `bill` confirmed); write it
to `AICategoryStatus='confirmed'` at that point. Retire `shared/classification/models.py::ClassificationType`
into the same vocabulary rather than keeping two enums.

### 5.3 Coding suggestion — one new line-grain table, `dbo.CodingSuggestion`
Generalises `dbo.ExpenseCodingItem`'s suggested/confirmed shape across families **without** absorbing its
QBO write-state (`SyncTokenAtSuggest`, `WrittenAt`, `WriteError`, `Claimed*`), which belongs to the Ramp
recode path and is being re-homed by U-486 anyway.
```
Id, PublicId, RowVersion, CompanyId DF 1, CreatedByUserId DF 17, CreatedDatetime, ModifiedDatetime   -- std
TargetKind              NVARCHAR(20)  NOT NULL  -- 'bill_line' | 'expense_line'
BillLineItemId          BIGINT NULL FK;  ExpenseLineItemId BIGINT NULL FK   -- exactly one non-NULL (CHECK)
AttachmentId            BIGINT NULL FK           -- the document scored, if any
VendorId                BIGINT NULL FK
SuggestedProjectId      BIGINT NULL FK;  SuggestedSubCostCodeId BIGINT NULL FK
SuggestionSource        NVARCHAR(50)  NULL   -- 'vendor_project_history' | 'vendor_history' | 'filename' | 'memo' | 'llm' | 'embedding_nn'
SuggestionReason        NVARCHAR(1024) NULL  -- human-readable, as U-005
SuggestionConfidence    DECIMAL(5,4)  NULL
FeaturesJson            NVARCHAR(MAX) NULL   -- the input snapshot: priors, top/total, margin, budget evidence, rung — THIS is the training record
BudgetCheck             NVARCHAR(20)  NULL   -- 'fits' | 'tight' | 'over' | 'unbudgeted' | 'no_budget' | 'no_project'
BudgetRemaining         DECIMAL(18,2) NULL   -- CostVariance after subtracting the scored bill
Route                   NVARCHAR(10)  NULL   -- 'fast' | 'llm' | 'human'
ModelVersion            NVARCHAR(50)  NULL   -- resolver/sproc version or model artifact id; what "compounds as it runs" is measured against
ConfirmedProjectId      BIGINT NULL;  ConfirmedSubCostCodeId BIGINT NULL
WasOverridden           BIT NULL;  ConfirmedByUserId BIGINT NULL;  ConfirmedAt DATETIME2(3) NULL
UQ (TargetKind, BillLineItemId, ExpenseLineItemId, ModelVersion)   -- one row per line per model version
```
**Confirmation hook (the label loop):** for `bill_line`, when the parent Bill's `dbo.Review` reaches
Approved, read the line's `(ProjectId, SubCostCodeId)` at that instant into `Confirmed*`, set
`WasOverridden`. Implement as a call from the review approval path **after** U-357/U-551 settle where that
path lives — not as a trigger. Until then, a nightly reconciler on `Review` rows is acceptable and honest.

### 5.4 Budget-check result — not an entity
Deterministic and recomputable; persisting it as its own table invites drift. Persist only the two columns in
5.3 (`BudgetCheck`, `BudgetRemaining`) because the **routing decision** and the **training snapshot** need
them. Compute with a pure function over `ReadBudgetVarianceByProjectId(@ProjectId)` for the resolved project
and SCC: `remaining = CostVariance + (this bill's draft lines already counted)`; `fits` if
`amount ≤ remaining × 0.9`, `tight` if `≤ remaining`, `over` otherwise; `unbudgeted` if the SCC has no
approved line; `no_budget` if the project has no active budget; `no_project` if Project unresolved. The
"specific open commitment" tightening in the prompt has no data and is dropped.

### 5.5 Vendor-history sproc — port, with the two things the expense version lacks
`ReadVendorDominantSubCostCode` has no as-of date and no project narrowing. The bill-line port:
```
ReadVendorDominantSubCostCodeForBills(@VendorId, @ProjectId BIGINT = NULL, @AsOfDatetime DATETIME2 = NULL, @ExcludeBillId BIGINT = NULL)
  source: dbo.BillLineItem ⋈ dbo.Bill  WHERE Bill.IsDraft = 0 AND BillLineItem.IsDraft = 0
          AND SubCostCodeId IS NOT NULL AND (@ProjectId IS NULL OR ProjectId = @ProjectId)
          AND (@AsOfDatetime IS NULL OR Bill.BillDate < @AsOfDatetime) AND Bill.Id <> ISNULL(@ExcludeBillId, -1)
  returns: SubCostCodeId, TopCount, SecondCount, TotalCount   -- confidence = Top/Total; margin = (Top−Second)/Total
```
`@AsOfDatetime` is what makes the backtest honest (no future leakage); `@ProjectId` is the commitment-shaped
signal without a commitment entity; `@ExcludeBillId` keeps the scored bill out of its own history. Resolution
order: vendor×project (≥3 priors) → vendor (≥3 priors) → flag.

### 5.6 Raw-document record — not a new entity
`dbo.Attachment` **is** the normalized raw-document record. `EmailMessage`/`EmailAttachment` and
`BillFolderRunItem` are the adapter-side raw stores, and 5.1 is the pointer between them. The prompt's
two-level "raw document + attachment" split maps onto what exists.

### 5.7 Embeddings (deferred milestone) — separate narrow tables, never on the hot row
```
dbo.AttachmentEmbedding     (AttachmentId FK, Model NVARCHAR(100), TextHash NVARCHAR(64), Vector VECTOR(1536), CreatedDatetime)
dbo.BudgetLineItemEmbedding (BudgetLineItemId FK, Model, TextHash, Vector VECTOR(1536), CreatedDatetime)
```
Separate tables so the 6 KB vector never widens `Attachment`, re-embedding is a `TextHash` miss, and both
drop cleanly if AI Search replaces them. Prerequisite: SQL tier bump.

---

## 6. Milestones — ordered by return per effort, with the dependency that actually gates each

| # | Unit | Depends on | Tier | Effort | Return |
|---|---|---|---|---|---|
| M0 | **U-535a** — configure DI endpoint + VNet, wire `extract/tick` on the `process_bill_folder` timer shape, reset the 200 failed, backfill 28,459 set-based | — | non-P0 | S (mostly config + a timer) | Unblocks *everything* text-based; also the search index the board already wants |
| M1 | **Bill-line coding suggestion, suggestion-only** — 5.3 table, 5.5 sproc, resolver port (vendor×project → vendor → filename/`FindSubCostCodeForReply`), **backtest harness** replaying ~20k historical lines with `@AsOfDatetime` and a temporal split, reporting auto-clear / override / flag by confidence bucket | nothing (structured labels exist for all 20k bills) | non-P0 | M | The number that decides M4-vs-GBM; immediate reviewer value on folder-intake bills |
| M2 | **Budget cross-check + routing** — pure function over the variance sproc (5.4), `Route` enum, scored-bill exclusion | M1 | non-P0 | S | Conflict detection; the "unbudgeted code" flag the owner has asked for in other forms (U-632 suspense coding) |
| M3 | **Stage-1 attachment-type classifier** — a `StructuredTask` on the cascade over the first ~4k chars of extracted text + filename, §4.5 vocabulary, writes 5.2 | M0 | non-P0 | S–M | Gate for M1 on email/Box intake; splits statements and multi-invoice PDFs before they become wrong bills |
| M4 | **LLM path** — a `StructuredTask` given bill text + M1 history + M2 budget evidence; validator = SCC ∈ project's approved budget set *or* explicit `unbudgeted`; writes 5.3 with `Source='llm'` and the rationale | M1, M2, M0 | non-P0 (suggestion-only) | M | Covers the multi-code-vendor residual M1's backtest exposes |
| M5 | **Pre-fill on fast path** — write `SubCostCodeId`/`ProjectId` onto the draft line when `Route='fast'` | M1–M4 + a read auto-clear metric on real volume | **P0-surface** (feeds draw packets) ⇒ Pass 3 | S | The actual time saving; held until the metric earns it, as U-005 v1 was |
| M6 | **Provenance columns + backfill** (5.1) | — (can run any time) | non-P0 | S | Required before email revival; nice-to-have for folder intake |
| M7 | **Email intake revival** | the U-218f teardown rationale; M6; M3 | P0-surface if it creates Bills (`SourceEmailMessageId`, review submit) | M | The second adapter — only if the original reason for tearing it down is addressed |
| M8 | **Embeddings** (5.7) + nearest-neighbour feature | M0, **U-535b** (else ~3k docs), SQL tier bump, and a backtest residual that looks text-separable | non-P0 | M–L | Unknown until M1 reports; may be zero |
| — | **Learned model (GBM)** | M1 backtest showing a gap the resolver + M4 cannot close | — | L (new runtime surface) | Not scheduled |
| — | **Commitment / Contract entity** | product decision | — | — | Out of scope here; on the roadmap as a draw-package dependency |

M1 is the first unit that produces evidence; M0 is the first that unblocks. Run M0 and M1 in parallel — they
touch disjoint files (scheduler + `entities/attachment` vs `entities/coding_suggestion` + `entities/bill`).

---

## 7. Decisions required from Chris (each with a recommendation)

| # | Decision | Recommendation |
|---|---|---|
| D1 | Label grain: per `BillLineItem` `(Project, SCC)`, or the bill's dominant code? | **Line.** It is the ledger truth and what the draw package pivots on. |
| D2 | Is Project an input or a prediction? | **Prediction** with its own confidence; the budget check is gated on it. Folder intake supplies it; email intake does not. |
| D3 | Revive the email intake torn down 2026-08-16? | **Not until the teardown rationale is recovered** (it is not in the U-218f session note). Then M7, after M3 + M6. |
| D4 | Commitments: drop the feature, or book a Contract/commitment entity first? | **Drop from this design.** Vendor×project history (5.5) gives the same signal from data that exists. The entity is a product unit on its own merits. |
| D5 | Accept a **backtest gate** before any learned model? | **Yes.** M1's harness is the deliverable that decides; no GBM is scheduled. |
| D6 | Suggestion-only (non-P0) first, or pre-fill (P0) from day one? | **Suggestion-only**, exactly as U-005 v1; promote (M5) on a read metric. |
| D7 | Accept the SQL tier bump as a prerequisite for embeddings, or defer embeddings? | **Defer** until M1 reports; bump only if M8 is scheduled. |
| D8 | Should `CodingSuggestion` sit beside `ExpenseCodingItem` (two tables) or replace it? | **Beside.** `ExpenseCodingItem` carries QBO write-state U-486 is re-homing; folding now entangles two in-flight designs (U-357, U-551). Revisit once U-486 lands. |
| D9 | Stage-1 vocabulary = §4.5 (8 labels), with `lien_waiver` routing to a human? | **Yes**; a lien-waiver workflow is a separate product ask. |

---

## 8. One measurement this evaluation could not take

A read-only prod aggregate that bounds the deterministic ceiling *before* M1 is built was prepared and
**blocked by the auto-mode permission classifier (production reads)**; it was not re-attempted. It measures,
over completed bill lines with a vendor and an SCC: lines whose vendor's modal SCC matches (in-sample
ceiling), the same at vendor×project grain, line-weighted consistency buckets (≥90 / 70–90 / 50–70 / <50),
distinct SCCs in use, bills by `IntakeSource`, and lines-per-bill. Expected to show vendor×project
consistency materially above vendor-only — the quantitative case for D4. It is SELECT-only; run it on
approval, or let M1's harness supply the honest temporal version.

---

## 9. Unit shape and gates

**Design-gated** on every criterion in `feedback_two_phase_dispatch_design_gated`: new schema used by
multiple families (5.1, 5.3), and an architecture decision (deterministic-first with a backtest gate; vectors
deferred). Therefore **two-phase**:

- **Phase 1 — DESIGN unit = U-639** (this document). Deliverable: this file, revised against D1–D9,
  plus the M1 backtest *spec* (metrics, temporal split, confidence buckets, acceptance threshold for M5).
  Claimed through the allocator in `BOARD.md` § Conventions (`OWNED U-639`, 2026-10-08).
- **Phase 2 — build units**, each its own row, branch, and worktree, in the §6 order: M0 (already U-535a),
  M1, M2, M3, M4, M6; M5 and M7 after their stated gates; M8 only if M1's residual justifies it.

⛔ Gate 1 has not been crossed: nothing in the repo was modified except this file. Committed by explicit pathspec
on Chris's approval, 2026-10-08; the `BOARD.md` row is U-639 in Ready / queued.
