"""JSON API routes.

    POST /api/v1/extract               - accept share-sheet text / URL, queue job
    GET  /api/v1/jobs                  - list jobs
    GET  /api/v1/jobs/{id}             - job detail + structured recipe
    GET  /api/v1/jobs/{id}/events      - stage-by-stage event log
    GET  /api/v1/jobs/{id}/markdown    - rendered markdown
    POST /api/v1/jobs/{id}/export      - on-demand export
    POST /api/v1/jobs/{id}/recompute   - re-run reconstruction from stored evidence
    POST /api/v1/jobs/{id}/rerun       - full re-run (re-download + re-process)
    DELETE /api/v1/jobs/{id}           - delete a job and its event log
    GET  /api/v1/settings              - masked settings dump
    PUT  /api/v1/settings              - update settings
    POST /api/v1/settings/test/{engine} - test engine connectivity (stt/llm/vision)
    POST /api/v1/instagram/login       - guided login step 1
    POST /api/v1/instagram/2fa         - guided login step 2 (2FA code)
    POST /api/v1/instagram/disconnect  - remove stored session
    GET  /health                       - health & config summary
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, ValidationError
from sqlmodel import select

from src import __version__
from src.acquisition import auth
from src.config import ExportTarget
from src.database.connection import get_session
from src.database.models import JobStatus, RecipeJob
from src.services import engine_test, settings_service
from src.services.export_service import ExportUnavailableError, export_job
from src.services.job_events import events_for_job
from src.services.job_management import (
    JobNotFoundError,
    delete_job,
    reset_job_for_rerun,
)
from src.utils.url_parser import URLParseError, extract_instagram_url

logger = logging.getLogger(__name__)

router = APIRouter()

# Populated by main.py with the pipeline executor's submit function.
_submit_job = None
# Populated by main.py with the reconstruction-only submit function.
_submit_recompute = None


def set_job_submitter(submit) -> None:
    """Dependency injection point for the background job executor."""
    global _submit_job
    _submit_job = submit


def set_recompute_submitter(submit) -> None:
    """Dependency injection point for the reconstruction-only executor."""
    global _submit_recompute
    _submit_recompute = submit


# ---------------------------------------------------------------- extraction


class ExtractRequest(BaseModel):
    """Share-sheet input: arbitrary text containing an Instagram URL."""

    text: str = Field(min_length=1, max_length=10_000, description="Shared text or URL")


class ExtractResponse(BaseModel):
    job_id: str
    url: str
    status: str


@router.post("/api/v1/extract", response_model=ExtractResponse, status_code=202)
def extract(request: ExtractRequest) -> ExtractResponse:
    """Accept share-sheet text, parse the Instagram URL, and queue extraction."""
    try:
        parsed = extract_instagram_url(request.text)
    except URLParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    with get_session() as session:
        # Reuse an existing completed job for the same shortcode (idempotency).
        existing = session.exec(
            select(RecipeJob).where(
                RecipeJob.shortcode == parsed.shortcode,
                RecipeJob.status == JobStatus.COMPLETED,
            )
        ).first()
        if existing:
            return ExtractResponse(
                job_id=existing.id, url=existing.url, status=existing.status.value
            )

        job = RecipeJob(url=parsed.canonical_url, shortcode=parsed.shortcode)
        session.add(job)
        session.commit()
        session.refresh(job)
        job_id = job.id

    if _submit_job is None:
        raise HTTPException(status_code=503, detail="Job executor not ready.")
    _submit_job(job_id)
    return ExtractResponse(
        job_id=job_id, url=parsed.canonical_url, status=JobStatus.PENDING.value
    )


# ---------------------------------------------------------------------- jobs


class JobSummary(BaseModel):
    id: str
    url: str
    status: str
    title: str | None
    error_message: str | None
    created_at: str


@router.get("/api/v1/jobs", response_model=list[JobSummary])
def list_jobs(limit: int = 50) -> list[JobSummary]:
    """List recent jobs, newest first."""
    limit = max(1, min(limit, 500))
    with get_session() as session:
        jobs = session.exec(
            select(RecipeJob).order_by(RecipeJob.created_at.desc()).limit(limit)
        ).all()
        return [_summarize(job) for job in jobs]


@router.get("/api/v1/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    """Full job detail: status, evidence, structured recipe, validation report."""
    with get_session() as session:
        job = session.get(RecipeJob, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found.")
        return {
            "id": job.id,
            "url": job.url,
            "status": job.status.value,
            "error_message": job.error_message,
            "video_metadata": _load_json(job.video_metadata),
            "structured_recipe": _load_json(job.structured_recipe),
            "validation_report": _load_json(job.validation_report),
            "export_results": _load_json(job.export_results),
            "created_at": job.created_at.isoformat(),
            "updated_at": job.updated_at.isoformat(),
        }


@router.get("/api/v1/jobs/{job_id}/events")
def get_job_events(job_id: str) -> list[dict]:
    """Stage-by-stage event log for a job."""
    with get_session() as session:
        if session.get(RecipeJob, job_id) is None:
            raise HTTPException(status_code=404, detail="Job not found.")
    return [
        {
            "stage": e.stage,
            "status": e.status.value,
            "message": e.message,
            "duration_ms": e.duration_ms,
            "created_at": e.created_at.isoformat(),
        }
        for e in events_for_job(job_id)
    ]


@router.get("/api/v1/jobs/{job_id}/markdown")
def get_job_markdown(job_id: str):
    """Rendered Markdown for the recipe (Obsidian-ready)."""
    from fastapi.responses import PlainTextResponse

    with get_session() as session:
        job = session.get(RecipeJob, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found.")
        if not job.markdown_content:
            raise HTTPException(
                status_code=409,
                detail=f"Markdown not available (job status: {job.status.value}).",
            )
        return PlainTextResponse(job.markdown_content)


class ExportJobRequest(BaseModel):
    targets: list[str] = Field(min_length=1, description="mealie/tandoor/markdown/json")


@router.post("/api/v1/jobs/{job_id}/export")
def export_job_endpoint(job_id: str, request: ExportJobRequest) -> dict:
    """On-demand export of a completed job to selected targets."""
    try:
        targets = [ExportTarget(t.strip().lower()) for t in request.targets]
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Unknown target: {exc}") from exc

    settings = settings_service.get_settings()
    try:
        results = export_job(job_id, targets, settings)
    except ExportUnavailableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"results": results}


@router.post("/api/v1/jobs/{job_id}/recompute", status_code=202)
def recompute_job_endpoint(job_id: str) -> dict:
    """Re-run reconstruction/validation/export from evidence already stored
    on the job, without re-downloading or re-transcribing anything."""
    with get_session() as session:
        if session.get(RecipeJob, job_id) is None:
            raise HTTPException(status_code=404, detail="Job not found.")
    if _submit_recompute is None:
        raise HTTPException(status_code=503, detail="Job executor not ready.")
    _submit_recompute(job_id)
    return {"job_id": job_id, "status": "recompute_queued"}


@router.post("/api/v1/jobs/{job_id}/rerun", status_code=202)
def rerun_job_endpoint(job_id: str) -> dict:
    """Full re-run of a job: re-downloads and re-processes everything."""
    try:
        reset_job_for_rerun(job_id)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if _submit_job is None:
        raise HTTPException(status_code=503, detail="Job executor not ready.")
    _submit_job(job_id)
    return {"job_id": job_id, "status": JobStatus.PENDING.value}


@router.delete("/api/v1/jobs/{job_id}", status_code=204)
def delete_job_endpoint(job_id: str) -> None:
    """Permanently delete a job and its event log."""
    try:
        delete_job(job_id)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ------------------------------------------------------------------ settings


@router.get("/api/v1/settings")
def get_settings_endpoint() -> dict:
    """All settings with secret values masked."""
    return settings_service.masked_settings_dump()


class SettingsUpdate(BaseModel):
    """Partial settings update; unknown keys are rejected."""

    values: dict[str, object] = Field(min_length=1)


@router.put("/api/v1/settings")
def update_settings_endpoint(request: SettingsUpdate) -> dict:
    """Update settings. Secret fields: empty/masked values keep current."""
    try:
        settings_service.save_settings(request.values)
    except (ValueError, ValidationError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return settings_service.masked_settings_dump()


@router.post("/api/v1/settings/test/{engine}")
def test_engine_endpoint(engine: str) -> dict:
    """Test connectivity of a configured engine: stt, llm, or vision."""
    if engine not in ("stt", "llm", "vision"):
        raise HTTPException(status_code=404, detail="Unknown engine.")
    settings = settings_service.get_settings()
    ok, message = engine_test.test_engine(engine, settings)
    return {"engine": engine, "ok": ok, "message": message}


# ----------------------------------------------------------------- instagram


class InstagramLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class InstagramTwoFactorRequest(BaseModel):
    wizard_token: str = Field(min_length=1)
    code: str = Field(min_length=4, max_length=16)


@router.post("/api/v1/instagram/login")
def instagram_login(request: InstagramLoginRequest) -> dict:
    """Step 1 of guided Instagram login. May require a 2FA follow-up."""
    result = auth.start_login(request.username, request.password)
    return {
        "state": result.state.value,
        "message": result.message,
        "wizard_token": result.wizard_token,
        "username": result.username,
    }


@router.post("/api/v1/instagram/2fa")
def instagram_two_factor(request: InstagramTwoFactorRequest) -> dict:
    """Step 2 of guided Instagram login: submit the 2FA code."""
    result = auth.complete_two_factor(request.wizard_token, request.code)
    return {
        "state": result.state.value,
        "message": result.message,
        "wizard_token": result.wizard_token,
        "username": result.username,
    }


@router.post("/api/v1/instagram/disconnect")
def instagram_disconnect() -> dict:
    """Remove the stored Instagram session."""
    auth.disconnect()
    return {"status": "disconnected"}


# -------------------------------------------------------------------- health


@router.get("/health")
def health() -> dict:
    """Health check with a non-secret configuration summary."""
    settings = settings_service.get_settings()
    return {
        "status": "ok",
        "version": __version__,
        "config": {
            "stt_engine_mode": settings.stt_engine_mode.value,
            "stt_model": settings.stt_model,
            "ocr_engine": settings.ocr_engine.value,
            "vision_enabled": settings.vision_enabled,
            "vision_model": settings.vision_model,
            "llm_engine_mode": settings.llm_engine_mode.value,
            "llm_model": settings.llm_model,
            "auto_export_on_success": settings.auto_export_on_success,
            "export_targets": [t.value for t in settings.export_target_list],
            "instagram_connected": auth.connection_status(settings)["connected"],
        },
    }


# ------------------------------------------------------------------- helpers


def _summarize(job: RecipeJob) -> JobSummary:
    title = None
    recipe = _load_json(job.structured_recipe)
    if isinstance(recipe, dict):
        title = recipe.get("title")
    return JobSummary(
        id=job.id,
        url=job.url,
        status=job.status.value,
        title=title,
        error_message=job.error_message,
        created_at=job.created_at.isoformat(),
    )


def _load_json(raw: str | None) -> object:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None
