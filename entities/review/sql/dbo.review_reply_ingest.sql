-- Contract: dbo.ReadOpenReviewReplyConversations — the Graph ConversationIds whose review thread still has an open document.
-- Owns: the read side of the reviewer-reply ingest (U-797c3). Returns DISTINCT ReviewNotification.ConversationId values, nothing else.
-- Invariants: a conversation qualifies only when its notification is inside the window AND an open target exists. Open means: bill target with Status IN ('submitted','in_review'); expense target with Status IN ('submitted','in_review') (IsDraft is computed as Status <> completed, so it also matched approved, declined and draft rows); contract_labor target with Status IN ('pending_review','submitted'). ConversationId compares case-sensitively (BIN). Read-only.
-- Sprocs: ReadOpenReviewReplyConversations

-- U-797c3: which Graph conversations the reviewer-reply ingest should watch. A reply
-- is copied only when its thread still has a document awaiting review, so the ingest
-- never stores mail for a thread that is already decided.
CREATE OR ALTER PROCEDURE dbo.ReadOpenReviewReplyConversations
    @SinceDays INT = 45
AS
BEGIN
    SET NOCOUNT ON;

    SELECT DISTINCT n.[ConversationId] COLLATE Latin1_General_BIN AS [ConversationId]
    FROM dbo.[ReviewNotification] n
    WHERE n.[ConversationId] IS NOT NULL
      AND n.[CreatedDatetime] >= DATEADD(day, -@SinceDays, SYSUTCDATETIME())
      AND (
            (n.[Kind] = N'bill' AND EXISTS (
                SELECT 1
                FROM dbo.[ReviewNotificationTarget] t
                JOIN dbo.[Bill] b ON b.[Id] = t.[BillId]
                WHERE t.[ReviewNotificationId] = n.[Id]
                  AND b.[Status] IN (N'submitted', N'in_review')))
         OR (n.[Kind] = N'expense' AND EXISTS (
                SELECT 1
                FROM dbo.[ReviewNotificationTarget] t
                JOIN dbo.[Expense] e ON e.[Id] = t.[ExpenseId]
                WHERE t.[ReviewNotificationId] = n.[Id]
                  AND e.[Status] IN (N'submitted', N'in_review')))
         OR (n.[Kind] = N'contract_labor' AND EXISTS (
                SELECT 1
                FROM dbo.[ReviewNotificationTarget] t
                JOIN dbo.[ContractLabor] c ON c.[Id] = t.[ContractLaborId]
                WHERE t.[ReviewNotificationId] = n.[Id]
                  AND c.[Status] IN (N'pending_review', N'submitted')))
          );
END
GO
