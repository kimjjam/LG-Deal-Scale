from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from pydantic import EmailStr, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[1] / ".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "LG Deal Scale API"
    cors_origins: str = "http://localhost:5173"
    database_url: str = "sqlite+aiosqlite:///./directdesk.db"
    database_readonly_url: str | None = None
    database_readonly_password: SecretStr | None = Field(default=None, min_length=16)
    jwt_secret_key: str | None = Field(default=None, min_length=32)
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 480
    llm_provider: Literal["google"] = "google"
    llm_model: str = "gemini-3.5-flash-lite"
    gemini_api_key: str | None = None
    localdata_api_key: str | None = None
    naver_client_id: str | None = None
    naver_client_secret: str | None = None
    outbound_email_mode: Literal["dry_run", "test_override"] = "dry_run"
    test_email_address: EmailStr | None = None
    email_provider_api_key: str | None = None
    seed_staff_password: str | None = Field(default=None, min_length=12)
    owner_name: str | None = Field(default=None, min_length=1, max_length=100)
    owner_email: EmailStr | None = None
    owner_password: SecretStr | None = Field(default=None, min_length=12, max_length=128)

    @field_validator("database_url", "database_readonly_url", mode="before")
    @classmethod
    def use_asyncpg_driver(cls, value: object) -> object:
        if not isinstance(value, str) or not value.startswith(
            ("postgresql://", "postgresql+asyncpg://")
        ):
            return value
        value = value.replace("postgresql://", "postgresql+asyncpg://", 1)
        parts = urlsplit(value)
        query = [
            ("ssl" if key == "sslmode" else key, item)
            for key, item in parse_qsl(parts.query, keep_blank_values=True)
            if key != "channel_binding"
        ]
        return urlunsplit((*parts[:3], urlencode(query), parts.fragment))

    @property
    def effective_database_readonly_url(self) -> str | None:
        if self.database_readonly_url:
            return self.database_readonly_url
        if not self.database_readonly_password or not self.database_url.startswith("postgresql"):
            return None
        parts = urlsplit(self.database_url)
        host = parts.netloc.rsplit("@", 1)[-1]
        password = quote(self.database_readonly_password.get_secret_value(), safe="")
        return urlunsplit((parts.scheme, f"directdesk_readonly:{password}@{host}", *parts[2:]))


@lru_cache
def get_settings() -> Settings:
    return Settings()
