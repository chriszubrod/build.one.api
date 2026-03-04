-- =============================================================================
-- InboxRecord Statistics
-- =============================================================================
-- Aggregate classification accuracy metrics for the admin dashboard.
-- Returns multiple result sets in a single call for efficiency.
-- =============================================================================

CREATE OR ALTER PROCEDURE [dbo].[GetInboxClassificationStats]
AS
BEGIN
    SET NOCOUNT ON;

    -- Result Set 1: Status distribution
    SELECT
        [Status],
        COUNT(*) AS [Count]
    FROM [dbo].[InboxRecord]
    GROUP BY [Status];

    -- Result Set 2: Overall accuracy
    SELECT
        COUNT(*) AS TotalProcessed,
        SUM(CASE WHEN [UserOverrideType] IS NULL AND [RecordType] IS NOT NULL THEN 1 ELSE 0 END) AS CorrectClassifications,
        SUM(CASE WHEN [UserOverrideType] IS NOT NULL THEN 1 ELSE 0 END) AS OverriddenClassifications,
        AVG([ClassificationConfidence]) AS AvgConfidence
    FROM [dbo].[InboxRecord]
    WHERE [RecordType] IS NOT NULL;

    -- Result Set 3: Accuracy by classification type
    SELECT
        [ClassificationType],
        COUNT(*) AS Total,
        SUM(CASE WHEN [UserOverrideType] IS NULL AND [RecordType] IS NOT NULL THEN 1 ELSE 0 END) AS Correct,
        SUM(CASE WHEN [UserOverrideType] IS NOT NULL THEN 1 ELSE 0 END) AS Overridden
    FROM [dbo].[InboxRecord]
    WHERE [ClassificationType] IS NOT NULL AND [RecordType] IS NOT NULL
    GROUP BY [ClassificationType];

    -- Result Set 4: Confidence distribution (histogram buckets)
    SELECT
        CASE
            WHEN [ClassificationConfidence] < 0.25 THEN '0-25%'
            WHEN [ClassificationConfidence] < 0.50 THEN '25-50%'
            WHEN [ClassificationConfidence] < 0.75 THEN '50-75%'
            WHEN [ClassificationConfidence] < 0.90 THEN '75-90%'
            ELSE '90-100%'
        END AS ConfidenceBucket,
        COUNT(*) AS [Count]
    FROM [dbo].[InboxRecord]
    WHERE [ClassificationConfidence] IS NOT NULL
    GROUP BY CASE
            WHEN [ClassificationConfidence] < 0.25 THEN '0-25%'
            WHEN [ClassificationConfidence] < 0.50 THEN '25-50%'
            WHEN [ClassificationConfidence] < 0.75 THEN '50-75%'
            WHEN [ClassificationConfidence] < 0.90 THEN '75-90%'
            ELSE '90-100%'
        END;

    -- Result Set 5: Top 10 common override patterns
    SELECT TOP 10
        [FromEmail],
        [ClassificationType] AS PredictedType,
        [RecordType] AS ActualType,
        COUNT(*) AS OverrideCount
    FROM [dbo].[InboxRecord]
    WHERE [UserOverrideType] IS NOT NULL
      AND [FromEmail] IS NOT NULL
    GROUP BY [FromEmail], [ClassificationType], [RecordType]
    ORDER BY COUNT(*) DESC;

    -- Result Set 6: Last 20 misclassifications
    SELECT TOP 20
        CAST([PublicId] AS NVARCHAR(36)) AS PublicId,
        [Subject],
        [FromEmail],
        [ClassificationType],
        [ClassificationConfidence],
        [RecordType],
        [UserOverrideType],
        CONVERT(NVARCHAR(23), [ProcessedAt], 126) AS ProcessedAt
    FROM [dbo].[InboxRecord]
    WHERE [UserOverrideType] IS NOT NULL
    ORDER BY [ProcessedAt] DESC;
END
GO
