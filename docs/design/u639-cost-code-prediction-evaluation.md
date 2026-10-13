# U-639 — Cost-code prediction for bill lines: evaluation of the 2026-10-08 design prompt

**Status:** DESIGN unit U-639 (Phase 1), claimed 2026-10-08. Nothing built. Terminology confirmed with Chris
2026-10-11 (below). **All decisions D0–D9 made with Chris on 2026-10-11 (§7); M1 backtest spec written (§10). Phase 1 complete.**
**Author:** `/em`, 2026-10-08. Every "exists / does not exist" claim below was checked against `build.one.api` on
this date; the live counts are quoted from board rows U-535a / U-535b (measured 2026-09-24) and are perishable.
**Input:** Chris's dictated prompt for an "automated cost-code prediction feature" (two intake adapters → Stage 1
document-type gate → Stage 2 cost-code classifier → deterministic budget cross-check → routing → human review).
**Terminology (confirmed with Chris, 2026-10-11).** The prompt uses generic construction-finance vocabulary;
this document uses the build.one names:

| Prompt says | build.one term | Note |
|---|---|---|
| vendor invoice | **Bill** (`dbo.Bill`, lines in `dbo.BillLineItem`) | Always "Bill". An invoice from a vendor is a Bill. |
| invoice (to the customer) | **Invoice** | The customer draw (AR). |
| cost code (the label) | **SubCostCode** (SCC) | Lines code to an SCC; `CostCode` is its parent group. |
| raw-document / attachment record | **Attachment** | One table for every uploaded file; join tables link it to Bill/Expense/Invoice lines. |
| review workflow | **Review** + **ReviewStatus** | Drives the inbox tasks. |
| receipt | **Expense** | Card receipts (Ramp) take the expense flow, not the Bill flow. |
| change order | **BudgetRevision** with `Type='change_order'` | Not a separate entity: a Budget is an original revision plus change-order revisions. |
| budget line item | **BudgetLineItem** | Carries **exactly one** SubCostCode (nullable) — not "one or more". |
| commitment (PO / subcontract) | **none yet — future name: Commitment** | Not built; left out of this design (D4). If built, the entity is named Commitment with a type of subcontract or purchase order. `dbo.Contract` is the *client* contract and holds only the builder's-fee rate. |
| quote / estimate, lien waiver | **none** | No entity; as document types they can only route to a human (D9). |

**Intake (Chris, 2026-10-11):** SharePoint bill-folder intake **will be removed**; Box is **not** an intake and
will not become one; email intake was torn down 2026-08-16. So once the folder intake goes, **no automated
path creates Bills from documents.** That makes D0 (§7) the first decision, ahead of D1–D9.

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
| The SharePoint/Box adapter is new work | ⚠️ **To be removed (Chris, 2026-10-11); Box is not an intake.** As of 2026-10-08: **SharePoint bill-folder intake is LIVE** (`process_bill_folder`, 1-min timer → `/admin/bill-folder/tick`). The filename convention `{Project} - {Vendor} - {BillNumber} - {Desc} - {SubCostCode} - {Rate} - {BillDate}.pdf` **is the classifier today — a human types the code.** Box inbound = vendor-compliance docs only; there is no Box → Bill path. | The folder adapter exists. The prompt's "platform metadata" is already captured in `dbo.BillFolderRunItem` (Filename, SharePoint ItemId, Result JSON). |
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

1. **Grain — per Bill (D1, Chris 2026-10-11).** Predict one `(ProjectId, SubCostCodeId)` per Bill; the
   label is the Bill's **dominant** SubCostCode (the one carrying the most line amount). Accepting the
   suggestion applies it to every line; individual lines stay editable. Only 3.76% of coded Bills mix codes
   (2.27% of dollars off the dominant code — §8). The original per-line recommendation is superseded: Folder-intake bills have one summary line; email-intake bills may have several. (Both intakes are
   gone or going — see D0 — but the ~20k historical lines remain the labelled backtest set.)
2. **Project: input when chosen, predicted from the Bill's text when blank (D2, Chris 2026-10-11).** Superseded wording: on email intake nothing supplies it. The U-005 resolver already
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
SourceKind              NVARCHAR(20)  NULL   -- 'upload' | 'email' | 'sharepoint_folder' (historical backfill only) | 'qbo_attachable' | 'agent'
SourceEmailAttachmentId BIGINT        NULL   FK → dbo.EmailAttachment(Id)      -- carries sender/subject/body via EmailMessage
SourceFolderRunItemId   BIGINT        NULL   FK → dbo.BillFolderRunItem(Id)    -- carries Filename, SharePoint ItemId, Result JSON
SourceExpenseFolderRunItemId BIGINT   NULL   FK → dbo.ExpenseFolderRunItem(Id)
SourceExternalId        NVARCHAR(255) NULL   -- Graph attachment id / SharePoint ItemId, for idempotent re-ingest
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

