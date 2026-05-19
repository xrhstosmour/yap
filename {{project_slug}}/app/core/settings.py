"""Application configuration using Pydantic Settings.

This module defines all application settings with environment variable support.
Settings are validated at startup to ensure required secrets are configured.
"""

from __future__ import annotations

import warnings
from typing import Annotated
from typing import Any
from typing import Literal
from typing import Self

from pydantic import AnyUrl
from pydantic import BeforeValidator
from pydantic import EmailStr
from pydantic import HttpUrl
from pydantic import PostgresDsn
from pydantic import computed_field
from pydantic import model_validator
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict


def parse_cors(v: Any) -> list[str] | str:
    """Parse CORS origins from string or list format.

    Handles both comma-separated strings and list inputs for CORS origins.
    Used as a BeforeValidator to normalize the CORS configuration.

    Args:
        v: CORS origins as string or list

    Returns:
        List of CORS origin strings

    Raises:
        ValueError: If input format is invalid
    """
    if isinstance(v, str) and not v.startswith("["):
        return [i.strip() for i in v.split(",") if i.strip()]
    elif isinstance(v, (list, str)):
        return v
    raise ValueError(v)


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    All settings can be overridden via environment variables. Sensitive values
    are validated to ensure they are not using default/placeholder values
    in non-local environments.

    Attributes:
        API_V1_STR: URL prefix for API versioning
        PROJECT_NAME: Display name for the application
        ENVIRONMENT: Deployment environment (local/staging/production)
        SECRET_KEY: Cryptographic key for JWT signing
        ACCESS_TOKEN_EXPIRE_MINUTES: JWT access token lifetime
        REFRESH_TOKEN_EXPIRE_DAYS: Refresh token lifetime
        FRONTEND_HOST: Frontend application URL for CORS
        BACKEND_CORS_ORIGINS: Allowed CORS origins

    Database:
        POSTGRES_SERVER: PostgreSQL host
        POSTGRES_PORT: PostgreSQL port
        POSTGRES_USER: Database user
        POSTGRES_PASSWORD: Database password
        POSTGRES_DB: Database name

    Redis:
        REDIS_HOST: Redis server host
        REDIS_PORT: Redis server port
        REDIS_DB: Redis database number

    Celery:
        CELERY_BROKER_URL: Celery message broker URL
        CELERY_RESULT_BACKEND: Celery result storage URL

    Rate Limiting:
        RATE_LIMIT_PER_MINUTE: Default rate limit per user
        RATE_LIMIT_PER_MINUTE_API_KEY: Rate limit per API key
    """

    model_config = SettingsConfigDict(
        env_file="../.env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    def __init__(self, **values: Any) -> None:
        super().__init__(**values)

    # API Configuration.
    API_V1_STR: str = "/api/v1"
    PROJECT_NAME: str = "{{ project_name }}"
    ENVIRONMENT: Literal["local", "staging", "production"] = "local"

    # Security.
    SECRET_KEY: str = "change_this"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    ALGORITHM: str = "HS256"

    # CORS.
    FRONTEND_HOST: str = "http://localhost:5173"
    BACKEND_CORS_ORIGINS: Annotated[
        list[AnyUrl | str] | str, BeforeValidator(parse_cors)
    ] = []

    @computed_field  # type: ignore[prop-decorator]  # pyright: ignore[reportDecoratorUsage]
    @property
    def all_cors_origins(self) -> list[str]:
        """Get all CORS origins including frontend host."""
        if isinstance(self.BACKEND_CORS_ORIGINS, str):
            parsed_origins = parse_cors(self.BACKEND_CORS_ORIGINS)
            if isinstance(parsed_origins, str):
                origins = [parsed_origins]
            else:
                origins = parsed_origins
        else:
            origins = [str(o) for o in self.BACKEND_CORS_ORIGINS]

        return [str(origin).rstrip("/") for origin in origins] + [self.FRONTEND_HOST]

    # PostgreSQL Database.
    POSTGRES_SERVER: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "{{ project_slug }}"
    POSTGRES_PASSWORD: str = "change_this"
    POSTGRES_DB: str = "{{ project_slug }}"
    POSTGRES_SSL_MODE: str = ""
    POSTGRES_SSL_CA: str = ""
    POSTGRES_SSL_CERT: str = ""
    POSTGRES_SSL_KEY: str = ""

    @computed_field  # type: ignore[prop-decorator]  # pyright: ignore[reportDecoratorUsage]
    @property
    def DATABASE_URI(self) -> PostgresDsn:
        """Build PostgreSQL connection string from components."""
        query_params: dict[str, str] = {}

        if self.POSTGRES_SSL_MODE:
            query_params["sslmode"] = self.POSTGRES_SSL_MODE
        if self.POSTGRES_SSL_CA:
            query_params["sslrootcert"] = self.POSTGRES_SSL_CA
        if self.POSTGRES_SSL_CERT:
            query_params["sslcert"] = self.POSTGRES_SSL_CERT
        if self.POSTGRES_SSL_KEY:
            query_params["sslkey"] = self.POSTGRES_SSL_KEY

        query = None
        if query_params:
            query = "&".join(f"{k}={v}" for k, v in query_params.items())

        return PostgresDsn.build(
            scheme="postgresql+psycopg",
            username=self.POSTGRES_USER,
            password=self.POSTGRES_PASSWORD,
            host=self.POSTGRES_SERVER,
            port=self.POSTGRES_PORT,
            path=self.POSTGRES_DB,
            query=query,
        )

    # Redis.
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0

    @computed_field  # type: ignore[prop-decorator]  # pyright: ignore[reportDecoratorUsage]
    @property
    def REDIS_URL(self) -> str:
        """Build Redis connection URL from components."""
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    # Celery.
    RABBITMQ_HOST: str = "localhost"
    RABBITMQ_PORT: int = 5672
    RABBITMQ_USER: str = "{{ project_slug }}"
    RABBITMQ_PASSWORD: str = "change_this"
    CELERY_BROKER_URL: str = ""

    @computed_field  # type: ignore[prop-decorator]  # pyright: ignore[reportDecoratorUsage]
    @property
    def CELERY_BROKER_URL_FINAL(self) -> str:
        """Resolve the effective Celery broker URL.

        Uses explicitly set CELERY_BROKER_URL if provided, otherwise
        constructs it from RabbitMQ settings.
        """
        if self.CELERY_BROKER_URL:
            return self.CELERY_BROKER_URL
        return f"amqp://{self.RABBITMQ_USER}:{self.RABBITMQ_PASSWORD}@{self.RABBITMQ_HOST}:{self.RABBITMQ_PORT}/%2F"

    @computed_field  # type: ignore[prop-decorator]  # pyright: ignore[reportDecoratorUsage]
    @property
    def CELERY_RESULT_BACKEND(self) -> str:
        """Build Celery result backend URL from Redis settings."""
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB + 1}"

    # Rate Limiting.
    RATE_LIMIT_PER_MINUTE: int = 60
    RATE_LIMIT_PER_MINUTE_API_KEY: int = 300

    # Performance Tuning.
    DATABASE_POOL_SIZE: int = 10
    DATABASE_MAX_OVERFLOW: int = 20
    DATABASE_POOL_RECYCLE: int = 3600
    REDIS_MAX_CONNECTIONS: int = 50

    # Superuser (created on first startup).
    FIRST_SUPERUSER_EMAIL: EmailStr = "admin@example.com"
    FIRST_SUPERUSER_PASSWORD: str = "change_this"
    FIRST_SUPERUSER_FULL_NAME: str | None = None

    # Feature Flags (default states for features not yet in DB).
    # Add flags here that should be enabled by default:.
    # FEATURE_FLAGS: dict[str, bool] = {"new_checkout": True}
    FEATURE_FLAGS: dict[str, bool] = {}

    # Sentry Monitoring.
    SENTRY_DSN: HttpUrl | None = None

    # Email (SMTP) - for sending transactional emails.
    SMTP_HOST: str = "localhost"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM_EMAIL: str = "noreply@example.com"
    SMTP_USE_TLS: bool = True
    CRYPTO_KEY: str = ""

    def _check_default_secret(self, var_name: str, value: str | None) -> None:
        """Check if a secret value is still the default placeholder.

        Warns in local environment, raises error in production.
        This prevents deploying with insecure default values.

        Args:
            var_name: Name of the environment variable
            value: Current value of the variable
        """
        if value == "change_this":
            message = (
                f'The value of {var_name} is "change_this", '
                "for security, please change it, at least for deployments."
            )
            if self.ENVIRONMENT == "local":
                warnings.warn(message, stacklevel=1)
            else:
                raise ValueError(message)

    @model_validator(mode="after")
    def _enforce_non_default_secrets(self) -> Self:
        """Validate all secrets are properly configured for non-local environments."""
        self._check_default_secret("SECRET_KEY", self.SECRET_KEY)
        self._check_default_secret("POSTGRES_PASSWORD", self.POSTGRES_PASSWORD)
        self._check_default_secret(
            "FIRST_SUPERUSER_PASSWORD", self.FIRST_SUPERUSER_PASSWORD
        )
        self._check_default_secret("RABBITMQ_PASSWORD", self.RABBITMQ_PASSWORD)

        return self


# Global settings instance - load once at module import.
settings = Settings()
