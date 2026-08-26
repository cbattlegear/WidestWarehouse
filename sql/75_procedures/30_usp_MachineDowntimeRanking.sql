-- Hand-written. Worst machines by recorded downtime, with a dense rank per plant.
--
-- TOP + ORDER BY over a ranked set gives the optimizer a sort-heavy plan, which is
-- deliberately different from the GROUP BY rollups the other procedures produce.
CREATE OR ALTER PROCEDURE rpt.usp_MachineDowntimeRanking
AS
BEGIN
    SET NOCOUNT ON;

    WITH downtime AS (
        SELECT
            pl.PlantCode,
            m.MachineNumber,
            m.MachineName,
            ms.MachineStateName,
            mst.StateTypeName,
            COUNT_BIG(*)                       AS StateEventCount,
            SUM(f.DurationMinutes)             AS TotalDownMinutes,
            AVG(f.DurationMinutes)             AS AvgDownMinutes,
            MAX(f.DurationMinutes)             AS LongestDownMinutes,
            SUM(f.CostAmount)                  AS TotalCostAmount
        FROM fact.MachineStateEvent AS f
        INNER JOIN dim.Date    AS d   ON d.DateKey        = f.StartDateKey
        INNER JOIN dim.Machine AS m   ON m.MachineKey     = f.MachineKey
        INNER JOIN dim.Plant   AS pl  ON pl.PlantKey      = f.PlantKey
        INNER JOIN dim.MachineState AS ms ON ms.MachineStateKey = f.MachineStateKey
        INNER JOIN ref.MachineStateType AS mst ON mst.MachineStateTypeKey = f.MachineStateTypeKey
        WHERE d.FullDate >= DATEADD(MONTH, -6, CAST(GETDATE() AS DATE))
        GROUP BY
            pl.PlantCode,
            m.MachineNumber,
            m.MachineName,
            ms.MachineStateName,
            mst.StateTypeName
    )
    SELECT TOP (100)
        PlantCode,
        MachineNumber,
        MachineName,
        MachineStateName,
        StateTypeName,
        StateEventCount,
        TotalDownMinutes,
        AvgDownMinutes,
        LongestDownMinutes,
        TotalCostAmount,
        DENSE_RANK() OVER (PARTITION BY PlantCode ORDER BY TotalDownMinutes DESC) AS PlantDowntimeRank
    FROM downtime
    ORDER BY
        TotalDownMinutes DESC,
        PlantCode,
        MachineNumber;
END
GO
