-- =====================================================================
-- Gap 2 Phase Core — thread CreatedByUserId on the transactional
-- money-entity Create sprocs whose entity base files do not yet carry it.
--
-- Pattern: add @CreatedByUserId BIGINT = NULL param; INSERT uses
-- COALESCE(@CreatedByUserId, 17) so the existing DEFAULT-trick fallback
-- still fires when callers do not pass an actor (scheduler / recovery
-- jobs / agents that have not been threaded yet keep working).
--
-- Idempotent (CREATE OR ALTER). Migration-only — does NOT replay the
-- base sproc files (which would roll back later migrations like Gap 1
-- list-path filters or Phase 3 actor params on Read sprocs).
--
-- NEUTRALIZED sections (now base-canonical pointer stubs — the per-unit list
-- below is the record; do not re-add a hand-maintained count here):
--   U-061 (2026-07-17) — CreateProject, CreateBill, CreateExpense,
--     CreateInvoiceLineItem: their bodies had drifted BEHIND their entity base
--     files, so re-running them reverted prod.
--   U-074 (2026-07-17) — CreateBillLineItem, CreateExpenseLineItem.
--   U-102 (2026-07-21) — CreateBillCredit, CreateBillCreditLineItem: the
--     INVERSE case — the BASE files were the stale copies (no @CreatedByUserId)
--     and were reconciled to this file's form verbatim.
--   U-158 (2026-07-28) — CreateInvoice: the INVERSE case — the BASE file was
--     the stale copy (no @CreatedByUserId) and was reconciled to this file's
--     form verbatim.
--   U-162 (2026-07-28) — CreateContractLabor, CreateContractLaborLineItem:
--     the INVERSE case for CreateContractLabor (base lacked @CreatedByUserId,
--     reconciled to this file's form verbatim); CreateContractLaborLineItem
--     was byte-identical (stubbed for single-source only).
-- Every section is now a base-canonical pointer stub.
-- =====================================================================

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO

-- ===== 1. CreateProject =====
-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-061, 2026-07-17) — body removed, NOT the @CreatedByUserId intent.
--
-- Canonical definition now lives in exactly ONE place:
--   entities/project/sql/dbo.project.sql
-- That base carries @CreatedByUserId (the original intent of this file) AND the
-- @Notes param this copy had drifted behind.
--
-- Drift: this body omitted @Notes. The repo layer sends @Notes unconditionally,
-- so re-running this file reverted prod CreateProject to the pre-@Notes shape and
-- broke project creation with SQL 8144 ("too many arguments") from ~2026-05-26
-- until the base was re-applied to prod on 2026-07-17. Re-running this file is
-- now a no-op for CreateProject. Do NOT reintroduce a body here.
-- ---------------------------------------------------------------------------
GO

-- ===== 2. CreateBill =====
-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-061, 2026-07-17) — body removed, NOT the @CreatedByUserId intent.
--
-- Canonical definition now lives in exactly ONE place:
--   entities/bill/sql/dbo.bill_create_source_email.sql
-- That base carries @CreatedByUserId (the original intent of this file).
--
-- Drift (body-level, params match): this body inserted the caller @DueDate,
-- whereas the base deliberately mirrors DueDate = @BillDate (migration
-- 005_bill_duedate_mirror_billdate). Because the params match, re-running this
-- file would NOT error — it would SILENTLY revert the DueDate = BillDate business
-- rule. Re-running this file is now a no-op for CreateBill. Do NOT reintroduce a
-- body here.
-- ---------------------------------------------------------------------------
GO

-- ===== 3. CreateBillCredit =====
-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-102, 2026-07-21) — body removed, NOT the @CreatedByUserId intent.
--
-- Canonical definition now lives in exactly ONE place:
--   entities/bill_credit/sql/dbo.bill_credit.sql
-- That base was STALE (it lacked @CreatedByUserId) and has been reconciled to
-- this file's live form verbatim.
--
-- Drift (INVERTED vs the U-061 stubs above — here the BASE was the copy that had
-- fallen behind, not this file): the base omitted @CreatedByUserId, which BillCreditRepository.create
-- sends unconditionally (entities/bill_credit/persistence/repo.py), so
-- re-applying the base file would have reverted prod CreateBillCredit to the
-- pre-threading shape and broken every BillCredit create with SQL 8145 — the
-- same param-drift that caused the U-089 and U-037 outages. Re-running this
-- file is now a no-op for CreateBillCredit. Do NOT reintroduce a body here.
-- ---------------------------------------------------------------------------
GO

-- ===== 4. CreateExpense =====
-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-061, 2026-07-17) — body removed, NOT the @CreatedByUserId intent.
--
-- Canonical definition now lives in exactly ONE place:
--   entities/expense/sql/dbo.expense.sql
-- That base carries @CreatedByUserId (the original intent of this file) AND the
-- @SourceEmailMessageId param this copy had drifted behind.
--
-- Drift: this body omitted @SourceEmailMessageId. ExpenseRepository.create sends
-- that param unconditionally (entities/expense/persistence/repo.py), so re-running
-- this file would revert prod CreateExpense to the pre-source-email shape and break
-- expense creation with SQL 8144 — the exact CreateProject failure mode. Re-running
-- this file is now a no-op for CreateExpense. Do NOT reintroduce a body here.
-- ---------------------------------------------------------------------------
GO

