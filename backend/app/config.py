from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "CaringTransitions API"
    secret_key: str = Field(default="change-me-dev-secret", alias="SECRET_KEY")
    database_url: str = Field(
        default="sqlite:///./app.db",
        alias="DATABASE_URL",
    )
    google_api_key: str = Field(default="", alias="GOOGLE_API_KEY")
    gemini_model: str = Field(default="gemini-flash-latest", alias="GEMINI_MODEL")
    gemini_api_version: str = Field(default="v1beta", alias="GEMINI_API_VERSION")
    skip_tape_verify: bool = Field(default=False, alias="SKIP_TAPE_VERIFY")


settings = Settings()

