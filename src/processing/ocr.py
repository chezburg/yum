"""OCR extraction of on-screen text overlays (configurable engine).

Engines:
    - paddleocr: PaddleOCR (preferred, requires requirements-local.txt)
    - tesseract: pytesseract (fallback, bundled in Docker image)
    - none: skip OCR stage
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from pathlib import Path

from src.config import OCREngine, Settings
from src.processing.frames import Keyframe, extract_keyframes

logger = logging.getLogger(__name__)

MIN_TEXT_LENGTH = 3
MIN_CONFIDENCE = 0.5


class OCRError(RuntimeError):
    """Raised when the OCR stage fails entirely."""


@dataclass
class OCRDetection:
    """Deduplicated text found on screen with its first-seen timestamp."""

    timestamp: float
    text: str
    confidence: float


def run_ocr(video_path: Path, settings: Settings) -> list[OCRDetection]:
    """Extract deduplicated on-screen text from a video."""
    if settings.ocr_engine == OCREngine.NONE:
        logger.info("OCR disabled (OCR_ENGINE=none).")
        return []

    keyframes = extract_keyframes(video_path, max_frames=settings.ocr_max_frames)
    if not keyframes:
        return []

    if settings.ocr_engine == OCREngine.PADDLEOCR:
        detections = _ocr_paddle(keyframes, settings)
    elif settings.ocr_engine == OCREngine.TESSERACT:
        detections = _ocr_tesseract(keyframes)
    else:
        raise OCRError(f"Unknown OCR engine: {settings.ocr_engine}")

    return _deduplicate(detections)


def _ocr_paddle(keyframes: list[Keyframe], settings: Settings) -> list[OCRDetection]:
    try:
        from paddleocr import PaddleOCR
    except ImportError as exc:
        raise OCRError(
            "paddleocr is not installed. Install requirements-local.txt "
            "or set OCR_ENGINE=tesseract."
        ) from exc

    ocr = PaddleOCR(use_angle_cls=True, lang=settings.ocr_language, show_log=False)
    detections: list[OCRDetection] = []
    for kf in keyframes:
        try:
            results = ocr.ocr(kf.image, cls=True)
        except Exception:  # noqa: BLE001 - per-frame failures are non-fatal
            logger.warning("PaddleOCR failed on frame @%.2fs", kf.timestamp)
            continue
        for page in results or []:
            for line in page or []:
                text, conf = line[1][0].strip(), float(line[1][1])
                if len(text) >= MIN_TEXT_LENGTH and conf >= MIN_CONFIDENCE:
                    detections.append(
                        OCRDetection(timestamp=kf.timestamp, text=text, confidence=conf)
                    )
    return detections


def _ocr_tesseract(keyframes: list[Keyframe]) -> list[OCRDetection]:
    try:
        import cv2
        import pytesseract
    except ImportError as exc:
        raise OCRError("pytesseract is not installed.") from exc

    detections: list[OCRDetection] = []
    for kf in keyframes:
        gray = cv2.cvtColor(kf.image, cv2.COLOR_BGR2GRAY)
        try:
            data = pytesseract.image_to_data(
                gray, output_type=pytesseract.Output.DICT
            )
        except Exception:  # noqa: BLE001
            logger.warning("Tesseract failed on frame @%.2fs", kf.timestamp)
            continue

        # Group words into lines using tesseract's block/line numbers.
        lines: dict[tuple, list[tuple[str, float]]] = {}
        for i, word in enumerate(data["text"]):
            word = word.strip()
            conf = float(data["conf"][i])
            if word and conf > 0:
                key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
                lines.setdefault(key, []).append((word, conf / 100.0))

        for words in lines.values():
            text = " ".join(w for w, _ in words)
            avg_conf = sum(c for _, c in words) / len(words)
            if len(text) >= MIN_TEXT_LENGTH and avg_conf >= MIN_CONFIDENCE:
                detections.append(
                    OCRDetection(timestamp=kf.timestamp, text=text, confidence=avg_conf)
                )
    return detections


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def _deduplicate(detections: list[OCRDetection]) -> list[OCRDetection]:
    """Keep the first (highest-context) occurrence of each unique text."""
    seen: dict[str, OCRDetection] = {}
    for det in sorted(detections, key=lambda d: d.timestamp):
        key = _normalize(det.text)
        if key not in seen or det.confidence > seen[key].confidence:
            existing = seen.get(key)
            seen[key] = OCRDetection(
                timestamp=existing.timestamp if existing else det.timestamp,
                text=det.text,
                confidence=det.confidence,
            )
    result = sorted(seen.values(), key=lambda d: d.timestamp)
    logger.info("OCR: %d unique texts after deduplication.", len(result))
    return result


def detections_to_dicts(detections: list[OCRDetection]) -> list[dict]:
    """Serialize detections for JSON storage."""
    return [asdict(d) for d in detections]
