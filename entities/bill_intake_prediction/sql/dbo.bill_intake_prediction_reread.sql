-- Contract: dbo.BillIntakePrediction
-- Owns: CK_BillIntakePrediction_IntakeSource — allow intake_source value 'reread' (U-704).
-- Invariants: same as dbo.bill_intake_prediction.sql; this file only widens IntakeSource CHECK.
-- Sprocs:
--
-- Run: ./.venv/bin/python scripts/run_sql.py entities/bill_intake_prediction/sql/dbo.bill_intake_prediction_reread.sql

GO

IF OBJECT_ID('dbo.BillIntakePrediction', 'U') IS NOT NULL
BEGIN
    IF EXISTS (
        SELECT 1
        FROM sys.check_constraints
        WHERE name = N'CK_BillIntakePrediction_IntakeSource'
          AND parent_object_id = OBJECT_ID(N'dbo.BillIntakePrediction')
          AND definition NOT LIKE N'%reread%'
    )
    BEGIN
        ALTER TABLE dbo.[BillIntakePrediction]
            DROP CONSTRAINT [CK_BillIntakePrediction_IntakeSource];

        ALTER TABLE dbo.[BillIntakePrediction]
            ADD CONSTRAINT [CK_BillIntakePrediction_IntakeSource]
                CHECK ([IntakeSource] IN ('pdf_drop','pdf_upload','list_drop','reread'));
    END
END
GO
