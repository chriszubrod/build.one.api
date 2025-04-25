SELECT [DataSyncGUID], [DataSourceName], [LastUpdateDatetime]
FROM [intuit].[DataSync]
ORDER BY [DataSourceName];

INSERT INTO [intuit].[DataSync] ([DataSourceName], [LastUpdateDatetime])
VALUES ('vendor', '1900-01-01 00:00:00.000');

UPDATE intuit.DataSync
SET LastUpdateDatetime='1900-01-01 00:00:00.000'
WHERE DataSourceName='item';

DELETE FROM intuit.DataSync
WHERE DataSyncGUID='EA4221C7-9DCA-4617-82E4-80BB5135338F';

DROP TABLE intuit.DataSync;

CREATE TABLE intuit.DataSync
(
	DataSyncGUID UNIQUEIDENTIFIER PRIMARY KEY default NEWID(),
	DataSourceName VARCHAR(MAX) NOT NULL,
	LastUpdateDatetime DATETIMEOFFSET NOT NULL
);

SELECT DataSyncGUID, DataSourceName, CONVERT(datetime2, LastUpdateDatetime) AS LastUpdateDatetime
FROM intuit.DataSync
WHERE DataSourceName='vendor';


CREATE PROCEDURE ReadDataSyncByDataSourceName
	@DataSourceName VARCHAR(MAX)
AS
BEGIN
	SELECT DataSyncGUID, DataSourceName, CONVERT(datetime2, LastUpdateDatetime) AS LastUpdateDatetime
	FROM intuit.DataSync
    WHERE DataSourceName=@DataSourceName;
END;
