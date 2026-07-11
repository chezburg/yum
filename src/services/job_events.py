"""Job event logging: per-stage audit trail for the pipeline.

Each pipeline stage emits STARTED and then COMPLETED / FAILED / SKIPPED
events with optional message and duration, powering the job logs viewer.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager

from sqlmodel import select

from src.database.connection import get_session
from src.database.models import EventStatus, JobEvent

logger = logging.getLogger(__name__)


def record_event(
    job_id: str,
    stage: str,
    status: EventStatus,
    message: str | None = None,
    duration_ms: int | None = None,
) -> None:
    """Persist a single job event."""
    with get_session() as session:
        session.add(
            JobEvent(
                job_id=job_id,
                stage=stage,
                status=status,
                message=(message or None) and str(message)[:2000],
                duration_ms=duration_ms,
            )
        )
        session.commit()


@contextmanager
def stage_timer(job_id: str, stage: str):
    """Context manager that logs STARTED and COMPLETED/FAILED with duration.

    Usage:
        with stage_timer(job_id, "download") as timer:
            ...
            timer.note("downloaded 4.2 MB")   # optional completion message
        # -> emits COMPLETED with duration

    Exceptions propagate after a FAILED event is recorded.
    """
    record_event(job_id, stage, EventStatus.STARTED)
    start = time.monotonic()

    class _Timer:
        message: str | None = None

        def note(self, message: str) -> None:
            self.message = message

    timer = _Timer()
    try:
        yield timer
    except Exception as exc:
        duration = int((time.monotonic() - start) * 1000)
        record_event(job_id, stage, EventStatus.FAILED, str(exc), duration)
        raise
    else:
        duration = int((time.monotonic() - start) * 1000)
        record_event(job_id, stage, EventStatus.COMPLETED, timer.message, duration)


def record_skip(job_id: str, stage: str, reason: str) -> None:
    """Record a skipped stage (e.g. vision disabled, comments unavailable)."""
    record_event(job_id, stage, EventStatus.SKIPPED, reason)


def events_for_job(job_id: str) -> list[JobEvent]:
    """All events for a job, oldest first."""
    with get_session() as session:
        return list(
            session.exec(
                select(JobEvent)
                .where(JobEvent.job_id == job_id)
                .order_by(JobEvent.created_at.asc())
            ).all()
        )
