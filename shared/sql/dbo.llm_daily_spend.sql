-- Contract: daily LLM spend totals by provider (UTC calendar date).
-- Build: shared/sql/README.md

IF OBJECT_ID('dbo.LlmDailySpend', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.LlmDailySpend (
        SpendDate DATE NOT NULL,
        Provider NVARCHAR(32) NOT NULL,
        CostUsd DECIMAL(12, 6) NOT NULL CONSTRAINT DF_LlmDailySpend_CostUsd DEFAULT (0),
        CallCount INT NOT NULL CONSTRAINT DF_LlmDailySpend_CallCount DEFAULT (0),
        ModifiedDatetime DATETIME2(3) NULL,
        CONSTRAINT PK_LlmDailySpend PRIMARY KEY (SpendDate, Provider)
    );
END;
GO

CREATE OR ALTER PROCEDURE dbo.AddLlmDailySpend
    @SpendDate DATE,
    @Provider NVARCHAR(32),
    @CostUsd DECIMAL(12, 6)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    IF NOT EXISTS (
        SELECT 1
        FROM dbo.LlmDailySpend WITH (UPDLOCK, HOLDLOCK)
        WHERE SpendDate = @SpendDate AND Provider = @Provider
    )
    BEGIN
        INSERT INTO dbo.LlmDailySpend (SpendDate, Provider, CostUsd, CallCount, ModifiedDatetime)
        VALUES (@SpendDate, @Provider, 0, 0, @Now);
    END;

    UPDATE dbo.LlmDailySpend
    SET
        CostUsd = CostUsd + @CostUsd,
        CallCount = CallCount + 1,
        ModifiedDatetime = @Now
    WHERE SpendDate = @SpendDate AND Provider = @Provider;

    COMMIT TRANSACTION;

    SELECT SpendDate, Provider, CostUsd, CallCount, ModifiedDatetime
    FROM dbo.LlmDailySpend
    WHERE SpendDate = @SpendDate AND Provider = @Provider;
END;
GO

CREATE OR ALTER PROCEDURE dbo.ReadLlmDailySpend
    @SpendDate DATE,
    @Provider NVARCHAR(32)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT SpendDate, Provider, CostUsd, CallCount, ModifiedDatetime
    FROM dbo.LlmDailySpend
    WHERE SpendDate = @SpendDate AND Provider = @Provider;
END;
GO

CREATE OR ALTER PROCEDURE dbo.ReadLlmSpendForRange
    @FromDate DATE,
    @ToDate DATE,
    @Provider NVARCHAR(32)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT SpendDate, Provider, CostUsd, CallCount, ModifiedDatetime
    FROM dbo.LlmDailySpend
    WHERE Provider = @Provider
      AND SpendDate >= @FromDate
      AND SpendDate <= @ToDate
    ORDER BY SpendDate;
END;
GO
