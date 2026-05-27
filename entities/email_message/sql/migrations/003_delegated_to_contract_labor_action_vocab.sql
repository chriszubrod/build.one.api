-- Migration 003 — add `delegated_to_contract_labor_specialist` to
-- AgentDecidedAction controlled vocabulary.
--
-- Part of the contract_labor_specialist agent build (TODO.md line 204,
-- item 5b). Mirrors migration 002 (which added the matching
-- `contract_labor_timesheet` classification vocab). Until Phase 6 wires
-- routing, no email will carry this action; the counter exists so the
-- agent CAN stamp it once routing lands without another migration.
--
-- AgentDecidedAction is a bare NVARCHAR(50) with no CHECK constraint,
-- so no table change is required — only the sender-history counter
-- sproc needs to know about the new value.
--
-- Idempotent: CREATE OR ALTER. Safe to re-apply.
GO


CREATE OR ALTER PROCEDURE ReadEmailSenderHistory
(
    @FromEmail NVARCHAR(320),
    @ExcludePublicId UNIQUEIDENTIFIER = NULL
)
AS
BEGIN
    SET NOCOUNT ON;

    -- Result set 1: aggregate counts
    SELECT
        COUNT(*)                                                                       AS PriorEmailsTotal,

        -- Workflow status counts
        SUM(CASE WHEN [ProcessingStatus] = 'pending'         THEN 1 ELSE 0 END)        AS StatusPending,
        SUM(CASE WHEN [ProcessingStatus] = 'processing'      THEN 1 ELSE 0 END)        AS StatusProcessing,
        SUM(CASE WHEN [ProcessingStatus] = 'extracted'       THEN 1 ELSE 0 END)        AS StatusExtracted,
        SUM(CASE WHEN [ProcessingStatus] = 'awaiting_review' THEN 1 ELSE 0 END)        AS StatusAwaitingReview,
        SUM(CASE WHEN [ProcessingStatus] = 'agent_complete'  THEN 1 ELSE 0 END)        AS StatusAgentComplete,
        SUM(CASE WHEN [ProcessingStatus] = 'irrelevant'      THEN 1 ELSE 0 END)        AS StatusIrrelevant,
        SUM(CASE WHEN [ProcessingStatus] = 'failed'          THEN 1 ELSE 0 END)        AS StatusFailed,

        -- Agent classification counts (controlled vocabulary)
        SUM(CASE WHEN [AgentClassification] = 'vendor_invoice'           THEN 1 ELSE 0 END) AS ClassVendorInvoice,
        SUM(CASE WHEN [AgentClassification] = 'vendor_credit_memo'       THEN 1 ELSE 0 END) AS ClassVendorCreditMemo,
        SUM(CASE WHEN [AgentClassification] = 'vendor_statement'         THEN 1 ELSE 0 END) AS ClassVendorStatement,
        SUM(CASE WHEN [AgentClassification] = 'vendor_expense_receipt'   THEN 1 ELSE 0 END) AS ClassVendorExpenseReceipt,
        SUM(CASE WHEN [AgentClassification] = 'customer_payment'         THEN 1 ELSE 0 END) AS ClassCustomerPayment,
        SUM(CASE WHEN [AgentClassification] = 'customer_question'        THEN 1 ELSE 0 END) AS ClassCustomerQuestion,
        SUM(CASE WHEN [AgentClassification] = 'customer_dispute'         THEN 1 ELSE 0 END) AS ClassCustomerDispute,
        SUM(CASE WHEN [AgentClassification] = 'internal_reply'           THEN 1 ELSE 0 END) AS ClassInternalReply,
        SUM(CASE WHEN [AgentClassification] = 'internal_forward'         THEN 1 ELSE 0 END) AS ClassInternalForward,
        SUM(CASE WHEN [AgentClassification] = 'vendor_newsletter'        THEN 1 ELSE 0 END) AS ClassVendorNewsletter,
        SUM(CASE WHEN [AgentClassification] = 'contract_labor_timesheet' THEN 1 ELSE 0 END) AS ClassContractLaborTimesheet,
        SUM(CASE WHEN [AgentClassification] = 'non_actionable'           THEN 1 ELSE 0 END) AS ClassNonActionable,
        SUM(CASE WHEN [AgentClassification] = 'unknown'                  THEN 1 ELSE 0 END) AS ClassUnknown,
        SUM(CASE WHEN [AgentClassification] IS NULL                      THEN 1 ELSE 0 END) AS ClassUnclassified,

        -- Agent action counts
        SUM(CASE WHEN [AgentDecidedAction] = 'delegated_to_bill_specialist'              THEN 1 ELSE 0 END) AS ActionDelegatedBill,
        SUM(CASE WHEN [AgentDecidedAction] = 'delegated_to_bill_credit_specialist'       THEN 1 ELSE 0 END) AS ActionDelegatedBillCredit,
        SUM(CASE WHEN [AgentDecidedAction] = 'delegated_to_expense_specialist'           THEN 1 ELSE 0 END) AS ActionDelegatedExpense,
        SUM(CASE WHEN [AgentDecidedAction] = 'delegated_to_contract_labor_specialist'    THEN 1 ELSE 0 END) AS ActionDelegatedContractLabor,
        SUM(CASE WHEN [AgentDecidedAction] = 'flagged_needs_review'                      THEN 1 ELSE 0 END) AS ActionFlaggedReview,
        SUM(CASE WHEN [AgentDecidedAction] = 'marked_irrelevant'                         THEN 1 ELSE 0 END) AS ActionMarkedIrrelevant,
        SUM(CASE WHEN [AgentDecidedAction] = 'marked_processed'                          THEN 1 ELSE 0 END) AS ActionMarkedProcessed,
        SUM(CASE WHEN [AgentDecidedAction] IS NULL                                        THEN 1 ELSE 0 END) AS ActionUnset,

        -- Committed-entity counts (cross joins via SourceEmailMessageId)
        ISNULL((SELECT COUNT(*) FROM dbo.[Bill] b
                INNER JOIN dbo.[EmailMessage] em ON em.[Id] = b.[SourceEmailMessageId]
                WHERE em.[FromAddress] = @FromEmail), 0)         AS PriorBillsCommitted,
        ISNULL((SELECT COUNT(*) FROM dbo.[Expense] e
                INNER JOIN dbo.[EmailMessage] em ON em.[Id] = e.[SourceEmailMessageId]
                WHERE em.[FromAddress] = @FromEmail), 0)         AS PriorExpensesCommitted,
        ISNULL((SELECT COUNT(*) FROM dbo.[BillCredit] bc
                INNER JOIN dbo.[EmailMessage] em ON em.[Id] = bc.[SourceEmailMessageId]
                WHERE em.[FromAddress] = @FromEmail), 0)         AS PriorBillCreditsCommitted
    FROM dbo.[EmailMessage]
    WHERE [FromAddress] = @FromEmail
      AND (@ExcludePublicId IS NULL OR [PublicId] <> @ExcludePublicId);

    -- Result set 2: distinct Vendors associated with prior committed Bills
    SELECT
        v.[Id]                                  AS VendorId,
        CAST(v.[PublicId] AS NVARCHAR(36))      AS VendorPublicId,
        v.[Name]                                AS VendorName,
        COUNT(b.[Id])                           AS BillCount
    FROM dbo.[Vendor] v
    INNER JOIN dbo.[Bill] b           ON b.[VendorId]              = v.[Id]
    INNER JOIN dbo.[EmailMessage] em  ON em.[Id]                   = b.[SourceEmailMessageId]
    WHERE em.[FromAddress] = @FromEmail
    GROUP BY v.[Id], v.[PublicId], v.[Name]
    ORDER BY VendorName;
END;
GO
