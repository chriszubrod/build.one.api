CREATE TABLE AttachmentDetail (
    [Id] INT IDENTITY(1,1) PRIMARY KEY,
    [GUID] UNIQUEIDENTIFIER DEFAULT NEWID() NOT NULL,
    [CreatedDatetime] DATETIMEOFFSET NOT NULL,
    [ModifiedDatetime] DATETIMEOFFSET NOT NULL,
    [AttachmentId] INT NOT NULL,
    [PageCount] INT NULL,
    [ImageHeight] INT NULL,
    [ImageWidth] INT NULL,
    [ImageSize] INT NULL,
    [ImageOrientation] VARCHAR(50) NULL,
    [AspectRatio] FLOAT NULL,
    [Brightness] FLOAT NULL,
    [Contrast] FLOAT NULL,
    [Blur] FLOAT NULL,
    [ColorDepth] INT NULL,
    [EdgeDensity] FLOAT NULL,
    [HasLines] BIT NULL,
    [IsColor] BIT NULL,
    [IsGrayscale] BIT NULL,
    [OCRText] VARCHAR(MAX) NULL,
    [OCRTextLength] INT NULL,
    [OCRTextCount] INT NULL,
    [OCRLineCount] INT NULL,
    [OCRAverageWordsPerLine] FLOAT NULL,
    [OCRAverageCharactersPerWord] FLOAT NULL,
    [OCRMaxLineLength] INT NULL,
    FOREIGN KEY (AttachmentId) REFERENCES [Attachment](Id)
);

DROP TABLE IF EXISTS AttachmentDetail;

ALTER TABLE AttachmentDetail
DROP COLUMN [WrOfPages];

ALTER TABLE AttachmentDetail
ADD [FileContent] VARBINARY(MAX) NULL;


SELECT * FROM [Transaction];
SELECT * FROM Attachment;
SELECT * FROM AttachmentDetail;

DROP PROCEDURE CreateAttachmentDetail;

CREATE PROCEDURE CreateAttachmentDetail
    @CreatedDatetime DATETIMEOFFSET,
    @ModifiedDatetime DATETIMEOFFSET,
    @AttachmentId INT,
    @PageCount INT,
    @ImageHeight INT,
    @ImageWidth INT,
    @ImageSize INT,
    @ImageOrientation VARCHAR(50),
    @AspectRatio FLOAT,
    @Brightness FLOAT,
    @Contrast FLOAT,
    @Blur FLOAT,
    @ColorDepth INT,
    @EdgeDensity FLOAT,
    @HasLines BIT,
    @IsColor BIT,
    @IsGrayscale BIT,
    @OCRText VARCHAR(MAX),
    @OCRTextLength INT NULL,
    @OCRTextCount INT NULL,
    @OCRLineCount INT NULL,
    @OCRAverageWordsPerLine FLOAT NULL,
    @OCRAverageCharactersPerWord FLOAT NULL,
    @OCRMaxLineLength INT NULL,
AS
BEGIN
    BEGIN TRANSACTION;

    -- Insert a new record into the Attachment Detail table
    INSERT INTO AttachmentDetail (
        [CreatedDatetime],
        [ModifiedDatetime],
        [AttachmentId],
        [PageCount],
        [ImageHeight],
        [ImageWidth],
        [ImageSize],
        [ImageOrientation],
        [AspectRatio],
        [Brightness],
        [Contrast],
        [Blur],
        [ColorDepth],
        [EdgeDensity],
        [HasLines],
        [IsColor],
        [IsGrayscale],
        [OCRText],
        [OCRTextLength],
        [OCRTextCount],
        [OCRLineCount],
        [OCRAverageWordsPerLine],
        [OCRAverageCharactersPerWord],
        [OCRMaxLineLength]
    )
    VALUES (
        CONVERT(DATETIMEOFFSET, @CreatedDatetime),
        CONVERT(DATETIMEOFFSET, @ModifiedDatetime),
        @AttachmentId,
        @PageCount,
        @ImageHeight,
        @ImageWidth,
        @ImageSize,
        @ImageOrientation,
        @AspectRatio,
        @Brightness,
        @Contrast,
        @Blur,
        @ColorDepth,
        @EdgeDensity,
        @HasLines,
        @IsColor,
        @IsGrayscale,
        @OCRText,
        @OCRTextLength,
        @OCRTextCount,
        @OCRLineCount,
        @OCRAverageWordsPerLine,
        @OCRAverageCharactersPerWord,
        @OCRMaxLineLength
    );

    COMMIT;
END

