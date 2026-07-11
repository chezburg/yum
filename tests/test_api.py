"""API endpoint tests using FastAPI TestClient with a temp database.

The pipeline executor is stubbed (see conftest.client) so no real
downloads/LLM calls occur.
"""

from __future__ import annotations


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
        assert body["job_id"] in client.submitted_jobs

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

        events = client.get(f"/api/v1/jobs/{job_id}/events")
        assert events.status_code == 200
        assert events.json() == []

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

    def test_export_unavailable_before_completion(self, client):
        create = client.post(
            "/api/v1/extract",
            json={"text": "https://www.instagram.com/reel/Cex1/"},
        )
        job_id = create.json()["job_id"]
        resp = client.post(
            f"/api/v1/jobs/{job_id}/export", json={"targets": ["json"]}
        )
        assert resp.status_code == 409

    def test_export_unknown_target_rejected(self, client):
        resp = client.post(
            "/api/v1/jobs/whatever/export", json={"targets": ["dropbox"]}
        )
        assert resp.status_code == 422


class TestSettingsEndpoints:
    def test_get_settings_masks_secrets(self, client):
        client.put(
            "/api/v1/settings", json={"values": {"llm_api_key": "sk-super-secret"}}
        )
        resp = client.get("/api/v1/settings")
        assert resp.status_code == 200
        body = resp.json()
        assert body["llm_api_key"] == "********"
        assert "sk-super-secret" not in resp.text

    def test_update_setting(self, client):
        resp = client.put(
            "/api/v1/settings", json={"values": {"llm_model": "gpt-4o-mini"}}
        )
        assert resp.status_code == 200
        assert resp.json()["llm_model"] == "gpt-4o-mini"

    def test_unknown_setting_rejected(self, client):
        resp = client.put(
            "/api/v1/settings", json={"values": {"nonsense_key": "x"}}
        )
        assert resp.status_code == 422

    def test_invalid_enum_rejected(self, client):
        resp = client.put(
            "/api/v1/settings", json={"values": {"stt_engine_mode": "banana"}}
        )
        assert resp.status_code == 422


class TestEngineTestEndpoint:
    def test_unknown_engine_404(self, client):
        assert client.post("/api/v1/settings/test/bogus").status_code == 404

    def test_stt_test_dispatches(self, client, monkeypatch):
        from src.services import engine_test

        monkeypatch.setattr(
            engine_test, "test_engine", lambda e, s: (True, f"pinged {e}")
        )
        resp = client.post("/api/v1/settings/test/stt")
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"engine": "stt", "ok": True, "message": "pinged stt"}

    def test_llm_test_reports_failure(self, client, monkeypatch):
        from src.services import engine_test

        monkeypatch.setattr(
            engine_test, "test_engine", lambda e, s: (False, "boom")
        )
        resp = client.post("/api/v1/settings/test/llm")
        assert resp.status_code == 200
        assert resp.json() == {"engine": "llm", "ok": False, "message": "boom"}


class TestHealth:
    def test_health_reports_config_without_secrets(self, client):
        client.put(
            "/api/v1/settings", json={"values": {"llm_api_key": "sk-leaky-key"}}
        )
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "llm_model" in body["config"]
        assert body["config"]["instagram_connected"] is False
        assert "sk-leaky-key" not in resp.text
