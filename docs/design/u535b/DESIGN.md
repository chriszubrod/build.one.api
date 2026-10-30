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

## 13. b4 as built (2026-10-30, uncommitted — pending review) — apply and undo

Gate 1 (Chris, 2026-10-30): apply-as-you-go (the run stays `ready` while review continues), dry run by default, a
database-size guard, undo by run, and the b2 undo audit trail.

- **Files:** `entities/attachment_backfill/sql/b4_apply.sql` (applied after `dbo.attachment_backfill.sql`),
  `business/apply.py`, new repo methods, `scripts/attachment_backfill_apply.py`. The undo-audit columns and the undo-proc
  change are in `dbo.attachment_backfill.sql`.
- **CLI:** `--run <PublicId>` (required). `--apply` writes. `--undo` reverses links, and also needs `--apply` to write.
  Other flags: `--limit N`, `--max-db-percent` (default 85), `--size-check-every` (default 25). Runs as the system caller
  under `system_authz()`. Output is counts only: no file names, no document text.
- **Dry run:** database reads only, with zero Box downloads and zero writes. It reports `would_apply`,
  `would_reuse_attachment` / `would_create_attachment` (looked up by the stored SourceSha256), `would_link_rows`, and
  `would_refuse_<reason>` for every refusal the database alone decides. It also prints the database size and warns when an
  apply would be refused.
- **Database-size guard:** `ReadAttachmentBackfillDatabaseSize` returns the allocated pages of the data files and
  `DATABASEPROPERTYEX(MaxSizeInBytes)`. If that is missing, it falls back to the data files' `max_size` when every file
  is capped. The CLI prints used MB / cap MB / %.
  - It refuses to apply (exit 3) when usage is at or over the threshold, or when the size is unknown (fail closed).
  - It re-checks every N candidates and stops between candidates (exit 3) when the threshold is crossed.
- **Per candidate** (approved, `AttachmentId` NULL, run `ready`):
  1. Database pre-checks, in this order:
     - `bill_missing`
     - `no_line_items`
     - `bill_has_attachment` (any line of the bill already has a BillLineItemAttachment)
     - `no_check_hash` (no SourceSha256 stored, so the bytes cannot be tied to what the reviewer saw)
     - `too_large` (stored size > 25 MB)
  2. Download by the stored SourceFileId, then check, in this order:
     - `fetch_failed`
     - `too_large`
     - `not_pdf` (no `%PDF-` in the first 1 KB)
     - `content_changed` (SHA-256 differs from SourceSha256; case-insensitive)
  3. Attachment:
     - If `read_by_hash` finds one, reuse it.
     - Otherwise upload a new blob named `<uuid4>.<ext>` and create an Attachment with the bill-PDF upload's fields:
       category `bill_line_item`, content type `application/pdf`, no compaction (so the stored hash is the reviewed one),
       and CreatedByUserId = NULL → 17 per the system-caller convention.
  4. `ApplyAttachmentBackfillCandidateLink` runs in ONE transaction with `XACT_ABORT ON`.
     - Locks, all UPDLOCK + HOLDLOCK, in this order: Attachment → Bill → run → candidate. This is the same
       Attachment-before-Bill order that CreateBillLineItemAttachment and DeleteAttachmentById use.
     - Refusal order (fixed): `not_found`, `run_not_ready`, `linked`, `not_approved`, `stale`, `bill_missing`,
       `no_line_items`, `bill_has_attachment`, `attachment_missing`, `attachment_hash_mismatch`.
     - On success it inserts one BLIA per bill line (CreatedByUserId 17), records each new BLIA id in
       `dbo.AttachmentBackfillCandidateLink`, and sets AttachmentId, LinkedDatetime and `AttachmentCreatedByApply`.
       The RowVersion bumps.
     - Completed bills are allowed. The write is add-only: no Bill, line, status, amount or coding write.
  5. After the commit, the attachment is marked pending extraction if its ExtractionStatus is NULL. This is
     failure-isolated.
- **Race with the b2 review undo:** confirmed. The b2 undo refuses `linked` once AttachmentId is set. If the b2 undo wins
  first, the apply refuses `not_approved`; any other change to the candidate after the apply's read refuses `stale`.
  Both orders are pinned by SQL tests.
- **Orphan cleanup (decision):** an attachment *created* for a candidate whose link is refused, raises, or is interrupted
  (Ctrl-C included, via `finally`) is deleted at once by `DeleteAttachmentBackfillCreatedAttachment`.
  - That proc locks the Attachment and requires its FileHash to match.
  - It refuses (`referenced`) if a backfill candidate, any BLIA, or any row in any table with a foreign key to
    `dbo.Attachment` references the attachment. Those tables are found dynamically from `sys.foreign_key_columns`.
    The attachment's own derived indexes (AttachmentTextTokenHash, AttachmentTextFingerprint) do not count; they are
    deleted with it.
  - The delete runs inside a savepoint plus TRY/CATCH, so a writer that skipped the lock yields `referenced` rather than
    an error.
  - Blobs (the file and the extracted-text JSON) are deleted only after the row delete commits. A kept orphan is logged
    by attachment id only and counted.
  - A reused attachment is never deleted.
