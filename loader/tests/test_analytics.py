import random

from app.discovery import ColumnInfo, ForeignKeyInfo
from app.jobs.analytics import SchemaCache, build_random_query


def _col(name: str, data_type: str) -> ColumnInfo:
    return ColumnInfo(name, data_type, True, 1)


class FakeCache(SchemaCache):
    """Stands in for the SQL Server catalog with a small snowflake branch."""

    def __init__(self) -> None:
        super().__init__(conn=None)
        self._col_map = {
            ("fact", "BomComponentUsage"): [
                _col("ProductKey", "int"),
                _col("BatchId", "bigint"),
                _col("UsageDateKey", "int"),
                _col("QuantityAmount", "decimal"),
                _col("CostAmount", "decimal"),
            ],
            ("dim", "Product"): [_col("ProductKey", "int"), _col("ProductNumber", "nvarchar")],
            ("dim", "ProductSubfamily"): [
                _col("ProductSubfamilyKey", "int"),
                _col("SubfamilyName", "nvarchar"),
            ],
        }
        self._fk_map = {
            ("fact", "BomComponentUsage"): [
                ForeignKeyInfo("fact", "BomComponentUsage", "ProductKey", "dim", "Product", "ProductKey")
            ],
            ("dim", "Product"): [
                ForeignKeyInfo(
                    "dim", "Product", "ProductSubfamilyKey", "dim", "ProductSubfamily", "ProductSubfamilyKey"
                )
            ],
            ("dim", "ProductSubfamily"): [],
        }

    def columns(self, schema, table):
        return self._col_map.get((schema, table), [])

    def foreign_keys(self, schema, table):
        return self._fk_map.get((schema, table), [])


def _generate(seed: int):
    return build_random_query(FakeCache(), random.Random(seed), ["BomComponentUsage"])


def test_generated_queries_are_well_formed() -> None:
    for seed in range(40):
        generated = _generate(seed)
        if generated is None:
            continue
        sql = generated.sql
        assert sql.startswith("SELECT TOP (")
        assert "FROM [fact].[BomComponentUsage] f" in sql
        assert "GROUP BY " in sql
        assert sql.rstrip().endswith(";")
        # Every non-aggregate projection must also be grouped or SQL Server rejects it.
        group_by = sql.split("GROUP BY ")[1].split("\n")[0]
        for alias_expr in ("d0_0.", "d0_1.", "d1_0.", "d2_0."):
            if f"{alias_expr}" in sql.split("FROM")[0]:
                assert alias_expr in group_by


def test_join_predicates_use_real_foreign_keys() -> None:
    joined = [g for g in (_generate(s) for s in range(40)) if g and "INNER JOIN" in g.sql]
    assert joined, "expected at least one query to join a dimension"
    for generated in joined:
        assert "INNER JOIN [dim].[Product] d0_0 ON d0_0.[ProductKey] = f.[ProductKey]" in generated.sql


def test_snowflake_branch_is_walked_beyond_the_first_level() -> None:
    depths = {g.depth for g in (_generate(s) for s in range(40)) if g}
    assert max(depths) >= 2, "generator should climb past the leaf dimension"


def test_generation_is_reproducible_from_the_seed() -> None:
    assert _generate(7).sql == _generate(7).sql


def test_self_referencing_dimension_cannot_loop() -> None:
    cache = FakeCache()
    cache._fk_map[("dim", "Product")].append(
        ForeignKeyInfo("dim", "Product", "ParentProductKey", "dim", "Product", "ProductKey")
    )
    for seed in range(25):
        generated = build_random_query(cache, random.Random(seed), ["BomComponentUsage"])
        if generated:
            assert generated.depth <= 4