-- ===== 5. CreateInvoice =====
-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-158, 2026-07-28) — body removed, NOT the @CreatedByUserId intent.
--
-- Canonical definition now lives in exactly ONE place:
--   entities/invoice/sql/dbo.invoice.sql
-- That base was STALE (it lacked @CreatedByUserId) and has been reconciled to
-- this file's live form verbatim.
--
-- Drift (INVERTED — here the BASE was the copy that had fallen behind, not this
-- file): the base omitted @CreatedByUserId, which InvoiceRepository.create sends
-- unconditionally (entities/invoice/persistence/repo.py), so re-applying the base
-- file would have reverted prod CreateInvoice to the pre-threading shape and broken
-- every invoice create with SQL 8145. Re-running this file is now a no-op for
-- CreateInvoice. Do NOT reintroduce a body here.
-- ---------------------------------------------------------------------------
GO

-- ===== 6. CreateContractLabor =====
-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-162, 2026-07-28) — body removed, NOT the @CreatedByUserId intent.
--
-- Canonical definition now lives in exactly ONE place:
--   entities/contract_labor/sql/dbo.contract_labor.sql
-- That base was STALE (it lacked @CreatedByUserId) and has been reconciled to
-- this file's live form verbatim.
--
-- Drift (INVERTED — here the BASE was the copy that had fallen behind, not this
-- file): the base omitted @CreatedByUserId, which ContractLaborRepository.create
-- sends unconditionally (entities/contract_labor/persistence/repo.py), so
-- re-applying the base file would have reverted prod CreateContractLabor to the
-- pre-threading shape and broken every CL create with SQL 8145 — the same
-- param-drift class as U-102/U-158. Re-running this file is now a no-op for
-- CreateContractLabor. Do NOT reintroduce a body here.
-- ---------------------------------------------------------------------------
GO

-- ===== 7. CreateBillLineItem =====
-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-074, 2026-07-17) - body removed, NOT the @CreatedByUserId intent.
--
-- Canonical definition now lives in exactly ONE place:
--   entities/bill_line_item/sql/dbo.bill_line_item.sql
-- That base carries @CreatedByUserId (this copy threading intent) AND @Quantity
-- DECIMAL(18,4). This copy had drifted behind on @Quantity INT, which silently
-- truncated fractional quantities on insert (prod ran this INT body). Re-running
-- this file is now a no-op for CreateBillLineItem. Do NOT reintroduce a body here.
-- ---------------------------------------------------------------------------
GO

-- ===== 8. CreateBillCreditLineItem =====
-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-102, 2026-07-21) — body removed, NOT the @CreatedByUserId intent.
--
-- Canonical definition now lives in exactly ONE place:
--   entities/bill_credit_line_item/sql/dbo.bill_credit_line_item.sql
-- That base was STALE (it lacked @CreatedByUserId) and has been reconciled to
-- this file's live form verbatim.
--
-- Drift (INVERTED vs the U-061 stubs above — here the BASE was the copy that had
-- fallen behind, not this file): the base omitted @CreatedByUserId, which BillCreditLineItemRepository.create
-- sends unconditionally (entities/bill_credit_line_item/persistence/repo.py), so
-- re-applying the base file would have reverted prod CreateBillCreditLineItem to the
-- pre-threading shape and broken every BillCreditLineItem create with SQL 8145 — the
-- same param-drift that caused the U-089 and U-037 outages. Re-running this
-- file is now a no-op for CreateBillCreditLineItem. Do NOT reintroduce a body here.
-- ---------------------------------------------------------------------------
GO

-- ===== 9. CreateExpenseLineItem =====
-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-074, 2026-07-17) - body removed, NOT the @CreatedByUserId intent.
--
-- Canonical definition now lives in exactly ONE place:
--   entities/expense_line_item/sql/dbo.expense_line_item.sql
-- That base carries @CreatedByUserId (this copy threading intent) AND @Quantity
-- DECIMAL(18,4). This copy had drifted behind on @Quantity INT, which silently
-- truncated fractional quantities on insert (prod ran this INT body). Re-running
-- this file is now a no-op for CreateExpenseLineItem. Do NOT reintroduce a body here.
-- ---------------------------------------------------------------------------
GO

-- ===== 10. CreateInvoiceLineItem =====
-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-061, 2026-07-17) — body removed, NOT the @CreatedByUserId intent.
--
-- Canonical definition now lives in exactly ONE place:
--   entities/invoice_line_item/sql/dbo.invoice_line_item.sql
-- That base carries @CreatedByUserId (the original intent of this file) AND the
-- @EmployeeLaborLineItemId param this copy had drifted behind.
--
-- Drift: this body omitted @EmployeeLaborLineItemId. The repo layer sends that
-- param (entities/invoice_line_item/persistence/repo.py), so re-running this file
-- would revert prod to the pre-employee-labor shape and break invoice-line creation
-- with SQL 8144 — the same drift that hid @EmployeeLaborLineItemId from prod through
-- incidents WVA-17 / WVA-18 (base made canonical 2026-07-06). Re-running this file is
-- now a no-op for CreateInvoiceLineItem. Do NOT reintroduce a body here.
-- ---------------------------------------------------------------------------
GO

-- ===== 11. CreateContractLaborLineItem =====
-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-162, 2026-07-28) — body removed, NOT the @CreatedByUserId intent.
--
-- Canonical definition now lives in exactly ONE place:
--   entities/contract_labor/sql/dbo.contract_labor.sql
-- Base and this copy were byte-identical; stubbed for single-source only.
--
-- Drift: NONE — re-running this file is now a no-op for CreateContractLaborLineItem.
-- Do NOT reintroduce a body here.
-- ---------------------------------------------------------------------------
GO

PRINT 'Gap 2 Phase Core: all sections are base-canonical pointer stubs (U-061/U-074/U-102/U-158/U-162)';
GO
