from urllib.parse import quote_plus
from pydantic import field_validator
from pydantic_settings import BaseSettings


# Application specific settings, via environment variables.
class ApplicationSettings(BaseSettings):
    postgresql_server: str | None = "localhost"
    postgresql_port: int | None = 5432
    postgresql_database: str | None = "postgres"
    postgresql_user: str | None = "postgres"
    postgresql_password: str | None = "postgres"
    postgresql_connection_string: str

    # Use a pre validator to construct PostgreSQL database connection string.
    @field_validator("postgresql_connection_string", mode="before")
    def set_postgresql_connection_string(cls, values: dict) -> dict:

        # Get the needed environment variables for final construction.
        pre_postgresql_server: str = values.get("postgresql_server")
        pre_postgresql_port: int = int(values.get("postgresql_port"))
        pre_postgresql_database: str = values.get("postgresql_database")
        pre_postgresql_user: str = values.get("postgresql_user")
        pre_postgresql_password: str = values.get("postgresql_password")

        # Set the values back to the settings class before returning them.
        values["postgresql_connection_string"] = (
            "postgresql://{postgresql_user}:{postgresql_password}@{postgresql_server}:{postgresql_port}/{postgresql_database}".format(
                postgresql_user=pre_postgresql_user,
                postgresql_password=quote_plus(
                    pre_postgresql_password,
                    safe="",
                ),
                postgresql_server=pre_postgresql_server,
                postgresql_port=pre_postgresql_port,
                postgresql_database=pre_postgresql_database,
            )
        )
        return values

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
