# The dbt Cloud Run Job. Build from the repo root:
#   docker build --platform=linux/amd64 --provenance=false --sbom=false \
#     -f dbt/Dockerfile -t <repo>/dbt:<git-sha> .
# Default command: dbt build --target prod, without unit tests (they run
# locally and in CI). Override the args to run anything else.
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.10 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# dbt-core and dbt-bigquery only (no DuckDB, no ingestion code).
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --only-group dbt --no-install-project

# Packages are installed at build time, so a run needs no network but BigQuery.
COPY dbt ./dbt
WORKDIR /app/dbt
RUN /app/.venv/bin/dbt deps


FROM python:3.12-slim

RUN useradd --create-home --uid 1000 app
WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder --chown=app:app /app/dbt /app/dbt

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    DBT_SEND_ANONYMOUS_USAGE_STATS=false
USER app
WORKDIR /app/dbt

ENTRYPOINT ["dbt"]
CMD ["build", "--target", "prod", "--exclude-resource-type", "unit_test"]
