CREATE TABLE intuit.AccountBasedExpenseLine
(
	[GUID] UNIQUEIDENTIFIER PRIMARY KEY default NEWID(),
	BillId VARCHAR(MAX) NOT NULL,
	Id VARCHAR(MAX),
	DetailType VARCHAR(MAX),
	Amount DECIMAL(18,2),
	LinkedTxn VARCHAR(MAX),
	[Description] VARCHAR(MAX),
	LineNum INT,
	/* AccountBasedExpenseLineDetail */
	AccountRefValue VARCHAR(MAX),
	TaxAmount DECIMAL(18,2),
	TaxInclusiveAmt DECIMAL(18,2),
	ClassRefValue VARCHAR(MAX),
	TaxCodeRefValue VARCHAR(MAX),
	MarkUpAccountRefValue VARCHAR(MAX),
	BillableStatus VARCHAR(MAX),
	CustomerRefValue VARCHAR(MAX)
);



DROP PROCEDURE IF EXISTS CreateAccountBasedExpenseLine;

CREATE PROCEDURE CreateAccountBasedExpenseLine
	@BillId VARCHAR(MAX),
	@Id VARCHAR(MAX),
	@DetailType VARCHAR(MAX),
	@Amount DECIMAL(18,2),
	@LinkedTxn VARCHAR(MAX),
	@Description VARCHAR(MAX),
	@LineNum INT,
	@AccountRefValue VARCHAR(MAX),
	@TaxAmount DECIMAL(18,2),
	@TaxInclusiveAmt DECIMAL(18,2),
	@ClassRefValue VARCHAR(MAX),
	@TaxCodeRefValue VARCHAR(MAX),
	@MarkUpAccountRefValue VARCHAR(MAX),
	@BillableStatus VARCHAR(MAX),
	@CustomerRefValue VARCHAR(MAX)
AS
BEGIN
	BEGIN TRANSACTION;

    -- Insert a new record
    INSERT INTO intuit.AccountBasedExpenseLine (BillId, Id, DetailType, Amount, LinkedTxn, [Description], LineNum, AccountRefValue, TaxAmount, TaxInclusiveAmt, ClassRefValue, TaxCodeRefValue, MarkUpAccountRefValue, BillableStatus, CustomerRefValue)
    VALUES (@BillId, @Id, @DetailType, @Amount, @LinkedTxn, @Description, @LineNum, @AccountRefValue, @TaxAmount, @TaxInclusiveAmt, @ClassRefValue, @TaxCodeRefValue, @MarkUpAccountRefValue, @BillableStatus, @CustomerRefValue);

    COMMIT;
END







DROP PROCEDURE IF EXISTS ReadAccountBasedExpenseLines;

CREATE PROCEDURE ReadAccountBasedExpenseLines
AS
BEGIN
	BEGIN TRANSACTION;
	
	SELECT 
		[GUID],
		[BillId],
		[Id],
		[DetailType],
		[Amount],
		[LinkedTxn],
		[Description],
		[LineNum],
		[AccountRefValue],
		[TaxAmount],
		[TaxInclusiveAmt],
		[ClassRefValue],
		[TaxCodeRefValue],
		[MarkUpAccountRefValue],
		[BillableStatus],
		[CustomerRefValue]
	FROM intuit.AccountBasedExpenseLine
	ORDER BY [LineNum] DESC;

	COMMIT;
END










DROP PROCEDURE IF EXISTS ReadAccountBasedExpenseLineById;


CREATE PROCEDURE ReadAccountBasedExpenseLineById
	@Id VARCHAR(MAX)
AS
BEGIN

	BEGIN TRANSACTION;

    SELECT
		[GUID],
		[BillId],
		[Id],
		[DetailType],
		[Amount],
		[LinkedTxn],
		[Description],
		[LineNum],
		[AccountRefValue],
		[TaxAmount],
		[TaxInclusiveAmt],
		[ClassRefValue],
		[TaxCodeRefValue],
		[MarkUpAccountRefValue],
		[BillableStatus],
		[CustomerRefValue]
    FROM intuit.AccountBasedExpenseLine
    WHERE [Id] = @Id;

	COMMIT;
END








DROP PROCEDURE IF EXISTS UpdateAccountBasedExpenseLineById;


CREATE PROCEDURE UpdateAccountBasedExpenseLineById
	@Id VARCHAR(MAX),
	@DetailType VARCHAR(MAX),
	@Amount DECIMAL(18,2),
	@LinkedTxn VARCHAR(MAX),
	@Description VARCHAR(MAX),
	@LineNum INT,
	@AccountRefValue VARCHAR(MAX),
	@TaxAmount DECIMAL(18,2),
	@TaxInclusiveAmt DECIMAL(18,2),
	@ClassRefValue VARCHAR(MAX),
	@TaxCodeRefValue VARCHAR(MAX),
	@MarkUpAccountRefValue VARCHAR(MAX),
	@BillableStatus VARCHAR(MAX),
	@CustomerRefValue VARCHAR(MAX)
AS
BEGIN
	BEGIN TRANSACTION;

    UPDATE intuit.AccountBasedExpenseLine
    SET
		DetailType=@DetailType,
		Amount=@Amount,
		LinkedTxn=@LinkedTxn,
		[Description]=@Description,
		LineNum=@LineNum,		
		AccountRefValue=@AccountRefValue,
		TaxAmount=@TaxAmount,
		TaxInclusiveAmt=@TaxInclusiveAmt,
		ClassRefValue=@ClassRefValue,
		TaxCodeRefValue=@TaxCodeRefValue,
		MarkUpAccountRefValue=@MarkUpAccountRefValue,
		BillableStatus=@BillableStatus,
		CustomerRefValue=@CustomerRefValue
    WHERE Id=@Id;

	COMMIT;
END



