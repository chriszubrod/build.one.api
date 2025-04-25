CREATE TABLE [Transaction] (
    [Id] INT IDENTITY(1,1) PRIMARY KEY,
    [GUID] UNIQUEIDENTIFIER DEFAULT NEWID() NOT NULL,
    [CreatedDatetime] DATETIMEOFFSET NOT NULL,
    [ModifiedDatetime] DATETIMEOFFSET NOT NULL
);

DELETE FROM dbo.[Transaction] WHERE [Id] IN (31,32,33,34,35);

SELECT * FROM dbo.[Transaction];
SELECT * FROM dbo.Module;
SELECT * FROM dbo.EntryType;


DROP PROCEDURE IF EXISTS ReadTransactionById;

CREATE PROCEDURE ReadTransactionById
    @TransactionId INT
AS
BEGIN
    SELECT
		[Id],
		[GUID],
		CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime
	FROM dbo.[Transaction]
	WHERE [Id] = @TransactionId;
END;




