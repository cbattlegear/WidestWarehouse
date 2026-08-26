-- Hand-written. Production throughput by shift and work center.
--
-- Joins the time-of-day dimension as well as the date dimension, so the fixed workload
-- covers the role-played date/time keys that the rest of these procedures do not touch.
CREATE OR ALTER PROCEDURE rpt.usp_ProductionThroughputByShift
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        wc.WorkCenterCode,
        wc.WorkCenterName,
        sh.ProductionShiftCode,
        sh.ProductionShiftName,
        d.CalendarYearNumber,
        d.CalendarMonthNumber,
        d.IsWeekend,
        COUNT_BIG(*)                                 AS ConfirmationCount,
        COUNT(DISTINCT f.ProductionWorkOrderKey)     AS DistinctWorkOrderCount,
        COUNT(DISTINCT f.OperatorEmployeeKey)        AS DistinctOperatorCount,
        SUM(CAST(f.EventCount AS BIGINT))            AS TotalEventCount,
        SUM(f.QuantityAmount)                        AS TotalQuantityAmount,
        SUM(f.DurationMinutes)                       AS TotalDurationMinutes,
        CAST(SUM(f.QuantityAmount) / NULLIF(SUM(f.DurationMinutes), 0)
             AS DECIMAL(18, 6))                      AS QuantityPerMinute,
        SUM(f.CostAmount)                            AS TotalCostAmount
    FROM fact.ProductionOperationConfirmation AS f
    INNER JOIN dim.Date            AS d  ON d.DateKey             = f.ConfirmationDateKey
    INNER JOIN dim.WorkCenter      AS wc ON wc.WorkCenterKey      = f.WorkCenterKey
    INNER JOIN dim.ProductionShift AS sh ON sh.ProductionShiftKey = f.ProductionShiftKey
    WHERE d.FullDate >= DATEADD(MONTH, -12, CAST(GETDATE() AS DATE))
    GROUP BY
        wc.WorkCenterCode,
        wc.WorkCenterName,
        sh.ProductionShiftCode,
        sh.ProductionShiftName,
        d.CalendarYearNumber,
        d.CalendarMonthNumber,
        d.IsWeekend
    ORDER BY
        d.CalendarYearNumber DESC,
        d.CalendarMonthNumber DESC,
        TotalQuantityAmount DESC;
END
GO
