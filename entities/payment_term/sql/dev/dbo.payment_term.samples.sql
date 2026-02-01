-- DEV/TEST ONLY: sample calls removed from production migration.
-- Source: services/payment_term/sql/dbo.payment_term.sql
-- Run manually in non-production environments.

EXEC CreatePaymentTerm
    @Name = '2/10 Net 30',
    @Description = '2% discount if paid within 10 days, otherwise due in 30 days.',
    @DiscountPercent = 2.00,
    @DiscountDays = 10,
    @DueDays = 30;
GO

EXEC CreatePaymentTerm
    @Name = 'Net 30',
    @Description = 'Payment due within 30 days.',
    @DiscountPercent = NULL,
    @DiscountDays = NULL,
    @DueDays = 30;
GO

EXEC ReadPaymentTerms;
GO

EXEC ReadPaymentTermById
    @Id = 1;
GO

EXEC ReadPaymentTermByPublicId
    @PublicId = '00000000-0000-0000-0000-000000000000';
GO

EXEC ReadPaymentTermByName
    @Name = 'Net 30';
GO

EXEC UpdatePaymentTermById
    @Id = 1,
    @RowVersion = 0x0000000000000000,
    @Name = 'Net 45',
    @Description = 'Payment due within 45 days.',
    @DiscountPercent = NULL,
    @DiscountDays = NULL,
    @DueDays = 45;
GO

EXEC DeletePaymentTermById
    @Id = 1;
GO
