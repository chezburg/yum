"""Web UI routes: server-rendered pages backed by the same services as the API.

Views contain no business logic - they call services and render templates,
so a future SPA migration only needs to replace this layer with API calls.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlmodel import select

from src.acquisition import auth
from src.config import ExportTarget, Settings, field_groups, secret_field_names
from src.database.connection import get_session
from src.database.models import JobStatus, RecipeJob
from src.services import engine_test, settings_service
from src.services.export_service import ExportUnavailableError, export_job
from src.services.job_events import events_for_job
from src.utils.url_parser import URLParseError, extract_instagram_url

logger = logging.getLogger(__name__)

router = APIRouter(include_in_schema=False)

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# Populated by main.py (same injection point as the API).
_submit_job = None


def set_job_submitter(submit) -> None:
    global _submit_job
    _submit_job = submit


def _field_meta(name: str) -> dict:
    field = Settings.model_fields[name]
    extra = dict(field.json_schema_extra or {})
    extra.setdefault("label", name)
    extra.setdefault("description", "")
    extra["name"] = name
    extra["is_bool"] = field.annotation is bool
    return extra


def _render(request: Request, template: str, **context) -> HTMLResponse:
    return templates.TemplateResponse(request, template, context)


# ----------------------------------------------------------------- dashboard


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    with get_session() as session:
        recent = session.exec(
            select(RecipeJob).order_by(RecipeJob.created_at.desc()).limit(8)
        ).all()
        jobs = [_job_row(j) for j in recent]
    settings = settings_service.get_settings()
    ig = auth.connection_status(settings)
    return _render(
        request, "dashboard.html", active="dashboard", jobs=jobs, instagram=ig
    )


@router.post("/submit", response_class=HTMLResponse)
def submit_url(request: Request, text: str = Form(...)):
    """HTMX form target: queue a job and return a status fragment."""
    try:
        parsed = extract_instagram_url(text)
    except URLParseError as exc:
        return _render(request, "partials/submit_result.html", error=str(exc))

    with get_session() as session:
        existing = session.exec(
            select(RecipeJob).where(
                RecipeJob.shortcode == parsed.shortcode,
                RecipeJob.status == JobStatus.COMPLETED,
            )
        ).first()
        if existing:
            return _render(
                request,
                "partials/submit_result.html",
                job_id=existing.id,
                already_done=True,
            )
        job = RecipeJob(url=parsed.canonical_url, shortcode=parsed.shortcode)
        session.add(job)
        session.commit()
        session.refresh(job)
        job_id = job.id

    if _submit_job is not None:
        _submit_job(job_id)
    return _render(request, "partials/submit_result.html", job_id=job_id)


# ------------------------------------------------------------------- recipes


@router.get("/recipes", response_class=HTMLResponse)
def recipes_page(request: Request, q: str = ""):
    with get_session() as session:
        rows = session.exec(
            select(RecipeJob)
            .where(RecipeJob.status == JobStatus.COMPLETED)
            .order_by(RecipeJob.created_at.desc())
            .limit(500)
        ).all()
        recipes = []
        for job in rows:
            data = _load_json(job.structured_recipe) or {}
            title = data.get("title", "(untitled)")
            if q and q.lower() not in title.lower():
                continue
            recipes.append(
                {
                    "id": job.id,
                    "title": title,
                    "url": job.url,
                    "confidence": data.get("overall_confidence"),
                    "tags": data.get("tags", []),
                    "created_at": job.created_at,
                }
            )
    return _render(request, "recipes.html", active="recipes", recipes=recipes, q=q)


@router.get("/recipes/{job_id}", response_class=HTMLResponse)
def recipe_detail(request: Request, job_id: str):
    with get_session() as session:
        job = session.get(RecipeJob, job_id)
        if job is None or not job.structured_recipe:
            raise HTTPException(status_code=404, detail="Recipe not found.")
        recipe = _load_json(job.structured_recipe) or {}
        validation = _load_json(job.validation_report) or {}
        exports = _load_json(job.export_results) or {}
        markdown = job.markdown_content or ""
        url = job.url
    settings = settings_service.get_settings()
    return _render(
        request,
        "recipe_detail.html",
        active="recipes",
        job_id=job_id,
        url=url,
        recipe=recipe,
        validation=validation,
        exports=exports,
        markdown=markdown,
        export_targets=[t.value for t in ExportTarget],
        configured_targets=[t.value for t in settings.export_target_list],
    )


@router.post("/recipes/{job_id}/export", response_class=HTMLResponse)
def recipe_export(request: Request, job_id: str, target: str = Form(...)):
    """HTMX form target: run a single export and return a result fragment."""
    try:
        targets = [ExportTarget(target.strip().lower())]
    except ValueError:
        return _render(
            request, "partials/export_result.html", error=f"Unknown target: {target}"
        )
    settings = settings_service.get_settings()
    try:
        results = export_job(job_id, targets, settings)
    except ExportUnavailableError as exc:
        return _render(request, "partials/export_result.html", error=str(exc))
    return _render(request, "partials/export_result.html", results=results)


# ---------------------------------------------------------------------- jobs


@router.get("/jobs", response_class=HTMLResponse)
def jobs_page(request: Request, status: str = ""):
    with get_session() as session:
        query = select(RecipeJob).order_by(RecipeJob.created_at.desc()).limit(200)
        if status:
            try:
                query = (
                    select(RecipeJob)
                    .where(RecipeJob.status == JobStatus(status))
                    .order_by(RecipeJob.created_at.desc())
                    .limit(200)
                )
            except ValueError:
                pass
        rows = session.exec(query).all()
        jobs = [_job_row(j) for j in rows]
    statuses = [s.value for s in JobStatus]
    return _render(
        request, "jobs.html", active="jobs", jobs=jobs, statuses=statuses,
        current_status=status,
    )


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(request: Request, job_id: str):
    with get_session() as session:
        job = session.get(RecipeJob, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found.")
        detail = {
            "id": job.id,
            "url": job.url,
            "status": job.status.value,
            "error_message": job.error_message,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
            "has_recipe": bool(job.structured_recipe),
            "metadata": _load_json(job.video_metadata) or {},
            "transcript": _load_json(job.raw_transcript) or [],
            "ocr": _load_json(job.raw_ocr_text) or [],
            "vision": _load_json(job.raw_vision) or [],
            "comments": _load_json(job.comments) or [],
            "validation": _load_json(job.validation_report) or {},
            "exports": _load_json(job.export_results) or {},
        }
    events = [
        {
            "stage": e.stage,
            "status": e.status.value,
            "message": e.message,
            "duration_ms": e.duration_ms,
            "created_at": e.created_at,
        }
        for e in events_for_job(job_id)
    ]
    return _render(
        request, "job_detail.html", active="jobs", job=detail, events=events
    )


@router.get("/jobs/{job_id}/status", response_class=HTMLResponse)
def job_status_fragment(request: Request, job_id: str):
    """HTMX polling target: compact status fragment for a running job."""
    with get_session() as session:
        job = session.get(RecipeJob, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found.")
        row = _job_row(job)
    return _render(request, "partials/job_status.html", job=row)


# ------------------------------------------------------------------ settings


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, saved: str = ""):
    masked = settings_service.masked_settings_dump()
    secrets = secret_field_names()
    groups = []
    for group_name, fields in field_groups().items():
        if group_name == "Instagram":
            continue  # rendered separately as the connection wizard card
        groups.append(
            {
                "name": group_name,
                "fields": [
                    {**_field_meta(f), "value": masked.get(f, "")} for f in fields
                ],
            }
        )
    settings = settings_service.get_settings()
    ig = auth.connection_status(settings)
    return _render(
        request,
        "settings.html",
        active="settings",
        groups=groups,
        instagram=ig,
        saved=saved == "1",
        secrets=secrets,
    )


@router.post("/settings")
async def settings_save(request: Request):
    """Persist settings form submission (full-page form, standard POST)."""
    form = await request.form()
    updates: dict[str, object] = {}
    bool_fields = {
        name for name, f in Settings.model_fields.items() if f.annotation is bool
    }
    for name in Settings.model_fields:
        if name in ("instagram_session", "instagram_username"):
            continue  # managed by the login wizard only
        if name in bool_fields:
            updates[name] = name in form
        elif name in form:
            updates[name] = str(form[name])
    try:
        settings_service.save_settings(updates)
    except (ValueError, ValidationError) as exc:
        masked = settings_service.masked_settings_dump()
        groups = []
        for group_name, fields in field_groups().items():
            if group_name == "Instagram":
                continue
            groups.append(
                {
                    "name": group_name,
                    "fields": [
                        {**_field_meta(f), "value": masked.get(f, "")} for f in fields
                    ],
                }
            )
        settings = settings_service.get_settings()
        return _render(
            request,
            "settings.html",
            active="settings",
            groups=groups,
            instagram=auth.connection_status(settings),
            saved=False,
            error=str(exc),
            secrets=secret_field_names(),
        )
    return RedirectResponse("/settings?saved=1", status_code=303)


@router.post("/settings/test/{engine}", response_class=HTMLResponse)
def settings_test_engine(request: Request, engine: str):
    """HTMX target: test connectivity of a configured engine (stt/llm/vision).

    Tests run against the currently *saved* settings, so users should save
    before testing.
    """
    if engine not in ("stt", "llm", "vision"):
        raise HTTPException(status_code=404, detail="Unknown engine.")
    settings = settings_service.get_settings()
    ok, message = engine_test.test_engine(engine, settings)
    return _render(
        request, "partials/engine_test_result.html", ok=ok, message=message
    )


# --------------------------------------------------------- instagram wizard


@router.post("/settings/instagram/login", response_class=HTMLResponse)
def instagram_login_step(
    request: Request, username: str = Form(...), password: str = Form(...)
):
    """HTMX target: step 1 of the guided Instagram login."""
    result = auth.start_login(username, password)
    return _render(request, "partials/instagram_wizard.html", result=result)


@router.post("/settings/instagram/2fa", response_class=HTMLResponse)
def instagram_2fa_step(
    request: Request, wizard_token: str = Form(...), code: str = Form(...)
):
    """HTMX target: step 2 (2FA code) of the guided Instagram login."""
    result = auth.complete_two_factor(wizard_token, code)
    return _render(request, "partials/instagram_wizard.html", result=result)


@router.post("/settings/instagram/disconnect", response_class=HTMLResponse)
def instagram_disconnect_action(request: Request):
    """HTMX target: remove the stored session."""
    auth.disconnect()
    return _render(request, "partials/instagram_card.html", instagram={"connected": False, "username": None})


# ------------------------------------------------------------------- helpers


def _job_row(job: RecipeJob) -> dict:
    title = None
    data = _load_json(job.structured_recipe)
    if isinstance(data, dict):
        title = data.get("title")
    return {
        "id": job.id,
        "url": job.url,
        "shortcode": job.shortcode,
        "status": job.status.value,
        "title": title,
        "error_message": job.error_message,
        "created_at": job.created_at,
        "is_running": job.status
        not in (JobStatus.COMPLETED, JobStatus.FAILED),
    }


def _load_json(raw: str | None):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None
