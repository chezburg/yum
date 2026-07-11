"""Export dispatch service.

Reusable by both the pipeline (auto-export on success) and the API/web UI
("export now" button). Each export attempt is recorded as a job event and
merged into the job's `export_results`.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from src.config import ExportTarget, Settings
from src.database.connection import get_session
from src.database.models import EventStatus, RecipeJob
from src.export.markdown import write_markdown_file
from src.export.mealie import export_to_mealie
from src.export.tandoor import export_to_tandoor
from src.reconstruction.schemas import StructuredRecipe
from src.services.job_events import record_event

logger = logging.getLogger(__name__)


class ExportUnavailableError(RuntimeError):
    """Raised when a job has no completed recipe to export."""


def run_exports(
    recipe: StructuredRecipe,
    markdown: str,
    source_url: str,
    settings: Settings,
    targets: list[ExportTarget],
    job_id: str | None = None,
) -> dict[str, str]:
    """Export a recipe to the given targets; per-target failures are recorded,
    not raised."""
    results: dict[str, str] = {}
    for target in targets:
        try:
            if target == ExportTarget.MEALIE:
                slug = export_to_mealie(recipe, source_url, settings)
                results["mealie"] = f"ok:{slug}"
            elif target == ExportTarget.TANDOOR:
                rid = export_to_tandoor(recipe, source_url, settings)
                results["tandoor"] = f"ok:{rid}"
            elif target == ExportTarget.MARKDOWN:
                path = write_markdown_file(
                    markdown, recipe.title, Path(settings.markdown_export_dir)
                )
                results["markdown"] = f"ok:{path}"
            elif target == ExportTarget.JSON:
                results["json"] = "ok:stored_in_db"
        except Exception as exc:  # noqa: BLE001 - report per-target failures
            logger.warning("Export to %s failed: %s", target.value, exc)
            results[target.value] = f"error:{exc}"

        if job_id:
            outcome = results.get(target.value, "")
            record_event(
                job_id,
                f"export:{target.value}",
                EventStatus.COMPLETED if outcome.startswith("ok") else EventStatus.FAILED,
                outcome,
            )
    return results


def export_job(job_id: str, targets: list[ExportTarget], settings: Settings) -> dict[str, str]:
    """On-demand export of a completed job (from API / web UI).

    Raises:
        ExportUnavailableError: If the job doesn't exist or has no recipe yet.
    """
    with get_session() as session:
        job = session.get(RecipeJob, job_id)
        if job is None:
            raise ExportUnavailableError("Job not found.")
        if not job.structured_recipe or not job.markdown_content:
            raise ExportUnavailableError(
                f"Job has no completed recipe (status: {job.status.value})."
            )
        recipe = StructuredRecipe.model_validate_json(job.structured_recipe)
        markdown = job.markdown_content
        url = job.url

    results = run_exports(recipe, markdown, url, settings, targets, job_id=job_id)

    # Merge results into the job record.
    with get_session() as session:
        job = session.get(RecipeJob, job_id)
        if job is not None:
            existing = {}
            if job.export_results:
                try:
                    existing = json.loads(job.export_results)
                except json.JSONDecodeError:
                    existing = {}
            existing.update(results)
            job.export_results = json.dumps(existing)
            session.add(job)
            session.commit()
    return results
