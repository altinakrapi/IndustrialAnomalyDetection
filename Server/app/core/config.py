
import os

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

from app.core.postgres_runtime import configure_postgres_dll_search_path


configure_postgres_dll_search_path()


class Settings(BaseSettings):
    DATABASE_URL: str
    SECRET_KEY: str
    CORS_ORIGINS: str = (
        "http://localhost:5173,http://localhost:5174,"
        "http://127.0.0.1:5173,http://127.0.0.1:5174"
    )
    UPLOAD_DIR: str = "uploads"
    MAX_UPLOAD_SIZE_MB: int = 2048
    NASA_IMS_SOURCE_URL: str = (
        "https://phm-datasets.s3.amazonaws.com/NASA/4.+Bearings.zip"
    )
    NASA_IMS_MAX_DOWNLOAD_SIZE_MB: int = Field(default=2048, ge=1, le=2048)
    ARCHIVE_TOOL: str = "tar.exe" if os.name == "nt" else "bsdtar"
    ADMIN_EMAIL: str | None = None
    ADMIN_PASSWORD: str | None = None
    ADMIN_FULL_NAME: str = "System Administrator"
    SMTP_HOST: str | None = None
    SMTP_PORT: int = 587
    SMTP_USERNAME: str | None = None
    SMTP_PASSWORD: str | None = None
    SMTP_FROM_EMAIL: str | None = None
    PASSWORD_RESET_FRONTEND_URL: str = "http://localhost:5173"

    @field_validator("DATABASE_URL")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        if make_url(value).drivername != "postgresql+psycopg":
            raise ValueError("DATABASE_URL must use PostgreSQL with the psycopg driver")
        return value

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
