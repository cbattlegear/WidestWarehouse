# Changelog

All notable changes to the loader container are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Versioning applies to the **loader container**. Because the loader discovers the warehouse
shape from the SQL Server catalog at runtime rather than hardcoding it, a schema change in
`model/` is only a breaking change when it changes what the loader requires of a database.

- **MAJOR** — the loader requires a newer warehouse schema, drops or renames an environment
  variable, or otherwise breaks an existing deployment.
- **MINOR** — new jobs, new configuration with a safe default, backwards-compatible model
  and schema additions.
- **PATCH** — bug fixes, dependency bumps, docs.

## [Unreleased]

## [1.1.0] - 2026-08-26

### Added

- **Fixed stored-procedure workload** — six reporting procedures in the new `rpt` schema
  (`sql/75_procedures/`) that the `analytics` job runs at the start of every cycle. Because
  they execute identical statements over identical rolling windows on each run, their
  timings are comparable between cycles, which the randomized queries are not.
- `ANALYTICS_RUN_PROCEDURES` (default `true`) and `ANALYTICS_PROCEDURE_SCHEMA` (default
  `rpt`) to control the new half of the workload.
- `sql/75_procedures/` is the first **static** SQL folder: the generator never rewrites or
  deletes it, but still includes it in `build_all.sql` in numeric order, so hand-written SQL
  and generated SQL can coexist.

### Changed

- The `analytics` job no longer exits early when the `fact` schema is missing — the
  procedures still run. A missing reporting schema logs a warning rather than failing.

## [1.0.0] - 2026-08-25

First tagged release. The warehouse, seed data, loader, and analytics workload are all
verified end to end against SQL Server 2022.

### Added

- **869-table snowflake schema** generated from the YAML model in `model/` by
  `tools/generator`, covering 68 fact tables and their conformed dimension branches.
- **Deterministic seed data** — CSV extracts plus `BULK INSERT` scripts, with all 1,474
  foreign keys re-validated after load so the seed is provably referentially sound.
- **Scheduled loader container** running four jobs: `pipeline`, `data_quality`,
  `housekeeping`, and `analytics`.
- **Randomized analytics workload** that walks the snowflake from the live catalog and logs
  per-query timings. Read-only, lock-free, and failure-isolated.
- **`scripts/deploy.ps1`** for one-command schema build, seed load, and verification.
- **CI** validating the model, running the test suite, and asserting `sql/` still matches
  `model/`.
- **Published image** at `ghcr.io/cbattlegear/widestwarehouse-loader` with build provenance
  attestation.

[Unreleased]: https://github.com/cbattlegear/WidestWarehouse/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/cbattlegear/WidestWarehouse/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/cbattlegear/WidestWarehouse/releases/tag/v1.0.0
