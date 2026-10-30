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

- `dbo.AttachmentBackfillRun` — Id, PublicId, RowVersion, Source (`box` | `qbo`), Status
  (`generating` -> `ready` -> `applied`, or -> `cancelled`), Notes, CreatedByUserId, CreatedDatetime,
  ModifiedDatetime.
- `dbo.AttachmentBackfillCandidate` — Id, PublicId, RowVersion, RunId (FK), BillId (no FK: a deleted
  bill leaves a stale candidate), SourceFileId, SourceFileName, SourceFolderId, SourceFileSize,
  SourceSha256 (filled by the check), MatchTier; **CheckStatus** (`checked` | `unreadable` | `error`,
  NULL = unchecked) and neutral evidence InvoiceSeenCount, AmountSeenCount, VendorSeen,
  OtherInvoiceLabelCount, StatementLike, UsedOcr, CheckedDatetime; ReviewStatus (`pending` |
  `approved` | `rejected`), ReviewedByUserId, ReviewedDatetime, RejectReason, AttachmentId,
  LinkedDatetime, CreatedDatetime, ModifiedDatetime. Unique (RunId, SourceFileId) and (RunId, BillId);
  indexes (RunId, ReviewStatus, CheckStatus) and (BillId). Approve/reject guarded by RowVersion.
  (As built in b1 — see §11; supersedes the AutoVerdict / *Found columns of the original design.)

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

## 11. b1 as built (2026-10-29, uncommitted — pending review)

- `entities/attachment_backfill/` — pure `filename` / `project_map` / `matcher` (tier A only) / `verify`,
  read-only `box_inventory` (marker-paginated `folders/{id}/items`), `service` (contract docstring) and
  `persistence/repo`; SQL in `sql/dbo.attachment_backfill.sql`.
- Job `scripts/attachment_backfill_candidates.py`: `generate` / `verify` / `summary`, dry-run by default,
  counts-only output. Writes only the two staging tables; never `Attachment`, BLIA, blob or outbox (pinned by
  `tests/test_u535b_service.py`).
- Added beyond §6: `ReadAttachmentBackfillRunByPublicId` (the job addresses runs by public id);
  `ReadAttachmentBackfillCandidatesForVerification` is keyset-paged (`@AfterId`) with `@IncludeErrors`;
  `UpdateAttachmentBackfillCandidateVerification` refuses a reviewed or linked candidate; insert is refused
  unless the run is `generating`.
- Mutual uniqueness counts every tier-A hit as a claim, so a bill also matched by an ambiguous file is
  contested. Exact duplicate copies (`name (1).pdf`) of one invoice therefore drop out as contested.
- OCR runs the whole document through the existing DI `prebuilt-read` path (no page cap) when the text layer
  has < 40 non-space characters.

### 11.1 Fix round 1 (EM review)

- **Verify:** invoice # on whitespace-token boundaries (alpha prefix <= 7 letters and one separator
  allowed: `INV-0012`, `Invoice#1001`); a 5-digit token after a US state code is a ZIP; containment
  only for >= 6-char mixed letter+digit numbers. A $0.00 / NULL bill amount is never evidence.
  Statement = >= 3 distinct labelled invoice numbers (inline or an `Inv No` column), or a statement
  keyword anywhere plus >= 2 other bill-number-shaped tokens. OCR when the layer has < 40 non-space
  chars, or < 400 chars with neither value found; DI gets the first 3 pages (trimmed with pypdf,
  the existing DI path takes no page range). Files > 25 MB (`SourceFileSize`) are `unverifiable`
  without download.
- **Matcher:** same sign required (credit files excluded as `credit`); byte-size-identical copies
  collapse to the lowest Box id (`duplicate_copy`); 3 head segments parse; `head[3]` retried as the
  invoice when `head[2]` finds nothing (tier `A_alt`, unique only); alpha-prefix alternate key
  (`INV0012` <-> `0012`, tier suffix `_altkey`) only when the primary key misses; a bill matches on the
  filename-prefix project OR the folder project.
