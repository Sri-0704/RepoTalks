import logging
import math
import os
from pathlib import Path
from typing import Optional

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_NAME: str = "RepoTalks AI Backend"
    PORT: int = int(os.getenv("PORT", "8080"))
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    DEFAULT_FLASH_MODEL: str = os.getenv("DEFAULT_FLASH_MODEL", "gemini-3.6-flash")
    DEFAULT_PRO_MODEL: str = os.getenv("DEFAULT_PRO_MODEL", "gemini-3.6-flash")
    GENERATION_FALLBACK_MODEL: Optional[str] = os.getenv("GENERATION_FALLBACK_MODEL", "gemini-3.5-flash")
    EMBEDDING_MODEL: str = "gemini-embedding-2"
    EMBEDDING_DIMENSION: int = int(os.getenv("EMBEDDING_DIMENSION", "768"))
    MAX_UPLOAD_SIZE_MB: int = int(os.getenv("MAX_UPLOAD_SIZE_MB", "50"))

    # ── LLM Gateway & Provider configuration ──────────────────────────
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "auto")
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL: str = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
    LLM_REQUEST_TIMEOUT_S: float = float(os.getenv("LLM_REQUEST_TIMEOUT_S", "60.0"))
    LLM_FIRST_CONTENT_TIMEOUT_S: float = float(os.getenv("LLM_FIRST_CONTENT_TIMEOUT_S", "15.0"))
    LLM_MAX_OUTPUT_TOKENS: int = int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "4096"))
    GROQ_CONNECT_TIMEOUT_S: float = float(os.getenv("GROQ_CONNECT_TIMEOUT_S", "5.0"))
    GROQ_READ_TIMEOUT_S: float = float(os.getenv("GROQ_READ_TIMEOUT_S", "60.0"))
    MAX_EXTRACTED_SIZE_MB: int = int(os.getenv("MAX_EXTRACTED_SIZE_MB", "250"))
    MAX_ARCHIVE_FILES: int = int(os.getenv("MAX_ARCHIVE_FILES", "10000"))
    MAX_SOURCE_FILE_SIZE_MB: int = int(os.getenv("MAX_SOURCE_FILE_SIZE_MB", "5"))
    MAX_IMAGE_SIZE_MB: int = int(os.getenv("MAX_IMAGE_SIZE_MB", "10"))
    GITHUB_CLONE_TIMEOUT_SECONDS: int = int(os.getenv("GITHUB_CLONE_TIMEOUT_SECONDS", "120"))

    # ── Generation retry configuration ────────────────────────────────
    GENERATION_MAX_RETRIES: int = int(os.getenv("GENERATION_MAX_RETRIES", "3"))
    GENERATION_BACKOFF_BASE_S: float = float(os.getenv("GENERATION_BACKOFF_BASE_S", "1.0"))
    GENERATION_BACKOFF_MAX_S: float = float(os.getenv("GENERATION_BACKOFF_MAX_S", "8.0"))
    GENERATION_RETRY_DEADLINE_S: float = float(os.getenv("GENERATION_RETRY_DEADLINE_S", "30.0"))

    # ── Embedding batch & retry configuration ──────────────────────────
    # Application policies — NOT Gemini quota rules.
    EMBEDDING_MAX_BATCH_INPUTS: int = int(os.getenv("EMBEDDING_MAX_BATCH_INPUTS", "25"))
    EMBEDDING_MAX_BATCH_CHARS: int = int(os.getenv("EMBEDDING_MAX_BATCH_CHARS", "30000"))
    EMBEDDING_INTER_BATCH_DELAY_S: float = float(os.getenv("EMBEDDING_INTER_BATCH_DELAY_S", "0.5"))
    EMBEDDING_MAX_RETRIES: int = int(os.getenv("EMBEDDING_MAX_RETRIES", "6"))
    EMBEDDING_BACKOFF_BASE_S: float = float(os.getenv("EMBEDDING_BACKOFF_BASE_S", "2.0"))
    EMBEDDING_BACKOFF_MAX_S: float = float(os.getenv("EMBEDDING_BACKOFF_MAX_S", "60.0"))
    EMBEDDING_RETRY_DEADLINE_S: float = float(os.getenv("EMBEDDING_RETRY_DEADLINE_S", "180.0"))

    # ── Optional quota-aware scheduling ────────────────────────────────
    # Default to None (unset). When unset, RepoTalk uses best-effort
    # pacing via minimum request spacing and provider cooldown
    # observation. It cannot guarantee quota compliance without actual
    # project limits.  Do not invent universal defaults.
    EMBEDDING_RPM_LIMIT: Optional[int] = Field(
        default_factory=lambda: _parse_optional_int(os.getenv("EMBEDDING_RPM_LIMIT")),
    )
    EMBEDDING_TPM_LIMIT: Optional[int] = Field(
        default_factory=lambda: _parse_optional_int(os.getenv("EMBEDDING_TPM_LIMIT")),
    )
    EMBEDDING_DIMENSION: int = int(os.getenv("EMBEDDING_DIMENSION", "768"))
    EMBEDDING_MIN_REQUEST_INTERVAL_S: float = float(
        os.getenv("EMBEDDING_MIN_REQUEST_INTERVAL_S", "0.5")
    )

    # ── Staging checkpoint configuration ───────────────────────────────
    # Configurable TTL (1–168 hours). 0 disables cross-attempt
    # checkpoints.  Default 24 hours balances retry window vs disk.
    EMBEDDING_CHECKPOINT_TTL_HOURS: int = int(
        os.getenv("EMBEDDING_CHECKPOINT_TTL_HOURS", "24")
    )
    # Maximum staging rows before oldest are evicted (prevents
    # abandoned ingestion from growing SQLite indefinitely).
    EMBEDDING_CHECKPOINT_MAX_ROWS: int = int(
        os.getenv("EMBEDDING_CHECKPOINT_MAX_ROWS", "50000")
    )

    CORS_ORIGINS: str = os.getenv(
        "CORS_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000,http://localhost:3001,http://127.0.0.1:3001,http://localhost:3002,http://127.0.0.1:3002"
    )
    CORS_ORIGIN_REGEX: Optional[str] = os.getenv("CORS_ORIGIN_REGEX", r"^https://.*\.vercel\.app$")
    COOKIE_SECURE: Optional[bool] = (
        os.getenv("COOKIE_SECURE").lower() in ("true", "1")
        if os.getenv("COOKIE_SECURE") is not None
        else None
    )
    RATE_LIMIT_INGEST_PER_MINUTE: int = int(os.getenv("RATE_LIMIT_INGEST_PER_MINUTE", "10"))
    RATE_LIMIT_CHAT_PER_MINUTE: int = int(os.getenv("RATE_LIMIT_CHAT_PER_MINUTE", "60"))
    DATA_DIR: Path = Field(default_factory=lambda: Path(os.getenv("DATA_DIR", Path(__file__).parent.parent / "data")))

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    @model_validator(mode="after")
    def validate_embedding_config(self) -> "Settings":
        """Reject invalid embedding configuration at startup."""
        errors: list[str] = []

        def _check_positive(name: str, val: float) -> None:
            if val <= 0 or not math.isfinite(val):
                errors.append(f"{name} must be a positive finite number (got {val})")

        def _check_non_negative(name: str, val: float) -> None:
            if val < 0 or not math.isfinite(val):
                errors.append(f"{name} must be a non-negative finite number (got {val})")

        _check_positive("EMBEDDING_MAX_BATCH_INPUTS", self.EMBEDDING_MAX_BATCH_INPUTS)
        _check_positive("EMBEDDING_MAX_BATCH_CHARS", self.EMBEDDING_MAX_BATCH_CHARS)
        _check_positive("EMBEDDING_DIMENSION", self.EMBEDDING_DIMENSION)
        _check_non_negative("EMBEDDING_INTER_BATCH_DELAY_S", self.EMBEDDING_INTER_BATCH_DELAY_S)
        _check_non_negative("EMBEDDING_MAX_RETRIES", self.EMBEDDING_MAX_RETRIES)
        _check_positive("EMBEDDING_BACKOFF_BASE_S", self.EMBEDDING_BACKOFF_BASE_S)
        _check_positive("EMBEDDING_BACKOFF_MAX_S", self.EMBEDDING_BACKOFF_MAX_S)
        _check_positive("EMBEDDING_RETRY_DEADLINE_S", self.EMBEDDING_RETRY_DEADLINE_S)
        _check_non_negative("EMBEDDING_MIN_REQUEST_INTERVAL_S", self.EMBEDDING_MIN_REQUEST_INTERVAL_S)

        _check_non_negative("GENERATION_MAX_RETRIES", self.GENERATION_MAX_RETRIES)
        _check_positive("GENERATION_BACKOFF_BASE_S", self.GENERATION_BACKOFF_BASE_S)
        _check_positive("GENERATION_BACKOFF_MAX_S", self.GENERATION_BACKOFF_MAX_S)
        _check_positive("GENERATION_RETRY_DEADLINE_S", self.GENERATION_RETRY_DEADLINE_S)

        if self.EMBEDDING_BACKOFF_MAX_S < self.EMBEDDING_BACKOFF_BASE_S:
            errors.append(
                f"EMBEDDING_BACKOFF_MAX_S ({self.EMBEDDING_BACKOFF_MAX_S}) "
                f"must be >= EMBEDDING_BACKOFF_BASE_S ({self.EMBEDDING_BACKOFF_BASE_S})"
            )

        if self.GENERATION_BACKOFF_MAX_S < self.GENERATION_BACKOFF_BASE_S:
            errors.append(
                f"GENERATION_BACKOFF_MAX_S ({self.GENERATION_BACKOFF_MAX_S}) "
                f"must be >= GENERATION_BACKOFF_BASE_S ({self.GENERATION_BACKOFF_BASE_S})"
            )

        if self.GENERATION_RETRY_DEADLINE_S < self.GENERATION_BACKOFF_BASE_S:
            errors.append(
                f"GENERATION_RETRY_DEADLINE_S ({self.GENERATION_RETRY_DEADLINE_S}) "
                f"must be >= GENERATION_BACKOFF_BASE_S ({self.GENERATION_BACKOFF_BASE_S})"
            )

        if self.GENERATION_FALLBACK_MODEL and self.GENERATION_FALLBACK_MODEL.strip():
            fallback_clean = self.GENERATION_FALLBACK_MODEL.strip()
            if fallback_clean == self.DEFAULT_FLASH_MODEL.strip() or fallback_clean == self.DEFAULT_PRO_MODEL.strip():
                logger.warning(
                    "GENERATION_FALLBACK_MODEL (%s) matches primary model (%s). "
                    "Distinct fallback failover will be disabled.",
                    self.GENERATION_FALLBACK_MODEL,
                    self.DEFAULT_FLASH_MODEL,
                )

        if self.EMBEDDING_RPM_LIMIT is not None and self.EMBEDDING_RPM_LIMIT <= 0:
            errors.append(f"EMBEDDING_RPM_LIMIT must be positive when set (got {self.EMBEDDING_RPM_LIMIT})")
        if self.EMBEDDING_TPM_LIMIT is not None and self.EMBEDDING_TPM_LIMIT <= 0:
            errors.append(f"EMBEDDING_TPM_LIMIT must be positive when set (got {self.EMBEDDING_TPM_LIMIT})")

        if not (0 <= self.EMBEDDING_CHECKPOINT_TTL_HOURS <= 168):
            errors.append(
                f"EMBEDDING_CHECKPOINT_TTL_HOURS must be 0–168 (got {self.EMBEDDING_CHECKPOINT_TTL_HOURS}). "
                f"0 disables cross-attempt checkpoints."
            )
        _check_positive("EMBEDDING_CHECKPOINT_MAX_ROWS", self.EMBEDDING_CHECKPOINT_MAX_ROWS)

        # Validate LLM Gateway & Groq settings
        allowed_providers = {"auto", "groq", "gemini"}
        if self.LLM_PROVIDER not in allowed_providers:
            errors.append(f"LLM_PROVIDER must be one of {sorted(allowed_providers)} (got {self.LLM_PROVIDER!r})")
        if self.LLM_PROVIDER == "groq" and not (self.GROQ_MODEL and self.GROQ_MODEL.strip()):
            errors.append("GROQ_MODEL must be non-empty when LLM_PROVIDER is 'groq'")
        _check_positive("LLM_REQUEST_TIMEOUT_S", self.LLM_REQUEST_TIMEOUT_S)
        _check_positive("LLM_FIRST_CONTENT_TIMEOUT_S", self.LLM_FIRST_CONTENT_TIMEOUT_S)
        if self.LLM_FIRST_CONTENT_TIMEOUT_S > self.LLM_REQUEST_TIMEOUT_S:
            errors.append(
                f"LLM_FIRST_CONTENT_TIMEOUT_S ({self.LLM_FIRST_CONTENT_TIMEOUT_S}s) cannot exceed "
                f"LLM_REQUEST_TIMEOUT_S ({self.LLM_REQUEST_TIMEOUT_S}s)"
            )
        _check_positive("LLM_MAX_OUTPUT_TOKENS", self.LLM_MAX_OUTPUT_TOKENS)
        _check_positive("GROQ_CONNECT_TIMEOUT_S", self.GROQ_CONNECT_TIMEOUT_S)
        _check_positive("GROQ_READ_TIMEOUT_S", self.GROQ_READ_TIMEOUT_S)

        if errors:
            raise ValueError(
                "Invalid configuration at startup:\n  • " + "\n  • ".join(errors)
            )
        return self


def _parse_optional_int(val: str | None) -> int | None:
    """Parse an env var as an optional integer, returning None when unset or empty."""
    if val is None or val.strip() == "":
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


settings = Settings()

# Ensure data directories exist
settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
(settings.DATA_DIR / "uploads").mkdir(parents=True, exist_ok=True)
(settings.DATA_DIR / "repos").mkdir(parents=True, exist_ok=True)
(settings.DATA_DIR / "staging").mkdir(parents=True, exist_ok=True)
(settings.DATA_DIR / "db").mkdir(parents=True, exist_ok=True)
