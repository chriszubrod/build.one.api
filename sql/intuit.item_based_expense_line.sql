CREATE TABLE intuit.ItemBasedExpenseLine
(
	[GUID] UNIQUEIDENTIFIER PRIMARY KEY default NEWID(),
	BillId VARCHAR(MAX) NOT NULL,
	Id VARCHAR(MAX),
	Amount DECIMAL(18,2),
	DetailType VARCHAR(MAX),
	LinkedTxn VARCHAR(MAX),
	[Description] VARCHAR(MAX),
	LineNum INT,
	/* ItemBasedExpenseLineDetail */
	TaxInclusiveAmt DECIMAL(18,2),
	ItemRefValue VARCHAR(MAX),
	CustomerRefValue VARCHAR(MAX),
	PriceLevelRefValue VARCHAR(MAX),
	ClassRefValue VARCHAR(MAX),
	TaxCodeRefValue VARCHAR(MAX),
	[Percent] DECIMAL(18,2),
	MarkUpIncomeAccountRefValue VARCHAR(MAX),
	BillableStatus VARCHAR(MAX),
	Qty DECIMAL(18,2),
	UnitPrice DECIMAL(18,2)
);



DROP PROCEDURE IF EXISTS CreateItemBasedExpenseLine;

CREATE PROCEDURE CreateItemBasedExpenseLine
	@BillId VARCHAR(MAX),
	@Id VARCHAR(MAX),
	@Amount DECIMAL(18,2),
	@DetailType VARCHAR(MAX),
	@LinkedTxn VARCHAR(MAX),
	@Description VARCHAR(MAX),
	@LineNum INT,
	@TaxInclusiveAmt DECIMAL(18,2),
	@ItemRefValue VARCHAR(MAX),
	@CustomerRefValue VARCHAR(MAX),
	@PriceLevelRefValue VARCHAR(MAX),
	@ClassRefValue VARCHAR(MAX),
	@TaxCodeRefValue VARCHAR(MAX),
	@Percent DECIMAL(18,2),
	@MarkUpIncomeAccountRefValue VARCHAR(MAX),
	@BillableStatus VARCHAR(MAX),
	@Qty DECIMAL(18,2),
	@UnitPrice DECIMAL(18,2)
AS
BEGIN
	BEGIN TRANSACTION;

    -- Insert a new record
    INSERT INTO intuit.ItemBasedExpenseLine (BillId, Id, Amount, DetailType, LinkedTxn, [Description], LineNum, TaxInclusiveAmt, ItemRefValue, CustomerRefValue, PriceLevelRefValue, ClassRefValue, TaxCodeRefValue, [Percent], MarkUpIncomeAccountRefValue, BillableStatus, Qty, UnitPrice)
    VALUES (@BillId, @Id, @Amount, @DetailType, @LinkedTxn, @Description, @LineNum, @TaxInclusiveAmt, @ItemRefValue, @CustomerRefValue, @PriceLevelRefValue, @ClassRefValue, @TaxCodeRefValue, @Percent, @MarkUpIncomeAccountRefValue, @BillableStatus, @Qty, @UnitPrice);

    COMMIT;
END







DROP PROCEDURE IF EXISTS ReadItemBasedExpenseLines;

CREATE PROCEDURE ReadItemBasedExpenseLines
AS
BEGIN
	BEGIN TRANSACTION;
	
	SELECT
		[GUID],
		[BillId],
		[Id],
		[Amount],
		[DetailType],
		[LinkedTxn],
		[Description],
		[LineNum],
		[TaxInclusiveAmt],
		[ItemRefValue],
		[CustomerRefValue],
		[PriceLevelRefValue],
		[ClassRefValue],
		[TaxCodeRefValue],
		[Percent],
		[MarkUpIncomeAccountRefValue],
		[BillableStatus],
		[Qty],
		[UnitPrice]
	FROM intuit.ItemBasedExpenseLine
	ORDER BY [LineNum] DESC;

	COMMIT;
END










DROP PROCEDURE IF EXISTS ReadItemBasedExpenseLineById;


CREATE PROCEDURE ReadItemBasedExpenseLineById
    @Id VARCHAR(MAX)
AS
BEGIN

	BEGIN TRANSACTION;

    SELECT 
		[GUID],
		[BillId],
		[Id],
		[Amount],
		[DetailType],
		[LinkedTxn],
		[Description],
		[LineNum],
		[TaxInclusiveAmt],
		[ItemRefValue],
		[CustomerRefValue],
		[PriceLevelRefValue],
		[ClassRefValue],
		[TaxCodeRefValue],
		[Percent],
		[MarkUpIncomeAccountRefValue],
		[BillableStatus],
		[Qty],
		[UnitPrice]
    FROM intuit.ItemBasedExpenseLine
    WHERE [Id] = @Id;

	COMMIT;
END








DROP PROCEDURE IF EXISTS UpdateItemBasedExpenseLineById;


CREATE PROCEDURE UpdateItemBasedExpenseLineById
	@Id VARCHAR(MAX),
	@Amount DECIMAL(18,2),
	@DetailType VARCHAR(MAX),
	@LinkedTxn VARCHAR(MAX),
	@Description VARCHAR(MAX),
	@LineNum INT,
	@TaxInclusiveAmt DECIMAL(18,2),
	@ItemRefValue VARCHAR(MAX),
	@CustomerRefValue VARCHAR(MAX),
	@PriceLevelRefValue VARCHAR(MAX),
	@ClassRefValue VARCHAR(MAX),
	@TaxCodeRefValue VARCHAR(MAX),
	@Percent DECIMAL(18,2),
	@MarkUpIncomeAccountRefValue VARCHAR(MAX),
	@BillableStatus VARCHAR(MAX),
	@Qty DECIMAL(18,2),
	@UnitPrice DECIMAL(18,2)
AS
BEGIN
	BEGIN TRANSACTION;

    UPDATE intuit.ItemBasedExpenseLine
    SET
		Amount=@Amount,
		DetailType=@DetailType,
		LinkedTxn=@LinkedTxn,
		[Description]=@Description,
		LineNum=@LineNum,
		TaxInclusiveAmt=@TaxInclusiveAmt,
		ItemRefValue=@ItemRefValue,
		CustomerRefValue=@CustomerRefValue,
		PriceLevelRefValue=@PriceLevelRefValue,
		ClassRefValue=@ClassRefValue,
		TaxCodeRefValue=@TaxCodeRefValue,
		[Percent]=@Percent,
		MarkUpIncomeAccountRefValue=@MarkUpIncomeAccountRefValue,
		BillableStatus=@BillableStatus,
		Qty=@Qty,
		UnitPrice=@UnitPrice
    WHERE Id=@Id;

	COMMIT;
END



