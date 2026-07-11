"""Pipeline orchestrator: runs all extraction stages for one job.

Each stage updates the job's status in the database so progress is
observable. Non-critical stage failures (comments, OCR, vision) degrade
gracefully; critical failures (download, reconstruction) fail the job.
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
from src.config import ExportTarget, Settings
from src.database.connection import get_session
from src.database.models import JobStatus, RecipeJob
from src.export.markdown import render_markdown, write_markdown_file
from src.export.mealie import export_to_mealie
from src.export.tandoor import export_to_tandoor
from src.processing.audio import (
    TranscriptionError,
    extract_audio,
    segments_to_dicts,
    transcribe,
)
from src.processing.ocr import detections_to_dicts, run_ocr
from src.processing.vision import run_vision
from src.reconstruction.llm_client import reconstruct_recipe
from src.reconstruction.schemas import StructuredRecipe, ValidationReport
from src.validation.validator import validate_recipe

logger = logging.getLogger(__name__)


def run_pipeline(job_id: str, settings: Settings) -> None:
    """Execute the full extraction pipeline for a stored job.

    Designed to run in a background worker; all state is persisted to the DB.
    """
    workdir = Path(tempfile.mkdtemp(prefix="igrecipe_"))
    try:
        with get_session() as session:
            job = session.get(RecipeJob, job_id)
            if job is None:
                logger.error("Job %s not found.", job_id)
                return
            url, shortcode = job.url, job.shortcode

            # --- Stage 1: Acquisition ---
            _set_status(session, job, JobStatus.DOWNLOADING)
            content = download_content(url, workdir, settings)
            job.video_metadata = json.dumps(
                {**content.metadata_dict(), "raw_info": content.raw_info}
            )
            _touch(session, job)

            comments: list[dict] = []
            try:
                comments = comments_to_dicts(fetch_comments(shortcode, settings))
            except CommentFetchError as exc:
                logger.warning("Comments unavailable (continuing): %s", exc)
            job.comments = json.dumps(comments)
            _touch(session, job)

            # --- Stage 2: Speech-to-text ---
            _set_status(session, job, JobStatus.TRANSCRIBING)
            transcript_dicts: list[dict] = []
            try:
                audio_path = extract_audio(content.video_path, workdir)
                transcript_dicts = segments_to_dicts(transcribe(audio_path, settings))
            except TranscriptionError as exc:
                logger.warning("Transcription failed (continuing): %s", exc)
            job.raw_transcript = json.dumps(transcript_dicts)
            _touch(session, job)

            # --- Stage 3: OCR ---
            _set_status(session, job, JobStatus.OCR)
            ocr_dicts: list[dict] = []
            try:
                ocr_dicts = detections_to_dicts(run_ocr(content.video_path, settings))
            except Exception as exc:  # noqa: BLE001 - OCR is best-effort
                logger.warning("OCR failed (continuing): %s", exc)
            job.raw_ocr_text = json.dumps(ocr_dicts)
            _touch(session, job)

            # --- Stage 4: Vision ---
            _set_status(session, job, JobStatus.VISION)
            vision_facts: list[str] = []
            try:
                vision_facts = run_vision(content.video_path, settings)
            except Exception as exc:  # noqa: BLE001 - vision is best-effort
                logger.warning("Vision failed (continuing): %s", exc)
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
            recipe = reconstruct_recipe(evidence, settings)
            job.structured_recipe = recipe.model_dump_json()
            _touch(session, job)

            # --- Stage 8: Validation ---
            _set_status(session, job, JobStatus.VALIDATING)
            report = validate_recipe(recipe)
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
                export_results = _run_exports(recipe, markdown, url, settings)
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


def _run_exports(
    recipe: StructuredRecipe,
    markdown: str,
    source_url: str,
    settings: Settings,
) -> dict[str, str]:
    """Run configured exports; individual export failures do not fail the job."""
    results: dict[str, str] = {}
    for target in settings.export_target_list:
        try:
            if target == ExportTarget.MEALIE:
                slug = export_to_mealie(recipe, source_url, settings)
                results["mealie"] = f"ok:{slug}"
            elif target == ExportTarget.TANDOOR:
                rid = export_to_tandoor(recipe, source_url, settings)
                results["tandoor"] = f"ok:{rid}"
            elif target == ExportTarget.MARKDOWN:
                path = write_markdown_file(
                    markdown, recipe.title, settings.markdown_export_dir
                )
                results["markdown"] = f"ok:{path}"
            elif target == ExportTarget.JSON:
                results["json"] = "ok:stored_in_db"
        except Exception as exc:  # noqa: BLE001 - report per-target failures
            logger.warning("Export to %s failed: %s", target.value, exc)
            results[target.value] = f"error:{exc}"
    return results


def _set_status(session: Session, job: RecipeJob, status: JobStatus) -> None:
    job.status = status
    _touch(session, job)


def _touch(session: Session, job: RecipeJob) -> None:
    job.updated_at = datetime.now(timezone.utc)
    session.add(job)
    session.commit()
    session.refresh(job)
