# YAP: Yet Another Project

Copier template for production-ready FastAPI backends. The root directory
contains the template configuration; all application code lives under `template/`.

## Generate a project

```bash
copier copy --trust gh:xrhstosmour/yap path/to/project
# or from a local clone
copier copy --trust /path/to/yap path/to/project
```

Required inputs: `project_name`, `project_slug`, `author_name`, `author_email`.
Secrets (`jwt_secret_key`, `postgresql_password`, etc.) auto-generate if left
empty. Optional Docker services (Traefik, GlitchTip, Metabase, pgAdmin4,
Mailpit, Redis Commander, Flower, MinIO, RedBeat) are toggled via boolean flags.

## Project layout

```
copier.yml              # Template variables, validators, post-copy tasks
template/               # Scaffolded application (Jinja2 suffix: .template)
  app/                  # FastAPI application code
  tests/                # pytest suite
  migrations/           # Alembic versions
  scripts/              # setup.sh, assemble.py, lint.sh, etc.
  Dockerfile            # Multi-stage build (uv)
  docker-compose.yml.template
  pyproject.toml.template
```

## Develop the template

1. Edit files under `template/`. Files ending in `.template` are Jinja2-rendered
   by Copier. Use `{{ project_slug }}` and other copier variables where needed.
2. Test locally: `copier copy --trust . /tmp/test-project`
3. After rendering, verify with `cd /tmp/test-project && uv sync --extra dev && uv run pytest`.

## Merge workflow

- Never push directly to `main`. All changes must go through a pull request.
- Branch protection is enabled: `main` blocks direct pushes, enforces admin
  compliance, and requires all CI checks (lint, test, security) to pass before merge.
- Create a PR, wait for green CI, then merge via `gh pr merge <n> --merge --delete-branch`.
- Force-push is only allowed on feature branches, never on `main`.

## CI pipeline (`.github/workflows/ci.yml`)

1. **lint**: Renders the template via Copier, runs `ruff check`, `ruff format`,
   `ruff format --check`, `mypy` (using `mypy-ci.toml`), and an Alembic
   single-head check that asserts exactly one migration head exists.
2. **test**: Renders the template (with tasks), sources `.env`, runs
   `pytest tests/ -v --tb=short --cov=app --cov-report=xml`.
   Minimum coverage: 82%.
3. **security**: Trivy filesystem scan on `template/` for CRITICAL/HIGH
   vulnerabilities, which fails the build, plus a non-blocking second scan
   that reports MEDIUM findings for periodic review.

All three jobs run on `ubuntu-latest`. Lint must pass before test and security run.

## Conventions

- Python 3.14, `uv` package manager.
- `ruff` for linting and formatting (single-line imports, double quotes, LF).
- `mypy-ci.toml` for type checking (less strict than local `pyproject.toml`).
- Pre-commit hooks: ruff, mypy, trailing whitespace, private key detection.
- Template files use `.template` suffix and Jinja2 syntax.
- Commit messages: imperative mood, descriptive, no co-author trailers.
