CREATE TABLE CertificateType (
    [Id] INT IDENTITY(1,1) PRIMARY KEY,
    [GUID] UNIQUEIDENTIFIER DEFAULT NEWID() NOT NULL,
    [CreatedDatetime] DATETIMEOFFSET NOT NULL,
    [ModifiedDatetime] DATETIMEOFFSET NOT NULL,
    [Name] VARCHAR(255) NOT NULL,
    [TransactionId] INT NOT NULL
);

-- Reads
DROP PROCEDURE IF EXISTS ReadCertificateTypes;
GO
CREATE PROCEDURE ReadCertificateTypes
AS
BEGIN
    BEGIN TRANSACTION;
    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [Name],
        [TransactionId]
    FROM CertificateType
    ORDER BY [Name];
    COMMIT;
END
GO

DROP PROCEDURE IF EXISTS ReadCertificateTypeByName;
GO
CREATE PROCEDURE ReadCertificateTypeByName
    @Name VARCHAR(255)
AS
BEGIN
    BEGIN TRANSACTION;
    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [Name],
        [TransactionId]
    FROM CertificateType
    WHERE [Name] = @Name;
    COMMIT;
END
GO

DROP PROCEDURE IF EXISTS ReadCertificateTypeByID;
GO
CREATE PROCEDURE ReadCertificateTypeByID
    @ID INT
AS
BEGIN
    BEGIN TRANSACTION;
    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [Name],
        [TransactionId]
    FROM CertificateType
    WHERE [Id] = @ID;
    COMMIT;
END
GO

DROP PROCEDURE IF EXISTS ReadCertificateTypeByGUID;
GO
CREATE PROCEDURE ReadCertificateTypeByGUID
    @GUID VARCHAR(255)
AS
BEGIN
    BEGIN TRANSACTION;
    SELECT
        [Id],
        [GUID],
        CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime,
        CAST([ModifiedDatetime] AS NVARCHAR(MAX)) AS ModifiedDatetime,
        [Name],
        [TransactionId]
    FROM CertificateType
    WHERE [GUID] = @GUID;
    COMMIT;
END
GO

-- Create
DROP PROCEDURE IF EXISTS CreateCertificateType;
GO
CREATE PROCEDURE CreateCertificateType
    @Name VARCHAR(255)
AS
BEGIN
    BEGIN TRANSACTION;
    DECLARE @Now DATETIMEOFFSET = SYSDATETIMEOFFSET();
    INSERT INTO [Transaction] (CreatedDatetime, ModifiedDatetime) VALUES (@Now, @Now);
    DECLARE @TransactionId INT = SCOPE_IDENTITY();
    INSERT INTO CertificateType (CreatedDatetime, ModifiedDatetime, [Name], TransactionId)
    VALUES (@Now, @Now, @Name, @TransactionId);
    COMMIT;
END
GO

-- Update
DROP PROCEDURE IF EXISTS UpdateCertificateType;
GO
CREATE PROCEDURE UpdateCertificateType
    @ID INT,
    @Name VARCHAR(255)
AS
BEGIN
    BEGIN TRANSACTION;
    DECLARE @Now DATETIMEOFFSET = SYSDATETIMEOFFSET();
    UPDATE CertificateType
    SET [ModifiedDatetime] = @Now,
        [Name] = @Name
    WHERE [Id] = @ID;
    COMMIT;
END
GO

-- Delete
DROP PROCEDURE IF EXISTS DeleteCertificateType;
GO
CREATE PROCEDURE DeleteCertificateType
    @ID INT
AS
BEGIN
    BEGIN TRANSACTION;
    DELETE FROM CertificateType WHERE [Id] = @ID;
    COMMIT;
END
GO

