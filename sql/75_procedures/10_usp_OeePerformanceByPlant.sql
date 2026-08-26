-- Hand-written. Fixed OEE rollup: plant x calendar month.
--
-- These procedures take no parameters on purpose. The loader discovers and runs every
-- parameterless procedure in rpt on each analytics cycle, and a fixed shape is what makes
-- run-to-run timings comparable. Windows are anchored on GETDATE() so they always overlap
-- freshly loaded data.
--
-- dim.Plant is SCD2 and is deliberately NOT filtered to IsCurrent = 1: each fact row
-- points at the dimension version in effect when it happened, so filtering here would
-- silently drop history rather than restate it.
CREATE OR ALTER PROCEDURE rpt.usp_OeePerformanceByPlant
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        p.PlantCode,
        p.PlantName,
        d.CalendarYearNumber,
        d.CalendarMonthNumber,
        MAX(d.CalendarMonthShortName)                AS CalendarMonthName,
        COUNT_BIG(*)                                 AS SnapshotCount,
        COUNT(DISTINCT f.MachineKey)                 AS DistinctMachineCount,
        COUNT(DISTINCT f.ProductKey)                 AS DistinctProductCount,
        SUM(CAST(f.EventCount AS BIGINT))            AS TotalEventCount,
        SUM(f.QuantityAmount)                        AS TotalQuantityAmount,
        SUM(f.DurationMinutes)                       AS TotalDurationMinutes,
        AVG(f.DurationMinutes)                       AS AvgDurationMinutes,
        SUM(f.CostAmount)                            AS TotalCostAmount
    FROM fact.OeePeriodicSnapshot AS f
    INNER JOIN dim.Date  AS d ON d.DateKey  = f.SnapshotDateKey
    INNER JOIN dim.Plant AS p ON p.PlantKey = f.PlantKey
    WHERE d.FullDate >= DATEADD(MONTH, -24, CAST(GETDATE() AS DATE))
    GROUP BY
        p.PlantCode,
        p.PlantName,
        d.CalendarYearNumber,
        d.CalendarMonthNumber
    ORDER BY
        d.CalendarYearNumber DESC,
        d.CalendarMonthNumber DESC,
        p.PlantCode;
END
GO
