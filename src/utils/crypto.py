"""Encryption-at-rest for secrets stored in the database.

Uses Fernet (AES-128-CBC + HMAC) with a key derived from the app-level
SECRET_KEY via PBKDF2. SECRET_KEY is the single bootstrap secret that must
live outside the database (environment variable).
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

# Static application salt: the derived key only needs to be unique per
# SECRET_KEY, not per value (Fernet handles per-message IVs internally).
_APP_SALT = b"yum-recipe-extractor-v1"
_PBKDF2_ITERATIONS = 600_000

# Prefix marking encrypted values in storage, so plain values (non-secrets)
# and encrypted values can share the same table safely.
ENCRYPTED_PREFIX = "enc:v1:"


class SecretBoxError(RuntimeError):
    """Raised when decryption fails (wrong SECRET_KEY or corrupted data)."""


class SecretBox:
    """Encrypts/decrypts secret strings for database storage."""

    def __init__(self, secret_key: str) -> None:
        if not secret_key or len(secret_key) < 16:
            raise SecretBoxError(
                "SECRET_KEY must be set and at least 16 characters long. "
                "Generate one with: openssl rand -hex 32"
            )
        derived = hashlib.pbkdf2_hmac(
            "sha256", secret_key.encode("utf-8"), _APP_SALT, _PBKDF2_ITERATIONS
        )
        self._fernet = Fernet(base64.urlsafe_b64encode(derived))

    def encrypt(self, plaintext: str) -> str:
        """Encrypt a string, returning a prefixed storage token."""
        token = self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")
        return f"{ENCRYPTED_PREFIX}{token}"

    def decrypt(self, stored: str) -> str:
        """Decrypt a prefixed storage token back to the plaintext string."""
        if not stored.startswith(ENCRYPTED_PREFIX):
            raise SecretBoxError("Value is not an encrypted token.")
        token = stored[len(ENCRYPTED_PREFIX):]
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise SecretBoxError(
                "Failed to decrypt secret - SECRET_KEY may have changed."
            ) from exc

    @staticmethod
    def is_encrypted(value: str | None) -> bool:
        return bool(value) and value.startswith(ENCRYPTED_PREFIX)
