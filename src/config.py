"""Application configuration loaded from environment variables / .env file.

All secrets (API keys, tokens) live in the environment - never hardcoded.
See `.env.example` for the full documented template.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class WhisperEngine(str, Enum):
    LOCAL = "local"
    OPENAI = "openai"
    GROQ = "groq"


class OCREngine(str, Enum):
    PADDLEOCR = "paddleocr"
    TESSERACT = "tesseract"
    NONE = "none"


class ExportTarget(str, Enum):
    MEALIE = "mealie"
    TANDOOR = "tandoor"
    MARKDOWN = "markdown"
    JSON = "json"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Core ---
    port: int = 8000
    database_url: str = "sqlite:///./data/recipes.db"
    data_dir: Path = Path("./data")
    log_level: str = "INFO"

    # --- Export behavior ---
    auto_export_on_success: bool = True
    export_targets: str = "markdown"  # comma-separated

    # --- Instagram auth ---
    instagram_cookie_file: Path | None = None
    instagram_username: str | None = None

    # --- Speech-to-Text ---
    whisper_engine: WhisperEngine = WhisperEngine.LOCAL
    whisper_model_size: str = "small"
    whisper_device: str = "auto"
    whisper_compute_type: str = "default"
    openai_api_key: str | None = None
    groq_api_key: str | None = None

    # --- OCR ---
    ocr_engine: OCREngine = OCREngine.PADDLEOCR
    ocr_language: str = "en"
    ocr_max_frames: int = 40

    # --- Vision (VLM) ---
    vision_enabled: bool = False
    vision_model: str | None = None
    vision_max_frames: int = 8

    # --- LLM reconstruction ---
    llm_model: str = "gemini/gemini-2.5-flash"
    llm_api_key: str | None = None
    llm_api_base: str | None = None

    # --- Mealie ---
    mealie_url: str | None = None
    mealie_api_token: str | None = None

    # --- Tandoor ---
    tandoor_url: str | None = None
    tandoor_api_token: str | None = None

    # --- Markdown export ---
    markdown_export_dir: Path = Path("./export")

    @field_validator("instagram_cookie_file", mode="before")
    @classmethod
    def _empty_str_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def export_target_list(self) -> list[ExportTarget]:
        """Parse the comma-separated export targets into validated enums."""
        targets: list[ExportTarget] = []
        for raw in self.export_targets.split(","):
            name = raw.strip().lower()
            if not name:
                continue
            try:
                targets.append(ExportTarget(name))
            except ValueError:
                # Ignore unknown targets rather than crash at startup;
                # they are reported by the /health endpoint.
                continue
        return targets


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor for dependency injection."""
    return Settings()
