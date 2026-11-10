# U-166c — Attachment access gating

Approved at Gate 1 by Chris, 2026-11-10.

## Problem

Every attachment route checked only the ATTACHMENTS module. Any holder of ATTACHMENTS read could:

- list every attachment and its BlobUrl (`GET /get/attachments`, by-category, by-hash);
- read, view or download any attachment by id or public id;
- link any attachment onto a line they can edit.

Live exposure on 2026-11-08: user 32 (the Apple Reviewer, a Tenant Admin, 3 of 141 projects) could download all attachments, including the 68 W-9 (TIN) links. Chris's decision: the role stays, because Apple still tests with it. The fix has to close this without changing it.

## Rule

`dbo.UserCanAccessAttachment(@ActorUserId, @ActorIsSystemAdmin, @AttachmentId)` returns 1 when any of these holds:

1. The actor is a system admin.
2. The actor uploaded it (`Attachment.CreatedByUserId = @ActorUserId`).
3. It is linked to a record the actor can access:
   - BillLineItemAttachment → `UserCanAccessBill(bill)`
   - BillCreditLineItemAttachment → `UserCanAccessBillCredit(credit)`
   - ExpenseLineItemAttachment → `UserCanAccessExpense(expense)`
   - InvoiceAttachment / InvoiceLineItemAttachment → `UserCanAccessProject(invoice.ProjectId)`
4. The actor holds the company role **Owner** or **Controller** (`dbo.UserRole` → `dbo.Role.Name`), and the attachment is either:
   - linked to a project-less record (Taxpayer, BusinessLicense, ContractorsLicense or Asset), or
   - not linked to anything.

Owners and Controllers do **not** get project-linked attachments outside their projects. They keep the project rule like everyone else; the role only adds the project-less documents (Chris, 2026-11-10).

The UDF lives in `shared/sql/dbo.access_udfs.sql` with the rest of the `UserCanAccess*` family. It is deliberately **not** `WITH SCHEMABINDING`. It reads nine link tables, and schemabinding would block every future `ALTER` of them. It calls the existing schemabound UDFs.

## Enforcement (API)

- `shared/access.py`: `can_access_attachment` / `assert_can_access_attachment`, the same shape as bill and expense. A denial raises `EntityNotAccessibleError`, which the API returns as **404**.
- `AttachmentService.read_by_public_id` / `read_by_id` check access after the fetch. This one resolver covers every route that reads, views, downloads, updates, archives or deletes an attachment. It also covers every link-create path (BLIA, BCLIA, ELIA, ILIA, Taxpayer, BusinessLicense, ContractorsLicense, Asset) and `BillService.create` / `ExpenseService.create`, since they all resolve the attachment through it.
- System callers bypass the check through the existing `system_authz()` / drain-secret context. That covers outbox workers, pulls, the CLI and the packet generator in system ticks.
- The list routes (`/get/attachments`, `/get/attachments/by-category/{c}`, `/get/attachments/by-hash/{h}`) become **system-admin only**. No web or iOS client calls them (checked 2026-11-10).
- Link creates (each resolves the attachment through the gated `AttachmentService.read_by_public_id` / `read_by_id`, so an attachment the actor cannot open is refused):
  BLIA, ELIA, ILIA, BCLIA, Taxpayer, BusinessLicense, ContractorsLicense, Asset, COI ingest. `InvoiceAttachment` (raw `invoice_id` / `attachment_id`) had no check and is now **system-admin only** (`require_system_admin()` on every route, U-166c fix round 2). Its service re-checks the invoice (gated `InvoiceService.read_by_id`) and the attachment (gated read) before the write, so the packet generator, which runs as the requesting user, still links its own attachment to an invoice it can open.
- BLIA and ELIA create: an existing link on the line pointing at a **different** attachment raises a conflict, returned as **409**. Previously it silently returned the other link. The same attachment still returns the existing link, which keeps the call idempotent.

## Not changed

- The module checks: ATTACHMENTS read, update and delete still apply on top.
- Upload routes. The uploader is the creator, so rule 2 grants them their own file.
- `EmailAttachment` and `MsMessageAttachment`. They are not `dbo.Attachment` rows.

## Deploy order (required)

1. Apply `shared/sql/dbo.access_udfs.sql` to prod with `scripts/run_sql.py` from the repo root.
2. Verify before the image ships: `SELECT OBJECT_ID('dbo.UserCanAccessAttachment')` returns a non-NULL id.
3. Only then deploy the API image.

If the image ships first, every non-admin attachment read calls a UDF that does not exist and returns 500.

## Measurement (prod, 2026-11-10, read-only simulation of the rule)

- User 32 (Apple Reviewer, Tenant Admin): 36 of 30,496 attachments visible, 0 of the 68 W-9s. Before the fix: all of them.
- Owners / Controllers (users 20, 44, 55, 57): about 29.9K attachments each, including all 68 W-9s (project-less documents).
- Tenant Admin user 56: 5,320 attachments, every one linked to a record they can see. About 24.5K attachments have no link at all, so only admins, Owners/Controllers and the uploader see those.

## Vendor compliance under the rule

COI, business-license, contractor's-license and W-9 documents are reached through link tables outside the nine in the UDF (`CertificateOfInsurance`, `CodingSuggestion`, `BillIntakePrediction`, the box and ms tables). Those attachments count as "unlinked" under rule 4. Only admins, Owners, Controllers and the uploader can read them. For any other VENDORS reader, `build_packet` omits the COI (the BL, CL and W-9 helpers already omit denied documents) instead of returning 500.

## Known residuals

- An unlinked attachment created by a system or agent user (for example, an email-bridged PDF before it is linked) is visible only to admins, Owners and Controllers until it is linked.
- `BlobUrl` is still returned to anyone allowed to read the row.
- The UDF does not filter `UserRole` by CompanyId; this is a single-company deployment (Phase 5b deferred).
- A project creator who has no `UserProject` row can open the invoice but not its BLIA attachments: `UserCanAccessBill` has no project-creator clause.
- The email bridge's `read_by_hash` dedup can return another user's existing Attachment dict. Its gate is not applied to hash reads.
- The packet regenerate path deletes the old packet blob before the row (the U-446b row-first order is not applied there). Packet attachments are not bill evidence, so the guard does not apply, but the order is inconsistent.
