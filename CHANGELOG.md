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

- Exclude `migrations/versions` from `ruff` and `mypy` rather than the `alembic/versions` path the template stopped using, the stale pattern matched nothing so `ruff check .` reported 86 errors in `Alembic`-generated revisions nobody edits
- Report what a template sync actually changed, `synchronize.sh` branched on `git diff --stat`, which exits 0 either way, so it never reached its own "up to date" message and told the user to review and commit an empty diff, and `git diff` never saw the new files a template update mostly consists of
- Stop `copier copy` failing at its very last step when the template remote is unreachable, the version-recording task indexed straight into an empty `git ls-remote` result and the `IndexError` aborted an otherwise complete generation, it now keeps the placeholder and says so
- Pin every `GitHub Actions` reference to a commit SHA in both the template's own workflow and the generated one, a tag can be repointed by whoever owns the action, so a compromised or simply retagged upstream would run in CI with its `GITHUB_TOKEN` without anything changing here
- Make `FeatureFlagRepository.list_active` return every flag, it asked for `limit=1000` which `BaseRepository.list` clamps to a hundred, so it returned the first hundred and presented them as all of them
- Remove the unregistered `TenantContextMiddleware` duplicate from `app/core/tenant.py`, the middleware that actually runs is the one in `main.py`
- Fail open when `Redis` is unavailable to the rate limiter, it had no error handling at all while running on every authenticated request, so a `Redis` blip surfaced as a `500` across the whole API, and revocation already fails open for the same reason
- Rotate refresh tokens atomically, the blacklist was checked and then written as two separate steps with the whole token mint in between, so two requests carrying the same refresh token both passed the check and both walked away with a fresh pair, and the reuse the blacklist exists to catch went unnoticed
- Exclude the `slow` markers from a generated project's CI, matching the template's own, they drive real processes and the assembled compose stack that the workflow never stands up, so a new project's first push went red on `test_core_compose_services_running` before its author had written any code
- Create `.env` and the reconstructed `.copier/.answers.yml` with mode `600`, `cp` and `cat >` left both world-readable under the default umask while they hold the JWT signing key, the `Fernet` key and every service password, and `.env` kept those permissions for the life of the project
- Point the container `HEALTHCHECK` at `/ready` instead of `/live`, Docker's health status is what `depends_on: service_healthy` waits on, and `/live` answers `200` for as long as the process is up, so a container that could not reach its database reported healthy and was handed traffic
- Bind the app's published port to loopback by default, `8000:8000` published on `0.0.0.0`, so with `Traefik` in front the app was still reachable directly in plaintext from anywhere that could route to the host, skipping the proxy's TLS and its middleware, override `APP_BIND_ADDRESS` to publish deliberately
- Gate `setup.sh` on real container health, the readiness loop read `docker compose ps` by column position and landed on `CREATED`, so every container looked pending, the loop always ran its full thirty iterations, and setup ended with a flat sixty-second sleep that checked nothing and reported nothing
- Record the generated `Traefik` dashboard password in `.env` instead of discarding it, `assemble.py` hashed it into `.htpasswd` and let the plaintext go out of scope, so the dashboard was protected by a password nobody could ever know, and it is now reused rather than rotated on every re-assemble
- Move the vendored containers cache out of `/tmp/containers` and verify it against the pinned commit, the path was fixed under a world-writable directory and reused on existence alone, so any local user could plant compose files a project then ran, and a bumped `REPO_COMMIT` was silently ignored on any machine that had assembled before
- Stop the file dedup migrations aborting an upgrade on data the final schema accepts, the chain enforced a global unique `content_hash` and then a `(tenant_id, content_hash)` constraint on the way to a schema that only requires `(tenant_id, uploaded_by, content_hash)`, so two tenants holding the same file stranded the database partway through with no way forward, only the final constraint is enforced now
- Stop `OpenTelemetry` printing every span to stdout in staging and production, a multi-line JSON span landed in the middle of the one-object-per-line log stream and broke any shipper parsing it
- Configure logging before the lifespan's first log line, `application_starting` went out through `structlog`'s default console renderer, so in production it appeared as coloured text among the JSON with no correlation ID and no service context
- Stop `synchronize.sh` resetting `include_redbeat`, `timezone` and `storage_region` on every run, it rebuilds the answers from the project's own files and none of those three were readable back, so `celery-redbeat` being an unconditional dependency flipped every project to `RedBeat`, and the other two fell through to their defaults
- Use the `storage_region` answer for `STORAGE_REGION`, the question was asked and the answer then discarded for a hardcoded `us-east-1`
- Give `celery beat` a writable directory for its schedule, the default scheduler persists to the working directory and `/app` is root-owned while the container runs as `appuser`, so beat died at startup with `PermissionError` and no periodic task ever ran in a project using the default scheduler
- Percent-encode the database credentials in `DATABASE_URI` and escape the URL for `Alembic`, a password holding `/`, `#` or `?` failed URL parsing so the application would not start at all, a `%` produced a malformed escape, and the encoded URL then killed every migration with `invalid interpolation syntax` because `Alembic` writes it through `configparser`
- Stop `delete_object` reporting a failed delete as a successful one, every `ClientError` was swallowed as "already deleted", so a bucket policy without `s3:DeleteObject` turned each purge into a silent no-op that dropped the row, kept the blob and logged nothing
- Stop publishing an uncommitted feature-flag state to `Redis`, and give the keys a TTL, the service pushed the new state from inside the transaction, so a request that rolled back afterwards left every instance on a value the database never held, and with no expiry on the key it stayed that way until someone flushed `Redis` by hand
- Answer an oversized upload with a `413` instead of a `500`, the size guard raised a plain `FileServiceError` and nothing on the route caught it, so sending a file over the 25MB limit read as a server fault
- Reject an unknown `?role=` on `GET /users` and an unsortable `?sort_by=` on `GET /tenants` with a `422` instead of a `500`, the role went straight into `UserRole(...)` and raised `ValueError` inside the handler, and `sort_by` reached `getattr` unchecked, so `?sort_by=metadata` handed SQLAlchemy's `MetaData` object to `order_by`
- Pass the list filters through user search, `?search=` built `is_active` and `role` and then dropped them on the way to the repository, so an admin narrowing a search still got deactivated users and every role back, with nothing to indicate the filter had been ignored
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

