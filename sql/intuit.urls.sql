SELECT [UrlsGUID], [CreatedDatetime], [ModifiedDatetime], [Name], [Slug]
FROM [intuit].[Urls]
ORDER BY [Name];

UPDATE intuit.Urls
SET Slug='69'
WHERE Name='minorversion';

INSERT INTO [intuit].[Urls] (CreatedDatetime, ModifiedDatetime, [Name], Slug)
VALUES ('2024-11-24 00:00:00.000', '2024-11-24 00:00:00.000', 'createbill', '/v3/company/{}/bill');