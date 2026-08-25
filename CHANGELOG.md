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

[Unreleased]: https://github.com/cbattlegear/WidestWarehouse/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/cbattlegear/WidestWarehouse/releases/tag/v1.0.0
