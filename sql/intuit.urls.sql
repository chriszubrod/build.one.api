SELECT [UrlsGUID], [CreatedDatetime], [ModifiedDatetime], [Name], [Slug]
FROM [intuit].[Urls]
ORDER BY [Name];

DECLARE @Now DATETIMEOFFSET = SYSDATETIMEOFFSET();

UPDATE intuit.Urls
SET ModifiedDatetime=@Now, Slug='75'
WHERE Name='minorversion';

DECLARE @Now DATETIMEOFFSET = SYSDATETIMEOFFSET();

INSERT INTO [intuit].[Urls] (CreatedDatetime, ModifiedDatetime, [Name], Slug)
VALUES (@Now, @Now, 'querybill', '/v3/company/{}/query?query={}&minorversion={}');
