# Build from the repo root:
#   docker build -f ingestion/Dockerfile -t ingestion .
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.10 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Dependencies first, so code changes reuse this layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY contracts ./contracts
COPY ingestion ./ingestion
RUN uv sync --locked --no-dev --no-editable


FROM python:3.12-slim

RUN useradd --create-home --uid 1000 app
WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
USER app

ENTRYPOINT ["python", "-m", "ingestion"]
