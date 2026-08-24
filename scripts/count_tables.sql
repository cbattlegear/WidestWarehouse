SET NOCOUNT ON;

DECLARE @RequiredTables int = 700;
DECLARE @ActualTables int;

;WITH schema_counts AS
(
    SELECT
        expected.name AS SchemaName,
        COUNT(t.object_id) AS TableCount
    FROM (VALUES ('ref'), ('dim'), ('bridge'), ('fact'), ('stg'), ('etl')) AS expected(name)
    LEFT JOIN sys.schemas AS s
        ON s.name = expected.name
    LEFT JOIN sys.tables AS t
        ON t.schema_id = s.schema_id
       AND t.is_ms_shipped = 0
    GROUP BY expected.name
)
SELECT
    SchemaName,
    TableCount
FROM schema_counts
ORDER BY CASE SchemaName
    WHEN 'ref' THEN 1
    WHEN 'dim' THEN 2
    WHEN 'bridge' THEN 3
    WHEN 'fact' THEN 4
    WHEN 'stg' THEN 5
    WHEN 'etl' THEN 6
    ELSE 7
END;

SELECT @ActualTables = COUNT(*)
FROM sys.tables AS t
JOIN sys.schemas AS s
    ON s.schema_id = t.schema_id
WHERE t.is_ms_shipped = 0
  AND s.name IN ('ref', 'dim', 'bridge', 'fact', 'stg', 'etl');

PRINT CONCAT('Total base table count: ', @ActualTables);

IF @ActualTables < @RequiredTables
BEGIN
    RAISERROR('WidestWarehouse table-count assertion failed: actual %d base tables, required at least %d.', 16, 1, @ActualTables, @RequiredTables);
END
ELSE
BEGIN
    PRINT CONCAT('WidestWarehouse table-count assertion passed: ', @ActualTables, ' base tables.');
END;
