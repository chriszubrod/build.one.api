CREATE TABLE Permission (
    [Id] INT IDENTITY(1,1) PRIMARY KEY,
    [GUID] UNIQUEIDENTIFIER DEFAULT NEWID() NOT NULL,
    [CreatedDatetime] DATETIMEOFFSET NOT NULL,
    [ModifiedDatetime] DATETIMEOFFSET NOT NULL,
    [IsAuthorized] BIT NOT NULL,
    [UserId] INT NOT NULL,
    [ModuleId] INT,
    [ProjectId] INT,
    [TransactionId] INT NOT NULL,
    FOREIGN KEY (UserId) REFERENCES [User](Id),
    FOREIGN KEY (ModuleId) REFERENCES Module(Id),
    FOREIGN KEY (ProjectId) REFERENCES Project(Id),
    FOREIGN KEY (TransactionId) REFERENCES [Transaction](Id)
);

SELECT * FROM [Transaction];
SELECT * FROM Permission;

CREATE PROCEDURE CreatePermission
    @CreatedDatetime DATETIMEOFFSET,
    @ModifiedDatetime DATETIMEOFFSET,
    @IsAuthorized BIT,
    @UserId INT,
    @ModuleId INT,
    @ProjectId INT
AS
BEGIN
    BEGIN TRANSACTION;

    -- Insert a new record into the Transaction table
    INSERT INTO [Transaction] (CreatedDatetime, ModifiedDatetime)
    VALUES (CONVERT(DATETIMEOFFSET, @CreatedDatetime), CONVERT(DATETIMEOFFSET, @ModifiedDatetime));

    -- Get the Id of the last inserted record
    DECLARE @TransactionId INT;
    SET @TransactionId = SCOPE_IDENTITY();

    -- Insert a new record into the Permission table using the TransactionId
    INSERT INTO Permission (CreatedDatetime, ModifiedDatetime, IsAuthorized, UserId, ModuleId, ProjectId, TransactionId)
    VALUES (CONVERT(DATETIMEOFFSET, @CreatedDatetime), CONVERT(DATETIMEOFFSET, @ModifiedDatetime), @IsAuthorized, @UserId, @ModuleId, @ProjectId, @TransactionId);

    COMMIT;
END

CREATE PROCEDURE ReadPermissionByIsAuthorized
    @IsAuthorized BIT
AS
BEGIN
    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)),
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)),
        [IsAuthorized],
        [UserId],
        [ModuleId],
        [ProjectId],
        [TransactionId]
    FROM Permission
    WHERE [IsAuthorized] = @IsAuthorized;
END
