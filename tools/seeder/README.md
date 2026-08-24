# WidestWarehouse seeder

The seeder emits deterministic UTF-8 CSV files and guarded BULK INSERT scripts.

`dim.TimeOfDay` uses **minute grain**: 1,440 rows with keys equal to seconds from
midnight (`0, 60, 120, ... 86340`), plus the `-1` unknown member. This keeps the
starter seed practical while preserving a stable key domain for fact FKs.

Very large metadata row counts are treated as relative sizing signals and capped
so the scale-1 starter set finishes in a few minutes.
