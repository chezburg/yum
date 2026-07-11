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


class EngineMode(str, Enum):
    """Where an AI engine's API endpoint lives.

    Both modes use the same generic HTTP client; 'local' simply means the
    endpoint is a self-hosted server (Ollama, whisper server, ...) and
    typically needs no API key.
    """

    LOCAL = "local"
    CLOUD = "cloud"


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
    # Absolute path matching the /data volume mount (see docker-compose.yml
    # and Dockerfile). Using a relative path here is dangerous: it would
    # resolve against the container's CWD (/app), which is never created/
    # writable, causing "unable to open database file" if DATABASE_URL is
    # ever left unset.
    database_url: str = "sqlite:////data/yum.db"
    data_dir: Path = Path("/data")
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
    stt_engine_mode: EngineMode = Field(
        default=EngineMode.LOCAL,
        json_schema_extra=_meta(
            "Speech-to-Text", "Engine",
            choices=[e.value for e in EngineMode],
            description="local = self-hosted OpenAI-compatible Whisper server "
            "(e.g. speaches, faster-whisper-server); cloud = hosted API "
            "(OpenAI, Groq, ...). Both use the same endpoint settings below.",
        ),
    )
    stt_api_base: str = Field(
        default="http://localhost:8000/v1",
        json_schema_extra=_meta(
            "Speech-to-Text", "API base URL",
            description="OpenAI-compatible endpoint base, e.g. "
            "http://localhost:8000/v1 (local server), "
            "https://api.openai.com/v1, or https://api.groq.com/openai/v1.",
        ),
    )
    stt_api_key: str = Field(
        default="",
        json_schema_extra=_meta(
            "Speech-to-Text", "API key", secret=True,
            description="Leave blank for local servers without auth.",
        ),
    )
    stt_model: str = Field(
        default="",
        json_schema_extra=_meta(
            "Speech-to-Text", "Model",
            description="Model name sent to the endpoint, e.g. whisper-1 "
            "(OpenAI), whisper-large-v3 (Groq). Leave blank to let a local "
            "server use its default.",
        ),
    )

    # --- OCR (local fallback; skipped when Vision is enabled) ---
    ocr_engine: OCREngine = Field(
        default=OCREngine.TESSERACT,
        json_schema_extra=_meta(
            "OCR", "Engine",
            choices=[e.value for e in OCREngine],
            description="Local on-screen text extraction, used only when "
            "Vision is disabled (Vision reads on-screen text itself). "
            "paddleocr requires local model install; tesseract is bundled.",
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
        json_schema_extra=_meta(
            "Vision", "Enable vision analysis",
            description="Analyze keyframes with a vision model. Also extracts "
            "on-screen text, replacing the local OCR stage.",
        ),
    )
    vision_engine_mode: EngineMode = Field(
        default=EngineMode.CLOUD,
        json_schema_extra=_meta(
            "Vision", "Engine",
            choices=[e.value for e in EngineMode],
            description="local = self-hosted endpoint (e.g. Ollama); "
            "cloud = hosted API. Both use the endpoint settings below.",
        ),
    )
    vision_api_base: str = Field(
        default="",
        json_schema_extra=_meta(
            "Vision", "API base URL",
            description="Endpoint base, e.g. http://localhost:11434 (Ollama). "
            "Leave blank for providers litellm knows from the model prefix.",
        ),
    )
    vision_api_key: str = Field(
        default="",
        json_schema_extra=_meta(
            "Vision", "API key", secret=True,
            description="Leave blank for local servers without auth.",
        ),
    )
    vision_model: str = Field(
        default="",
        json_schema_extra=_meta(
            "Vision", "Model",
            description="LiteLLM model string, e.g. gemini/gemini-2.5-flash, "
            "gpt-4o, or ollama/qwen2.5vl.",
        ),
    )
    vision_max_frames: int = Field(
        default=8,
        ge=1,
        le=50,
        json_schema_extra=_meta("Vision", "Max keyframes"),
    )

    # --- LLM reconstruction ---
    llm_engine_mode: EngineMode = Field(
        default=EngineMode.CLOUD,
        json_schema_extra=_meta(
            "LLM", "Engine",
            choices=[e.value for e in EngineMode],
            description="local = self-hosted endpoint (e.g. Ollama); "
            "cloud = hosted API. Both use the endpoint settings below.",
        ),
    )
    llm_api_base: str = Field(
        default="",
        json_schema_extra=_meta(
            "LLM", "API base URL",
            description="Endpoint base, e.g. http://localhost:11434 (Ollama). "
            "Leave blank for providers litellm knows from the model prefix.",
        ),
    )
    llm_api_key: str = Field(
        default="",
        json_schema_extra=_meta(
            "LLM", "API key", secret=True,
            description="Leave blank for local servers without auth.",
        ),
    )
    llm_model: str = Field(
        default="gemini/gemini-2.5-flash",
        json_schema_extra=_meta(
            "LLM", "Model",
            description="LiteLLM model string: gemini/gemini-2.5-flash, "
            "gpt-4o-mini, anthropic/claude-sonnet-4-5, ollama/llama3.1, ...",
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
