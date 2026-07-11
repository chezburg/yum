"""Tests for the engine connection-test service."""

from __future__ import annotations

import httpx

from src.config import Settings
from src.services import engine_test


class _FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


class TestSttTest:
    def test_no_base_url(self):
        ok, msg = engine_test.test_stt(Settings(stt_api_base=""))
        assert not ok
        assert "No API base URL" in msg

    def test_reachable(self, monkeypatch):
        captured: dict = {}

        def fake_get(url, headers=None, timeout=None):
            captured.update(url=url, headers=headers)
            return _FakeResponse(200)

        monkeypatch.setattr(engine_test.httpx, "get", fake_get)
        ok, msg = engine_test.test_stt(
            Settings(stt_api_base="http://host:8000/v1", stt_api_key="sk-x")
        )
        assert ok
        assert captured["url"] == "http://host:8000/v1/models"
        assert captured["headers"]["Authorization"] == "Bearer sk-x"

    def test_auth_failure(self, monkeypatch):
        monkeypatch.setattr(
            engine_test.httpx, "get", lambda *a, **k: _FakeResponse(401)
        )
        ok, msg = engine_test.test_stt(Settings(stt_api_base="http://h/v1"))
        assert not ok
        assert "authentication" in msg.lower()

    def test_unreachable(self, monkeypatch):
        def fake_get(*a, **k):
            raise httpx.ConnectError("refused")

        monkeypatch.setattr(engine_test.httpx, "get", fake_get)
        ok, msg = engine_test.test_stt(Settings(stt_api_base="http://h/v1"))
        assert not ok
        assert "Unreachable" in msg

    def test_server_error(self, monkeypatch):
        monkeypatch.setattr(
            engine_test.httpx, "get", lambda *a, **k: _FakeResponse(500)
        )
        ok, msg = engine_test.test_stt(Settings(stt_api_base="http://h/v1"))
        assert not ok
        assert "500" in msg


class _FakeCompletion:
    def __init__(self, content: str = "OK"):
        message = type("M", (), {"content": content})()
        choice = type("C", (), {"message": message})()
        self.choices = [choice]


class TestLlmTest:
    def test_no_model(self):
        ok, msg = engine_test.test_llm(Settings(llm_model=""))
        assert not ok

    def test_success(self, monkeypatch):
        captured: dict = {}

        def fake_completion(**kwargs):
            captured.update(kwargs)
            return _FakeCompletion("OK")

        monkeypatch.setattr(engine_test.litellm, "completion", fake_completion)
        settings = Settings(
            llm_model="gpt-4o-mini", llm_api_key="sk-l", llm_api_base="http://b"
        )
        ok, msg = engine_test.test_llm(settings)
        assert ok
        assert "OK" in msg
        assert captured["model"] == "gpt-4o-mini"
        assert captured["api_key"] == "sk-l"
        assert captured["api_base"] == "http://b"

    def test_failure(self, monkeypatch):
        def fake_completion(**kwargs):
            raise RuntimeError("bad key")

        monkeypatch.setattr(engine_test.litellm, "completion", fake_completion)
        ok, msg = engine_test.test_llm(Settings(llm_model="gpt-4o-mini"))
        assert not ok
        assert "bad key" in msg


class TestVisionTest:
    def test_no_model(self):
        ok, _ = engine_test.test_vision(Settings(vision_model=""))
        assert not ok

    def test_uses_vision_credentials(self, monkeypatch):
        captured: dict = {}

        def fake_completion(**kwargs):
            captured.update(kwargs)
            return _FakeCompletion()

        monkeypatch.setattr(engine_test.litellm, "completion", fake_completion)
        settings = Settings(
            vision_model="gpt-4o",
            vision_api_key="sk-v",
            vision_api_base="http://v",
        )
        ok, _ = engine_test.test_vision(settings)
        assert ok
        assert captured["api_key"] == "sk-v"
        assert captured["api_base"] == "http://v"
        # Message content includes an image part.
        content = captured["messages"][0]["content"]
        assert any(part.get("type") == "image_url" for part in content)


class TestDispatch:
    def test_unknown_engine(self):
        ok, msg = engine_test.test_engine("bogus", Settings())
        assert not ok
        assert "Unknown engine" in msg
