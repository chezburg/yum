"""Connection tests for the configurable AI engines (STT, LLM, Vision).

Each test uses currently-saved settings and returns a (ok, message) tuple
suitable for surfacing directly in the settings UI. Tests are best-effort:
network/auth problems are reported, never raised.
"""

from __future__ import annotations

import logging

import httpx
import litellm

from src.config import Settings

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT = 15.0

# 1x1 transparent PNG, used for a minimal vision round-trip.
_TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)

TestResult = tuple[bool, str]


def test_engine(engine: str, settings: Settings) -> TestResult:
    """Dispatch a connection test for 'stt', 'llm', or 'vision'."""
    if engine == "stt":
        return test_stt(settings)
    if engine == "llm":
        return test_llm(settings)
    if engine == "vision":
        return test_vision(settings)
    return False, f"Unknown engine: {engine}"


def test_stt(settings: Settings) -> TestResult:
    """Check reachability of the OpenAI-compatible STT endpoint via GET /models."""
    if not settings.stt_api_base:
        return False, "No API base URL configured."

    url = f"{settings.stt_api_base.rstrip('/')}/models"
    headers: dict[str, str] = {}
    if settings.stt_api_key:
        headers["Authorization"] = f"Bearer {settings.stt_api_key}"

    try:
        response = httpx.get(url, headers=headers, timeout=_HTTP_TIMEOUT)
    except httpx.HTTPError as exc:
        return False, f"Unreachable: {exc}"

    code = response.status_code
    if code in (401, 403):
        return False, f"Reachable, but authentication failed (HTTP {code})."
    if code < 500:
        return True, f"Endpoint reachable (HTTP {code})."
    return False, f"Server error (HTTP {code})."


def test_llm(settings: Settings) -> TestResult:
    """Send a minimal completion to confirm the LLM engine works end-to-end."""
    if not settings.llm_model:
        return False, "No model configured."
    return _ping_completion(
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        api_base=settings.llm_api_base,
        content="Reply with the single word: OK",
    )


def test_vision(settings: Settings) -> TestResult:
    """Send a minimal image completion to confirm the Vision engine works."""
    if not settings.vision_model:
        return False, "No model configured."
    content = [
        {"type": "text", "text": "Reply with the single word: OK"},
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{_TINY_PNG_B64}"},
        },
    ]
    return _ping_completion(
        model=settings.vision_model,
        api_key=settings.vision_api_key,
        api_base=settings.vision_api_base,
        content=content,
    )


def _ping_completion(
    *, model: str, api_key: str, api_base: str, content: object
) -> TestResult:
    kwargs: dict = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.0,
        "max_tokens": 5,
    }
    if api_key:
        kwargs["api_key"] = api_key
    if api_base:
        kwargs["api_base"] = api_base

    try:
        response = litellm.completion(**kwargs)
    except Exception as exc:  # noqa: BLE001 - provider errors vary widely
        return False, f"Call failed: {exc}"

    try:
        reply = (response.choices[0].message.content or "").strip()
    except (AttributeError, IndexError):
        reply = ""
    return True, f"Success. Model replied: {reply[:80] or '(empty)'}"
