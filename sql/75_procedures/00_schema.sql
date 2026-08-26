-- Hand-written. This folder is NOT generated: tools.generator never writes or deletes
-- anything under sql/75_procedures, it only folds these files into build_all.sql.
--
-- rpt holds the fixed reporting workload the loader runs on every analytics cycle.
IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = N'rpt')
    EXEC(N'CREATE SCHEMA [rpt]');
GO
