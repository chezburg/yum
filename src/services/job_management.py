"""Job lifecycle management: full re-run and deletion.

Reusable by both the API and the web UI ("re-run" / "delete" buttons).
"""

from __future__ import annotations

import logging

from sqlmodel import delete, select

from src.database.connection import get_session
from src.database.models import JobEvent, JobStatus, RecipeJob

logger = logging.getLogger(__name__)


class JobNotFoundError(RuntimeError):
    """Raised when the referenced job does not exist."""


def reset_job_for_rerun(job_id: str) -> None:
    """Reset a job back to PENDING, clearing all prior evidence/results.

    Prepares the job to be resubmitted through the normal pipeline
    (re-downloads and re-processes everything from scratch). Job events
    from the previous run are cleared so the timeline reflects only the
    new run.

    Raises:
        JobNotFoundError: If the job doesn't exist.
    """
    with get_session() as session:
        job = session.get(RecipeJob, job_id)
        if job is None:
            raise JobNotFoundError(f"Job {job_id} not found.")

        session.exec(delete(JobEvent).where(JobEvent.job_id == job_id))

        job.status = JobStatus.PENDING
        job.error_message = None
        job.video_metadata = None
        job.raw_transcript = None
        job.raw_ocr_text = None
        job.raw_vision = None
        job.comments = None
        job.structured_recipe = None
        job.markdown_content = None
        job.validation_report = None
        job.export_results = None
        session.add(job)
        session.commit()


def delete_job(job_id: str) -> None:
    """Permanently delete a job and its associated event log.

    Raises:
        JobNotFoundError: If the job doesn't exist.
    """
    with get_session() as session:
        job = session.get(RecipeJob, job_id)
        if job is None:
            raise JobNotFoundError(f"Job {job_id} not found.")
        session.exec(delete(JobEvent).where(JobEvent.job_id == job_id))
        session.delete(job)
        session.commit()
    logger.info("Job %s deleted.", job_id)
