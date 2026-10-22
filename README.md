# Yet Another Project

This boilerplate provides a quick start for building a FastAPI backend, with PostgreSQL, Redis, Celery and Docker, scaffolded with Copier. It's designed to help developers kick off their project with best practices, including project structure, dependency management, and more.

## Installation

You need:

- [Python 3.14+](https://www.python.org/downloads/)
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- [Docker](https://docs.docker.com/engine/install/)
- [copier](https://copier.readthedocs.io/en/stable/#installation)

Generate a new boilerplate project:

``` bash
# From GitHub:
copier copy --trust --vcs-ref main gh:xrhstosmour/yap path/to/store/project

# Or from a local clone:
copier copy --trust /path/to/yap path/to/store/project
```

Generation does more than copy files. `scripts/setup.sh` runs as its last
step: it writes `.env` with generated secrets, starts the Docker services,
applies the migrations and seeds the first superuser. It needs Docker
running, and stops with a link to install it when it is missing.

Then, in the new project:

``` bash
# Review the generated secrets and settings.
$EDITOR .env

# Put it under version control, `synchronize.sh` needs a clean tree later.
git init && git add -A && git commit -m "Generate project"

# Run setup again, now that there is a repository to install the
# pre-commit hooks into.
./scripts/setup.sh

# Serve the API.
./scripts/start.sh
```
