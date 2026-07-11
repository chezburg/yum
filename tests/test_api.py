"""API endpoint tests using FastAPI TestClient with a temp database.

The pipeline executor is stubbed so no real downloads/LLM calls occur.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Point the app at a temp SQLite DB before anything imports settings.
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("INSTAGRAM_COOKIE_FILE", "")
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    # Reset cached settings/engine so env vars take effect.
    import src.config as config
    import src.database.connection as connection

    config.get_settings.cache_clear()
    connection._engine = None

    import src.main as main

    # Stub out the heavy pipeline: jobs are queued but never executed.
    monkeypatch.setattr(main._executor, "submit", lambda *a, **kw: None)

    with TestClient(main.app) as test_client:
        yield test_client

    config.get_settings.cache_clear()
    connection._engine = None


class TestExtractEndpoint:
    def test_accepts_share_sheet_text(self, client):
        resp = client.post(
            "/api/v1/extract",
            json={"text": "So good! https://www.instagram.com/reel/Cabc123/?igsh=xyz"},
        )
        assert resp.status_code == 202
        body = resp.json()
        assert body["url"] == "https://www.instagram.com/reel/Cabc123/"
        assert body["status"] == "pending"
        assert body["job_id"]

    def test_rejects_text_without_url(self, client):
        resp = client.post("/api/v1/extract", json={"text": "no url here"})
        assert resp.status_code == 422

    def test_rejects_empty_body(self, client):
        resp = client.post("/api/v1/extract", json={"text": ""})
        assert resp.status_code == 422


class TestJobEndpoints:
    def test_job_lifecycle(self, client):
        create = client.post(
            "/api/v1/extract",
            json={"text": "https://www.instagram.com/reel/Cjob1/"},
        )
        job_id = create.json()["job_id"]

        detail = client.get(f"/api/v1/jobs/{job_id}")
        assert detail.status_code == 200
        assert detail.json()["status"] == "pending"

        listing = client.get("/api/v1/jobs")
        assert listing.status_code == 200
        assert any(j["id"] == job_id for j in listing.json())

    def test_missing_job_404(self, client):
        assert client.get("/api/v1/jobs/doesnotexist").status_code == 404

    def test_markdown_unavailable_before_completion(self, client):
        create = client.post(
            "/api/v1/extract",
            json={"text": "https://www.instagram.com/reel/Cmd1/"},
        )
        job_id = create.json()["job_id"]
        resp = client.get(f"/api/v1/jobs/{job_id}/markdown")
        assert resp.status_code == 409


class TestHealth:
    def test_health_reports_config_without_secrets(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "llm_model" in body["config"]
        # Ensure no secret values leak into the health output.
        assert "test-key" not in resp.text