- Export every API key in the GDPR data export, the underlying `list()` defaults to twenty and nothing overrode it, so a user with more keys silently got the first twenty, and report when the audit activity is truncated instead of capping it silently
- Run the `Celery` worker probes off the event loop and stop reporting a broker outage as a healthy idle cluster, three inline `inspect` calls froze the whole process for up to three seconds per request, and any failure was swallowed into a `200` with zeros
- Stop `CacheService.get_or_set` treating a cached `None` as a miss, a `compute()` returning `None` was recomputed on every call and never served, and lock-losing callers polled for a value that could never appear, so the stampede protection guaranteed a stampede
- Stop holding a `FOR UPDATE` lock across recovery-code hashing, every attempt pinned all of a user's unused codes for up to ten sequential `bcrypt` comparisons, so anyone with a challenge token could stall the account's recovery path, single use is now enforced by a conditional update
- Set `FORWARDED_ALLOW_IPS` for the app container, `uvicorn` defaults it to `127.0.0.1` and the reverse proxy is a separate container, so `X-Forwarded-For` was ignored and every request looked like it came from the proxy, collapsing the per-IP auth rate limiter into one bucket for the whole internet
- Stop the idempotency middleware caching `4xx` responses, a `429` or an expired-token `401` stuck to the key for `IDEMPOTENCY_TTL_HOURS` (24 by default) and was replayed long after the client had waited or re-authenticated
- Stop the WebSocket broadcast relay dying permanently when a client connects or disconnects mid-broadcast, the fan-out iterated the live connection set across `await`s, so `RuntimeError: Set changed size during iteration` escaped the relay task and no broadcast was delivered again until the process restarted
- Fix the docs `Content-Security-Policy` blocking `Swagger` and `ReDoc`, `Swagger` rendered a completely blank page because its inline bootstrap script was refused, and `ReDoc` rendered "Something went wrong"
- Stop `backup.sh` sourcing `.env` as shell, an ordinary superuser name containing a space killed the nightly backup silently under `set -e`, and a `$(...)` in any value executed on every run
- Gitignore `backups/` and `*.sql.gz`, database dumps were landing in the working tree where `git add -A` would sweep them up
- Refuse to start a staging or production deployment whose CORS origins point at the local machine, `FRONTEND_HOST` defaults to `http://localhost:5173` and is served with `allow_credentials=True`, so an unset value returned authenticated responses to whatever the victim ran on that port, and mailed reset links pointing at their own machine
- Stop `/auth/webauthn/login/begin` disclosing whether an address has an account, `challenge_session_key` was omitted only for real accounts and `allowCredentials` was populated only for real accounts, which also handed out their credential IDs, and rate limit both WebAuthn login endpoints per client IP
- Close a user-enumeration oracle on `/auth/forgot-password` and `/auth/magic-link`, the per-user token cooldown only fires for addresses that exist, so a 429 on a repeated request confirmed the account was real despite both endpoints documenting an unconditional 204
- Rate limit `/auth/google` and `/auth/google/callback` per client IP, the Google state token's own cooldown was keyed on the shared `redirect_uri`, so one anonymous request 429'd every other Google sign-in in the deployment for ten seconds, repeatable indefinitely
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
