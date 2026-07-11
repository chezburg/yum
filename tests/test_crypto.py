"""Tests for the SecretBox encryption utility."""

import pytest

from src.utils.crypto import SecretBox, SecretBoxError


class TestSecretBox:
    def test_roundtrip(self):
        box = SecretBox("a-sufficiently-long-secret-key")
        stored = box.encrypt("my-api-key-12345")
        assert stored.startswith("enc:v1:")
        assert "my-api-key-12345" not in stored
        assert box.decrypt(stored) == "my-api-key-12345"

    def test_unicode_roundtrip(self):
        box = SecretBox("a-sufficiently-long-secret-key")
        assert box.decrypt(box.encrypt("pässwörd-日本語")) == "pässwörd-日本語"

    def test_wrong_key_fails(self):
        box_a = SecretBox("first-secret-key-abcdefgh")
        box_b = SecretBox("second-secret-key-abcdefg")
        stored = box_a.encrypt("secret")
        with pytest.raises(SecretBoxError):
            box_b.decrypt(stored)

    def test_short_key_rejected(self):
        with pytest.raises(SecretBoxError):
            SecretBox("short")

    def test_empty_key_rejected(self):
        with pytest.raises(SecretBoxError):
            SecretBox("")

    def test_decrypt_non_token_fails(self):
        box = SecretBox("a-sufficiently-long-secret-key")
        with pytest.raises(SecretBoxError):
            box.decrypt("plain-value")

    def test_is_encrypted(self):
        box = SecretBox("a-sufficiently-long-secret-key")
        assert SecretBox.is_encrypted(box.encrypt("x"))
        assert not SecretBox.is_encrypted("plain")
        assert not SecretBox.is_encrypted("")
        assert not SecretBox.is_encrypted(None)
