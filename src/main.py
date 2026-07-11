"""FastAPI application: share-sheet ingestion endpoint + job management.

Endpoints:
    POST /api/v1/extract      - accept share-sheet text / URL, start extraction
    GET  /api/v1/jobs         - list jobs
    GET  /api/v1/jobs/{id}    - job status + structured recipe
    GET  /api/v1/jobs/{id}/markdown - rendered markdown (download or view)
    GET  /health              - health & configuration summary
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlmodel import select

from src import __version__
from src.config import get_settings
from src.database.connection import get_session, init_db
from src.database.models import JobStatus, RecipeJob
from src.pipeline import run_pipeline
from src.utils.url_parser import URLParseError, extract_instagram_url

logger = logging.getLogger(__name__)

# Single-worker executor: extraction is resource-heavy (Whisper/OCR), so jobs
# are processed one at a time. Increase max_workers if you have the hardware.
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pipeline")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    init_db()
    logger.info("Instagram Recipe Extractor v%s started.", __version__)
    yield
    _executor.shutdown(wait=False, cancel_futures=True)


app = FastAPI(
    title="Instagram Recipe Extractor",
    version=__version__,
    lifespan=lifespan,
)


class ExtractRequest(BaseModel):
    """Share-sheet input: arbitrary text containing an Instagram URL."""

    text: str = Field(min_length=1, max_length=10_000, description="Shared text or URL")


class ExtractResponse(BaseModel):
    job_id: str
    url: str
    status: str


class JobSummary(BaseModel):
    id: str
    url: str
    status: str
    title: str | None
    error_message: str | None
    created_at: str


@app.post("/api/v1/extract", response_model=ExtractResponse, status_code=202)
def extract(request: ExtractRequest) -> ExtractResponse:
    """Accept share-sheet text, parse the Instagram URL, and queue extraction."""
    settings = get_settings()
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

    _executor.submit(run_pipeline, job_id, settings)
    return ExtractResponse(
        job_id=job_id, url=parsed.canonical_url, status=JobStatus.PENDING.value
    )


@app.get("/api/v1/jobs", response_model=list[JobSummary])
def list_jobs(limit: int = 50) -> list[JobSummary]:
    """List recent jobs, newest first."""
    limit = max(1, min(limit, 500))
    with get_session() as session:
        jobs = session.exec(
            select(RecipeJob).order_by(RecipeJob.created_at.desc()).limit(limit)
        ).all()
        return [_summarize(job) for job in jobs]


@app.get("/api/v1/jobs/{job_id}")
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


@app.get("/api/v1/jobs/{job_id}/markdown", response_class=PlainTextResponse)
def get_job_markdown(job_id: str) -> str:
    """Rendered Markdown for the recipe (Obsidian-ready)."""
    with get_session() as session:
        job = session.get(RecipeJob, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found.")
        if not job.markdown_content:
            raise HTTPException(
                status_code=409,
                detail=f"Markdown not available (job status: {job.status.value}).",
            )
        return job.markdown_content


@app.get("/health")
def health() -> dict:
    """Health check with a non-secret configuration summary."""
    settings = get_settings()
    return {
        "status": "ok",
        "version": __version__,
        "config": {
            "whisper_engine": settings.whisper_engine.value,
            "ocr_engine": settings.ocr_engine.value,
            "vision_enabled": settings.vision_enabled,
            "llm_model": settings.llm_model,
            "auto_export_on_success": settings.auto_export_on_success,
            "export_targets": [t.value for t in settings.export_target_list],
            "instagram_cookies_configured": bool(
                settings.instagram_cookie_file
                and settings.instagram_cookie_file.is_file()
            ),
        },
    }


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
