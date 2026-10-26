# FastAPI Backend

Production-ready FastAPI backend with authentication, multi-tenancy,
background tasks, and comprehensive security features.

## What's provided

### Authentication and authorization
- JWT access + refresh tokens with `jti` blacklisting via Redis.
- TOTP 2FA with encrypted secrets, recovery codes, replay prevention.
- WebAuthn passkeys.
- Magic link (passwordless) login via email.
- Google OAuth 2.0, and Sign in with Apple, native (`POST /auth/apple/native`,
  identity token and nonce from the app) and web (`GET /auth/apple`). Extensible
  to Microsoft via the `OAuthProvider` enum.
- Account deletion unlinks every provider and revokes the Apple refresh token,
  which the app gets by also sending its `authorization_code` to the native
  endpoint. Without it there is nothing to revoke.
- API key authentication (SHA-256-hashed, prefix-based scoping).
- `token_version` column invalidates all JWTs on password change or admin revoke.
- A provider identity (Google, Apple) that links to an email-matched account which
  is not verified first takes the account over: unusable password, `token_version`
  bumped, mailed links revoked, passkeys, 2FA and API keys removed, email marked
  verified. An email change marks the account unverified and mails the new address. A
  verification link is bound to the address it was mailed to. One live reset, magic
  link and verification token per user, all revoked on a password or email change.

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
- S3-compatible object storage, `SeaweedFS` locally, with SHA-256 dedup,
  thumbnails, presigned URLs.
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
- GDPR data export (Article 20): JSON with profile, API keys, audit activity, and
  every row of every table pointing at the account, as classified in
  `app/core/personal_data.py` and `personal_data_project.py`. A test fails
  until a new column pointing at the account is exported or withheld.
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
scripts/            setup.sh, lint.sh, deploy.sh, backup.sh, offsite_backup.sh, restore.sh, assemble.py
deploy/             First-boot server setup, off-box backup configuration and its timer
templates/email/    Jinja2 email templates
```

## Hosting

The server needs a login user with Docker and Compose, rootless or not,
and `rsync`. `deploy/cloud-init.yaml` sets one up on a fresh Debian
server: an `admin` user for sudo and a `deploy` user without it, both
logging in with a key only, no root or password SSH, `ufw` open to SSH,
80 and 443 only, `fail2ban` on SSH, unattended security updates, and
rootless Docker for `deploy`. Put your public keys in it and paste it as
the provider's user data when creating the machine.

Deploy from your own machine:

```bash
cp .env.example .env.production   # fill in production values, never commit it
./scripts/deploy.sh deploy@203.0.113.10 api.example.com -p 2222 -i ~/.ssh/app_deploy
```

It refuses placeholder or development settings, copies the project to
`~/<project>` on the server without git history or local settings,
writes `.env` there readable by the login user only, sets `APP_DOMAIN`,
hashes the dashboard password, builds, starts, and waits for every
container to report healthy, then for `https://<domain>/api/v1/ready`.
With a domain, `Traefik` gets a `Let's Encrypt` certificate, point its
DNS at the server first. With an IP address it runs on a self-signed
certificate, for trying a server out only. Run it again to deploy a new
version, issued certificates and the settings on the server are kept.

Docker publishes ports past `ufw` unless it runs rootless. The compose
files publish the infrastructure on `127.0.0.1`, keep any port added
later there too unless it is meant to be public.

To run the nightly off-box backup, write `deploy/backup.env` on the
server, then install `deploy/backup.service` and `deploy/backup.timer` as
user units of the login user, the steps are at the top of
`deploy/backup.service`. Deploys keep `deploy/backup.env` in place.

## Backups

`backup.sh` dumps the database to `backups/`, on the same disk as the
database, so on its own it survives a bad migration and nothing else.
`offsite_backup.sh` takes a fresh dump and has restic push it, with the
storage volume when there is one, encrypted to a repository off the
machine, configured in `deploy/backup.env`. `deploy/backup.timer` runs it
nightly. `restore.sh` pulls a snapshot back and loads it into a new
database beside the live one, never over it.

Rehearse the restore on a throwaway machine on a schedule. A backup that
has never been restored is a hope, not a backup. Keep a copy of
`RESTIC_PASSWORD` off the machine: without it the repository is
unreadable.

## Key commands

```bash
# Development server
uv run fastapi dev app/main.py --host 0.0.0.0 --port 8000

# Start all services, the app, worker and beat included, the base file
# alone only starts the infrastructure
docker compose -f docker-compose.yml -f docker-compose.app.yml up -d

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
# Change a generation option on the way, any `copier.yml` question.
./scripts/synchronize.sh --data include_redbeat=true
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
  Unknown modes are rejected with 400. Alembic builds its own engine from the settings,
  so migrate the additional database by naming it:
  `POSTGRESQL_DATABASE=<additional database> uv run alembic upgrade head`. The additional database failure is non-fatal
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
