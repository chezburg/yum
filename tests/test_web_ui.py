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
