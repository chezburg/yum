"""Tests for pipeline recompute (reconstruction-only re-run)."""

from __future__ import annotations

import json

import pytest

from src.database.connection import get_session
from src.database.models import JobStatus, RecipeJob
from src.pipeline import run_reconstruction_only
from src.reconstruction.schemas import StructuredRecipe
from src.services import settings_service


def _make_recipe(title: str = "Recomputed Pasta") -> StructuredRecipe:
    return StructuredRecipe(
        title=title,
        description=None,
        ingredients=[],
        instructions=[],
        equipment=[],
        prep_time=None,
        cook_time=None,
        servings=None,
        tags=[],
        overall_confidence=0.8,
        notes=[],
    )


@pytest.fixture
def job_with_evidence(app_env) -> str:
    with get_session() as session:
        job = RecipeJob(
            url="https://www.instagram.com/reel/RC1/",
            shortcode="RC1",
            status=JobStatus.FAILED,
            error_message="No ingredients were extracted",
            video_metadata=json.dumps(
                {
                    "title": "Seafood Pasta",
                    "caption": "Full recipe in comments!",
                    "author": "chef",
                    "hashtags": ["#pasta"],
                    "duration_seconds": 30,
                }
            ),
            raw_transcript=json.dumps([{"start": 0.0, "text": "boil water"}]),
            raw_ocr_text=json.dumps([]),
            raw_vision=json.dumps([]),
            comments=json.dumps([{"author": "chef", "text": "recipe below", "is_creator": True}]),
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        return job.id


class TestRunReconstructionOnly:
    def test_recomputes_from_stored_evidence(self, job_with_evidence, monkeypatch):
        settings_service.save_settings({"auto_export_on_success": False})
        import src.pipeline as pipeline_module

        monkeypatch.setattr(
            pipeline_module, "reconstruct_recipe", lambda evidence, settings: _make_recipe()
        )

        run_reconstruction_only(job_with_evidence)

        with get_session() as session:
            job = session.get(RecipeJob, job_with_evidence)
            assert job.status == JobStatus.COMPLETED
            assert job.error_message == "No ingredients were extracted"  # untouched
            recipe = json.loads(job.structured_recipe)
            assert recipe["title"] == "Recomputed Pasta"
            assert job.markdown_content
            assert "Recomputed Pasta" in job.markdown_content

    def test_uses_stored_evidence_fields_in_bundle(self, job_with_evidence, monkeypatch):
        settings_service.save_settings({"auto_export_on_success": False})
        captured = {}

        def _capture(evidence, settings):
            captured["evidence"] = evidence
            return _make_recipe()

        import src.pipeline as pipeline_module

        monkeypatch.setattr(pipeline_module, "reconstruct_recipe", _capture)

        run_reconstruction_only(job_with_evidence)

        evidence = captured["evidence"]
        assert evidence.title == "Seafood Pasta"
        assert evidence.caption == "Full recipe in comments!"
        assert evidence.author == "chef"
        assert evidence.hashtags == ["#pasta"]
        assert evidence.transcript_segments == [{"start": 0.0, "text": "boil water"}]
        assert evidence.comments[0]["author"] == "chef"

    def test_missing_evidence_marks_job_failed(self, app_env, monkeypatch):
        with get_session() as session:
            job = RecipeJob(url="https://x/", shortcode="NOEV")
            session.add(job)
            session.commit()
            session.refresh(job)
            job_id = job.id

        run_reconstruction_only(job_id)

        with get_session() as session:
            job = session.get(RecipeJob, job_id)
            assert job.status == JobStatus.FAILED
            assert "no stored evidence" in job.error_message.lower()

    def test_reconstruction_failure_marks_job_failed(self, job_with_evidence, monkeypatch):
        import src.pipeline as pipeline_module

        def _boom(evidence, settings):
            raise RuntimeError("LLM exploded")

        monkeypatch.setattr(pipeline_module, "reconstruct_recipe", _boom)

        run_reconstruction_only(job_with_evidence)

        with get_session() as session:
            job = session.get(RecipeJob, job_with_evidence)
            assert job.status == JobStatus.FAILED
            assert "LLM exploded" in job.error_message