### 5.3 Coding suggestion — `dbo.ExpenseCodingItem` repurposed and renamed to `dbo.CodingSuggestion` (D8): Bill, Expense, BillCredit
Generalises `dbo.ExpenseCodingItem`'s suggested/confirmed shape across families **without** absorbing its
QBO write-state (`SyncTokenAtSuggest`, `WrittenAt`, `WriteError`, `Claimed*`), which belongs to the Ramp
recode path and is being re-homed by U-486 anyway.
```
Id, PublicId, RowVersion, CompanyId DF 1, CreatedByUserId DF 17, CreatedDatetime, ModifiedDatetime   -- std
TargetKind              NVARCHAR(20)  NOT NULL  -- 'bill' | 'bill_credit' | 'expense' (all document grain)
BillId                  BIGINT NULL FK;  BillCreditId BIGINT NULL FK;  ExpenseId BIGINT NULL FK   -- exactly one non-NULL (CHECK)
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
ConfirmedMixedCodes     BIT NULL             -- the approved Bill's lines carried >1 SCC (the per-Bill grain's known miss)
PrefillState            NVARCHAR(12)  NULL   -- 'prefilled' | 'accepted' | 'changed' | 'untouched' (D6: an untouched pre-fill is not a confirmation)
UQ (TargetKind, BillId, BillCreditId, ExpenseId, ModelVersion)   -- one row per document per model version
```
**Confirmation hook (the label loop):** for `bill`, when the Bill's `dbo.Review` reaches Approved, read its
dominant `(ProjectId, SubCostCodeId)` (largest line amount) at that instant into `Confirmed*`, set
`WasOverridden` and `ConfirmedMixedCodes`. Implement as a call from the review approval path **after** U-357/U-551 settle where that
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
`ReadVendorDominantSubCostCode` (expense lines) has no as-of date and counts line-grain votes. The bill port
(U-653a, Chris 2026-10-13) uses **Bill-grain dominant votes** and **`Bill.CreatedDatetime`** as the clock
(§10.2), not `BillDate`:
```
ReadVendorDominantSubCostCodeForBills(@VendorId, @ProjectId BIGINT = NULL, @AsOfDatetime DATETIME2(3) = NULL, @ExcludeBillId BIGINT = NULL)
  source: completed Bills (Bill.IsDraft = 0) for @VendorId with CreatedDatetime < @AsOfDatetime (strict) and Id <> @ExcludeBillId
  per Bill: dominant SubCostCode = largest summed ABS(Amount) across lines (NULL SCC ignored); tie → lowest line Id;
            dominant Project = ProjectId on the lowest-Id line carrying that SCC
  when @ProjectId is set: keep only Bills whose dominant Project = @ProjectId
  each prior Bill votes once with its dominant SubCostCode; minimum support TotalCount >= 3 distinct Bills
  returns: TopSubCostCodeId, TopCount, SecondCount, TotalCount, Number, Name — or no row when TotalCount < 3
ReadVendorDominantProjectForBills(@VendorId, @AsOfDatetime, @ExcludeBillId) — same population/rule; votes dominant ProjectId (Mode B)
```
`@AsOfDatetime` + `@ExcludeBillId` keep the backtest honest (§10.2); `@ProjectId` is the vendor×project
signal without a commitment entity. Resolution order in Python: vendor×project (≥3 priors) → vendor (≥3 priors) → flag.

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
| M1a | **Repurpose `ExpenseCodingItem` → `CodingSuggestion` (D8)** — table + entity rename; targets Bill, BillCredit and Expense at document grain; carry or relocate the QBO write-tracking columns; expense suggestions move from line to Expense while the QBO recode stays per line; web expense-coding pages follow | — | **P0-surface** (the only app→QBO expense write) ⇒ Pass 3 | M | The one table M1 writes to; must land before M1 |
| M1 | **Bill coding pre-fill, per Bill, in the web Bill edit form (D0, D1, D6)** — 5.3 table, 5.5 sproc, resolver port (vendor×project → vendor → `FindSubCostCodeForReply`; the filename-convention source dies with the folder intake), **backtest harness** replaying ~20k historical lines with `@AsOfDatetime` and a temporal split, reporting auto-clear / override / flag by confidence bucket | M1a (the table); structured labels exist for all ~19k Bills | **P0-surface** (D6) ⇒ Pass 3 | M | The number that decides M4-vs-GBM; reviewer value on whichever intake D0 picks |
| M2 | **Budget cross-check + routing** — pure function over the variance sproc (5.4), `Route` enum, scored-bill exclusion | M1 | non-P0 | S | Conflict detection; the "unbudgeted code" flag the owner has asked for in other forms (U-632 suspense coding) |
| M3 | ~~**Stage-1 attachment-type classifier**~~ **Off the critical path (D0):** the person entering the Bill decides the document type. Kept for a future automated intake. Was: — a `StructuredTask` on the cascade over the first ~4k chars of extracted text + filename, §4.5 vocabulary, writes 5.2 | M0 | non-P0 | S–M | Gate for M1 on document intake (D0); splits statements and multi-invoice PDFs before they become wrong bills |
| M4 | **LLM path** — a `StructuredTask` given bill text + M1 history + M2 budget evidence; validator = SCC ∈ project's approved budget set *or* explicit `unbudgeted`; writes 5.3 with `Source='llm'` and the rationale | M1, M2, M0 | non-P0 (suggestion-only) | M | Covers the multi-code-vendor residual M1's backtest exposes |
| M5 | ~~**Pre-fill on fast path**~~ **Folded into M1 (D6, Chris 2026-10-11): pre-fill from day one,** per Bill (D1), applied to every line, marked *suggested* until touched, untouched pre-fills recorded as unreviewed | M1 | **P0-surface** (feeds draw packets) ⇒ Pass 3 | — | — |
| M6 | **Provenance columns + backfill** (5.1) | — (can run any time) | non-P0 | S | Required before any document intake that D0 picks |
| M7 | **Email intake revival** — **off the critical path (D0)**; | the U-218f teardown rationale; M6; M3 | P0-surface if it creates Bills (`SourceEmailMessageId`, review submit) | M | The second adapter — only if the original reason for tearing it down is addressed |
| M8 | **Embeddings** (5.7) + nearest-neighbour feature | M0, **U-535b** (else ~3k docs), SQL tier bump, and a backtest residual that looks text-separable | non-P0 | M–L | Unknown until M1 reports; may be zero |
| — | **Learned model (GBM)** | M1 backtest showing a gap the resolver + M4 cannot close | — | L (new runtime surface) | Not scheduled |
| — | **Commitment / Contract entity** | product decision | — | — | Out of scope here; on the roadmap as a draw-package dependency |

