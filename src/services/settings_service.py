"""Database-backed settings service with encryption at rest.

- Settings live in the `app_settings` table (key-value).
- Secret fields (per Settings field metadata) are encrypted with a key
  derived from the env-provided SECRET_KEY.
- A version-stamped cache avoids hitting the DB on every access while
  still picking up web-UI changes immediately (cache is invalidated on
  every save and revalidated against max(updated_at)).
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

from sqlmodel import Session, select

from src.config import Settings, get_bootstrap, secret_field_names
from src.database.connection import get_session
from src.database.models import AppSetting
from src.utils.crypto import SecretBox, SecretBoxError

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_cache: Settings | None = None
_cache_stamp: datetime | None = None
_box: SecretBox | None = None

# Placeholder returned to the UI instead of decrypted secret values.
SECRET_MASK = "********"


def _secret_box() -> SecretBox:
    global _box
    if _box is None:
        _box = SecretBox(get_bootstrap().secret_key)
    return _box


def get_settings() -> Settings:
    """Return current Settings, reading from DB (cached until settings change)."""
    global _cache, _cache_stamp
    with _lock:
        with get_session() as session:
            stamp = _max_updated_at(session)
            if _cache is not None and stamp == _cache_stamp:
                return _cache
            _cache = _load(session)
            _cache_stamp = stamp
            return _cache


def save_settings(updates: dict[str, object]) -> Settings:
    """Validate and persist setting updates; returns the new Settings.

    Secret fields: empty values and the mask placeholder are ignored
    (meaning "keep current value"); non-empty values are encrypted.
    """
    secrets = secret_field_names()
    current = get_settings()

    # Build the merged model first so validation happens before any write.
    merged = current.model_dump()
    changed: dict[str, object] = {}
    for key, value in updates.items():
        if key not in Settings.model_fields:
            raise ValueError(f"Unknown setting: {key}")
        if key in secrets and (value in ("", None, SECRET_MASK)):
            continue  # keep existing secret
        merged[key] = value
        changed[key] = value
    validated = Settings.model_validate(merged)

    global _cache, _cache_stamp
    with _lock:
        with get_session() as session:
            now = datetime.now(timezone.utc)
            for key in changed:
                raw = _serialize(getattr(validated, key))
                if key in secrets and raw:
                    raw = _secret_box().encrypt(raw)
                row = session.get(AppSetting, key)
                if row is None:
                    row = AppSetting(key=key, value=raw, updated_at=now)
                else:
                    row.value = raw
                    row.updated_at = now
                session.add(row)
            session.commit()
        _cache = None
        _cache_stamp = None
    logger.info("Settings updated: %s", ", ".join(sorted(changed)))
    return get_settings()


def clear_secret(field: str) -> None:
    """Explicitly clear a secret field (bypasses the keep-on-empty rule)."""
    if field not in secret_field_names():
        raise ValueError(f"Not a secret field: {field}")
    global _cache, _cache_stamp
    with _lock:
        with get_session() as session:
            row = session.get(AppSetting, field)
            if row is not None:
                row.value = ""
                row.updated_at = datetime.now(timezone.utc)
                session.add(row)
                session.commit()
        _cache = None
        _cache_stamp = None
    logger.info("Secret cleared: %s", field)


def masked_settings_dump() -> dict[str, object]:
    """Settings as a dict with secrets masked - safe for UI/API exposure."""
    settings = get_settings()
    secrets = secret_field_names()
    dump = settings.model_dump()
    for key in secrets:
        dump[key] = SECRET_MASK if dump.get(key) else ""
    return dump


def secret_is_set(field: str) -> bool:
    """Whether a secret field currently has a value (without revealing it)."""
    return bool(getattr(get_settings(), field, ""))


def invalidate_cache() -> None:
    """Force settings reload on next access (used by tests)."""
    global _cache, _cache_stamp, _box
    with _lock:
        _cache = None
        _cache_stamp = None
        _box = None


def _load(session: Session) -> Settings:
    rows = session.exec(select(AppSetting)).all()
    secrets = secret_field_names()
    data: dict[str, object] = {}
    for row in rows:
        if row.key not in Settings.model_fields:
            continue  # obsolete key from an older version
        value = row.value
        if row.key in secrets and SecretBox.is_encrypted(value):
            try:
                value = _secret_box().decrypt(value)
            except SecretBoxError:
                logger.error(
                    "Cannot decrypt setting '%s' - SECRET_KEY changed? "
                    "Treating as unset.",
                    row.key,
                )
                value = ""
        data[row.key] = value
    return Settings.model_validate(data)


def _max_updated_at(session: Session) -> datetime | None:
    rows = session.exec(select(AppSetting.updated_at)).all()
    return max(rows) if rows else None


def _serialize(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    if hasattr(value, "value"):  # Enum
        return str(value.value)
    return str(value)