- **Undo by run (BLIA identification, decision):** the apply records an explicit link table
  (`AttachmentBackfillCandidateLink`: CandidateId, BillLineItemAttachmentId UNIQUE).
  - `UndoAttachmentBackfillCandidateLink` deletes only the recorded BLIA rows that still point at the candidate's
    attachment. Because the apply refuses a bill that has any BLIA, every recorded row was created by the apply, and a
    pre-existing or later hand-added link is never touched.
  - It clears AttachmentId, LinkedDatetime and AttachmentCreatedByApply, and leaves ReviewStatus `approved`. It runs on
    any run status. It uses the same lock order as the apply.
  - The CLI then deletes the attachment (and its blobs) through the same guarded proc, only when
    `AttachmentCreatedByApply = 1`.
  - Each candidate is its own transaction, so a Ctrl-C leaves the rest still linked, and a re-run continues.
- **Run closure:** at the end of an uninterrupted pass with no `--limit` stop, `CloseAttachmentBackfillRunIfDone` moves
  `ready` → `applied` atomically, but only while no candidate is pending or approved-unlinked (corrected in §13.1:
  the run row is locked first, and the b2 review procs read the run with UPDLOCK (§13.2), because the DB runs
  READ_COMMITTED_SNAPSHOT). A candidate refused by the
  apply stays approved-unlinked and keeps the run open; the reviewer can undo it and reject it on the b3 page.
- **Undo audit (b2 follow-up):** the `UndoneByUserId` / `UndoneDatetime` columns are additive and nullable, added with
  guarded ALTERs. `UndoAttachmentBackfillCandidateReview` takes `@UndoneByUserId = NULL` and sets both columns in the
  same UPDATE that clears the reviewer.
  - The repo passes the request's authenticated user (the `shared.authz.current_user_id` ContextVar, the existing
    ActorUserId pattern) unless an explicit id is given.
  - The router and the b2 router tests are unchanged.
- **No outbound side effects:** no outbox row, SharePoint, Box write, Excel, QBO or email. A unit test patches every
  `enqueue*` on the Box, MS and QBO outbox services, Box client write verbs, and `get_connection`, and asserts that none
  is called. Blob I/O and the Box download are injected by the CLI (the composition root). The entity package sees only
  `store` / `remove` callables, which also keeps b1's package-wide write-surface guard
  (`tests/test_u535b_service.py`) green.
- **Exit codes:** 0 ok; 1 the pass failed (DB error, counts so far printed); 2 unknown or non-`ready` run; 3 database-size
  refusal or stop; 4 finished but some candidate hit a counted error (§13.2); 130 Ctrl-C. The first Ctrl-C
  finishes the current candidate; a second one aborts at once, and a just-created attachment is still cleaned up.

### 13.1 Fix round 1 (EM review)

- **R1 — undo never strips evidence that has since left the building.**
  - The apply records the bill's Status at link time (`AttachmentBackfillCandidate.LinkedBillStatus`).
  - `UndoAttachmentBackfillCandidateLink` refusal order: `not_found`, `not_linked`, `attachment_pushed` (Attachment.QboId
    set), `bill_completed_since_apply` (completed now, not completed at link time).
  - No path deletes an attachment that has a QboId.
- **Intended evidence flow:** linking a bill that is not yet completed means its later completion carries this PDF through
  the normal completion flows (SharePoint/Box upload, QBO attachable push, packets). That is the purpose of the link,
  and it is why undo refuses once the bill has completed since the apply.
- **R2 — the delete guard fails closed.** `DeleteAttachmentBackfillCreatedAttachment` counts references in every user table,
  in any schema, that has either a foreign key to `dbo.Attachment` or a column named `AttachmentId`.
  - That covers loose pointers such as box.File, BillIntakePrediction, CodingSuggestion\*, ExpenseReceiptMatch.
  - Excluded: the attachment's own text indexes, which are deleted with it, and the registry.
  - The dynamic SQL is built with `STRING_AGG`, uses QUOTENAME, and takes the id as a parameter. The count runs inside
    TRY. A 547 error refuses `referenced`; any other error refuses `error`.
  - A doomed transaction is rolled back (the caller counts it as kept); otherwise the proc commits.
  - Refusal order: `not_registered`, `already_gone` (the registry row is closed), `not_found` (the hash differs),
    `attachment_pushed`, `referenced`, `error`.
