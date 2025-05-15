CREATE TABLE [Address] (
    [Id] INT IDENTITY(1,1) PRIMARY KEY,
    [GUID] UNIQUEIDENTIFIER DEFAULT NEWID() NOT NULL,
    [CreatedDatetime] DATETIMEOFFSET NOT NULL,
    [ModifiedDatetime] DATETIMEOFFSET NOT NULL,
    [StreetOne] VARCHAR(255) NOT NULL,
    [StreetTwo] VARCHAR(255),
    [City] VARCHAR(255) NOT NULL,
    [State] VARCHAR(2) NOT NULL,
    [Zip] VARCHAR(10) NOT NULL,
    [TransactionId] INT NOT NULL,
    FOREIGN KEY (TransactionId) REFERENCES [Transaction](Id)
);

SELECT * FROM [Transaction];
SELECT * FROM [Address];




CREATE PROCEDURE CreateAddress
    @CreatedDatetime DATETIMEOFFSET,
    @ModifiedDatetime DATETIMEOFFSET,
    @StreetOne VARCHAR(255),
    @StreetTwo VARCHAR(255),
    @City VARCHAR(255),
    @State VARCHAR(2),
    @Zip VARCHAR(10)
AS
BEGIN
    BEGIN TRANSACTION;

    -- Insert a new record into the Transaction table
    INSERT INTO [Transaction] (CreatedDatetime, ModifiedDatetime)
    VALUES (CONVERT(DATETIMEOFFSET, @CreatedDatetime), CONVERT(DATETIMEOFFSET, @ModifiedDatetime));

    -- Get the Id of the last inserted record
    DECLARE @TransactionId INT;
    SET @TransactionId = SCOPE_IDENTITY();

    -- Insert a new record into the Address table using the TransactionId
    INSERT INTO [Address] (CreatedDatetime, ModifiedDatetime, StreetOne, StreetTwo, City, [State], Zip, TransactionId)
    VALUES (CONVERT(DATETIMEOFFSET, @CreatedDatetime), CONVERT(DATETIMEOFFSET, @ModifiedDatetime), @StreetOne, @StreetTwo, @City, @State, @Zip, @TransactionId);

    COMMIT;
END




DROP PROCEDURE IF EXISTS ReadAddresses;

CREATE PROCEDURE ReadAddresses
AS
BEGIN

    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [StreetOne],
        [StreetTwo],
        [City],
        [State],
        [Zip],
        [TransactionId]
    FROM [Address];

    COMMIT;
END;



DROP PROCEDURE IF EXISTS ReadAddressById;

CREATE PROCEDURE ReadAddressById
    @Id INT
AS
BEGIN

    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [StreetOne],
        [StreetTwo],
        [City],
        [State],
        [Zip],
        [TransactionId]
    FROM [Address]
    WHERE [Id] = @Id;

    COMMIT;
END;



DROP PROCEDURE IF EXISTS ReadAddressByGUID;

CREATE PROCEDURE ReadAddressByGUID
    @GUID VARCHAR(255)
AS
BEGIN

    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [StreetOne],
        [StreetTwo],
        [City],
        [State],
        [Zip],
        [TransactionId]
    FROM [Address]
    WHERE [GUID] = @GUID;

    COMMIT;
END;





CREATE PROCEDURE ReadAddressByStreetCityStateZip
    @StreetOne VARCHAR(255),
    @StreetTwo VARCHAR(255),
    @City VARCHAR(255),
    @State VARCHAR(2),
    @Zip VARCHAR(10)
AS
BEGIN
    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime], AS NVARCHAR(MAX)),
        CAST([ModifiedDatetime], AS NVARCHAR(MAX)),
        [StreetOne],
        [StreetTwo],
        [City],
        [State],
        [Zip],
        [TransactionId]
    FROM [Address]
    WHERE [StreetOne] = @StreetOne
    AND [StreetTwo] = @StreetTwo
    AND [City] = @City
    AND [State] = @State
    AND [Zip] = @Zip;
END





DELETE FROM dbo.[Address];