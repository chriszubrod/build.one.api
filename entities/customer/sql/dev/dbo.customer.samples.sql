-- DEV/TEST ONLY: sample calls removed from production migration.
-- Source: services/customer/sql/dbo.customer.sql
-- Run manually in non-production environments.

EXEC CreateCustomer
    @Name = 'John Doe',
    @Email = 'john.doe@example.com',
    @Phone = '555-1234';
GO

EXEC ReadCustomers;
GO

EXEC ReadCustomerById
    @Id = '00000000-0000-0000-0000-000000000000';
GO

EXEC ReadCustomerByPublicId
    @PublicId = '00000000-0000-0000-0000-000000000000';
GO

EXEC ReadCustomerByName
    @Name = 'John Doe';
GO

EXEC UpdateCustomerById
    @Id = 2,
    @RowVersion = 0x0000000000020B74,
    @Name = 'Jane Doe',
    @Email = 'jane.doe@example.com',
    @Phone = '555-5678';
GO

EXEC DeleteCustomerById
    @Id = 3;
GO
