# Changelog

All notable changes to this project will be documented in this file.

The format is based on the '[Keep a Changelog](https://keepachangelog.com/en/1.0.0/)',
and this project adheres to [semantic versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Add multi-tenancy with automatic tenant filtering and a `Tenant` model
- Add TOTP two-factor authentication with single-use recovery codes
- Add WebAuthn credential registration and assertion
- Add Google OAuth sign-in and an `OAuthAccount` model
- Add passwordless authentication via magic links
- Add password reset and email verification flows
- Add GDPR endpoints for data export and right-to-erasure
- Add field-level encryption for user PII, with searchable blind-index hashes
- Add file storage with S3-compatible backends and optional MinIO
- Add per-tenant file deduplication keyed on content hash
- Add the outbox pattern for transactional event publishing
- Add a graveyard table retaining soft-deleted records
- Add search infrastructure with `pg_trgm` and `unaccent` trigram indexes
- Add Greeklish transliteration for search
- Add a Redis-backed JWT blacklist for token revocation
- Add API keys with scopes, expiry, and usage tracking
- Add phone numbers to `User`, with normalization and validation
- Add a `UserRole` enum on `User`, replacing `is_superuser`
- Add pagination headers and a shared pagination helper
- Add a multi-database engine option and a Copier question for extra databases
- Add `scripts/synchronize.sh` for pulling template updates into generated projects
- Add nested-repository support, moving CI into the parent repo's workflows
- Add compound and performance indexes across the schema
- Add `AGENTS.md` documenting the template's conventions

### Changed

- Stop eager-loading collection relationships, `User` and `Tenant` collections are `lazy="raise"` so one user fetch runs 2 statements instead of 5, and no longer pulls every API key in the tenant on every authenticated request
- Move settings-driven database pool and Celery tuning into `settings.py`
- Standardize on whole-word names across the codebase, no abbreviations
- Standardize SQLModel imports to one symbol per line
- Standardize audit log error handling
- Offload blocking bcrypt hashing and storage I/O off the event loop
- Reduce graveyard retention
- Give Celery tasks pooled database connections
- Improve outbox and audit log query efficiency
- Pin the containers repo and supply-chain dependencies to fixed commits
- Run `ruff` and `mypy` as local pre-commit hooks against the project's own environment
- Upgrade to the latest FastAPI and align the Docker base image with the project's Python version

### Fixed

- Fail closed when a tenant-scoped query runs with no tenant context, and add `system_context()` for deliberate cross-tenant access
- Make `AuditLogRepository.get_recent_failures` fail closed too, it had its own hand-rolled tenant filter that silently returned every tenant's failed audit records when no tenant context was set
- Stop 2FA sign-in returning a 500 and locking the account out, `_consume_challenge` looked the user up without `system_context()` on an unauthenticated endpoint, so the fail-closed tenant filter rejected every verification after the challenge had already been consumed
- Close a 2FA bypass reachable through OAuth, magic link, and WebAuthn sign-in
- Authenticate WebSocket endpoints
- Check the blacklist on refresh tokens
- Place the CORS middleware outermost in the stack
- Tenant-scope `AuditLogRepository.get_recent_failures`, `increment_token_version`, graveyard recovery, and tenant slug lookup
- Encrypt `actor_email` on `audit_logs`
- Recompute `email_hash` when `email` is cleared, and stop `hash_for_search` breaking after key rotation
- Resolve an `email_hash` collision during erasure
- Complete GDPR erasure for phone, TOTP secret, and 2FA credentials
- Deduplicate recovery codes within a batch, and harden TOTP recovery-code verification against races and abuse
- Make `decrement_reference_count`, `update_profile` token-version bumps, and other update paths single atomic statements
- Close races on file upload content hashing and outbox double dispatch
- Propagate `tenant_id` through outbox event publishing
- Stop re-uploading previously deleted content returning a 500 forever, the soft-deleted row still won the unique-constraint conflict but was never revived, so `create_or_increment` succeeded and then could not find its own row
- Stop a duplicate upload that loses the dedup race from deleting the winner's blob, object keys are content-addressed so both racers wrote the same bytes to the same key and the "discard the loser" cleanup purged the only copy
- Stop deleting one file reference from retiring the row every other reference shares, `FileService.delete` soft-deleted unconditionally, so the remaining referencers got "File not found." on a file they still owned
- Respect soft deletes in email-exists checks, feature flag lookups, and tenant slug lookups
- Stop the async circuit breaker from crashing
- Stop audit log write failures from poisoning the session
- Give anonymous idempotency keys distinct scopes
- Fix an unstable sort order in the tenant repository
- Fix a tenant search index gap and a cache stampede
- Drop redundant secondary indexes on primary key columns
- Register missing tables in `models/__init__`
- Make the `unaccent` search path immutable
- Generate a real `RABBITMQ_ERLANG_COOKIE` secret
- Backfill missing keys in `.env`, not just placeholder values, and preserve volumes across setup runs
- Track the full commit SHA in `.copier/.version`, and record it before Copier runs
- Fix `synchronize.sh` version tracking, commit tracking, inline conflict handling, and a redundant clone
- Stop `synchronize.sh` from `source`ing `.env` wholesale, a free-text field containing a space (an ordinary superuser name) crashed the script outright
- Assert a single Alembic head in CI, and fix a CI port conflict causing double migration
- Scope CI/CD to conditional secrets, and harden workflow permissions
- Backfill `MINIO_DEFAULT_BUCKET`, `MAILPIT_UI_AUTH`, and `POSTGRESQL_HOST` in `.env`, closing a gap where `docker compose config` failed once MinIO, Mailpit, or GlitchTip was enabled

### Security

- Stop `FeatureFlagService.delete_flag` reporting success for a delete that matched nothing, feature flags read globally but wrote per tenant, so a superuser could evict any flag from Redis, disabling it deployment-wide, while the row survived and the API answered 204
- Scope file deduplication to the uploader, not the tenant, uploading content a colleague had already uploaded returned their row, exposing their filename and visibility and stranding a reference the caller could never release
- Enforce tenant deactivation and deletion at every auth path, offboarding an organization revoked nothing, so its members' access tokens kept working until they expired and its API keys kept working forever
- Verify API keys through `APIKeyService`, the repository path it replaced ran `bcrypt` synchronously on the event loop, returned early for an unknown key ID so response time revealed which IDs exist, and never stamped `last_used_at`
- Namespace blob object keys by tenant, deduplication is per tenant but the keys were global, so one tenant's file purge deleted the bytes another tenant's rows still pointed at
- Serve Swagger, ReDoc, and `openapi.json` only in local development, a deployed project published its entire API surface to anonymous callers
- Stop `.env.example` shipping real secrets, the scrub in `setup.sh` missed `METABASE_DATABASE_PASSWORD`, `DATABASE_URL`, and `REDIS_URL`, and was skipped entirely on the sync path where `copier update` re-renders every secret into a file that is not gitignored
- Keep secrets out of scaffolding output
- Auto-generate every secret Copier collects when left empty
- Add supply-chain pinning for third-party GitHub Actions and container images

## [0.2.0] - 2026-05-14

### Added

- Add Feature Flags admin API endpoints (CRUD + toggle)
- Add Feature Flag model, schemas, repository, and service
- Add Feature Flags test suite with cache tier coverage
- Add optional GlitchTip (self-hosted Sentry alternative) to docker-compose
- Add Celery beat schedule for periodic tasks (audit cleanup, cache warm)
- Add WebSocket support (live metrics streaming, health, notifications)
- Add security headers middleware (HSTS, X-Frame-Options, X-Content-Type)
- Add AI bot/crawler blocking (GPTBot, ClaudeBot, etc.)
- Add Celery worker monitoring endpoint (/api/v1/workers)
- Add GitHub Actions CI/CD workflow (lint, test, security scan)
- Add scripts/setup.sh for automated project setup
- Add Traefik reverse proxy config with Let's Encrypt SSL

### Changed

- Rename apikeys.py to api_keys.py for Python naming conventions
- Add async lock to Redis singleton for thread-safe initialization
- Add threading lock to CircuitBreakerService for concurrent safety
- Hoist sentry_sdk.capture_exception into global error handler
- Update README: automated setup, API docs, WebSocket, security, worker monitoring
- Clean up duplicate sections in README (635 to ~600 lines)

### Fixed

- `/auth/me` endpoint returns UserResponse (no longer exposes `hashed_password`)
- get_any_auth dependency injection with get_optional_current_user
- All UUID wrapping redundancies removed from tenant_id/user_id usage
- custom_generate_unique_id handles routes without tags gracefully
- Sentry/GlitchTip now receives exceptions from global handler

## [0.1.0] - 2026-03-30

### Added

- Add GZip compression middleware
- Add configurable database pool settings
- Add Redis max connections config
- Add metrics endpoint for monitoring
- Add pre-commit hooks configuration
- Add OpenTelemetry tracing support
- Add Request ID middleware for request tracing
- Add Celery support with RabbitMQ broker
- Add Feature Flags system with 3-tier cache (memory/Redis/DB)

### Changed

- Optimize Dockerfile with multi-stage build
- Normalize UUID types across all models for consistency
- Hoist time import to module level in request logging
- Make Redis singleton initialization async-safe with lock
- Fix `get_any_auth` dependency injection pattern
- Convert CELERY_BROKER_URL to computed field (removes side-effect)

### Fixed

- `/auth/me` endpoint no longer exposes `hashed_password`
- RequestIDMiddleware properly registered in middleware chain
- Tracing hook added to application lifespan
- Empty task stubs implemented with actual logic (API key expiry cleanup)
- Circuit breaker dict access made thread-safe
- Type annotations and docstrings standardized across all modules

## [0.0.2] - 2024-02-27

### Added

- Added PostgreSQL psycopg adapter
- Added SQLModel database ORM

### Changed

- Migrate from Cookiecutter to Copier for rendering project templates

## [0.0.1] - 2024-02-08

### Added

- Initial release of the project including:
  - Git files
  - IDE configurations
  - project structure
  - dependency management
  - database migrations
  - data validation
  - web server
