-- Default review workflow statuses
-- SortOrder gaps (10, 20, 30, 100) allow inserting intermediate statuses later

IF NOT EXISTS (SELECT 1 FROM dbo.[ReviewStatus] WHERE [Name] = 'Submitted')
BEGIN
    INSERT INTO dbo.[ReviewStatus] ([CreatedDatetime], [ModifiedDatetime], [Name], [Description], [SortOrder], [IsFinal], [IsDeclined], [IsActive], [Color])
    VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), 'Submitted', 'Submitted for review', 10, 0, 0, 1, '#2196F3');
END

IF NOT EXISTS (SELECT 1 FROM dbo.[ReviewStatus] WHERE [Name] = 'In Review')
BEGIN
    INSERT INTO dbo.[ReviewStatus] ([CreatedDatetime], [ModifiedDatetime], [Name], [Description], [SortOrder], [IsFinal], [IsDeclined], [IsActive], [Color])
    VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), 'In Review', 'Currently being reviewed', 20, 0, 0, 1, '#FF9800');
END

IF NOT EXISTS (SELECT 1 FROM dbo.[ReviewStatus] WHERE [Name] = 'Approved')
BEGIN
    INSERT INTO dbo.[ReviewStatus] ([CreatedDatetime], [ModifiedDatetime], [Name], [Description], [SortOrder], [IsFinal], [IsDeclined], [IsActive], [Color])
    VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), 'Approved', 'Review approved', 30, 1, 0, 1, '#4CAF50');
END

IF NOT EXISTS (SELECT 1 FROM dbo.[ReviewStatus] WHERE [Name] = 'Declined')
BEGIN
    INSERT INTO dbo.[ReviewStatus] ([CreatedDatetime], [ModifiedDatetime], [Name], [Description], [SortOrder], [IsFinal], [IsDeclined], [IsActive], [Color])
    VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), 'Declined', 'Review declined', 100, 0, 1, 1, '#F44336');
END
