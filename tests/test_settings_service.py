"""Tests for the database-backed settings service."""

from __future__ import annotations

import pytest
from sqlmodel import select

from src.config import EngineMode, OCREngine
from src.database.connection import get_session
from src.database.models import AppSetting
from src.services import settings_service


class TestDefaults:
    def test_defaults_when_db_empty(self, app_env):
        settings = settings_service.get_settings()
        assert settings.llm_model == "gemini/gemini-2.5-flash"
        assert settings.auto_export_on_success is True
        assert settings.ocr_engine == OCREngine.TESSERACT

    def test_engine_defaults(self, app_env):
        settings = settings_service.get_settings()
        assert settings.stt_engine_mode == EngineMode.LOCAL
        assert settings.stt_api_base == "http://localhost:8000/v1"
        assert settings.stt_api_key == ""
        assert settings.stt_model == ""
        assert settings.llm_engine_mode == EngineMode.CLOUD
        assert settings.vision_engine_mode == EngineMode.CLOUD
        assert settings.vision_api_base == ""
        assert settings.vision_api_key == ""


class TestSaveAndLoad:
    def test_save_plain_setting(self, app_env):
        settings_service.save_settings({"llm_model": "gpt-4o-mini"})
        assert settings_service.get_settings().llm_model == "gpt-4o-mini"

    def test_save_bool_and_enum(self, app_env):
        settings_service.save_settings(
            {"auto_export_on_success": False, "ocr_engine": "paddleocr"}
        )
        settings = settings_service.get_settings()
        assert settings.auto_export_on_success is False
        assert settings.ocr_engine == OCREngine.PADDLEOCR

    def test_save_engine_mode(self, app_env):
        settings_service.save_settings(
            {"stt_engine_mode": "cloud", "stt_api_base": "https://api.groq.com/openai/v1"}
        )
        settings = settings_service.get_settings()
        assert settings.stt_engine_mode == EngineMode.CLOUD
        assert settings.stt_api_base == "https://api.groq.com/openai/v1"

    def test_engine_secrets_encrypted(self, app_env):
        settings_service.save_settings(
            {"stt_api_key": "sk-stt", "vision_api_key": "sk-vis"}
        )
        with get_session() as session:
            for key in ("stt_api_key", "vision_api_key"):
                row = session.exec(
                    select(AppSetting).where(AppSetting.key == key)
                ).one()
                assert row.value.startswith("enc:v1:")
        settings = settings_service.get_settings()
        assert settings.stt_api_key == "sk-stt"
        assert settings.vision_api_key == "sk-vis"

    def test_unknown_key_rejected(self, app_env):
        with pytest.raises(ValueError, match="Unknown setting"):
            settings_service.save_settings({"bogus": "x"})

    def test_invalid_value_rejected_before_write(self, app_env):
        with pytest.raises(Exception):
            settings_service.save_settings({"ocr_max_frames": "not-a-number"})
        # Original value untouched
        assert settings_service.get_settings().ocr_max_frames == 40

    def test_cache_invalidated_on_save(self, app_env):
        first = settings_service.get_settings()
        assert first.llm_model == "gemini/gemini-2.5-flash"
        settings_service.save_settings({"llm_model": "changed"})
        assert settings_service.get_settings().llm_model == "changed"


class TestSecrets:
    def test_secret_encrypted_at_rest(self, app_env):
        settings_service.save_settings({"llm_api_key": "sk-plaintext-key"})
        with get_session() as session:
            row = session.exec(
                select(AppSetting).where(AppSetting.key == "llm_api_key")
            ).one()
            assert row.value.startswith("enc:v1:")
            assert "sk-plaintext-key" not in row.value
        # But decrypted transparently on load
        assert settings_service.get_settings().llm_api_key == "sk-plaintext-key"

    def test_empty_secret_update_keeps_existing(self, app_env):
        settings_service.save_settings({"llm_api_key": "sk-original"})
        settings_service.save_settings({"llm_api_key": ""})
        assert settings_service.get_settings().llm_api_key == "sk-original"

    def test_mask_placeholder_keeps_existing(self, app_env):
        settings_service.save_settings({"llm_api_key": "sk-original"})
        settings_service.save_settings(
            {"llm_api_key": settings_service.SECRET_MASK}
        )
        assert settings_service.get_settings().llm_api_key == "sk-original"

    def test_clear_secret(self, app_env):
        settings_service.save_settings({"llm_api_key": "sk-to-clear"})
        settings_service.clear_secret("llm_api_key")
        assert settings_service.get_settings().llm_api_key == ""

    def test_clear_non_secret_rejected(self, app_env):
        with pytest.raises(ValueError):
            settings_service.clear_secret("llm_model")

    def test_masked_dump_never_reveals(self, app_env):
        settings_service.save_settings({"mealie_api_token": "tok-secret"})
        dump = settings_service.masked_settings_dump()
        assert dump["mealie_api_token"] == settings_service.SECRET_MASK
        assert dump["llm_api_key"] == ""  # unset secrets show empty
        assert "tok-secret" not in str(dump)
