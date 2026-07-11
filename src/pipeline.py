"""Pipeline orchestrator: runs all extraction stages for one job.

Each stage updates the job's status and emits job events (audit trail).
Non-critical stage failures (comments, OCR, vision) degrade gracefully;
critical failures (download, reconstruction) fail the job.

Settings are loaded fresh from the database at the start of each run so
web-UI configuration changes apply to the next job immediately.
"""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import Session

from src.acquisition.comments import CommentFetchError, comments_to_dicts, fetch_comments
from src.acquisition.downloader import download_content
from src.analysis.text_parser import EvidenceBundle
from src.database.connection import get_session
from src.database.models import JobStatus, RecipeJob
from src.export.markdown import render_markdown
from src.processing.audio import (
    TranscriptionError,
    extract_audio,
    segments_to_dicts,
    transcribe,
)
from src.processing.ocr import detections_to_dicts, run_ocr
from src.processing.vision import run_vision
from src.reconstruction.llm_client import reconstruct_recipe
from src.services import settings_service
from src.services.export_service import run_exports
from src.services.job_events import record_skip, stage_timer
from src.validation.validator import validate_recipe

logger = logging.getLogger(__name__)


def run_pipeline(job_id: str) -> None:
    """Execute the full extraction pipeline for a stored job.

    Designed to run in a background worker; all state is persisted to the DB.
    """
    settings = settings_service.get_settings()
    workdir = Path(tempfile.mkdtemp(prefix="yum_"))
    try:
        with get_session() as session:
            job = session.get(RecipeJob, job_id)
            if job is None:
                logger.error("Job %s not found.", job_id)
                return
            url, shortcode = job.url, job.shortcode

            # --- Stage 1: Acquisition ---
            _set_status(session, job, JobStatus.DOWNLOADING)
            with stage_timer(job_id, "download") as timer:
                content = download_content(url, workdir, settings)
                timer.note(f"Downloaded {content.video_path.name} by @{content.author}")
            job.video_metadata = json.dumps(
                {**content.metadata_dict(), "raw_info": content.raw_info}
            )
            _touch(session, job)

            comments: list[dict] = []
            try:
                with stage_timer(job_id, "comments") as timer:
                    comments = comments_to_dicts(fetch_comments(shortcode, settings))
                    timer.note(f"Fetched {len(comments)} comments")
            except CommentFetchError as exc:
                logger.warning("Comments unavailable (continuing): %s", exc)
            job.comments = json.dumps(comments)
            _touch(session, job)

            # --- Stage 2: Speech-to-text ---
            _set_status(session, job, JobStatus.TRANSCRIBING)
            transcript_dicts: list[dict] = []
            try:
                with stage_timer(job_id, "transcribe") as timer:
                    audio_path = extract_audio(content.video_path, workdir)
                    transcript_dicts = segments_to_dicts(
                        transcribe(audio_path, settings)
                    )
                    timer.note(
                        f"{len(transcript_dicts)} segments "
                        f"({settings.stt_engine_mode.value})"
                    )
            except TranscriptionError as exc:
                logger.warning("Transcription failed (continuing): %s", exc)
            job.raw_transcript = json.dumps(transcript_dicts)
            _touch(session, job)

            # When Vision is enabled it reads on-screen text itself, so the
            # local OCR stage is skipped and its evidence slot is filled from
            # the vision model's extracted text overlays.
            vision_active = bool(settings.vision_enabled and settings.vision_model)

            # --- Stage 3: OCR (local fallback) ---
            _set_status(session, job, JobStatus.OCR)
            ocr_dicts: list[dict] = []
            if vision_active:
                record_skip(
                    job_id, "ocr",
                    "Vision enabled - on-screen text handled by the vision model",
                )
            else:
                try:
                    with stage_timer(job_id, "ocr") as timer:
                        ocr_dicts = detections_to_dicts(
                            run_ocr(content.video_path, settings)
                        )
                        timer.note(
                            f"{len(ocr_dicts)} unique texts ({settings.ocr_engine.value})"
                        )
                except Exception as exc:  # noqa: BLE001 - OCR is best-effort
                    logger.warning("OCR failed (continuing): %s", exc)

            # --- Stage 4: Vision ---
            _set_status(session, job, JobStatus.VISION)
            vision_facts: list[str] = []
            if vision_active:
                try:
                    with stage_timer(job_id, "vision") as timer:
                        result = run_vision(content.video_path, settings)
                        vision_facts = result.facts
                        # Vision-read overlays fill the OCR evidence slot.
                        ocr_dicts = _vision_text_to_ocr_dicts(result.onscreen_text)
                        timer.note(
                            f"{len(vision_facts)} facts, "
                            f"{len(ocr_dicts)} on-screen texts"
                        )
                except Exception as exc:  # noqa: BLE001 - vision is best-effort
                    logger.warning("Vision failed (continuing): %s", exc)
            else:
                record_skip(job_id, "vision", "Vision analysis disabled in settings")
            job.raw_ocr_text = json.dumps(ocr_dicts)
            job.raw_vision = json.dumps(vision_facts)
            _touch(session, job)

            # --- Stages 5-7: Reconstruction ---
            _set_status(session, job, JobStatus.RECONSTRUCTING)
            evidence = EvidenceBundle(
                caption=content.caption,
                title=content.title,
                author=content.author,
                hashtags=content.hashtags,
                transcript_segments=transcript_dicts,
                ocr_detections=ocr_dicts,
                vision_facts=vision_facts,
                comments=comments,
            )
            with stage_timer(job_id, "reconstruct") as timer:
                recipe = reconstruct_recipe(evidence, settings)
                timer.note(
                    f"'{recipe.title}' - confidence {recipe.overall_confidence:.2f} "
                    f"({settings.llm_model})"
                )
            job.structured_recipe = recipe.model_dump_json()
            _touch(session, job)

            # --- Stage 8: Validation ---
            _set_status(session, job, JobStatus.VALIDATING)
            with stage_timer(job_id, "validate") as timer:
                report = validate_recipe(recipe)
                timer.note(f"{len(report.issues)} issue(s) flagged")
            job.validation_report = report.model_dump_json()

            # --- Stage 9: Markdown rendering (stored in DB) ---
            markdown = render_markdown(
                recipe, source_url=url, author=content.author, validation=report
            )
            job.markdown_content = markdown
            _touch(session, job)

            # --- Stage 10: Export ---
            export_results: dict[str, str] = {}
            if settings.auto_export_on_success:
                _set_status(session, job, JobStatus.EXPORTING)
                export_results = run_exports(
                    recipe,
                    markdown,
                    url,
                    settings,
                    settings.export_target_list,
                    job_id=job_id,
                )
            else:
                record_skip(job_id, "export", "Auto-export disabled in settings")
            job.export_results = json.dumps(export_results)

            _set_status(session, job, JobStatus.COMPLETED)
            logger.info("Job %s completed: %s", job_id, recipe.title)

    except Exception as exc:  # noqa: BLE001 - top-level job failure handler
        logger.exception("Job %s failed.", job_id)
        with get_session() as session:
            job = session.get(RecipeJob, job_id)
            if job is not None:
                job.status = JobStatus.FAILED
                job.error_message = str(exc)[:2000]
                _touch(session, job)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _set_status(session: Session, job: RecipeJob, status: JobStatus) -> None:
    job.status = status
    _touch(session, job)


def _touch(session: Session, job: RecipeJob) -> None:
    job.updated_at = datetime.now(timezone.utc)
    session.add(job)
    session.commit()
    session.refresh(job)


def _vision_text_to_ocr_dicts(onscreen_text: list[str]) -> list[dict]:
    """Adapt vision-read on-screen text into OCR-detection-shaped dicts.

    Keeps EvidenceBundle / prompt construction unchanged (they consume
    OCR detections regardless of whether text came from a local OCR engine
    or the vision model).
    """
    return [
        {"timestamp": 0.0, "text": text, "confidence": 1.0}
        for text in onscreen_text
        if text.strip()
    ]
