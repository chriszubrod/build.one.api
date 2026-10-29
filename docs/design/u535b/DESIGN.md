# U-535b — Backfill the Bill → PDF link for historical bills (DESIGN)

Status: design phase complete 2026-10-29; awaiting Gate 1b (build plan).
Tier: **P0-surface** — writes `BillLineItemAttachment`, which feeds client draw packets. All three
review passes apply to every build phase.

Owner decisions (Chris, 2026-10-29):

- **Accuracy is paramount. Every link is approved individually by Chris before it is written.**
  Automated checks only rank and highlight; they never write a link on their own.
- Backfilled links are **visible** to packets, search and coding like any other link, and are
  **tagged** with a backfill run id so they can be found and removed.
- A match rule is eligible only at **≥ 99 % precision** (95 % Wilson lower bound) on the answer key.
- The review queue is a **system-admin page in build.one.web**.

## 1. The gap (measured 2026-10-29, non-draft bills)

| BillDate year | bills | with a linked PDF |
|---|---|---|
| 2022 | 2,864 | 0 |
| 2023 | 4,709 | 32 |
| 2024 | 4,914 | 14 |
| 2025 | 4,521 | 595 |
| 2026 | 3,632 | 3,282 |

About 17,000 bills have no document in the database.

## 2. Sources

| source | finding | role |
|---|---|---|
| **Box project folders** (`General / 200 - Rogers Build Projects / <project> / 14 - Invoices…`) | 107 project folders, **25,005 PDFs** (3.85 GB, median 65 KB); 89 folders map to a `dbo.Project` by the abbreviation prefix of `Project.Name` (+ street number to break ties); **86 %** of unlinked bills sit on a mapped project | **primary source** |
| QBO Attachables | 22,088 attachables; 3,850 bills have one, almost all 2025–26 and already linked; **337 unlinked bills** recoverable (291 from 2023) | secondary, exact identity (QBO asserts the bill) — same review queue |
| SharePoint `Bills` folders | Box mirrors SharePoint 1:1 in count; SharePoint renames to the convention where Box keeps raw names | later phase, for the 1,368 Box PDFs (5 %) whose names do not parse |
| Email archive (`dbo.EmailMessage`) | 2026 only | not a source for historical bills |

Box is the source of record: one listable tree, already integrated (CCG as-user auth), complete
back to 2020.

## 3. Matching

Filenames follow `PROJ - Vendor - Inv# - Description - CostCode - $Amount - M-D-YYYY.pdf`
(86 % split into exactly seven parts; 87.5 % of PDFs parse). Parse right to left (date, amount
including `($x)` credits, optional cost code), then project, vendor and invoice number from the left.
Files with our own Box-push suffix (`-xxxxxxxx` before `.pdf`) are excluded: they are already linked.

Tiers (lower = stronger), compared on **normalized** invoice numbers (alphanumerics, upper case,
leading zeros stripped) — never raw names:

| tier | key |
|---|---|
| A | invoice # + amount (absolute, exact) + project |
| B | invoice # + amount + vendor (name similarity ≥ 0.6) |
| C | invoice # + project + vendor |
| D | amount + project + vendor + date within 3 days |

A candidate must be **mutually unique**: one bill at the file's best tier, and that bill claimed by
no other file at that tier.

### 3.1 Answer key and precision

Ground truth: 7,099 Box PDFs from the nine folders with the most linked bills were downloaded and
SHA-256 hashed; 2,666 are byte-identical to a linked bill's `Attachment.FileHash`. 1,184 of those are
our own pushes (excluded), leaving **1,482 evaluable files**.

| tier | linked | wrong | precision | 95 % lower bound |
|---|---|---|---|---|
| **A** | 1,032 | 0 | 100 % | **99.63 %** ✅ |
| B | 35 | 0 | 100 % | 90.1 % (n too small) |
| C | 16 | 0 | 100 % | 80.6 % |
| D | 44 | 0 | 100 % | 92.0 % |

Recall 76 % (213 truth files do not parse). **Only tier A is eligible** under the 99 % bar.

### 3.2 Projection and content check on the target population

