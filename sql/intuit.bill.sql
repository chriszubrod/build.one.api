CREATE TABLE intuit.Bill
(
	[GUID] UNIQUEIDENTIFIER PRIMARY KEY default NEWID(),
	RealmId VARCHAR(MAX) NOT NULL,
	Id VARCHAR(MAX),
	VendorRefValue VARCHAR(MAX),
	/* Line[] - See intuit.AccountBasedExpenseLine and intuit.ItemBasedExpenseLine */
	SyncToken VARCHAR(MAX),
	CurrencyRefValue VARCHAR(MAX),
	TxnDate DATETIMEOFFSET,
	APAccountRefValue VARCHAR(MAX),
	SalesTermRefValue VARCHAR(MAX),
	TotalAmt DECIMAL(18,2),
	DueDate DATETIMEOFFSET,
	DocNumber VARCHAR(MAX),
	PrivateNote VARCHAR(MAX),
	Balance DECIMAL(18,2),
	CreatedDatetime DATETIMEOFFSET,
	LastUpdatedDatetime DATETIMEOFFSET
);



DROP PROCEDURE CreateIntuitBill;

CREATE PROCEDURE CreateIntuitBill
    @RealmId VARCHAR(MAX),
	@Id VARCHAR(MAX),
	@VendorRefValue VARCHAR(MAX),
	@SyncToken VARCHAR(MAX),
	@CurrencyRefValue VARCHAR(MAX),
	@TxnDate DATETIMEOFFSET,
	@APAccountRefValue VARCHAR(MAX),
	@SalesTermRefValue VARCHAR(MAX),
	@TotalAmt DECIMAL(18,2),
	@DueDate DATETIMEOFFSET,
	@DocNumber VARCHAR(MAX),
	@PrivateNote VARCHAR(MAX),
	@Balance DECIMAL(18,2),
	@CreatedDatetime DATETIMEOFFSET,
	@LastUpdatedDatetime DATETIMEOFFSET
AS
BEGIN
	BEGIN TRANSACTION;

    -- Insert a new record
    INSERT INTO intuit.Bill (RealmId, Id, VendorRefValue, SyncToken, CurrencyRefValue, TxnDate, APAccountRefValue, SalesTermRefValue, TotalAmt, DueDate, DocNumber, PrivateNote, Balance, CreatedDatetime, LastUpdatedDatetime)
    VALUES (@RealmId, @Id, @VendorRefValue, @SyncToken, @CurrencyRefValue, CONVERT(DATETIMEOFFSET, @TxnDate), @APAccountRefValue, @SalesTermRefValue, @TotalAmt, CONVERT(DATETIMEOFFSET, @DueDate), @DocNumber, @PrivateNote, @Balance, CONVERT(DATETIMEOFFSET, @CreatedDatetime), CONVERT(DATETIMEOFFSET, @LastUpdatedDatetime));

    COMMIT;
END







DROP PROCEDURE IF EXISTS ReadIntuitBills;

CREATE PROCEDURE ReadIntuitBills
AS
BEGIN
	BEGIN TRANSACTION;
	
	SELECT 
		[GUID],
		[RealmId],
		[Id],
		[VendorRefValue],
		[SyncToken],
		[CurrencyRefValue],
		CAST([TxnDate] AS NVARCHAR(MAX)) AS [TxnDate],
		[APAccountRefValue],
		[SalesTermRefValue],
		[TotalAmt],
		CAST([DueDate] AS NVARCHAR(MAX)) AS [DueDate],
		[DocNumber],
		[PrivateNote],
		[Balance],
		CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS [CreatedDatetime],
		CAST([LastUpdatedDatetime] AS NVARCHAR(MAX)) AS [LastUpdatedDatetime]
	FROM intuit.Bill
	ORDER BY [TxnDate] DESC;

	COMMIT;
END

EXEC ReadIntuitBills;








DROP PROCEDURE ReadIntuitBillById;


CREATE PROCEDURE ReadIntuitBillById
    @Id VARCHAR(MAX)
AS
BEGIN

	BEGIN TRANSACTION;

    SELECT 
		[GUID],
		[RealmId],
		[Id],
		[VendorRefValue],
		[SyncToken],
		[CurrencyRefValue],
		CAST([TxnDate] AS NVARCHAR(MAX)) AS [TxnDate],
		[APAccountRefValue],
		[SalesTermRefValue],
		[TotalAmt],
		CAST([DueDate] AS NVARCHAR(MAX)) AS [DueDate],
		[DocNumber],
		[PrivateNote],
		[Balance],
		CAST([CreatedDatetime] AS NVARCHAR(MAX)) AS [CreatedDatetime],
		CAST([LastUpdatedDatetime] AS NVARCHAR(MAX)) AS [LastUpdatedDatetime]
    FROM intuit.Bill
    WHERE [Id] = @Id;

	COMMIT;
END








DROP PROCEDURE UpdateIntuitBillByRealmIdAndId;


CREATE PROCEDURE UpdateIntuitBillByRealmIdAndId
    @RealmId VARCHAR(MAX),
	@Id VARCHAR(MAX),
	@VendorRefValue VARCHAR(MAX),
	@SyncToken VARCHAR(MAX),
	@CurrencyRefValue VARCHAR(MAX),
	@TxnDate DATETIMEOFFSET,
	@APAccountRefValue VARCHAR(MAX),
	@SalesTermRefValue VARCHAR(MAX),
	@TotalAmt DECIMAL(18,2),
	@DueDate DATETIMEOFFSET,
	@DocNumber VARCHAR(MAX),
	@PrivateNote VARCHAR(MAX),
	@Balance DECIMAL(18,2),
	@CreatedDatetime DATETIMEOFFSET,
	@LastUpdatedDatetime DATETIMEOFFSET
AS
BEGIN
	BEGIN TRANSACTION;

    UPDATE intuit.Bill
    SET
		VendorRefValue=@VendorRefValue,
		SyncToken=@SyncToken,
		CurrencyRefValue=@CurrencyRefValue,
		TxnDate=CONVERT(DATETIMEOFFSET, @TxnDate),
		APAccountRefValue=@APAccountRefValue,
		SalesTermRefValue=@SalesTermRefValue,
		TotalAmt=@TotalAmt,
		DueDate=CONVERT(DATETIMEOFFSET, @DueDate),
		DocNumber=@DocNumber,
		PrivateNote=@PrivateNote,
		Balance=@Balance,
		CreatedDatetime=CONVERT(DATETIMEOFFSET, @CreatedDatetime),
		LastUpdatedDatetime=CONVERT(DATETIMEOFFSET, @LastUpdatedDatetime)
    WHERE RealmId=@RealmId AND Id=@Id;

	COMMIT;
END



