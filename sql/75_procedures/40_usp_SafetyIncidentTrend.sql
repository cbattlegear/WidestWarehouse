-- Hand-written. Safety incident trend by plant, month, and severity.
--
-- ref.Severity carries curated values (SeverityRank, RequiresEscalation), so this one
-- computes genuinely meaningful safety KPIs rather than counting synthetic codes.
CREATE OR ALTER PROCEDURE rpt.usp_SafetyIncidentTrend
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        p.PlantCode,
        p.PlantName,
        d.CalendarYearNumber,
        d.CalendarMonthNumber,
        sv.SeverityCode,
        sv.SeverityName,
        MAX(sv.SeverityRank)                      AS SeverityRank,
        COUNT_BIG(*)                              AS IncidentCount,
        SUM(CAST(f.InjuredPersonCount AS BIGINT)) AS InjuredPersonCount,
        SUM(f.LostWorkdayCount)                   AS LostWorkdayCount,
        SUM(f.EstimatedCostAmount)                AS EstimatedCostAmount,
        AVG(CAST(f.SafetyIncidentLagDays AS DECIMAL(18, 4))) AS AvgReportingLagDays,
        SUM(CASE WHEN sv.RequiresEscalation = 1 THEN 1 ELSE 0 END) AS EscalatedIncidentCount
    FROM fact.SafetyIncident AS f
    INNER JOIN dim.Date   AS d  ON d.DateKey     = f.OccurredDateKey
    INNER JOIN dim.Plant  AS p  ON p.PlantKey    = f.PlantKey
    INNER JOIN ref.Severity AS sv ON sv.SeverityKey = f.SeverityKey
    WHERE d.FullDate >= DATEADD(MONTH, -24, CAST(GETDATE() AS DATE))
    GROUP BY
        p.PlantCode,
        p.PlantName,
        d.CalendarYearNumber,
        d.CalendarMonthNumber,
        sv.SeverityCode,
        sv.SeverityName
    HAVING COUNT_BIG(*) > 0
    ORDER BY
        d.CalendarYearNumber DESC,
        d.CalendarMonthNumber DESC,
        SeverityRank DESC,
        p.PlantCode;
END
GO
