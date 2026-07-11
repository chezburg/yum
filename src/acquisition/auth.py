"""Guided Instagram authentication (username/password + 2FA wizard).

Flow:
    1. `start_login(username, password)` - attempts login via Instaloader.
       Returns COMPLETE, or TWO_FACTOR_REQUIRED with a short-lived wizard
       token holding the pending Instaloader instance in memory.
    2. `complete_two_factor(token, code)` - finishes a 2FA login.
    3. On success the session cookies are serialized and stored encrypted
       in the app settings (never the password - it is used once and
       discarded).

Consumers use `build_loader()` (Instaloader with the stored session) and
`write_netscape_cookies()` (cookies.txt for yt-dlp) from the same session.
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import instaloader
from instaloader.exceptions import (
    BadCredentialsException,
    ConnectionException,
    InvalidArgumentException,
    TwoFactorAuthRequiredException,
)

from src.config import Settings
from src.services import settings_service

logger = logging.getLogger(__name__)

# Pending 2FA logins expire after this many seconds.
_PENDING_TTL = 300.0


class LoginState(str, Enum):
    COMPLETE = "complete"
    TWO_FACTOR_REQUIRED = "two_factor_required"
    FAILED = "failed"


@dataclass
class LoginResult:
    state: LoginState
    message: str
    wizard_token: str | None = None
    username: str | None = None


_pending_lock = threading.Lock()
_pending: dict[str, tuple[float, instaloader.Instaloader, str]] = {}


def start_login(username: str, password: str) -> LoginResult:
    """Attempt an Instagram login; may require a follow-up 2FA step."""
    username = username.strip().lstrip("@").lower()
    if not username or not password:
        return LoginResult(LoginState.FAILED, "Username and password are required.")

    loader = instaloader.Instaloader(quiet=True)
    try:
        loader.login(username, password)
    except TwoFactorAuthRequiredException:
        token = secrets.token_urlsafe(32)
        with _pending_lock:
            _prune_pending()
            _pending[token] = (time.monotonic(), loader, username)
        logger.info("2FA required for @%s - awaiting code.", username)
        return LoginResult(
            LoginState.TWO_FACTOR_REQUIRED,
            "Two-factor authentication required. Enter the code from your "
            "authenticator app or SMS.",
            wizard_token=token,
        )
    except BadCredentialsException:
        return LoginResult(LoginState.FAILED, "Incorrect username or password.")
    except ConnectionException as exc:
        return LoginResult(
            LoginState.FAILED,
            "Instagram refused the login. If you received a security "
            "notification in the Instagram app, approve it and try again. "
            f"Details: {exc}",
        )
    except Exception as exc:  # noqa: BLE001 - instaloader raises many types
        logger.exception("Unexpected login failure for @%s", username)
        return LoginResult(LoginState.FAILED, f"Login failed: {exc}")

    _store_session(loader, username)
    return LoginResult(
        LoginState.COMPLETE, f"Connected as @{username}.", username=username
    )


def complete_two_factor(wizard_token: str, code: str) -> LoginResult:
    """Complete a pending two-factor login with the user's code."""
    code = code.strip().replace(" ", "")
    with _pending_lock:
        _prune_pending()
        entry = _pending.pop(wizard_token, None)
    if entry is None:
        return LoginResult(
            LoginState.FAILED,
            "Login session expired or invalid. Start the login again.",
        )
    _started, loader, username = entry

    try:
        loader.two_factor_login(code)
    except BadCredentialsException:
        # Put the pending login back so the user can retry the code.
        with _pending_lock:
            _pending[wizard_token] = (time.monotonic(), loader, username)
        return LoginResult(
            LoginState.TWO_FACTOR_REQUIRED,
            "Invalid 2FA code - try again.",
            wizard_token=wizard_token,
        )
    except (InvalidArgumentException, ConnectionException) as exc:
        return LoginResult(LoginState.FAILED, f"2FA login failed: {exc}")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected 2FA failure for @%s", username)
        return LoginResult(LoginState.FAILED, f"2FA login failed: {exc}")

    _store_session(loader, username)
    return LoginResult(
        LoginState.COMPLETE, f"Connected as @{username}.", username=username
    )


def disconnect() -> None:
    """Remove the stored Instagram session."""
    settings_service.clear_secret("instagram_session")
    settings_service.save_settings({"instagram_username": ""})


def connection_status(settings: Settings) -> dict:
    """Non-secret summary of the Instagram connection for UI display."""
    connected = bool(settings.instagram_session.strip())
    return {
        "connected": connected,
        "username": settings.instagram_username if connected else None,
    }


def build_loader(settings: Settings) -> instaloader.Instaloader:
    """Instaloader instance restored from the stored session (or anonymous)."""
    loader = instaloader.Instaloader(
        download_pictures=False,
        download_videos=False,
        download_video_thumbnails=False,
        download_comments=False,
        save_metadata=False,
        quiet=True,
    )
    session = _session_dict(settings)
    if session and settings.instagram_username:
        loader.load_session(settings.instagram_username, session)
        logger.debug("Restored Instagram session for @%s", settings.instagram_username)
    else:
        logger.warning("No Instagram session stored - operating anonymously.")
    return loader


def write_netscape_cookies(settings: Settings, dest_dir: Path) -> Path | None:
    """Write the stored session as a Netscape cookies.txt for yt-dlp.

    Returns None when no session is stored.
    """
    session = _session_dict(settings)
    if not session:
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / "instagram_cookies.txt"
    # Expiry far in the future; Instagram sessions are invalidated server-side.
    expiry = int(time.time()) + 365 * 24 * 3600
    lines = ["# Netscape HTTP Cookie File"]
    for name, value in session.items():
        lines.append(f".instagram.com\tTRUE\t/\tTRUE\t{expiry}\t{name}\t{value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _store_session(loader: instaloader.Instaloader, username: str) -> None:
    """Persist the session cookies (encrypted) and the username."""
    session_data = loader.save_session()
    settings_service.save_settings(
        {
            "instagram_session": json.dumps(session_data),
            "instagram_username": username,
        }
    )
    logger.info("Stored Instagram session for @%s.", username)


def _session_dict(settings: Settings) -> dict | None:
    raw = settings.instagram_session.strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        logger.error("Stored Instagram session is corrupted - ignoring.")
        return None


def _prune_pending() -> None:
    now = time.monotonic()
    expired = [k for k, (t, _, _) in _pending.items() if now - t > _PENDING_TTL]
    for key in expired:
        del _pending[key]
