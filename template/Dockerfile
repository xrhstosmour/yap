FROM python:3.14-alpine@sha256:26730869004e2b9c4b9ad09cab8625e81d256d1ce97e72df5520e806b1709f92 AS builder

WORKDIR /app

RUN apk add --no-cache build-base postgresql-dev

COPY --from=ghcr.io/astral-sh/uv:0.7.3 /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-cache --no-install-project

FROM python:3.14-alpine@sha256:26730869004e2b9c4b9ad09cab8625e81d256d1ce97e72df5520e806b1709f92

WORKDIR /app

# `/app` stays root-owned so the running user cannot rewrite its own code.
# `celery beat` needs somewhere to write its schedule though, and its default
# is the working directory, so give it a directory of its own.
RUN apk add --no-cache libpq ca-certificates tzdata curl \
    && adduser -D -s /bin/sh appuser \
    && mkdir -p /var/lib/celery \
    && chown appuser:appuser /var/lib/celery

COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv
COPY --chown=appuser:appuser pyproject.toml uv.lock alembic.ini ./
COPY --chown=appuser:appuser app/ ./app/
COPY --chown=appuser:appuser scripts/ ./scripts/
COPY --chown=appuser:appuser templates/ ./templates/
COPY --chown=appuser:appuser migrations/ ./migrations/

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/api/v1/live || exit 1

CMD ["fastapi", "run", "app/main.py", "--host", "0.0.0.0", "--port", "8000"]