- **SQL/job:** no FK to `dbo.Bill` (dropped from an applied copy; a deleted bill leaves a stale
  candidate never handed to the verifier); insert refuses a bill with a pending/approved candidate
  in another non-cancelled run (`RefusedReason`); status moves generating -> ready -> applied or
  -> cancelled only; verification reads only a `ready` run. `generate --resume-run`, `cancel`.

### 11.2 Fix round 2 (final; strictness over recall)

- **Verdict set:** `verified` (invoice # AND amount, no labelled conflict) / `partial` (exactly one) /
  `mismatch` / `unverifiable` / `error`. `mismatch` = the PDF prints an invoice number right after an
  `INVOICE #|NO|NUMBER` or `INV #|NO` label (same line, next token) and none of those equals the bill's;
  it outranks everything. `statement` is no longer written (still allowed by the CHECK for an
  applied pre-round-2 copy).
- **Invoice # found:** exact normalized-token equality only; prefix strip only for the allowlist
  INV / INVOICE / INVNO / NO / NUM / NBR directly followed by a digit; no substring containment.
  PO1001, JOB#1001, QUOTE-1001, ACCOUNT#1001, Lot12 no longer find the invoice.
- **`StatementLike` flag (new column), not a verdict:** >= 3 distinct labelled invoice numbers, or a
  statement title phrase (STATEMENT OF ACCOUNT, ACCOUNT STATEMENT, ACCOUNT ACTIVITY, AGING, BALANCE
  FORWARD, OPEN INVOICES, INVOICE REGISTER, or a line that is exactly STATEMENT) with >= 2. The round-1
  column scan and same-shape rule were deleted: they flagged real single invoices (line-item qtys,
  ZIPs, street numbers).
- **Removed alternate tiers** (`A_alt` = next head segment as the invoice; `_altkey` = alpha-prefix
  alternate key): they turned description / PO segments into unique tier-A candidates and, with the
  old prefix stripping, into false `verified`. Tier A = the third filename segment's primary
  normalized invoice # only. The recall loss is accepted.
- **SQL:** insert refuses a (SourceFileId, BillId) pair rejected in any run (`previously_rejected`);
  the verification update enforces a `ready` run (refusal row `run_not_ready` / `reviewed_or_linked`
  / `not_found`); `generate --resume-run` first deletes this run's pending, unverified, unlinked
  candidates absent from the new plan (`DeleteStaleAttachmentBackfillCandidates`, reported as
  `pruned_stale`). A pending candidate left in an `applied` run blocks its bill from later runs
  until b4 resolves it.

### 11.3 Round 3 (Chris): neutral evidence, never a conclusion

The per-PDF check no longer presents a verdict. It stores a **check status** (`AutoVerdict` column (renamed `CheckStatus` in §11.4):
`checked` = text obtained from the layer or OCR, `unreadable` = < 40 non-space chars or > 25 MB,
`error` = exception) and **neutral evidence** the reviewer weighs against the highlighted PDF (b3
highlights the hits live; b1 stores counts/flags only, never text or values):

- `InvoiceSeenCount` — tokens equal to the bill number, both sides normalized the same way
  (allowlisted INV/INVOICE/INVNO/NO/NUM/NBR prefix + digit stripped, so `INV-1001` = `1001`); NULL
  when the bill number has < 3 normalized chars.
- `AmountSeenCount` — standalone occurrences with the bill's sign (`(x)` / `-x` are negative); NULL
  for a $0 / missing amount.
- `VendorSeen`, `UsedOcr`.
- `OtherInvoiceLabelCount` — distinct values after an invoice label (`INVOICE`/`INV` + `#`, `NO`,
  `NUMBER`, `NUM`, `ID` or `:`; value on the label's line, else the next line) that differ from the
  bill's; Previous/Original/Prior Invoice labels ignored.
- `StatementLike` — >= 3 distinct labelled values; or a statement title with >= 2, or with >= 3 lines
  each carrying a distinct invoice-shaped token and a money amount.

`InvoiceFound`/`AmountFound`/`VendorFound` are dropped. Re-running the SQL file on an applied earlier
copy maps verified/partial/mismatch/statement -> `checked` and unverifiable -> `unreadable` before the
new CHECK is added. The run summary reports distributions only (unchecked/checked/unreadable/errored,
invoice seen 0/1/2+, amount seen 0/1+, other label 0/1+, statement-like, vendor seen, OCR, linked).

### 11.4 Pass 2 refactor (behaviour-preserving, plus small ops changes)

- `AutoVerdict` -> `CheckStatus`, `VerifiedDatetime` -> `CheckedDatetime`, `verify.py` -> `evidence.py`.
  The round-to-round migration blocks are gone: the CREATE TABLE is the only shape (the tables were
  never applied outside local test databases).
- Writes run on one connection per loop (generate inserts; each check batch) and commit after every
  row. `ReadBillsForAttachmentBackfill` returns only Id, BillNumber, TotalAmount, ProjectIds.
- Ops: Ctrl-C during `verify` cancels queued downloads and exits (130), leaving unwritten rows
  unchecked; `@Keep` has a PK; a bill number holding whitespace also matches the same number of
  consecutive tokens joined; `generate --apply` refuses while a `generating`/`ready` run of the same
  source exists (`ReadOpenAttachmentBackfillRuns`) unless `--force-new-run`.

### Follow-ups (booked, out of b1 scope)

- Promote the marker-paged Box lister (`entities/attachment_backfill/business/box_inventory.py`
  `list_folder_items`) to `BoxHttpClient.iter_folder_items`, and repoint
  `vendor_service._box_list_children` and `BoxHttpClient.find_folder_child_by_name` at it.

## 12. b2 as built (2026-10-30) — review API

Gate 1 (Chris, 2026-10-30): b2 then b3; scans are re-OCR'd when opened (snippets never stored); the queue opens on the clean group; no bulk approve.

- Routes (all `require_system_admin`, prefix `/api/v1/admin/attachment-backfill`): `GET /runs`, `GET /runs/{run}/facets`,
  `GET /runs/{run}/queue` (keyset by PublicId cursor; filters group, project PublicId or `none`, year or `none`, review status),
  `GET /candidates/{id}` (row + live snippets + `content_matches_check`), `GET /candidates/{id}/pdf`, `POST /candidates/{id}/review`,
  `POST /candidates/{id}/undo`.
- Evidence groups are one SQL function, `dbo.AttachmentBackfillEvidenceGroup`: clean, flagged, invoice_only, amount_only, neither.
  They are filter labels, not conclusions.
- Each candidate has one primary project (lowest Name, then Id), so facet totals equal queue totals.
- Review and undo are each one atomic UPDATE, checking:
  - the RowVersion;
  - the run is 'ready';
  - the candidate is unlinked;
  - on approve, the bill still exists;
  - on approve, or on undo of a rejected candidate, no other non-cancelled run holds the bill as pending or approved
    (`NOT EXISTS ... UPDLOCK, HOLDLOCK`). This keeps it to one claim per bill.
- Refusal order: invalid, not_found, run_not_ready, bill_missing, bill_claimed_elsewhere, linked, not_pending/not_reviewed, stale.
  not_found returns 404, invalid returns 422, and every other refusal returns 409.
- The PDF is fetched only by the stored SourceFileId and capped at 25 MB, with a `%PDF-` check.
  - Response headers: `application/pdf`, `inline; filename="candidate.pdf"`, nosniff, `CSP: sandbox`, no-store,
    `X-Content-Matches-Check` (SHA-256 of the served bytes vs the stored SourceSha256).
  - If the file has changed since the check, the detail returns no snippets and `snippet_error: content_changed`.
- Responses carry no internal int ids; the reviewer is identified by User PublicId.
- Follow-ups are in `TODO.md` under "U-535b b2 follow-ups".
