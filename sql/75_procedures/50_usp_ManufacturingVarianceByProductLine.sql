-- Hand-written. Manufacturing cost variance rolled up the full product snowflake.
--
-- This is the deep one: fact -> Product -> Subfamily -> Family -> Line -> Division is five
-- joins up a single branch, which is the shape this warehouse exists to demonstrate. It is
-- also the most expensive procedure in the fixed set, so it is a useful canary for plan
-- regressions after an index or statistics change.
CREATE OR ALTER PROCEDURE rpt.usp_ManufacturingVarianceByProductLine
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        pdv.ProductDivisionCode,
        pdv.ProductDivisionName,
        pl.ProductLineCode,
        pl.ProductLineName,
        vt.VarianceTypeCode,
        vt.VarianceTypeName,
        d.CalendarYearNumber,
        d.CalendarQuarterNumber,
        COUNT_BIG(*)                    AS PostingCount,
        COUNT(DISTINCT f.ProductKey)    AS DistinctProductCount,
        COUNT(DISTINCT f.PlantKey)      AS DistinctPlantCount,
        SUM(f.StandardAmount)           AS StandardAmount,
        SUM(f.ActualAmount)             AS ActualAmount,
        SUM(f.VarianceAmount)           AS VarianceAmount,
        CAST(100.0 * SUM(f.VarianceAmount) / NULLIF(SUM(f.StandardAmount), 0)
             AS DECIMAL(18, 4))         AS VariancePercentOfStandard
    FROM fact.FinanceManufacturingVariance AS f
    INNER JOIN dim.Date             AS d   ON d.DateKey              = f.PostingDateKey
    INNER JOIN dim.Product          AS pr  ON pr.ProductKey          = f.ProductKey
    INNER JOIN dim.ProductSubfamily AS ps  ON ps.ProductSubfamilyKey = pr.ProductSubfamilyKey
    INNER JOIN dim.ProductFamily    AS pf  ON pf.ProductFamilyKey    = ps.ProductFamilyKey
    INNER JOIN dim.ProductLine      AS pl  ON pl.ProductLineKey      = pf.ProductLineKey
    INNER JOIN dim.ProductDivision  AS pdv ON pdv.ProductDivisionKey = pl.ProductDivisionKey
    INNER JOIN ref.FinanceVarianceType AS vt ON vt.FinanceVarianceTypeKey = f.FinanceVarianceTypeKey
    WHERE d.FullDate >= DATEADD(MONTH, -18, CAST(GETDATE() AS DATE))
      AND vt.IsManufacturingVariance = 1
    GROUP BY
        pdv.ProductDivisionCode,
        pdv.ProductDivisionName,
        pl.ProductLineCode,
        pl.ProductLineName,
        vt.VarianceTypeCode,
        vt.VarianceTypeName,
        d.CalendarYearNumber,
        d.CalendarQuarterNumber
    ORDER BY
        ABS(SUM(f.VarianceAmount)) DESC,
        pdv.ProductDivisionCode,
        pl.ProductLineCode;
END
GO
