"""Tests for the guided Instagram login (Instaloader mocked)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from instaloader.exceptions import (
    BadCredentialsException,
    ConnectionException,
    TwoFactorAuthRequiredException,
)

from src.acquisition import auth
from src.services import settings_service


def _mock_loader(session_data: dict | None = None):
    loader = MagicMock()
    loader.save_session.return_value = session_data or {
        "sessionid": "abc123",
        "ds_user_id": "42",
        "csrftoken": "tok",
    }
    return loader


class TestStartLogin:
    def test_successful_login_stores_session(self, app_env):
        loader = _mock_loader()
        with patch.object(auth.instaloader, "Instaloader", return_value=loader):
            result = auth.start_login("Chef.User", "hunter22")
        assert result.state == auth.LoginState.COMPLETE
        assert result.username == "chef.user"
        loader.login.assert_called_once_with("chef.user", "hunter22")

        settings = settings_service.get_settings()
        assert settings.instagram_username == "chef.user"
        assert json.loads(settings.instagram_session)["sessionid"] == "abc123"

    def test_bad_credentials(self, app_env):
        loader = _mock_loader()
        loader.login.side_effect = BadCredentialsException("nope")
        with patch.object(auth.instaloader, "Instaloader", return_value=loader):
            result = auth.start_login("user", "wrong")
        assert result.state == auth.LoginState.FAILED
        assert "Incorrect" in result.message

    def test_connection_refused_gives_guidance(self, app_env):
        loader = _mock_loader()
        loader.login.side_effect = ConnectionException("checkpoint required")
        with patch.object(auth.instaloader, "Instaloader", return_value=loader):
            result = auth.start_login("user", "pass")
        assert result.state == auth.LoginState.FAILED
        assert "approve it" in result.message

    def test_empty_credentials(self, app_env):
        result = auth.start_login("", "")
        assert result.state == auth.LoginState.FAILED

    def test_two_factor_required(self, app_env):
        loader = _mock_loader()
        loader.login.side_effect = TwoFactorAuthRequiredException("2fa")
        with patch.object(auth.instaloader, "Instaloader", return_value=loader):
            result = auth.start_login("user", "pass")
        assert result.state == auth.LoginState.TWO_FACTOR_REQUIRED
        assert result.wizard_token


class TestTwoFactor:
    def _pending_login(self):
        loader = _mock_loader()
        loader.login.side_effect = TwoFactorAuthRequiredException("2fa")
        with patch.object(auth.instaloader, "Instaloader", return_value=loader):
            result = auth.start_login("user", "pass")
        return loader, result.wizard_token

    def test_complete_two_factor(self, app_env):
        loader, token = self._pending_login()
        result = auth.complete_two_factor(token, "123 456")
        assert result.state == auth.LoginState.COMPLETE
        loader.two_factor_login.assert_called_once_with("123456")
        assert settings_service.get_settings().instagram_username == "user"

    def test_invalid_code_allows_retry(self, app_env):
        loader, token = self._pending_login()
        loader.two_factor_login.side_effect = BadCredentialsException("bad code")
        result = auth.complete_two_factor(token, "000000")
        assert result.state == auth.LoginState.TWO_FACTOR_REQUIRED
        assert result.wizard_token == token  # same token still valid

    def test_expired_token(self, app_env):
        result = auth.complete_two_factor("nonexistent-token", "123456")
        assert result.state == auth.LoginState.FAILED
        assert "expired" in result.message.lower()


class TestDisconnectAndStatus:
    def test_disconnect_clears_session(self, app_env):
        loader = _mock_loader()
        with patch.object(auth.instaloader, "Instaloader", return_value=loader):
            auth.start_login("user", "pass")
        assert auth.connection_status(settings_service.get_settings())["connected"]

        auth.disconnect()
        status = auth.connection_status(settings_service.get_settings())
        assert not status["connected"]
        assert status["username"] is None


class TestCookieExport:
    def test_write_netscape_cookies(self, app_env, tmp_path):
        loader = _mock_loader({"sessionid": "s3ss10n", "csrftoken": "csrf"})
        with patch.object(auth.instaloader, "Instaloader", return_value=loader):
            auth.start_login("user", "pass")

        settings = settings_service.get_settings()
        path = auth.write_netscape_cookies(settings, tmp_path)
        assert path is not None
        content = path.read_text(encoding="utf-8")
        assert content.startswith("# Netscape HTTP Cookie File")
        assert ".instagram.com" in content
        assert "sessionid\ts3ss10n" in content

    def test_no_session_returns_none(self, app_env, tmp_path):
        settings = settings_service.get_settings()
        assert auth.write_netscape_cookies(settings, tmp_path) is None