- **R3 — durable registry.** `dbo.AttachmentBackfillCreatedAttachment` holds RunId, CandidateId, AttachmentId (UNIQUE),
  FileHash, BlobUrl, CreatedDatetime and DeletedDatetime.
  - The registry row is written right after the Attachment row is created. A registration failure skips the link and
    is counted as `error_register`; the attachment is logged by id.
  - The delete proc deletes only registered attachments.
  - Undo unlinks every candidate first, then SWEEPS every open registry row of the run. This fixes the shared-bytes
    leak: creator A is kept as `referenced` while B still links the attachment, and B's unlink then frees it. Counts:
    `swept_deleted`, `swept_kept_<reason>`.
  - An interrupted undo skips the sweep; a re-run sweeps.
- **R4:** if the attachment create fails with an error, the new blob is removed. On Ctrl-C the blob is left (only its
  name is logged), so a committed row never points at a deleted blob.
- **R5 — choosing which attachment to reuse.** `ReadAttachmentBackfillAttachmentsByHash` returns every row with that
  hash. `apply.pick_reusable` skips rows that are archived, have a Status other than NULL/`active` (the two values the
  code writes), or have an expiry in the past or unparseable. Among the rest, the lowest Id wins.
  - The dry run uses the same rule.
  - Reuse assumes a single company: `dbo.Attachment` has no CompanyId (TODO for multi-tenant).
- **R6:**
  - A hash-read error counts as `error_read` for that candidate and the pass continues.
  - Every pass fills a Counter in place, and the CLI prints it in `finally`. That holds after a DB error (exit 1, which
    prints the exception type name only), a second Ctrl-C (exit 130), and a size stop.
  - Both dry runs honour Ctrl-C (exit 130).
- **R7:** undoing a link in an `applied` run moves the run back to `ready` in the same transaction, so it can be
  re-applied and reviewed. `ready` and `cancelled` runs are left as they are.
- **R8:** `UpdateAttachmentBackfillCandidateReview` and `UndoAttachmentBackfillCandidateReview` join the run
  `WITH (READCOMMITTEDLOCK)` (changed to `UPDLOCK` in §13.2). Without it, under RCSI they could read a stale `ready` while the closure commits
  `applied`; with it they wait and then refuse `run_not_ready`.
  - In the other order, a review undo that commits first holds its candidate row; the closure's UPDLOCK read waits,
    sees `pending`, and does not close.
  - This is pinned by a proc-text test only. The shared Docker DB has RCSI off, and turning it on needs exclusive
    access, so no behavioural RCSI test was run.
- **R9:** `CloseAttachmentBackfillRunIfDone` runs in a transaction and locks the run row (UPDLOCK, HOLDLOCK) before its
  candidate check: run → candidate, the same order the apply uses.

### 13.2 Fix round 2 (EM, final)

- **S1:** the delete guard matches a column by the name `AttachmentId` only when its type is int or bigint. Columns found
  through a foreign key count whatever their type.
  - Why: `dbo.MsMessageAttachment.AttachmentId` is NVARCHAR(500) and holds Graph ids. Comparing it with the BIGINT id
    converted the column and raised error 245, so every delete refused `error` whenever that table had rows.
- **S2:** the b2 review and review-undo UPDATEs read the run `WITH (UPDLOCK)` instead of READCOMMITTEDLOCK.
  - The U lock conflicts with the closure's run lock whatever plan the optimiser picks.
  - Cost: review writes on one run serialize on the run row. They are single-reviewer and fast.
- **S3:** a failure of `CloseAttachmentBackfillRunIfDone` after a successful pass (deadlock, DB blip) is counted as
  `close_error` instead of raised. A re-run closes the run.
- **P1:** registering a created attachment is retried once (the proc is idempotent). On any failure, Ctrl-C included,
  the log line carries the attachment id AND the blob name, so an unregistered orphan can always be found.
- **P3:** the dry run counts `would_apply` / `would_link_rows` only after the reuse read succeeds.
- **P4:** the CLI exits 4 after printing the counts when any `error_*`, `*_kept_error`, `close_error` or
  `blob_remove_failed` count is non-zero.
- **P2 runbook — the sweep is per run.** Undo sweeps only the registry rows of the run being undone.
  - Within one run, shared bytes are freed: the creator is kept as `referenced` until the last candidate that reuses
    the attachment is unlinked, and then the sweep deletes it.
  - Across runs it is different. Suppose run R2 reused an attachment that run R1 created. Undoing R2 does not delete
    it: R2's registry does not hold it, and R1's sweep had already kept it as `referenced`.
  - To free it, undo R2, then re-run R1's undo (`--run <R1> --undo --apply`). R1's sweep then finds the attachment
    unreferenced and deletes it.
