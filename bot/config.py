from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    postgres_host: str = "db"
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_db: str = "assistant"
    postgres_user: str = "assistant"
    postgres_password: SecretStr = Field(min_length=1)

    @property
    def database_url(self) -> URL:
        return URL.create(
            "postgresql+asyncpg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )


class Settings(DatabaseSettings):
    bot_token: SecretStr = Field(min_length=1)
    ai_api_key: SecretStr = Field(min_length=1)
    ai_base_url: str = "https://openrouter.ai/api/v1"
    ai_model: str = Field(min_length=1)
    allowed_user_ids: str = ""
    allow_all_users: bool = True
    ai_timeout_seconds: float = Field(default=40, ge=1, le=120)
    ai_max_tokens: int = Field(default=1500, ge=16, le=8192)
    history_turns: int = Field(default=10, ge=0, le=50)
    context_max_chars: int = Field(default=24000, ge=1000, le=100000)
    max_input_chars: int = Field(default=4000, ge=1, le=4000)
    max_concurrent_requests: int = Field(default=8, ge=1, le=100)
    request_interval_seconds: float = Field(default=3, ge=0, le=60)
    system_prompt: str = Field(
        default="Отвечай по существу и на языке пользователя. Если не знаешь ответ, скажи об этом.",
        min_length=1,
        max_length=4000,
    )

    @field_validator("ai_base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        from urllib.parse import urlsplit

        parts = urlsplit(value)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.netloc
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
        ):
            raise ValueError("Expected an HTTP(S) base URL without credentials or query parameters")
        return value.rstrip("/")

    @field_validator("allowed_user_ids")
    @classmethod
    def validate_user_ids(cls, value: str) -> str:
        if value.strip():
            try:
                if any(int(item.strip()) <= 0 for item in value.split(",")):
                    raise ValueError
            except ValueError:
                raise ValueError("Expected positive Telegram IDs separated by commas") from None
        return value

    @property
    def allowed_ids(self) -> frozenset[int]:
        return frozenset(
            int(item.strip()) for item in self.allowed_user_ids.split(",") if item.strip()
        )
