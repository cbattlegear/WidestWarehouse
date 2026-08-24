SET NOCOUNT ON;

DECLARE @Failures int = 0;

PRINT 'Check: no orphaned or untrusted foreign keys';
IF EXISTS
(
    SELECT 1
    FROM sys.foreign_keys AS fk
    LEFT JOIN sys.tables AS parent_table
        ON parent_table.object_id = fk.parent_object_id
    LEFT JOIN sys.tables AS referenced_table
        ON referenced_table.object_id = fk.referenced_object_id
    WHERE parent_table.object_id IS NULL
       OR referenced_table.object_id IS NULL
       OR fk.is_not_trusted <> 0
)
BEGIN
    SET @Failures += 1;
    PRINT 'FAIL: orphaned or untrusted foreign keys were found.';
    SELECT
        OBJECT_SCHEMA_NAME(fk.parent_object_id) AS ParentSchema,
        OBJECT_NAME(fk.parent_object_id) AS ParentTable,
        fk.name AS ForeignKeyName,
        OBJECT_SCHEMA_NAME(fk.referenced_object_id) AS ReferencedSchema,
        OBJECT_NAME(fk.referenced_object_id) AS ReferencedTable,
        fk.is_not_trusted
    FROM sys.foreign_keys AS fk
    LEFT JOIN sys.tables AS parent_table
        ON parent_table.object_id = fk.parent_object_id
    LEFT JOIN sys.tables AS referenced_table
        ON referenced_table.object_id = fk.referenced_object_id
    WHERE parent_table.object_id IS NULL
       OR referenced_table.object_id IS NULL
       OR fk.is_not_trusted <> 0
    ORDER BY ParentSchema, ParentTable, ForeignKeyName;
END
ELSE
BEGIN
    PRINT 'PASS: all foreign keys reference existing tables and are trusted.';
END;

PRINT 'Check: every ref, dim, and bridge table has a primary key';
IF EXISTS
(
    SELECT 1
    FROM sys.tables AS t
    JOIN sys.schemas AS s
        ON s.schema_id = t.schema_id
    WHERE s.name IN ('ref', 'dim', 'bridge')
      AND NOT EXISTS
      (
          SELECT 1
          FROM sys.indexes AS i
          WHERE i.object_id = t.object_id
            AND i.is_primary_key = 1
      )
)
BEGIN
    SET @Failures += 1;
    PRINT 'FAIL: tables without primary keys were found.';
    SELECT s.name AS SchemaName, t.name AS TableName
    FROM sys.tables AS t
    JOIN sys.schemas AS s
        ON s.schema_id = t.schema_id
    WHERE s.name IN ('ref', 'dim', 'bridge')
      AND NOT EXISTS
      (
          SELECT 1
          FROM sys.indexes AS i
          WHERE i.object_id = t.object_id
            AND i.is_primary_key = 1
      )
    ORDER BY s.name, t.name;
END
ELSE
BEGIN
    PRINT 'PASS: every ref, dim, and bridge table has a primary key.';
END;

PRINT 'Check: every dim table has an Unknown member row with key -1';
CREATE TABLE #MissingDimUnknown
(
    SchemaName sysname NOT NULL,
    TableName sysname NOT NULL,
    KeyColumn sysname NOT NULL
);

DECLARE @SchemaName sysname;
DECLARE @TableName sysname;
DECLARE @KeyColumn sysname;
DECLARE @Sql nvarchar(max);

DECLARE dim_unknown_cursor CURSOR LOCAL FAST_FORWARD FOR
SELECT
    s.name,
    t.name,
    c.name
FROM sys.tables AS t
JOIN sys.schemas AS s
    ON s.schema_id = t.schema_id
JOIN sys.indexes AS i
    ON i.object_id = t.object_id
   AND i.is_primary_key = 1
JOIN sys.index_columns AS ic
    ON ic.object_id = i.object_id
   AND ic.index_id = i.index_id
   AND ic.key_ordinal = 1
JOIN sys.columns AS c
    ON c.object_id = ic.object_id
   AND c.column_id = ic.column_id
WHERE s.name = 'dim'
  AND TYPE_NAME(c.user_type_id) IN ('int', 'bigint', 'smallint', 'tinyint');

OPEN dim_unknown_cursor;
FETCH NEXT FROM dim_unknown_cursor INTO @SchemaName, @TableName, @KeyColumn;

WHILE @@FETCH_STATUS = 0
BEGIN
    SET @Sql = N'IF NOT EXISTS (SELECT 1 FROM '
        + QUOTENAME(@SchemaName) + N'.' + QUOTENAME(@TableName)
        + N' WHERE ' + QUOTENAME(@KeyColumn) + N' = -1) '
        + N'INSERT INTO #MissingDimUnknown (SchemaName, TableName, KeyColumn) VALUES (@p_schema, @p_table, @p_column);';

    EXEC sys.sp_executesql
        @Sql,
        N'@p_schema sysname, @p_table sysname, @p_column sysname',
        @p_schema = @SchemaName,
        @p_table = @TableName,
        @p_column = @KeyColumn;

    FETCH NEXT FROM dim_unknown_cursor INTO @SchemaName, @TableName, @KeyColumn;
END;

CLOSE dim_unknown_cursor;
DEALLOCATE dim_unknown_cursor;

