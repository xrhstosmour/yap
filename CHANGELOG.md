# Changelog

All notable changes to this project will be documented in this file.

The format is based on the '[Keep a Changelog](https://keepachangelog.com/en/1.0.0/)',
and this project adheres to [semantic versioning](https://semver.org/spec/v2.0.0.html).

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
