FROM python:3.13-alpine AS builder

WORKDIR /app

RUN apk add --no-cache build-base postgresql-dev

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-cache --no-install-project

FROM python:3.13-alpine

WORKDIR /app

RUN apk add --no-cache libpq ca-certificates tzdata \
    && adduser -D -s /bin/bash appuser

COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv
COPY --chown=appuser:appuser pyproject.toml uv.lock ./
COPY --chown=appuser:appuser app/ ./app/
COPY --chown=appuser:appuser scripts/ ./scripts/
COPY --chown=appuser:appuser templates/ ./templates/

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/api/v1/live')" || exit 1

CMD ["fastapi", "run", "app/main.py", "--host", "0.0.0.0", "--port", "8000"]