M1 is the first unit that produces evidence; M0 is the first that unblocks. Run M0 and M1 in parallel — they
touch disjoint files (scheduler + `entities/attachment` vs `entities/coding_suggestion` + `entities/bill`).

---

## 7. Decisions required from Chris (each with a recommendation)

| # | Decision | Recommendation |
|---|---|---|
| D0 | **Which path brings Bills into build.one once SharePoint folder intake is removed?** | ✅ **Decided (Chris, 2026-10-11): manual entry in the web app is the intake** (§8: person-entered Bills dominate since July; folder and email intake are already near zero). The predictor becomes a **suggestion in the Bill edit form** as each line is coded, not a pipeline that creates Bills. Consequences: Stage 1 (M3) and email revival (M7) leave the critical path; provenance (M6) is optional. |
| D1 | Label grain: per `BillLineItem` `(Project, SCC)`, or the bill's dominant code? | ✅ **Decided (Chris, 2026-10-11): per Bill** — one suggestion per Bill, label = the dominant SCC by line amount, applied to every line on accept. Measured: 3.76% of coded Bills mix codes, 2.27% of dollars off the dominant code. The backtest reports mixed-code Bills separately. |
| D2 | Is Project an input or a prediction? | ✅ **Decided (Chris, 2026-10-11): input when the user has picked it, prediction when blank.** A chosen project is used as-is and never overridden (vendor×project history is 11 points stronger than vendor-only, §8); when blank, the project is predicted — **chiefly from the Bill's text** (job name / address on the document, the U-005 shorthand + address resolver), with vendor×project history as a second source — and suggested once per Bill (D1). A wrong project guess is corrected by the user picking the right one; that correction is a label (`Confirmed*`, `WasOverridden`). The budget check runs only against a chosen or confidently predicted project. |
| D3 | Revive the email intake torn down 2026-08-16? | ✅ **Decided (Chris, 2026-10-11): not now; reconsider later** as its own decision, once the teardown rationale is recovered (it is not in the U-218f session note). D0 makes it unnecessary for this design; nothing here blocks adding it later — the per-Bill suggestion works the same on an email-created Bill. M7 stays off the critical path. |
| D4 | Commitments: drop the feature, or book a Contract/commitment entity first? | ✅ **Decided (Chris, 2026-10-11): leave out of this design for now.** The predictor runs on vendor×project history + the Bill's text; a future vendor-commitment entity would become one more input to the suggestion and the budget check with no redesign. Whether to build one is a separate product decision. **Name decided (Chris, 2026-10-11): Commitment**, with a type of subcontract or purchase order; not "Contract", which is the existing *client* contract (`dbo.Contract`, builder's-fee rate only). |
| D5 | Accept a **backtest gate** before any learned model? | ✅ **Decided (Chris, 2026-10-11): yes.** Build the history-based and LLM suggesters first, backtest them, and build a trained model only if the backtest shows a gap they cannot close. The harness is part of M1. **Two tiers (measured 2026-10-11):** history-based suggestions are backtested on all **19,224** labelled Bills (no documents needed); text-based suggestions only on the **3,706** with a linked PDF (2026: 3,087 of 3,170 = 97%; 2025: 588; 2022–2024: 31), and only after M0 extracts their text (117 have text today; attachment extraction status: 28,507 NULL · 1,051 pending · 200 failed · 170 completed). Older years join the text tier only if U-535b recovers their PDFs. **Chris (2026-10-11): they all exist** — every Project has a full archive of Bill, Expense, BillCredit and Invoice PDFs across Box, SharePoint and email, so U-535b is a *recover-and-link* job (match each archived PDF to its Bill), not a search for missing files. |
| D6 | Suggestion-only (non-P0) first, or pre-fill (P0) from day one? | ✅ **Decided (Chris, 2026-10-11): pre-fill from day one;** the user changes a wrong value. Persist both the predicted and the finalized value as the learning signal (already the 5.3 shape: `Suggested*`, `Confirmed*`, `WasOverridden`). **Guards (EM, recorded with the decision):** (1) the Bill edit form auto-saves ~300 ms after any change, so a pre-filled value is persisted before the user looks — the field is shown as *suggested* with its confidence until touched or accepted; (2) a pre-fill never touched is recorded as **unreviewed** (`ConfirmedByUserId` NULL / a `PrefillState`), so it is not counted as a confirmation in the label loop or the acceptance rate; (3) the unit is **P0-surface** (it writes coding that flows into draw packages) ⇒ Pass 3 security review. M5 folds into M1. |
| D7 | Accept the SQL tier bump as a prerequisite for embeddings, or defer embeddings? | ✅ **Decided (Chris, 2026-10-11): defer embeddings until after the backtest.** Build M0, M1 and M4 and run the D5 backtest first; add embeddings (M8) and the SQL tier bump only if the residual errors look like cases where similar past text would have helped. |
| D8 | Should `CodingSuggestion` sit beside `ExpenseCodingItem` (two tables) or replace it? | ✅ **Decided (Chris, 2026-10-11): replace — repurpose `ExpenseCodingItem` into the one coding-suggestion table for Bills and expenses, with a table and entity rename.** U-486's phases have shipped (expense coding now runs through draft → review), so the main entanglement is gone; but the table still carries the QBO write-tracking columns (`QboPurchaseId`, `SyncTokenAtSuggest`, `WrittenAt`, `WriteError`, `Claimed*`) and backs the only sanctioned app→QBO expense write (the double-gated recode), so the rename/repurpose unit is **P0-surface** and must carry or deliberately relocate those columns. Footprint on trunk: `entities/expense_coding_item` (8 files), `integrations/intuit` (7), migrations (3), `entities/expense` (3), 6 tests; web: 6 files under the expense-coding pages. **Name decided (Chris, 2026-10-11): `CodingSuggestion`**, covering **Bill, Expense and BillCredit**, all at **document grain** (one suggestion per Bill / BillCredit / Expense, applied to every line — Chris, 2026-10-11). For expenses this moves the suggestion from line to Expense; the QBO recode stays a per-line surgical write, so an accepted per-Expense code becomes one recode per line, each still under the double gate and SyncToken fail-closed rule. |
| D9 | Stage-1 vocabulary = §4.5 (8 labels), with `lien_waiver` routing to a human? | ✅ **Decided (Chris, 2026-10-11): moot for now** — D0 removes the document-type gate because the person entering the document decides its type; the §4.5 labels stay recorded for any future automated intake. **Kept for later:** a light check in the form that reads an uploaded PDF and warns when it does not look like the document being entered (e.g. a vendor statement holding several invoices), once M0 text extraction runs. |

