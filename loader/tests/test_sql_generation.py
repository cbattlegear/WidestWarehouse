from __future__ import annotations

from app.jobs.load_facts import KeyPassThrough, LookupMap, build_fact_insert_sql
from app.jobs.merge_dimensions import build_scd2_merge_sql


def test_scd2_merge_sql_closes_current_and_inserts_new_version() -> None:
    sql = build_scd2_merge_sql(
        "Product",
        "ProductNumber",
        ["BatchId", "RowNumber", "ProductNumber", "ProductName", "RowHash"],
        [
            "ProductKey",
            "ProductNumber",
            "ProductName",
            "SourceSystemKey",
            "BatchId",
            "EffectiveFromUtc",
            "EffectiveToUtc",
            "IsCurrent",
            "RowHash",
        ],
    )
    assert "SET EffectiveToUtc = SYSUTCDATETIME(), IsCurrent = 0" in sql
    assert "t.[ProductNumber] = s.[ProductNumber]" in sql
    assert "t.RowHash <> s.[RowHash]" in sql
    assert "INSERT INTO [dim].[Product]" in sql
    assert "CONVERT(datetime2(3), '9999-12-31')" in sql


def test_fact_insert_uses_natural_key_lookup_and_unknown_fallback() -> None:
    delete_sql, insert_sql = build_fact_insert_sql(
        "ProductionOrder",
        ["BatchId", "RowNumber", "ProductProductNumber", "GoodQuantity"],
        ["ProductionOrderKey", "ProductKey", "GoodQuantity", "SourceSystemKey", "BatchId"],
        [LookupMap("ProductKey", "dim", "Product", "ProductKey", "ProductNumber", "ProductProductNumber")],
    )
    assert "LEFT JOIN [dim].[Product] lk0" in insert_sql
    assert "lk0.[ProductNumber] = s.[ProductProductNumber]" in insert_sql
    assert "COALESCE(lk0.[ProductKey], -1) AS [ProductKey]" in insert_sql
    assert delete_sql == "DELETE FROM [fact].[ProductionOrder] WHERE BatchId = ?;"
    # The delete must stay a separate statement: parameters in a multi-statement
    # batch bind incorrectly and silently insert nothing.
    assert "DELETE" not in insert_sql


def test_fact_insert_validates_pass_through_keys() -> None:
    _, insert_sql = build_fact_insert_sql(
        "ProductionOrder",
        ["BatchId", "RowNumber", "OrderDateKey"],
        ["ProductionOrderKey", "OrderDateKey", "SourceSystemKey", "BatchId"],
        [],
        [KeyPassThrough("OrderDateKey", "dim", "Date", "DateKey")],
    )
    assert "LEFT JOIN [dim].[Date] kv0 ON kv0.[DateKey] = s.[OrderDateKey]" in insert_sql
    assert "COALESCE(kv0.[DateKey], -1) AS [OrderDateKey]" in insert_sql
