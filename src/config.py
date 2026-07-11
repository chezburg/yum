"""Application configuration.

Two layers:

1. `BootstrapSettings` - the minimal environment-provided settings needed
   before the database exists: SECRET_KEY (encrypts secrets at rest),
   database URL, port, data dir, log level.

2. `Settings` - all runtime configuration, stored in the database
   (`app_settings` table) and editable through the web UI. Secret fields
   are encrypted at rest. Field metadata (`json_schema_extra`) drives the
   settings UI rendering: group, label, secret flag, and choices.

Use `src.services.settings_service.get_settings()` to obtain `Settings`.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field
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


class BootstrapSettings(BaseSettings):
    """Environment-only settings required before the DB is available."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    secret_key: str = Field(
        default="",
        description="Encrypts secrets stored in the database. Required. "
        "Generate with: openssl rand -hex 32",
    )
    port: int = 8000
    database_url: str = "sqlite:///./data/yum.db"
    data_dir: Path = Path("./data")
    log_level: str = "INFO"


@lru_cache
def get_bootstrap() -> BootstrapSettings:
    """Cached bootstrap settings accessor."""
    return BootstrapSettings()


def _meta(
    group: str,
    label: str,
    *,
    secret: bool = False,
    description: str = "",
    choices: list[str] | None = None,
) -> dict:
    """UI metadata attached to each setting field."""
    extra: dict = {"group": group, "label": label, "secret": secret}
    if description:
        extra["description"] = description
    if choices:
        extra["choices"] = choices
    return extra


class Settings(BaseModel):
    """Runtime configuration (database-backed, web-UI editable)."""

    # --- Export behavior ---
    auto_export_on_success: bool = Field(
        default=True,
        json_schema_extra=_meta(
            "Export", "Auto-export on success",
            description="Automatically export recipes after successful extraction.",
        ),
    )
    export_targets: str = Field(
        default="markdown",
        json_schema_extra=_meta(
            "Export", "Export targets",
            description="Comma-separated: mealie, tandoor, markdown, json.",
        ),
    )

    # --- Instagram (session is set via the guided login wizard) ---
    instagram_username: str = Field(
        default="",
        json_schema_extra=_meta(
            "Instagram", "Connected account",
            description="Set automatically by the Instagram login wizard.",
        ),
    )
    instagram_session: str = Field(
        default="",
        json_schema_extra=_meta(
            "Instagram", "Session data", secret=True,
            description="Serialized Instagram session (managed by the login wizard).",
        ),
    )

    # --- Speech-to-Text ---
    whisper_engine: WhisperEngine = Field(
        default=WhisperEngine.LOCAL,
        json_schema_extra=_meta(
            "Speech-to-Text", "Engine",
            choices=[e.value for e in WhisperEngine],
            description="local = faster-whisper on this machine; openai/groq = cloud API.",
        ),
    )
    whisper_model_size: str = Field(
        default="small",
        json_schema_extra=_meta(
            "Speech-to-Text", "Local model size",
            choices=["tiny", "base", "small", "medium", "large-v3"],
        ),
    )
    whisper_device: str = Field(
        default="auto",
        json_schema_extra=_meta(
            "Speech-to-Text", "Local device", choices=["auto", "cpu", "cuda"]
        ),
    )
    whisper_compute_type: str = Field(
        default="default",
        json_schema_extra=_meta(
            "Speech-to-Text", "Local compute type",
            choices=["default", "int8", "float16"],
        ),
    )
    openai_api_key: str = Field(
        default="",
        json_schema_extra=_meta(
            "Speech-to-Text", "OpenAI API key", secret=True,
            description="Required when engine is 'openai'.",
        ),
    )
    groq_api_key: str = Field(
        default="",
        json_schema_extra=_meta(
            "Speech-to-Text", "Groq API key", secret=True,
            description="Required when engine is 'groq'.",
        ),
    )

    # --- OCR ---
    ocr_engine: OCREngine = Field(
        default=OCREngine.TESSERACT,
        json_schema_extra=_meta(
            "OCR", "Engine",
            choices=[e.value for e in OCREngine],
            description="paddleocr requires local model install; tesseract is bundled.",
        ),
    )
    ocr_language: str = Field(
        default="en", json_schema_extra=_meta("OCR", "Language")
    )
    ocr_max_frames: int = Field(
        default=40,
        ge=1,
        le=500,
        json_schema_extra=_meta(
            "OCR", "Max keyframes", description="Scene-change filtered frames to OCR."
        ),
    )

    # --- Vision ---
    vision_enabled: bool = Field(
        default=False,
        json_schema_extra=_meta("Vision", "Enable vision analysis"),
    )
    vision_model: str = Field(
        default="",
        json_schema_extra=_meta(
            "Vision", "Vision model",
            description="LiteLLM model string, e.g. gemini/gemini-2.5-flash or ollama/qwen2.5vl.",
        ),
    )
    vision_max_frames: int = Field(
        default=8,
        ge=1,
        le=50,
        json_schema_extra=_meta("Vision", "Max keyframes"),
    )

    # --- LLM reconstruction ---
    llm_model: str = Field(
        default="gemini/gemini-2.5-flash",
        json_schema_extra=_meta(
            "LLM", "Model",
            description="LiteLLM model string: gemini/gemini-2.5-flash, gpt-4o-mini, "
            "anthropic/claude-sonnet-4-5, ollama/llama3.1, ...",
        ),
    )
    llm_api_key: str = Field(
        default="",
        json_schema_extra=_meta(
            "LLM", "API key", secret=True,
            description="Provider API key (not needed for Ollama).",
        ),
    )
    llm_api_base: str = Field(
        default="",
        json_schema_extra=_meta(
            "LLM", "API base URL",
            description="Override for Ollama or self-hosted endpoints, "
            "e.g. http://ollama:11434.",
        ),
    )

    # --- Mealie ---
    mealie_url: str = Field(
        default="",
        json_schema_extra=_meta("Mealie", "Mealie URL"),
    )
    mealie_api_token: str = Field(
        default="",
        json_schema_extra=_meta("Mealie", "API token", secret=True),
    )

    # --- Tandoor ---
    tandoor_url: str = Field(
        default="",
        json_schema_extra=_meta("Tandoor", "Tandoor URL"),
    )
    tandoor_api_token: str = Field(
        default="",
        json_schema_extra=_meta("Tandoor", "API token", secret=True),
    )

    # --- Markdown export ---
    markdown_export_dir: str = Field(
        default="./export",
        json_schema_extra=_meta(
            "Export", "Markdown export directory",
            description="Directory (mounted volume) where .md files are written.",
        ),
    )

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
                continue
        return targets


def secret_field_names() -> frozenset[str]:
    """Names of Settings fields flagged as secrets (encrypted at rest)."""
    return frozenset(
        name
        for name, field in Settings.model_fields.items()
        if (field.json_schema_extra or {}).get("secret")
    )


def field_groups() -> dict[str, list[str]]:
    """Ordered mapping of UI group -> field names, for settings page rendering."""
    groups: dict[str, list[str]] = {}
    for name, field in Settings.model_fields.items():
        group = (field.json_schema_extra or {}).get("group", "Other")
        groups.setdefault(group, []).append(name)
    return groups
