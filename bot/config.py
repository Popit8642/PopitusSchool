from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


# bot/config.py → parent = bot/
BASE_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    BOT_TOKEN: str
    PROXY_URL: str | None = None

    DB_HOST: str
    DB_PORT: int
    DB_USER: str
    DB_PASS: str
    DB_NAME: str

    LLM_BASE_URL: str = "http://localhost:11434"
    LLM_MODEL_CHAT: str = "qwen2.5:3b-instruct-q4_K_M"

    EMBEDDING_BASE_URL: str = "http://localhost:11434"
    EMBEDDING_MODEL: str = "embeddinggemma"
    EMBEDDING_DIM: int = 768

    REDIS_URL: str = "redis://localhost:6379"

    @property
    def DATABASE_URL_psycopg(self) -> str:
        return (
            f"postgresql+psycopg://{self.DB_USER}:{self.DB_PASS}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )

    @property
    def DATABASE_URL_asyncpg(self) -> str:
        return (
            f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASS}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()