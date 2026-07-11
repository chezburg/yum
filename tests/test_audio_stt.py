"""Tests for the generic OpenAI-compatible STT client."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from src.config import Settings
from src.processing import audio
from src.processing.audio import TranscriptionError, transcribe


@pytest.fixture
def wav_file(tmp_path: Path) -> Path:
    path = tmp_path / "clip.wav"
    path.write_bytes(b"RIFF....WAVEfmt fake")
    return path


def _settings(**overrides) -> Settings:
    values = {
        "stt_api_base": "http://localhost:9999/v1",
        "stt_api_key": "",
        "stt_model": "",
    }
    values.update(overrides)
    return Settings(**values)


class _FakeResponse:
    def __init__(self, status_code: int, payload: object = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or (str(payload) if payload is not None else "")

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class TestTranscribe:
    def test_posts_to_audio_transcriptions(self, wav_file, monkeypatch):
        captured: dict = {}

        def fake_post(url, headers=None, data=None, files=None, timeout=None):
            captured.update(url=url, headers=headers, data=data, files=files)
            return _FakeResponse(
                200,
                {
                    "segments": [
                        {"start": 0.0, "end": 2.5, "text": " Boil water. "},
                        {"start": 2.5, "end": 4.0, "text": "Add pasta."},
                    ],
                    "text": "Boil water. Add pasta.",
                },
            )

        monkeypatch.setattr(audio.httpx, "post", fake_post)
        segments = transcribe(
            wav_file,
            _settings(stt_api_key="sk-test", stt_model="whisper-large-v3"),
        )

        assert captured["url"] == "http://localhost:9999/v1/audio/transcriptions"
        assert captured["headers"]["Authorization"] == "Bearer sk-test"
        assert captured["data"]["model"] == "whisper-large-v3"
        assert captured["data"]["response_format"] == "verbose_json"
        assert [s.text for s in segments] == ["Boil water.", "Add pasta."]
        assert segments[0].start == 0.0
        assert segments[1].end == 4.0

    def test_no_key_omits_auth_header(self, wav_file, monkeypatch):
        captured: dict = {}

        def fake_post(url, headers=None, data=None, files=None, timeout=None):
            captured.update(headers=headers, data=data)
            return _FakeResponse(200, {"text": "hello"})

        monkeypatch.setattr(audio.httpx, "post", fake_post)
        segments = transcribe(wav_file, _settings())
        assert "Authorization" not in captured["headers"]
        assert "model" not in captured["data"]  # blank model omitted
        assert segments[0].text == "hello"

    def test_flat_text_fallback(self, wav_file, monkeypatch):
        monkeypatch.setattr(
            audio.httpx,
            "post",
            lambda *a, **k: _FakeResponse(200, {"text": "just words"}),
        )
        segments = transcribe(wav_file, _settings())
        assert len(segments) == 1
        assert segments[0].text == "just words"

    def test_http_error_status_raises(self, wav_file, monkeypatch):
        monkeypatch.setattr(
            audio.httpx,
            "post",
            lambda *a, **k: _FakeResponse(401, text="unauthorized"),
        )
        with pytest.raises(TranscriptionError, match="401"):
            transcribe(wav_file, _settings())

    def test_connection_error_raises(self, wav_file, monkeypatch):
        def fake_post(*a, **k):
            raise httpx.ConnectError("refused")

        monkeypatch.setattr(audio.httpx, "post", fake_post)
        with pytest.raises(TranscriptionError, match="failed"):
            transcribe(wav_file, _settings())

    def test_missing_base_url_raises(self, wav_file):
        with pytest.raises(TranscriptionError, match="not configured"):
            transcribe(wav_file, _settings(stt_api_base=""))

    def test_non_json_response_raises(self, wav_file, monkeypatch):
        monkeypatch.setattr(
            audio.httpx,
            "post",
            lambda *a, **k: _FakeResponse(200, None, text="<html>oops</html>"),
        )
        with pytest.raises(TranscriptionError, match="non-JSON"):
            transcribe(wav_file, _settings())
