# WidestWarehouse loader

This container runs the Python APScheduler batch loader only. It connects to an external SQL Server; compose does not start SQL Server.

## Configure

```powershell
Copy-Item loader\.env.example loader\.env
notepad loader\.env
```

Set `DW_SERVER`, `DW_DATABASE`, `DW_USER`, and `DW_PASSWORD`. Use `host.docker.internal,1433` for SQL Server on the Docker host.

## Build and run

```powershell
docker build -t widestwarehouse-loader loader
docker compose -f loader\docker-compose.yml up -d --build
```

Logs:

```powershell
docker compose -f loader\docker-compose.yml logs -f loader
```

Force an immediate run:

```powershell
docker compose -f loader\docker-compose.yml run --rm loader python -c "from app.config import LoaderConfig; from app.logging_config import configure_logging; from app.main import run_pipeline; c=LoaderConfig.from_env(); configure_logging(c.log_level); run_pipeline(c)"
```

## Troubleshooting

* Deploy the warehouse schema before expecting successful loads; if `etl`/`stg` tables are missing the loader logs a clear error and retries on the next schedule.
* ODBC errors usually mean `DW_SERVER`, port, SQL authentication, firewall, or encryption settings are wrong. For local developer SQL Server with self-signed certs keep `DW_ENCRYPT=yes` and `DW_TRUST_SERVER_CERTIFICATE=yes`.
* Check health with `docker inspect --format='{{json .State.Health}}' <container>`.