---

## 8. Measurement (run 2026-10-11 on Chris's approval, read-only)

**How Bills arrive.** Origin per Bill: `IntakeSource='bill_folder'` → SharePoint folder; `SourceEmailMessageId`
set → email; QBO staging row created no later than the dbo row (+5s) → QBO pull; creator `IsAgent` → agent;
otherwise a person in build.one. Last 12 months: QBO pull 18,277 (mostly older history), person 1,903,
SharePoint folder 124, agent 106, email 102. Last six months by month:

| Month | Person in build.one | QBO pull | Agent | Email | SharePoint folder |
|---|---|---|---|---|---|
| 2026-04 | 278 | 244 | | 7 | |
| 2026-05 | | 87 | | 65 | 124 |
| 2026-06 | 1 | 514 | | 11 | |
| 2026-07 | 207 | 195 | 62 | | |
| 2026-08 | 478 | 173 | | 13 | |
| 2026-09 | 245 | 60 | 38 | | |
| 2026-10 (to the 11th) | 41 | 4 | 6 | | |

Person-entered Bills are almost all Chris's (1,250 in six months); SharePoint folder and email intake are
already near zero; the QBO-pull share is falling. Lines per Bill ≈ 1.1–1.3 for every origin.

**Deterministic ceiling (in-sample, so optimistic).** Over 22,610 coded lines (503 vendors, 326 SCCs in use):
the vendor's modal SCC matches **54.0%**; the modal SCC at vendor×project grain matches **65.1%**. Line-weighted
vendor consistency: ≥90% → 7.0% of lines; 70–90% → 15.8%; 50–70% → 28.2%; <50% → **48.9%**.

