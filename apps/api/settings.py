from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = Field(
        default="postgresql+psycopg://rag:rag@postgres:5432/rag",
        validation_alias="DATABASE_URL",
    )
    valkey_url: str = Field(default="redis://valkey:6379/0", validation_alias="VALKEY_URL")
    qdrant_url: str = Field(default="http://qdrant:6333", validation_alias="QDRANT_URL")
    qdrant_collection: str = Field(default="chunks_v1", validation_alias="QDRANT_COLLECTION")
    ollama_host: str = Field(default="http://ollama:11434", validation_alias="OLLAMA_HOST")
    ollama_model: str = Field(default="qwen3:8b", validation_alias="OLLAMA_MODEL")
    session_secret: str = Field(default="change-me-long-random", validation_alias="SESSION_SECRET")
    admin_login: str = Field(default="admin", validation_alias="ADMIN_LOGIN")
    # Default local password; change anytime via UI (/api/auth/change-password).
    admin_password: str = Field(default="admin", min_length=1, validation_alias="ADMIN_PASSWORD")
    data_root: str = Field(default="/var/lib/rag", validation_alias="DATA_ROOT")
    import_inbox: str = Field(default="/var/lib/rag/inbox", validation_alias="IMPORT_INBOX")
    import_inbox_host_hint: str = Field(
        default=r"D:\RAG5060\inbox",
        validation_alias="IMPORT_INBOX_HOST_HINT",
    )
    upload_max_mb: int = Field(default=25, validation_alias="UPLOAD_MAX_MB")
    model_import_max_gb: float = Field(default=20.0, validation_alias="MODEL_IMPORT_MAX_GB")
    embedding_dim: int = Field(default=1024, validation_alias="EMBEDDING_DIM")
    ollama_context_length: int = Field(default=4096, validation_alias="OLLAMA_CONTEXT_LENGTH")
    compose_profile: str = Field(default="core", validation_alias="COMPOSE_PROFILE")
    offline_mode: bool = Field(default=True, validation_alias="OFFLINE_MODE")

    @property
    def database_url_sync(self) -> str:
        url = self.database_url
        return url.replace("postgresql+asyncpg://", "postgresql+psycopg://").replace(
            "postgresql://", "postgresql+psycopg://"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
