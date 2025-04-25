CREATE TABLE [User] (
    [Id] INT IDENTITY(1,1) PRIMARY KEY,
    [GUID] UNIQUEIDENTIFIER DEFAULT NEWID() NOT NULL,
    [CreatedDatetime] DATETIMEOFFSET NOT NULL,
    [ModifiedDatetime] DATETIMEOFFSET NOT NULL,
    [IsActive] BIT NULL,
	[Username] VARCHAR(MAX) NULL,
	[PasswordHash] VARCHAR(MAX) NULL,
	[PasswordSalt] VARCHAR(MAX) NULL,
    [RoleId] INT NULL,
    [TransactionId] INT NULL,
    FOREIGN KEY (RoleId) REFERENCES [Role](Id),
    FOREIGN KEY (TransactionId) REFERENCES [Transaction](Id)
);





ALTER TABLE [User] ALTER COLUMN [TransactionId] INT NULL;

SELECT * FROM [Transaction];
SELECT * FROM [Contact];
SELECT * FROM [Role];
SELECT * FROM [User];


ALTER TABLE [User] ADD [PasswordSalt] VARCHAR(255) NULL;





DELETE FROM dbo.[User];






DROP PROCEDURE IF EXISTS CreateUser;

CREATE PROCEDURE CreateUser
    @CreatedDatetime DATETIMEOFFSET,
    @ModifiedDatetime DATETIMEOFFSET,
    @IsActive BIT,
	@RoleId INT,
	@Username VARCHAR(MAX),
	@PasswordHash VARCHAR(MAX),
	@PasswordSalt VARCHAR(MAX)
AS
BEGIN
    BEGIN TRANSACTION;

    -- Insert a new record into the Transaction table
    INSERT INTO [Transaction] (CreatedDatetime, ModifiedDatetime)
    VALUES (CONVERT(DATETIMEOFFSET, @CreatedDatetime), CONVERT(DATETIMEOFFSET, @ModifiedDatetime));

    -- Get the Id of the last inserted record
    DECLARE @TransactionId INT;
    SET @TransactionId = SCOPE_IDENTITY();

    -- Insert a new record into the User table using the TransactionId
    INSERT INTO [User] (CreatedDatetime, ModifiedDatetime, IsActive, RoleId, Username, PasswordHash, PasswordSalt, TransactionId)
    VALUES (CONVERT(DATETIMEOFFSET, @CreatedDatetime), CONVERT(DATETIMEOFFSET, @ModifiedDatetime), @IsActive, @RoleId, @Username, @PasswordHash, @PasswordSalt, @TransactionId);

    COMMIT;
END



DROP PROCEDURE IF EXISTS ReadUsers;

CREATE PROCEDURE ReadUsers
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [IsActive],
        [RoleId],
        [TransactionId],
		[Username]
    FROM [User];

    COMMIT;
END


DROP PROCEDURE IF EXISTS ReadUserByGUID;

CREATE PROCEDURE ReadUserByGUID
    @GUID UNIQUEIDENTIFIER
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [IsActive],
        [RoleId],
        [TransactionId],
		[Username],
		[PasswordHash],
		[PasswordSalt]
    FROM [User]
    WHERE [GUID] = @GUID;

    COMMIT;
END






DROP PROCEDURE IF EXISTS ReadUserByUsername;

CREATE PROCEDURE ReadUserByUsername
    @Username VARCHAR(255)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [IsActive],
        [RoleId],
        [TransactionId],
		[Username],
		[PasswordHash],
		[PasswordSalt]
    FROM [User]
    WHERE [Username] = @Username;

    COMMIT;
END


DROP PROCEDURE IF EXISTS UpdateUserById;

CREATE PROCEDURE UpdateUserById
    @Id INT,
    @GUID UNIQUEIDENTIFIER,
    @CreatedDatetime DATETIMEOFFSET,
    @ModifiedDatetime DATETIMEOFFSET,
    @IsActive BIT,
    @RoleId INT,
    @TransactionId INT,
    @Username VARCHAR(MAX),
    @PasswordHash VARCHAR(MAX),
    @PasswordSalt VARCHAR(MAX)
AS
BEGIN
    BEGIN TRANSACTION

    UPDATE [User]
    SET
        [ModifiedDatetime] = @ModifiedDatetime,
        [IsActive] = @IsActive,
        [RoleId] = @RoleId
    WHERE [Id] = @Id;

    COMMIT;
END

EXEC UpdateUserById @Id=2, @ModifiedDatetime='2025-01-26 00:00:00', @IsActive=True, @RoleId=Null;





