# FastAPI Backend

Production-ready FastAPI backend with authentication, multi-tenancy,
background tasks, and comprehensive security features.

## What's provided

### Authentication and authorization
- JWT access + refresh tokens with `jti` blacklisting via Redis.
- TOTP 2FA with encrypted secrets, recovery codes, replay prevention.
- WebAuthn passkeys.
- Magic link (passwordless) login via email.
- Google OAuth 2.0 (extensible to Apple, Microsoft via `OAuthProvider` enum).
- API key authentication (bcrypt-hashed, prefix-based scoping).
- `token_version` column invalidates all JWTs on password change or admin revoke.

### Multi-tenancy
- `tenant_id` on all models via `BaseModel`.
- Automatic tenant scoping in `BaseRepository._apply_tenant_filter()`.
- Tenant context propagated via `contextvars` from JWT claims or API keys.
- System tenant fallback for unscoped operations.

### Data and storage
- PostgreSQL with `pg_trgm` and `unaccent` extensions.
- Full-text search (`to_tsvector`/`plainto_tsquery`) + trigram similarity.
- Greeklish transliteration and Greek language support.
- `SearchMixin` for reusable multi-field search on any model.
- MinIO/S3 object storage with SHA-256 dedup, thumbnails, presigned URLs.
- PII fields (`email`, `phone` on `User`) encrypted at rest via `EncryptedString`
  (Fernet); deterministic HMAC search hashes (`email_hash`, `phone_hash`) support
  exact-match lookups without exposing ciphertext to search or indexes.
- Soft deletes with `graveyard` tombstone table for recovery.
- Alembic migrations in `migrations/`.
- Multi-database engine with header-based routing for preview/demo/staging isolation.

### Background processing
- Celery workers with RabbitMQ broker (AMQPS/TLS).
- RedBeat or Celery beat for periodic tasks.
- Email sending, cache warming, outbox event dispatch, data cleanup.

### Reliability
- Idempotency via `X-Idempotency-Key` header on mutating endpoints.
- `CacheService.get_or_set()` prevents cache stampedes: callers race for a
  short-lived per-key lock on a miss, losers poll for the winner's result
  instead of recomputing in parallel.
- Circuit breaker for external service calls (`pybreaker`).
- Rate limiting per user and per API key (Redis sliding window).
- Resilient container startup with retries.

### Compliance
- GDPR account deletion (Article 17): anonymizes PII, revokes keys, invalidates JWTs.
- GDPR data export (Article 20): JSON with profile, API keys, audit activity.
- Audit logging on all significant actions.

### Observability
- Structured JSON logging via `structlog` with correlation IDs.
- OpenTelemetry distributed tracing.
- Sentry/GlitchTip error tracking.
- Health endpoints: REST liveness/readiness probes, plus authenticated WebSocket
  streams for live health (`/ws/health`, any user) and metrics (`/ws/metrics`,
  superuser-only, pool/cache stats).

## Where things live

```
app/
  api/v1/           Route handlers, thin, delegate to services
  schemas/          Pydantic request/response models
  services/         Business logic, orchestrates repositories
  repositories/     Database queries (BaseRepository generic CRUD)
    mixins/         Reusable query mixins (SearchMixin)
  models/           SQLModel table definitions (BaseModel with tenant_id)
  core/             Settings, security, cache, rate limiting, logging, encryption
  tasks/            Celery background tasks
  dependencies.py   FastAPI dependency injection (CurrentUser, SessionDependency, etc.)
  database.py       Async engine and session factory
  main.py           FastAPI app factory

migrations/         Alembic versions
tests/              pytest suite (api/, services/, repositories/, core/, models/)
scripts/            setup.sh, lint.sh, backup.sh, assemble.py
templates/email/    Jinja2 email templates
```

## Key commands

```bash
# Development server
uv run fastapi dev app/main.py --host 0.0.0.0 --port 8000

# Start all services
docker compose up -d

# Tests
uv run pytest
uv run pytest tests/api/test_auth.py -xvs   # single file
uv run pytest -m "not slow"                  # skip integration tests

# Lint and format
./scripts/lint.sh --fix
uv run ruff check app/ tests/
uv run ruff format app/ tests/

# Type checking
uv run mypy app/ --config-file mypy-ci.toml

# Database migrations
uv run alembic revision --autogenerate -m "Add users table"
uv run alembic upgrade head
uv run alembic downgrade -1

# Celery
uv run celery -A app.celery_app worker --loglevel=info
uv run celery -A app.celery_app beat --loglevel=info

# Setup and sync
./scripts/setup.sh
# Requires clean git status and `copier` installed.
./scripts/synchronize.sh
```

`YAP_SYNC=1 ./scripts/setup.sh` skips Docker/migrations/seeding, only ensure `.env` exists. Automatically set by `synchronize.sh`.

## Architecture patterns

- **Layered**: api → services → repositories → models. Never call repositories
  from route handlers; always go through a service.
- **Dependency injection**: Type aliases in `dependencies.py`, `CurrentUser`,
  `SuperuserUser`, `SessionDependency`, `AccessTokenDependency`, `AnyAuth`.
- **Custom exceptions**: Each service defines its own exception hierarchy
  (e.g. `AuthenticationError`, `InvalidCredentialsError`). API layer catches
  and converts to HTTP responses.
- **Async everywhere**: All database, Redis, and HTTP calls are async.
- **Multi-database routing**: An `X-Database-Mode` header sets a `contextvars.ContextVar`
  that `get_async_session()` reads to route requests to an additional PostgreSQL database.
  Unknown modes are rejected with 400. Alembic migrations on the additional database
  use `get_additional_sync_session()`. The additional database failure is non-fatal
  during startup so boot succeeds when it has not been provisioned yet.
- **Structured logging**: `get_logger("module.name")` with `logger.info("event", key=value)`.
- **UUIDv7 primary keys**: Time-sortable, 128-bit, generated by `uuid7()`.

## Conventions

- Python 3.14, `uv` package manager.
- Full descriptive variable names, no abbreviations (`reference` not `ref`).
- Google-style docstrings: `Args:`, `Returns:`, `Raises:`, `Example:`.
- `from __future__ import annotations` at the top of every module.
- `ruff` for linting (single-line imports, double quotes, LF).
- `mypy-ci.toml` for CI type checking.
- Pre-commit hooks enforce formatting, type checking, and secret detection.
- Commit messages: imperative mood, descriptive, prefixed with scope if needed.
