"""Vision analysis of keyframes via a Vision Language Model (LiteLLM).

Captures information neither spoken nor written by narration: equipment,
un-narrated ingredients, pan sizes, techniques. It also reads on-screen text
overlays, which is why the local OCR stage is skipped when Vision is enabled.
Optional stage (vision_enabled). Works with any LiteLLM-supported VLM:
gemini/*, gpt-4o, ollama/qwen2.5vl, etc.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from pathlib import Path

import cv2
import litellm

from src.config import Settings
from src.processing.frames import extract_keyframes

logger = logging.getLogger(__name__)

_VISION_PROMPT = """\
You are analyzing keyframes from an Instagram cooking video.
Report concrete, observable information ONLY, one item per line.

Prefix each line with exactly one tag:
- "FACT:" for observed culinary facts:
    - Visible ingredients (including brands/packages and package sizes)
    - Equipment used (pan type/size, appliances, utensils)
    - Cooking techniques shown
    - Any measurements visible (measuring cups, scale readouts)
- "TEXT:" for on-screen text overlays / captions you can read verbatim
    (recipe steps, ingredient lists, titles burned into the video).

Do NOT guess amounts or text you cannot clearly see. Skip decorative details.
No numbering, no commentary outside the tagged lines."""


class VisionError(RuntimeError):
    """Raised when the vision stage fails entirely."""


@dataclass
class VisionResult:
    """Observations from the vision stage."""

    facts: list[str]
    onscreen_text: list[str]


def run_vision(video_path: Path, settings: Settings) -> VisionResult:
    """Analyze keyframes with a VLM; return culinary facts and on-screen text."""
    if not settings.vision_enabled:
        logger.info("Vision stage disabled (vision_enabled=false).")
        return VisionResult(facts=[], onscreen_text=[])
    if not settings.vision_model:
        logger.warning("vision_enabled=true but vision_model is not set - skipping.")
        return VisionResult(facts=[], onscreen_text=[])

    keyframes = extract_keyframes(video_path, max_frames=settings.vision_max_frames)
    if not keyframes:
        return VisionResult(facts=[], onscreen_text=[])

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
    if settings.vision_api_key:
        kwargs["api_key"] = settings.vision_api_key
    if settings.vision_api_base:
        kwargs["api_base"] = settings.vision_api_base

    try:
        response = litellm.completion(**kwargs)
        text = response.choices[0].message.content or ""
    except Exception as exc:  # noqa: BLE001 - provider errors vary widely
        raise VisionError(f"Vision model call failed: {exc}") from exc

    return _parse_vision_output(text)


def _parse_vision_output(text: str) -> VisionResult:
    """Split tagged VLM output into facts and on-screen text lines."""
    facts: list[str] = []
    onscreen_text: list[str] = []
    for raw in text.splitlines():
        line = raw.strip("-• \t")
        if not line:
            continue
        lower = line.lower()
        if lower.startswith("text:"):
            value = line[5:].strip()
            if value:
                onscreen_text.append(value)
        elif lower.startswith("fact:"):
            value = line[5:].strip()
            if value:
                facts.append(value)
        else:
            # Untagged line: treat as a fact (backwards-compatible).
            facts.append(line)
    logger.info(
        "Vision: %d facts, %d on-screen text lines.", len(facts), len(onscreen_text)
    )
    return VisionResult(facts=facts, onscreen_text=onscreen_text)
