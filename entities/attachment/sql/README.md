# Attachment SQL build order

## Single source of truth

`dbo.attachment.sql` is the **single canonical source** for the `dbo.Attachment`
table bootstrap and all 10 of its stored procedures. No migration may redefine
those sprocs — change the base file and apply it. Enforced by
`tests/test_sproc_single_source.py`.
Duplicate bodies that drift from the base file break net-zero with prod.

The 10 sprocs: `CreateAttachment`, `ReadAttachments`, `ReadAttachmentById`,
`ReadAttachmentByPublicId`, `ReadAttachmentByCategory`, `ReadAttachmentByHash`,
`UpdateAttachmentById`, `DeleteAttachmentById`, `ReadAttachmentsByIds`,
`IncrementDownloadCount`.

`CreateAttachment` was reconciled verbatim from
`scripts/migrations/gap2_adjacent_threading.sql` (Phase-Adjacent body) under
U-148 (2026-07-24) — the base was stale by one layer (missing
`@CreatedByUserId`).

## ⚠️ Applying this file to prod

**The base file must match live prod before any re-apply** — a stale base that
drops attribution params is the 2026-07-15 outage class (SQL 8144 parameter
errors).

The guarded `CREATE TABLE` in the base file predates the extraction,
categorization, and attribution columns; prod already has `ExtractionStatus`
(+3 more, `add_extraction_fields.sql`), the `AICategory` family
(`add_categorization_fields.sql`), and `CreatedByUserId`
(`scripts/migrations/gap2_created_by_user_id.sql`). Re-applying only the sproc
section is the usual path once bodies are verified base==live. For a fresh
database, follow the build order below.

## From-scratch build order

1. **`entities/attachment/sql/dbo.attachment.sql` (first pass)** — the guarded
   `CREATE TABLE` succeeds (original schema), but the sproc batches that
   reference later-layer columns **fail** at `CREATE PROCEDURE` time: SQL
   Server validates columns on existing tables, and `ExtractionStatus`, the
   `AICategory` family, and `CreatedByUserId` are not present yet. Continue —
   the second pass (step 5) applies them.

2. **`entities/attachment/sql/add_extraction_fields.sql`** — extraction columns
   + its 2 sprocs (`UpdateAttachmentExtraction`,
   `ReadAttachmentsPendingExtraction`).

3. **`entities/attachment/sql/add_categorization_fields.sql`** — AI categorization
   columns + its 3 sprocs (`UpdateAttachmentCategorization`,
   `ReadAttachmentsPendingCategorization`, `ConfirmAttachmentCategorization`).

4. **`scripts/migrations/gap2_created_by_user_id.sql`** — `CreatedByUserId`
   column on `dbo.Attachment` (and 29 other tables).

5. **`entities/attachment/sql/dbo.attachment.sql` (second pass)** — idempotent
   `CREATE OR ALTER`; all 10 sprocs apply with the full column set.

## Superseded stubs

`update_procedures_with_extraction.sql` carries a SUPERSEDED banner (U-148) and
no live sproc bodies — re-running it is a no-op for the five read sprocs it
formerly duplicated. The `CreateAttachment` copy in
`scripts/migrations/gap2_adjacent_threading.sql` is likewise stubbed (U-148);
that file's other sections stay live for their own entities.
