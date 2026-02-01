-- DEV/TEST ONLY: sample calls removed from production migration.
-- Source: services/project/sql/dbo.project.sql
-- Run manually in non-production environments.

SELECT * FROM dbo.Project;

EXEC CreateProject
    @Name = 'Sample Project',
    @Description = 'This is a sample project description',
    @Status = 'Active',
    @CustomerId = NULL;
GO

EXEC ReadProjects;
GO

EXEC ReadProjectById
    @Id = '00000000-0000-0000-0000-000000000000';
GO

EXEC ReadProjectByPublicId
    @PublicId = '00000000-0000-0000-0000-000000000000';
GO

EXEC ReadProjectByName
    @Name = 'Sample Project';
GO

EXEC UpdateProjectById
    @Id = 2,
    @RowVersion = 0x0000000000020B74,
    @Name = 'Updated Project',
    @Description = 'This is an updated project description',
    @Status = 'In Progress',
    @CustomerId = NULL;
GO

EXEC DeleteProjectById
    @Id = 3;
GO
