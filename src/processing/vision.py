"""Vision analysis of keyframes via a Vision Language Model (LiteLLM).

Captures information neither spoken nor written: equipment, un-narrated
ingredients, pan sizes, techniques. Optional stage (VISION_ENABLED).
Works with any LiteLLM-supported VLM: gemini/*, gpt-4o, ollama/qwen2.5vl, etc.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path

import cv2
import litellm

from src.config import Settings
from src.processing.frames import extract_keyframes

logger = logging.getLogger(__name__)

_VISION_PROMPT = """\
You are analyzing keyframes from an Instagram cooking video.
List concrete, observable culinary facts ONLY. Focus on:
- Visible ingredients (including brands/packages and package sizes)
- Equipment used (pan type/size, appliances, utensils)
- Cooking techniques shown
- Any measurements visible (measuring cups, scale readouts)

Return one fact per line, no numbering, no commentary.
Do NOT guess amounts you cannot see. Skip decorative details."""


class VisionError(RuntimeError):
    """Raised when the vision stage fails entirely."""


def run_vision(video_path: Path, settings: Settings) -> list[str]:
    """Analyze keyframes with a VLM and return observed culinary facts."""
    if not settings.vision_enabled:
        logger.info("Vision stage disabled (VISION_ENABLED=false).")
        return []
    if not settings.vision_model:
        logger.warning("VISION_ENABLED=true but VISION_MODEL is not set - skipping.")
        return []

    keyframes = extract_keyframes(video_path, max_frames=settings.vision_max_frames)
    if not keyframes:
        return []

    content: list[dict] = [{"type": "text", "text": _VISION_PROMPT}]
    for kf in keyframes:
        ok, buf = cv2.imencode(".jpg", kf.image, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            continue
        b64 = base64.b64encode(buf.tobytes()).decode("ascii")
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
            }
        )

    kwargs: dict = {
        "model": settings.vision_model,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.1,
    }
    if settings.llm_api_key:
        kwargs["api_key"] = settings.llm_api_key
    if settings.llm_api_base:
        kwargs["api_base"] = settings.llm_api_base

    try:
        response = litellm.completion(**kwargs)
        text = response.choices[0].message.content or ""
    except Exception as exc:  # noqa: BLE001 - provider errors vary widely
        raise VisionError(f"Vision model call failed: {exc}") from exc

    facts = [line.strip("-• \t") for line in text.splitlines() if line.strip()]
    logger.info("Vision: extracted %d facts.", len(facts))
    return facts
