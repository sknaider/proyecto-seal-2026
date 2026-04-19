"""SEAL Memory System — Centralized configuration via Pydantic BaseSettings.

All credentials and service URLs are loaded from environment variables.
Falls back to defaults suitable for local development.

Usage:
    from config import settings, PERU_TZ, now_lima

    # Access any setting:
    settings.pg_dsn
    settings.neo4j_password
    settings.qdrant_url

    # Timezone-aware timestamps:
    now_lima()                    # datetime with America/Lima tz
    now_lima().isoformat()        # 2026-04-08T22:30:00-05:00
    format_lima(utc_dt)           # convert UTC datetime to Lima display string
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# ── Timezone centralizada para todo SEAL ──
PERU_TZ = ZoneInfo("America/Lima")


def now_lima() -> datetime:
    """Current time in America/Lima (UTC-5, no DST)."""
    return datetime.now(PERU_TZ)


def now_utc() -> datetime:
    """Current time in UTC (for DB storage)."""
    return datetime.now(timezone.utc)


def format_lima(dt: datetime | None = None, fmt: str = "%Y-%m-%dT%H:%M:%S%z") -> str:
    """Format a datetime as Lima local time string.

    If dt is None, uses current time.
    If dt is UTC-aware, converts to Lima first.
    """
    if dt is None:
        dt = now_lima()
    elif dt.tzinfo is not None and dt.utcoffset() != PERU_TZ.utcoffset(dt):
        dt = dt.astimezone(PERU_TZ)
    return dt.strftime(fmt)


def utc_to_lima(dt: datetime) -> datetime:
    """Convert a UTC datetime to America/Lima."""
    return dt.astimezone(PERU_TZ)


try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
    _PYDANTIC_V2 = True
except ImportError:
    from pydantic import BaseSettings  # type: ignore[assignment]
    _PYDANTIC_V2 = False


class SealSettings(BaseSettings):
    """All externalized configuration for the SEAL Memory System."""

    # ── PostgreSQL ──────────────────────────────────────────────────────────
    pg_host: str = "localhost"
    pg_port: int = 5433
    pg_user: str = "seal"
    pg_password: str = "seal_memory_2026"
    pg_database: str = "seal_memory"
    pg_pool_min: int = 1
    pg_pool_max: int = 3

    @property
    def pg_dsn(self) -> str:
        return (
            f"postgresql://{self.pg_user}:{self.pg_password}"
            f"@{self.pg_host}:{self.pg_port}/{self.pg_database}"
        )

    # ── Qdrant ──────────────────────────────────────────────────────────────
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    qdrant_collection: str = "soul_memories"

    @property
    def qdrant_url(self) -> str:
        return f"http://{self.qdrant_host}:{self.qdrant_port}"

    # ── Neo4j ───────────────────────────────────────────────────────────────
    neo4j_host: str = "localhost"
    neo4j_port: int = 7687
    neo4j_user: str = "neo4j"
    neo4j_password: str = "seal2026soul"

    @property
    def neo4j_uri(self) -> str:
        return f"bolt://{self.neo4j_host}:{self.neo4j_port}"

    @property
    def neo4j_auth(self) -> tuple[str, str]:
        return (self.neo4j_user, self.neo4j_password)

    # ── Ollama ──────────────────────────────────────────────────────────────
    ollama_host: str = "localhost"
    ollama_port: int = 11434
    ollama_model: str = "qwen2.5:7b"

    @property
    def ollama_gen_url(self) -> str:
        return f"http://{self.ollama_host}:{self.ollama_port}/api/generate"

    # ── Web Chat Bridge ─────────────────────────────────────────────────────
    web_chat_host: str = "localhost"
    web_chat_port: int = 8765

    @property
    def web_chat_url(self) -> str:
        return f"http://{self.web_chat_host}:{self.web_chat_port}"

    # ── Soul mode ───────────────────────────────────────────────────────────
    soul_lite: bool = False  # SOUL_LITE=true → PostgreSQL+pgvector only

    if _PYDANTIC_V2:
        model_config = SettingsConfigDict(
            env_file=".env",
            env_file_encoding="utf-8",
            case_sensitive=False,
            extra="ignore",
        )
    else:
        class Config:
            env_file = ".env"
            env_file_encoding = "utf-8"
            case_sensitive = False


settings = SealSettings()
