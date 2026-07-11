"""Tests for job event logging."""

from __future__ import annotations

import pytest

from src.database.connection import get_session
from src.database.models import EventStatus, RecipeJob
from src.services.job_events import (
    events_for_job,
    record_event,
    record_skip,
    stage_timer,
)


@pytest.fixture
def job_id(app_env) -> str:
    with get_session() as session:
        job = RecipeJob(url="https://www.instagram.com/reel/EV1/", shortcode="EV1")
        session.add(job)
        session.commit()
        session.refresh(job)
        return job.id


class TestRecordEvent:
    def test_basic_event(self, job_id):
        record_event(job_id, "download", EventStatus.COMPLETED, "done", 1500)
        events = events_for_job(job_id)
        assert len(events) == 1
        assert events[0].stage == "download"
        assert events[0].status == EventStatus.COMPLETED
        assert events[0].duration_ms == 1500

    def test_skip_event(self, job_id):
        record_skip(job_id, "vision", "disabled in settings")
        events = events_for_job(job_id)
        assert events[0].status == EventStatus.SKIPPED
        assert "disabled" in events[0].message

    def test_long_message_truncated(self, job_id):
        record_event(job_id, "x", EventStatus.FAILED, "e" * 5000)
        assert len(events_for_job(job_id)[0].message) <= 2000


class TestStageTimer:
    def test_success_emits_started_and_completed(self, job_id):
        with stage_timer(job_id, "transcribe") as timer:
            timer.note("42 segments")
        events = events_for_job(job_id)
        assert [e.status for e in events] == [
            EventStatus.STARTED,
            EventStatus.COMPLETED,
        ]
        assert events[1].message == "42 segments"
        assert events[1].duration_ms is not None

    def test_failure_emits_failed_and_reraises(self, job_id):
        with pytest.raises(RuntimeError, match="boom"):
            with stage_timer(job_id, "download"):
                raise RuntimeError("boom")
        events = events_for_job(job_id)
        assert [e.status for e in events] == [EventStatus.STARTED, EventStatus.FAILED]
        assert "boom" in events[1].message

    def test_events_ordered_oldest_first(self, job_id):
        with stage_timer(job_id, "a"):
            pass
        with stage_timer(job_id, "b"):
            pass
        stages = [e.stage for e in events_for_job(job_id)]
        assert stages == ["a", "a", "b", "b"]