Tier A proposes **13,143 links to unlinked bills** (2022 2,092 · 2023 4,109 · 2024 4,250 ·
2025 3,186). The answer key is 2026-only, so a random 300 of these old-bill candidates were
content-checked (text layer, OCR for scans): **263 (87.7 %) contain both the bill's invoice number and
amount**. The 37 others: ~18 thin OCR on scans, ~11 one value found (formatting), ~3 **vendor
statements** carrying many invoices, ~5 near-empty / no vendor. None was shown wrong, but the
statement class is a real hazard (the file is not the bill's own invoice) — hence §4.

## 4. Verification and review (every link)

1. **Automated per-PDF verdict for every candidate** (not a sample): extract text (text layer;
   OCR first pages for scans), look for the bill's normalized invoice number, amount (with and without
   thousands separators) and vendor tokens. Verdict: `verified` (invoice # + amount),
   `partial`, `statement` (multiple invoice headers), `unverifiable`.
2. **Chris approves or rejects each candidate individually** in the review queue. The page shows the
   bill (vendor, invoice #, amount, date, project, line count) beside the rendered PDF, with found
   values highlighted and the verdict shown. One key to approve, one to reject (optional reason),
   batched by project or year, verified first.
3. Only **approved** candidates are written (§5). Rejected and unreviewed candidates never link.

Estimated effort: ~13,000 candidates × 3–5 s ≈ 11–18 hours of review, in batches.

## 5. Write path (approved candidates only)

- **Store:** download the Box file, compute SHA-256; if an `Attachment` with that `FileHash` exists,
  **reuse it** (never duplicate); else upload to blob and create one `Attachment` row.
- **Link:** one `BillLineItemAttachment` row per bill line, all to that attachment (the U-166
  invariant). Idempotent: an existing (line, attachment) link is left as is.
- **Completed-bill lock:** runs as a system caller (the CLI exemption), adding links only — no status,
  amount or coding change.
- **Extraction:** mark the attachment pending (`MarkAttachmentsPendingExtractionByGroup` shape); the
  live extraction timer reads it (text layer → DI OCR → TIN masking), and the word-hash and
  fingerprint indexes follow.
- **No outbound writes:** no SharePoint upload, Box push, Excel row, QBO attachable upload, review
  email or outbox row. A test pins that the write path enqueues nothing.
- **Tagging and undo:** every write is recorded on its candidate row (run id, attachment id, link
  datetime). Undo removes exactly the BLIA rows a run created (and an attachment it created, if
  nothing else references it).

## 6. Data model (new, additive)

- `dbo.AttachmentBackfillRun` — Id, PublicId, Source (`box` | `qbo`), CreatedByUserId, Status,
  CreatedDatetime, Notes.
- `dbo.AttachmentBackfillCandidate` — Id, PublicId, RowVersion, RunId, BillId, SourceFileId,
  SourceFileName, SourceSha256 (filled at verification), MatchTier, AutoVerdict, InvoiceFound,
  AmountFound, VendorFound, ReviewStatus (`pending` | `approved` | `rejected`), ReviewedByUserId,
  ReviewedDatetime, RejectReason, AttachmentId, LinkedDatetime. Unique (RunId, SourceFileId) and
  (RunId, BillId). Approve/reject guarded by RowVersion.

No change to `BillLineItemAttachment` or `Attachment`.

## 7. Phases (each its own Gate 1 / Gate 2)

| phase | what | writes |
|---|---|---|
| **b1** | candidate generation + per-PDF verification job (Box listing, folder→project map, parser, tiers, verdicts) into the candidate tables | new staging tables only |
| **b2** | admin API: queue, candidate detail, PDF proxy (streams the Box file; nothing stored until approval), approve/reject — `require_system_admin` | review state only |
| **b3** | web admin review page (keyboard, highlights, batches) | — (web deploy on ask) |
| **b4** | apply approved candidates (§5), dry-run default, per-batch commit, undo by run | `Attachment`, BLIA, blob |
| later | QBO 337 through the same queue; SharePoint names for the unparsed 5 %; tiers B–D only if a larger answer key proves ≥ 99 %; re-run U-697 vectors on the larger PDF corpus (~$0.25, ask first) | — |

## 8. Cost and capacity

- Blob: ≤ 3.85 GB ≈ $0.08/month; writes ≈ $0.30 one-time.
- Document Intelligence OCR: ≈ $5–21 one-time (0.13–0.56 OCR pages per document measured on the
  2026 bill and expense backfills, $1.50 / 1,000 pages).
- LLM: $0 (deterministic matcher; extraction triggers no model calls; no embeddings).
- **Database:** Basic tier, 2 GB cap, 1,324 MB used. Backfill adds ~120–200 MB (Attachment rows,
  word-hash rows, BLIA, candidate rows) → ~1.5 GB (~75 %). Gate 1b decision: size alert vs. Standard
  S0 (~$15/month, 250 GB).

## 9. Risks

- **Statements and multi-invoice PDFs** — surfaced by the `statement` verdict; Chris decides.
- **Invoice-number reuse across years** — mitigated by amount + project in tier A and mutual
  uniqueness; every link reviewed.
- **Folder→project mapping** — sub-unit projects share the parent folder; the filename's own project
  prefix resolves the sub-unit before the folder's project.
- **Taxpayer documents** — any W-9 in an invoices folder goes through the same TIN masking at
  extraction; W-9 extraction itself stays held (U-535a).
- **Vendor history ignores project access** (pre-existing, booked) — unchanged by this unit.

## 10. Probe artefacts

Read-only probes ran 2026-10-29 from a scratch ops worktree; derived data (inventory, hashes, bill
fields, verification results) is kept outside the repo in `.data/u535b/` and is never committed.
