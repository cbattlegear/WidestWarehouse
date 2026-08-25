FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates gnupg apt-transport-https \
    && install -d -m 0755 /etc/apt/keyrings \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /etc/apt/keyrings/microsoft.gpg \
    && chmod a+r /etc/apt/keyrings/microsoft.gpg \
    && echo "deb [arch=amd64,arm64,armhf signed-by=/etc/apt/keyrings/microsoft.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 unixodbc \
    && apt-get purge -y --auto-remove curl gnupg apt-transport-https \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

# Supplied by CI from the git tag and commit. Defaults keep local builds working and
# make an un-stamped image obvious at a glance. LOADER_VERSION only feeds the OCI
# label -- at runtime app/version.py is the single source of truth, so the two can
# never disagree about what the code actually is.
ARG LOADER_VERSION=0.0.0-dev
ARG LOADER_REVISION=unknown
ENV LOADER_REVISION=${LOADER_REVISION}

LABEL org.opencontainers.image.title="WidestWarehouse loader" \
      org.opencontainers.image.description="Scheduled batch loader for the WidestWarehouse SQL Server data warehouse" \
      org.opencontainers.image.version="${LOADER_VERSION}" \
      org.opencontainers.image.revision="${LOADER_REVISION}" \
      org.opencontainers.image.source="https://github.com/cbattlegear/WidestWarehouse"

RUN useradd --create-home --uid 10001 loader \
    && mkdir -p /data/landing /logs \
    && chown -R loader:loader /app /data /logs
USER loader

HEALTHCHECK --interval=60s --timeout=15s --start-period=30s --retries=3 CMD python -m app.healthcheck
CMD ["python", "-m", "app.main"]
