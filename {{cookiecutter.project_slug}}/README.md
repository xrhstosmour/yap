
# FastAPI

This boilerplate provides a quick start for building high-performance, efficient web applications using FastAPI.

## Features

- **FastAPI** for creating RESTful APIs with Python 3.11+.
- **Uvicorn** as the lightning-fast ASGI server.
- **Pydantic** for data validation.
- **SQLAlchemy** for ORM support.
- **Alembic** for database migrations.
- **JWT Authentication** for secure routes.
- Docker support for easy deployment.
- Pre-configured **Pytest** setup for testing.
- Integration with **GitHub Actions** for CI/CD.

## Installation

First, navigate into your project directory:

``` bash
cd <project_name>
```

Ensure you have `poetry` installed. If not, you can install it using pip:

``` bash
pip install poetry
```

Install dependencies:

``` bash
poetry install
```

## Getting Started

To get your FastAPI server running:

``` bash
uvicorn app.main:app --reload
```

## Running Tests

Run tests using pytest:

``` bash
pytest
```

## Docker Support

To build and run your application in a Docker container:

``` bash
docker-compose up -d
```

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- FastAPI community
- Cookiecutter project
- All contributors of this project
