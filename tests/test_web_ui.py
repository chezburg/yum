"""Tests for the server-rendered web UI routes."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from src.database.connection import get_session
from src.database.models import JobStatus, RecipeJob


def _make_completed_job(title: str = "Test Pasta") -> str:
    recipe = {
        "title": title,
        "description": None,
        "ingredients": [
            {
                "name": "spaghetti",
                "amount": "200 g",
                "preparation": None,
                "source": "caption",
                "confidence": 0.95,
            }
        ],
        "instructions": [
            {
                "step_number": 1,
                "text": "Boil pasta.",
                "duration": None,
                "source": "transcript",
                "confidence": 0.9,
            }
        ],
        "equipment": ["pot"],
        "prep_time": "5 min",
        "cook_time": "10 min",
        "servings": "2",
        "tags": ["italian"],
        "overall_confidence": 0.9,
        "notes": [],
    }
    with get_session() as session:
        job = RecipeJob(
            url="https://www.instagram.com/reel/WEB1/",
            shortcode="WEB1",
            status=JobStatus.COMPLETED,
            structured_recipe=json.dumps(recipe),
            markdown_content="# Test Pasta\n",
            validation_report=json.dumps({"issues": []}),
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        return job.id


class TestPages:
    def test_dashboard_renders(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Extract a recipe" in resp.text

    def test_recipes_page_lists_completed(self, client):
        _make_completed_job("Garlic Noodles")
        resp = client.get("/recipes")
        assert resp.status_code == 200
        assert "Garlic Noodles" in resp.text

    def test_recipes_search_filters(self, client):
        _make_completed_job("Garlic Noodles")
        resp = client.get("/recipes?q=zzzz")
        assert "Garlic Noodles" not in resp.text

    def test_recipe_detail_renders(self, client):
        job_id = _make_completed_job()
        resp = client.get(f"/recipes/{job_id}")
        assert resp.status_code == 200
        assert "Test Pasta" in resp.text
        assert "spaghetti" in resp.text

    def test_recipe_detail_404_for_pending(self, client):
        with get_session() as session:
            job = RecipeJob(url="https://x/", shortcode="P404")
            session.add(job)
            session.commit()
            session.refresh(job)
            job_id = job.id
        assert client.get(f"/recipes/{job_id}").status_code == 404

    def test_jobs_page_renders(self, client):
        _make_completed_job()
        resp = client.get("/jobs")
        assert resp.status_code == 200

    def test_job_detail_renders_timeline(self, client):
        job_id = _make_completed_job()
        resp = client.get(f"/jobs/{job_id}")
        assert resp.status_code == 200
        assert "Pipeline timeline" in resp.text

    def test_settings_page_renders_groups(self, client):
        resp = client.get("/settings")
        assert resp.status_code == 200
        assert "Instagram account" in resp.text
        assert "Speech-to-Text" in resp.text
        assert "LLM" in resp.text


class TestJobActions:
    def test_job_detail_shows_action_buttons_when_completed(self, client):
        job_id = _make_completed_job()
        resp = client.get(f"/jobs/{job_id}")
        assert resp.status_code == 200
        assert f"/jobs/{job_id}/recompute" in resp.text
        assert f"/jobs/{job_id}/rerun" in resp.text
        assert f"/jobs/{job_id}/delete" in resp.text

    def test_job_detail_hides_action_buttons_when_running(self, client):
        with get_session() as session:
            job = RecipeJob(url="https://x/", shortcode="RUN1")
            session.add(job)
            session.commit()
            session.refresh(job)
            job_id = job.id
        resp = client.get(f"/jobs/{job_id}")
        assert resp.status_code == 200
        assert f"/jobs/{job_id}/recompute" not in resp.text
        assert f"/jobs/{job_id}/rerun" not in resp.text

    def test_recompute_queues_and_redirects(self, client):
        job_id = _make_completed_job()
        resp = client.post(
            f"/jobs/{job_id}/recompute", follow_redirects=False
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == f"/jobs/{job_id}"
        assert job_id in client.recomputed_jobs

    def test_recompute_missing_job_404(self, client):
        resp = client.post("/jobs/doesnotexist/recompute")
        assert resp.status_code == 404

    def test_rerun_resets_and_redirects(self, client):
        job_id = _make_completed_job()
        resp = client.post(f"/jobs/{job_id}/rerun", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == f"/jobs/{job_id}"
        assert job_id in client.submitted_jobs

        with get_session() as session:
            job = session.get(RecipeJob, job_id)
            assert job.status == JobStatus.PENDING
            assert job.structured_recipe is None

    def test_rerun_missing_job_404(self, client):
        resp = client.post("/jobs/doesnotexist/rerun")
        assert resp.status_code == 404

    def test_delete_removes_job_and_redirects(self, client):
        job_id = _make_completed_job()
        resp = client.post(f"/jobs/{job_id}/delete", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/jobs"
        assert client.get(f"/jobs/{job_id}").status_code == 404

    def test_delete_missing_job_404(self, client):
        resp = client.post("/jobs/doesnotexist/delete")
        assert resp.status_code == 404


class TestSubmitForm:
    def test_submit_queues_job(self, client):
        resp = client.post(
            "/submit",
            data={"text": "https://www.instagram.com/reel/Cweb9/"},
        )
        assert resp.status_code == 200
        assert "queued" in resp.text.lower()
        assert len(client.submitted_jobs) == 1

    def test_submit_invalid_url_shows_error(self, client):
        resp = client.post("/submit", data={"text": "not a url"})
        assert resp.status_code == 200
        assert "No Instagram" in resp.text
        assert not client.submitted_jobs

class TestSettingsForm:
    def test_save_settings_via_form(self, client):
        resp = client.post(
            "/settings",
            data={"llm_model": "gpt-4o-mini", "export_targets": "json"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        from src.services import settings_service

        assert settings_service.get_settings().llm_model == "gpt-4o-mini"

    def test_checkbox_unchecked_means_false(self, client):
        client.post("/settings", data={"llm_model": "x"}, follow_redirects=False)
        from src.services import settings_service

        settings = settings_service.get_settings()
        assert settings.auto_export_on_success is False
        assert settings.vision_enabled is False

    def test_secret_left_blank_is_kept(self, client):
        from src.services import settings_service

        settings_service.save_settings({"llm_api_key": "sk-keepme"})
        client.post(
            "/settings",
            data={"llm_model": "y", "llm_api_key": ""},
            follow_redirects=False,
        )
        assert settings_service.get_settings().llm_api_key == "sk-keepme"

    def test_settings_page_shows_test_buttons(self, client):
        resp = client.get("/settings")
        assert resp.status_code == 200
        assert "/settings/test/stt" in resp.text
        assert "/settings/test/llm" in resp.text
        assert "/settings/test/vision" in resp.text


class TestEngineTestRoute:
    def test_unknown_engine_404(self, client):
        assert client.post("/settings/test/bogus").status_code == 404

    def test_result_banner_rendered(self, client, monkeypatch):
        from src.services import engine_test

        monkeypatch.setattr(
            engine_test, "test_engine", lambda e, s: (True, "Endpoint reachable")
        )
        resp = client.post("/settings/test/stt")
        assert resp.status_code == 200
        assert "Endpoint reachable" in resp.text

    def test_failure_banner_rendered(self, client, monkeypatch):
        from src.services import engine_test

        monkeypatch.setattr(
            engine_test, "test_engine", lambda e, s: (False, "Unreachable")
        )
        resp = client.post("/settings/test/llm")
        assert resp.status_code == 200
        assert "Unreachable" in resp.text


class TestInstagramWizardRoutes:
    def test_login_flow_via_web(self, client):
        from src.acquisition import auth

        loader = MagicMock()
        loader.save_session.return_value = {"sessionid": "s1"}
        with patch.object(auth.instaloader, "Instaloader", return_value=loader):
            resp = client.post(
                "/settings/instagram/login",
                data={"username": "chef", "password": "pw"},
            )
        assert resp.status_code == 200
        assert "Connected as @chef" in resp.text

    def test_disconnect_via_web(self, client):
        resp = client.post("/settings/instagram/disconnect")
        assert resp.status_code == 200
        assert "Connect" in resp.text
