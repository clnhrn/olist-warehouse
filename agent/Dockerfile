# Streamlit UI for the text-to-SQL agent, for the private Cloud Run service.
# Build from the repo root (test only; CI pushes):
#   docker build --platform=linux/amd64 --provenance=false --sbom=false \
#     -f agent/Dockerfile -t agent .
#
# The project is installed editable from /app, so the agent finds the gold
# model YAML files (dbt/models/gold) and the semantic layer at runtime.
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.10 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Dependencies first, so code changes reuse this layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --group agent --no-install-project

# The package ships ingestion and contracts too; the agent imports only
# ingestion.config. Only the gold YAML files come from dbt.
COPY contracts ./contracts
COPY ingestion ./ingestion
COPY agent ./agent
COPY dbt/models/gold/*.yml ./dbt/models/gold/
RUN uv sync --locked --no-dev --group agent


FROM python:3.12-slim

RUN useradd --create-home --uid 1000 app
WORKDIR /app
COPY --from=builder --chown=app:app /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    OLIST_AGENT_TARGET=prod \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    STREAMLIT_SERVER_HEADLESS=true
USER app

# Cloud Run sets PORT (8080).
CMD ["sh", "-c", "exec streamlit run agent/app.py --server.address=0.0.0.0 --server.port=${PORT:-8080}"]
