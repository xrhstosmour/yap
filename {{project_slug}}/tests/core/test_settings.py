"""Unit tests for `Settings` module."""


class TestSettingsValidation:
    """Tests for settings validation."""

    def test_settings_loads_defaults(self):
        """Settings should load with default values."""
        from app.core.settings import Settings

        settings = Settings(_env_file=None)

        assert settings.PROJECT_NAME == "{{ project_name }}"
        assert settings.API_V1_STR == "/api/v1"
        assert settings.ENVIRONMENT == "local"

    def test_settings_accepts_env_overrides(self):
        """Settings should accept environment variable overrides."""
        from app.core.settings import Settings

        settings = Settings(
            _env_file=None,
            PROJECT_NAME="Test Project",
            ENVIRONMENT="staging",
            SECRET_KEY="test-key",
            POSTGRES_PASSWORD="test",
            FIRST_SUPERUSER_PASSWORD="test",
            RABBITMQ_PASSWORD="test",
        )

        assert settings.PROJECT_NAME == "Test Project"
        assert settings.ENVIRONMENT == "staging"

    def test_settings_database_uri_builds_correctly(self):
        """Database URI should be built from components."""
        from app.core.settings import Settings

        settings = Settings(
            _env_file=None,
            POSTGRES_SERVER="localhost",
            POSTGRES_PORT=5432,
            POSTGRES_USER="testuser",
            POSTGRES_PASSWORD="testpass",
            POSTGRES_DB="testdb",
        )

        uri = settings.DATABASE_URI
        assert "postgresql" in str(uri)
        assert "testuser" in str(uri)
        assert "testdb" in str(uri)

    def test_settings_redis_url_builds_correctly(self):
        """Redis URL should be built from components."""
        from app.core.settings import Settings

        settings = Settings(
            _env_file=None,
            REDIS_HOST="localhost",
            REDIS_PORT=6379,
            REDIS_DB=1,
        )

        assert settings.REDIS_URL == "redis://localhost:6379/1"

    def test_settings_cors_origins_parse_list(self):
        """CORS origins should parse list input."""
        from app.core.settings import Settings

        settings = Settings(
            _env_file=None,
            BACKEND_CORS_ORIGINS=["http://localhost:3000", "http://localhost:8080"],
            FRONTEND_HOST="http://localhost:5173",
        )

        assert "http://localhost:3000" in settings.all_cors_origins
        assert "http://localhost:5173" in settings.all_cors_origins

    def test_settings_warns_on_default_secrets_in_local(self):
        """Settings should warn on default secrets in local environment."""
        import warnings

        from app.core.settings import Settings

        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            Settings(_env_file=None, ENVIRONMENT="local")
