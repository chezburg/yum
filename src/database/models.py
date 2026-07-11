"""SQLModel database models.

A single `RecipeJob` row is the source of truth for one extraction:
raw scraped evidence, structured recipe JSON, rendered Markdown, validation
report, and processing status/history.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import Column, Text
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid.uuid4().hex


class JobStatus(str, Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    TRANSCRIBING = "transcribing"
    OCR = "ocr"
    VISION = "vision"
    RECONSTRUCTING = "reconstructing"
    VALIDATING = "validating"
    EXPORTING = "exporting"
    COMPLETED = "completed"
    FAILED = "failed"


class RecipeJob(SQLModel, table=True):
    """One extraction job: raw evidence + final structured recipe."""

    __tablename__ = "recipe_jobs"

    id: str = Field(default_factory=_new_id, primary_key=True)
    url: str = Field(index=True)
    shortcode: str = Field(index=True)

    status: JobStatus = Field(default=JobStatus.PENDING, index=True)
    error_message: str | None = Field(default=None, sa_column=Column(Text))

    # --- Raw evidence (JSON-serialized strings) ---
    video_metadata: str | None = Field(default=None, sa_column=Column(Text))
    raw_transcript: str | None = Field(default=None, sa_column=Column(Text))
    raw_ocr_text: str | None = Field(default=None, sa_column=Column(Text))
    raw_vision: str | None = Field(default=None, sa_column=Column(Text))
    comments: str | None = Field(default=None, sa_column=Column(Text))

    # --- Results ---
    structured_recipe: str | None = Field(default=None, sa_column=Column(Text))
    markdown_content: str | None = Field(default=None, sa_column=Column(Text))
    validation_report: str | None = Field(default=None, sa_column=Column(Text))
    export_results: str | None = Field(default=None, sa_column=Column(Text))

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