IF EXISTS (SELECT 1 FROM #MissingDimUnknown)
BEGIN
    SET @Failures += 1;
    PRINT 'FAIL: dim tables missing Unknown member rows were found.';
    SELECT SchemaName, TableName, KeyColumn
    FROM #MissingDimUnknown
    ORDER BY SchemaName, TableName;
END
ELSE
BEGIN
    PRINT 'PASS: every dim table has an Unknown member row with key -1.';
END;

PRINT 'Check: every fact FK column defaults to -1 (SPEC 3.6 permits nullable: true)';
;WITH fact_fk_columns AS
(
    SELECT DISTINCT
        s.name AS SchemaName,
        t.name AS TableName,
        c.name AS ColumnName,
        c.is_nullable,
        dc.definition AS DefaultDefinition
    FROM sys.foreign_key_columns AS fkc
    JOIN sys.tables AS t
        ON t.object_id = fkc.parent_object_id
    JOIN sys.schemas AS s
        ON s.schema_id = t.schema_id
    JOIN sys.columns AS c
        ON c.object_id = fkc.parent_object_id
       AND c.column_id = fkc.parent_column_id
    LEFT JOIN sys.default_constraints AS dc
        ON dc.parent_object_id = c.object_id
       AND dc.parent_column_id = c.column_id
    WHERE s.name = 'fact'
)
SELECT *
INTO #FactFkExceptions
FROM fact_fk_columns
WHERE DefaultDefinition IS NULL
   OR DefaultDefinition NOT LIKE '%-1%';

IF EXISTS (SELECT 1 FROM #FactFkExceptions)
BEGIN
    SET @Failures += 1;
    PRINT 'FAIL: fact FK columns without a -1 default were found.';
    SELECT SchemaName, TableName, ColumnName, is_nullable, DefaultDefinition
    FROM #FactFkExceptions
    ORDER BY SchemaName, TableName, ColumnName;
END
ELSE
BEGIN
    PRINT 'PASS: every fact FK column defaults to -1.';
END;

PRINT 'Check: maximum dim snowflake depth';
;WITH dim_edges AS
(
    SELECT
        fk.parent_object_id AS ChildObjectId,
        fk.referenced_object_id AS ParentObjectId
    FROM sys.foreign_keys AS fk
    JOIN sys.tables AS child_table
        ON child_table.object_id = fk.parent_object_id
    JOIN sys.schemas AS child_schema
        ON child_schema.schema_id = child_table.schema_id
       AND child_schema.name = 'dim'
    JOIN sys.tables AS parent_table
        ON parent_table.object_id = fk.referenced_object_id
    JOIN sys.schemas AS parent_schema
        ON parent_schema.schema_id = parent_table.schema_id
       AND parent_schema.name = 'dim'
    WHERE fk.parent_object_id <> fk.referenced_object_id
),
chains AS
(
    SELECT
        t.object_id AS RootObjectId,
        t.object_id AS CurrentObjectId,
        CAST(0 AS int) AS Depth,
        CAST('|' + CAST(t.object_id AS varchar(20)) + '|' AS varchar(max)) AS Path
    FROM sys.tables AS t
    JOIN sys.schemas AS s
        ON s.schema_id = t.schema_id
       AND s.name = 'dim'

    UNION ALL

    SELECT
        chains.RootObjectId,
        dim_edges.ParentObjectId,
        chains.Depth + 1,
        CAST(chains.Path + CAST(dim_edges.ParentObjectId AS varchar(20)) + '|' AS varchar(max))
    FROM chains
    JOIN dim_edges
        ON dim_edges.ChildObjectId = chains.CurrentObjectId
    WHERE chains.Path NOT LIKE '%|' + CAST(dim_edges.ParentObjectId AS varchar(20)) + '|%'
      AND chains.Depth < 100
),
ranked_chains AS
(
    SELECT TOP (1)
        OBJECT_SCHEMA_NAME(RootObjectId) AS RootSchema,
        OBJECT_NAME(RootObjectId) AS RootTable,
        Depth,
        Path
    FROM chains
    ORDER BY Depth DESC, RootTable
)
SELECT
    RootSchema,
    RootTable,
    Depth AS MaxSnowflakeDepth
FROM ranked_chains
OPTION (MAXRECURSION 32767);

PRINT 'Check: tables with zero rows after seeding';
;WITH row_counts AS
(
    SELECT
        s.name AS SchemaName,
        t.name AS TableName,
        SUM(CASE WHEN p.index_id IN (0, 1) THEN p.row_count ELSE 0 END) AS TableRowCount
    FROM sys.tables AS t
    JOIN sys.schemas AS s
        ON s.schema_id = t.schema_id
    LEFT JOIN sys.dm_db_partition_stats AS p
        ON p.object_id = t.object_id
    WHERE t.is_ms_shipped = 0
      AND s.name IN ('ref', 'dim', 'bridge', 'fact', 'stg', 'etl')
    GROUP BY s.name, t.name
)
SELECT SchemaName, TableName, TableRowCount
FROM row_counts
WHERE TableRowCount = 0
  AND SchemaName <> 'stg'
ORDER BY SchemaName, TableName;

IF @Failures > 0
BEGIN
    RAISERROR('WidestWarehouse schema verification failed: %d check(s) failed.', 16, 1, @Failures);
END
ELSE
BEGIN
    PRINT 'WidestWarehouse schema verification passed.';
END;
