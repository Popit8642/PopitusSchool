import logging
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


# bot/config.py -> parent = bot/, parent.parent = корень проекта
BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent


def normalize_openai_base_url(url: str) -> str:
    """Приводит адрес OpenAI-совместимого API к виду .../v1."""
    url = url.rstrip("/")
    if not url.endswith("/v1"):
        url = f"{url}/v1"
    return url


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
    LLM_TIMEOUT_SECONDS: int = 240
    LLM_MAX_TOKENS: int = 3000
    LLM_MAX_RETRIES: int = 0
    LLM_MAX_CONCURRENCY: int = 1

    EMBEDDING_BASE_URL: str = "http://localhost:11434"
    EMBEDDING_MODEL: str = "embeddinggemma"
    EMBEDDING_DIM: int = 768
    EMBEDDING_BATCH_SIZE: int = 16

    REDIS_URL: str = "redis://localhost:6379"

    RAG_TOP_K: int = 5
    RAG_MAX_DISTANCE: float = 0.7

    MAX_PDF_SIZE_MB: int = 20
    MIN_PDF_SIZE_KB: int = 10
    OCR_LANG: str = "rus+eng"
    INGEST_JOB_TIMEOUT_SECONDS: int = 3600

    MODERATION_MODEL: str = "qwen2.5:3b-instruct-q4_K_M"
    MODERATION_TIMEOUT_SECONDS: int = 60
    MODERATION_MAX_CHARS: int = 2000

    QUIZ_CONTEXT_TTL_SECONDS: int = 3600
    TUTOR_CONTEXT_TTL_SECONDS: int = 21600

    PRO_PRICE_STARS: int = 100

    BROADCAST_COOLDOWN_SECONDS: int = 60
    BROADCAST_DELAY_SECONDS: float = 0.05

    ADMIN_IDS: str = ""
    LOG_LEVEL: str = "INFO"
    UPLOAD_DIR: str | None = None

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

    @property
    def LLM_OPENAI_BASE_URL(self) -> str:
        return normalize_openai_base_url(self.LLM_BASE_URL)

    @property
    def EMBEDDING_OPENAI_BASE_URL(self) -> str:
        return normalize_openai_base_url(self.EMBEDDING_BASE_URL)

    @property
    def MAX_PDF_SIZE_BYTES(self) -> int:
        return self.MAX_PDF_SIZE_MB * 1024 * 1024

    @property
    def admin_ids(self) -> list[int]:
        result: list[int] = []
        for part in self.ADMIN_IDS.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                result.append(int(part))
            except ValueError:
                logging.getLogger(__name__).warning(
                    "Некорректный ADMIN_IDS: %r", part
                )
        return result

    @property
    def upload_dir(self) -> Path:
        if self.UPLOAD_DIR:
            path = Path(self.UPLOAD_DIR).expanduser()
            if not path.is_absolute():
                path = PROJECT_DIR / path
            return path
        return PROJECT_DIR / "data" / "uploads"

    @property
    def log_level(self) -> int:
        return getattr(logging, self.LOG_LEVEL.upper(), logging.INFO)

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
