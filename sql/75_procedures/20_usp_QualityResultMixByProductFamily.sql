-- Hand-written. Quality inspection volume by product family and result status.
--
-- Climbs three levels of the product snowflake (Product -> Subfamily -> Family) and uses a
-- windowed aggregate to express each status as a share of its family, which exercises a
-- different plan shape from the plain rollups.
CREATE OR ALTER PROCEDURE rpt.usp_QualityResultMixByProductFamily
AS
BEGIN
    SET NOCOUNT ON;

    WITH inspection AS (
        SELECT
            pf.ProductFamilyCode,
            pf.ProductFamilyName,
            qs.ResultStatusCode,
            qs.ResultStatusName,
            COUNT_BIG(*)                      AS InspectionCount,
            SUM(f.QuantityAmount)             AS InspectedQuantity,
            AVG(f.DurationMinutes)            AS AvgInspectionMinutes,
            COUNT(DISTINCT f.ProductKey)      AS DistinctProductCount
        FROM fact.QualityInspectionResult AS f
        INNER JOIN dim.Date             AS d  ON d.DateKey                 = f.InspectionDateKey
        INNER JOIN dim.Product          AS pr ON pr.ProductKey             = f.ProductKey
        INNER JOIN dim.ProductSubfamily AS ps ON ps.ProductSubfamilyKey    = pr.ProductSubfamilyKey
        INNER JOIN dim.ProductFamily    AS pf ON pf.ProductFamilyKey       = ps.ProductFamilyKey
        INNER JOIN ref.QualityResultStatus AS qs ON qs.QualityResultStatusKey = f.QualityResultStatusKey
        WHERE d.FullDate >= DATEADD(MONTH, -12, CAST(GETDATE() AS DATE))
        GROUP BY
            pf.ProductFamilyCode,
            pf.ProductFamilyName,
            qs.ResultStatusCode,
            qs.ResultStatusName
    )
    SELECT
        ProductFamilyCode,
        ProductFamilyName,
        ResultStatusCode,
        ResultStatusName,
        InspectionCount,
        InspectedQuantity,
        AvgInspectionMinutes,
        DistinctProductCount,
        SUM(InspectionCount) OVER (PARTITION BY ProductFamilyCode) AS FamilyInspectionCount,
        CAST(100.0 * InspectionCount
             / NULLIF(SUM(InspectionCount) OVER (PARTITION BY ProductFamilyCode), 0)
             AS DECIMAL(9, 4))                                     AS PercentOfFamily
    FROM inspection
    ORDER BY
        ProductFamilyCode,
        InspectionCount DESC;
END
GO
