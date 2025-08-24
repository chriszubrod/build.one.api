CREATE TABLE intuit.LinkedTxn
(
	[GUID] UNIQUEIDENTIFIER PRIMARY KEY default NEWID(),
	RealmId VARCHAR(MAX) NOT NULL,
	BillId VARCHAR(MAX) NOT NULL,
	TxnId VARCHAR(MAX) NOT NULL,
	TxnType VARCHAR(MAX) NOT NULL,
	TxnLineId VARCHAR(MAX) NOT NULL
);



DROP PROCEDURE IF EXISTS CreateLinkedTxn;

CREATE PROCEDURE CreateLinkedTxn
    @RealmId VARCHAR(MAX),
	@BillId VARCHAR(MAX),
	@TxnId VARCHAR(MAX),
	@TxnType VARCHAR(MAX),
	@TxnLineId VARCHAR(MAX)
AS
BEGIN
	BEGIN TRANSACTION;

    -- Insert a new record
    INSERT INTO intuit.LinkedTxn (RealmId, BillId, TxnId, TxnType, TxnLineId)
    VALUES (@RealmId, @BillId, @TxnId, @TxnType, @TxnLineId);

    COMMIT;
END







DROP PROCEDURE IF EXISTS ReadLinkedTxns;

CREATE PROCEDURE ReadLinkedTxns
AS
BEGIN
	BEGIN TRANSACTION;
	
	SELECT 
		[GUID],
		[RealmId],
		[BillId],
		[TxnId],
		[TxnType],
		[TxnLineId]
	FROM intuit.LinkedTxn
	ORDER BY [TxnDate] DESC;

	COMMIT;
END










DROP PROCEDURE IF EXISTS ReadLinkedTxnByTxnId;


CREATE PROCEDURE ReadLinkedTxnByTxnId
    @TxnId VARCHAR(MAX)
AS
BEGIN

	BEGIN TRANSACTION;

    SELECT 
		[GUID],
		[RealmId],
		[BillId],
		[TxnId],
		[TxnType],
		[TxnLineId]
    FROM intuit.LinkedTxn
    WHERE [TxnId] = @TxnId;

	COMMIT;
END








DROP PROCEDURE UpdateLinkedTxnByTxnId;


CREATE PROCEDURE UpdateLinkedTxnByTxnId
    @TxnId VARCHAR(MAX),
	@BillId VARCHAR(MAX),
	@TxnType VARCHAR(MAX),
	@TxnLineId VARCHAR(MAX)
AS
BEGIN
	BEGIN TRANSACTION;

    UPDATE intuit.LinkedTxn
    SET
		BillId=@BillId,
		TxnType=@TxnType,
		TxnLineId=@TxnLineId
    WHERE TxnId=@TxnId;

	COMMIT;
END



