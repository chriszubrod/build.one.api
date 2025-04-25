CREATE TABLE Template (
    [Id] INT IDENTITY(1,1) PRIMARY KEY,
    [GUID] UNIQUEIDENTIFIER DEFAULT NEWID() NOT NULL,
    [CreatedDatetime] DATETIMEOFFSET NOT NULL,
    [ModifiedDatetime] DATETIMEOFFSET NOT NULL,
    [TransactionId] INT NOT NULL,
    FOREIGN KEY (TransactionId) REFERENCES [Transaction](Id)
);

SELECT * FROM [Transaction];
SELECT * FROM Template;
 
CREATE PROCEDURE CreateTemplate
    @CreatedDatetime DATETIMEOFFSET,
    @ModifiedDatetime DATETIMEOFFSET
AS
BEGIN
    BEGIN TRANSACTION;

    -- Insert a new record into the Transaction table
    INSERT INTO [Transaction] (CreatedDatetime, ModifiedDatetime)
    VALUES (CONVERT(DATETIMEOFFSET, @CreatedDatetime), CONVERT(DATETIMEOFFSET, @ModifiedDatetime));

    -- Get the Id of the last inserted record
    DECLARE @TransactionId INT;
    SET @TransactionId = SCOPE_IDENTITY();

    -- Insert a new record into the Template table using the TransactionId
    INSERT INTO Template (CreatedDatetime, ModifiedDatetime, TransactionId)
    VALUES (CONVERT(DATETIMEOFFSET, @CreatedDatetime), CONVERT(DATETIMEOFFSET, @ModifiedDatetime), @TransactionId);

    COMMIT;
END

CREATE PROCEDURE ReadTemplateById
    @Id INT
AS
BEGIN
    SELECT
        [Id],
        [GUID],
        [CreatedDatetime],
        [ModifiedDatetime],
        [TransactionId]
    FROM Template
    WHERE [Id] = @Id;
END