**What it means.** Vendor history alone cannot carry the prediction: half of all coded lines come from vendors
who use their own most common code less than half the time. The line's text (description, extracted
document text) has to carry the rest, which raises the priority of M0 (extraction) and M4 (LLM path). M1's
temporal backtest will give the honest (lower) number. **Mixed-code Bills (D1 follow-up):** of 19,224 completed coded Bills, 1,018 have more than one line and 722
(3.76%) carry more than one SCC; 2.27% of dollars sit off a Bill's dominant SCC. Vendor×project beats vendor-only by 11 points, which
supports keeping project in the signal (D2, D4). Query: kept with the EM's session records; SELECT-only.

---

## 9. Unit shape and gates

**Design-gated** on every criterion in `feedback_two_phase_dispatch_design_gated`: new schema used by
multiple families (5.1, 5.3), and an architecture decision (deterministic-first with a backtest gate; vectors
deferred). Therefore **two-phase**:

- **Phase 1 — DESIGN unit = U-639** (this document). Deliverable: this file, revised against D1–D9,
  plus the M1 backtest *spec* — **done: §10** (decision bars proposed, confirmed at M1's Gate 1).
  Claimed through the allocator in `BOARD.md` § Conventions (`OWNED U-639`, 2026-10-08).
- **Phase 2 — build units**, each its own row, branch, and worktree, in the §6 order: M0 (already U-535a),
  M1, M2, M3, M4, M6; M5 and M7 after their stated gates; M8 only if M1's residual justifies it.

⛔ Gate 1 has not been crossed: nothing in the repo was modified except this file. Committed by explicit pathspec
on Chris's approval, 2026-10-08; the `BOARD.md` row is U-639 in Ready / queued.

---

## 10. M1 backtest specification (D5)

**Purpose.** Give an honest, reproducible accuracy figure for the suggesters before anything learned is built,
and turn it into four decisions: whether the LLM path (M4) earns its cost, whether a trained model is ever
needed (D5), whether embeddings are worth the SQL tier bump (D7), and what confidence the pre-fill shows (D6).

### 10.1 What is scored
- **Population (v1):** completed Bills (`IsDraft = 0`) with at least one coded line (19,224 on 2026-10-11).
  BillCredits and Expenses join in a second run once `CodingSuggestion` covers them (M1a); same method.
- **Label (D1):** the Bill's **dominant SubCostCode** — the SCC carrying the largest summed `ABS(Amount)` across
  its lines (ties: the lowest line `Id`). **Project label (D2):** the `ProjectId` of that dominant-amount line.
- **Mixed-code Bills** (more than one SCC; 3.76%) are scored on the dominant label **and** reported separately,
  so the per-Bill grain's known miss is visible, never hidden in the average.

### 10.2 Time and leakage
- **The clock is `Bill.CreatedDatetime`** — when the Bill entered build.one, i.e. what was knowable when a
  person would have seen the pre-fill. Not `BillDate` (vendor's date) and not completion time.
  Bill list/read sprocs stringify `CreatedDatetime` to whole seconds (`CONVERT(VARCHAR(19), …, 120)`); the
  backtest harness must read the raw `DATETIME2(3)` column (or an sproc that returns it) so millisecond
  ordering matches SQL Server's `< @AsOfDatetime` cutoff.
- **Rolling origin, no training step:** every scored Bill is predicted using **only Bills created strictly
  before it**, with itself excluded (`@AsOfDatetime`, `@ExcludeBillId` on the §5.5 sproc). Nothing about the
  scored Bill — its own lines, codes, review outcome — reaches its features.
- **Windows:**
  - **Calibration window:** Bills created 2025-10-11 to 2026-04-10 — used only to fit the confidence mapping.
  - **Evaluation window:** Bills created 2026-04-11 to 2026-10-10 — every reported number comes from here.
  - History before 2025-10-11 is input only.
- **Known residual leak, stated not fixed:** stored codes are the values *as they are now*, not as they were
  at the time; a later recode of an older Bill leaks forward. Small (Bills are rarely re-coded after
  completion) and noted in every report.

### 10.3 Modes and suggesters
- **Mode A — project chosen (D2):** the true project is given; predict the SCC.
- **Mode B — project blank (D2):** predict project and SCC; report project accuracy separately.
- **History suggester (M1), in resolution order:** vendor×project modal SCC → vendor modal SCC → none.
  Minimum support: 3 prior Bills at that grain, else fall through. Raw score = smoothed modal share
  `top / (total + 2)`; mapped to a calibrated confidence on the calibration window.
- **Text suggester (M4):** text tier only (Bills whose PDF has extracted text — needs M0). Run on a **stratified
  sample of 500** evaluation Bills (by vendor-consistency bucket and origin) to bound LLM spend; same prompt,
  model and rung the production path will use, recorded in `ModelVersion`.
- **Combined:** the production rule — history when its calibrated confidence clears the line, text otherwise.

### 10.4 Metrics (evaluation window, per mode, per suggester)
- **Coverage:** share of Bills that get any suggestion.
- **Top-1 accuracy**, Bill-count-weighted **and dollar-weighted**; **top-3** (alternatives the form may show).
- **Calibration:** accuracy per confidence decile against the stated confidence (a reliability table).
- **Breakdowns:** by vendor-consistency bucket (§8), by origin (§8), cold-start vendors (< 3 prior Bills),
  mixed-code Bills, and the 20 vendors with the most errors.
- **Cost:** LLM tokens and dollars per 1,000 Bills for the text suggester.

### 10.5 Decision rules (numbers are **proposed** — confirm at M1's Gate 1)
| Question | Rule |
|---|---|
| Does M4 earn its cost? | Build/keep M4 if, on the text tier, combined top-1 beats history-only by **≥ 10 points** in Mode A. |
| Trained model (D5)? | Only if combined top-1 in Mode A is below **85%** **and** an error review of 50 misses shows they are not text-reachable (i.e. structured features the suggesters don't use would fix them). |
| Embeddings (D7)? | Only if ≥ **half** of 50 reviewed text-tier misses would be fixed by "a similar past Bill was coded to X". |
| Pre-fill confidence shown (D6)? | Show the calibrated confidence; pre-fill always applies (D6), but values below **60%** calibrated confidence are styled as low-confidence. |
| Live check after launch | Weekly from `CodingSuggestion.PrefillState`: if the live change rate exceeds the backtest error rate by **> 10 points for two weeks**, re-run the backtest and review. |

### 10.6 How it runs
- **Read-only, SELECT-only** against production, run on approval as the §8 measurement was; or against a
  restored copy. It **never writes** `CodingSuggestion` rows; results go to a report file.
- **Output per run:** a markdown report (the tables above) plus a CSV of per-Bill predictions, stamped with
  the date, window boundaries and `ModelVersion`, kept under `docs/design/` beside this file.
- **Reproducible:** the same windows and versions produce the same numbers; the harness is code in M1, reviewed
  like any unit (P0-surface with M1, D6).
