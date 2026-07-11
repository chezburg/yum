"""Keyframe extraction using scene-change detection (OpenCV).

Shared by the OCR and Vision stages. Instead of OCR-ing every frame,
we sample frames and keep only those that differ meaningfully from the
previously kept frame - text overlays and scene changes both trigger this.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# Sample this many frames per second of video for change detection.
SAMPLE_FPS = 2.0
# Mean absolute grayscale difference (0-255) above which a frame is "new".
DEFAULT_CHANGE_THRESHOLD = 12.0


class FrameExtractionError(RuntimeError):
    """Raised when the video cannot be read for frame extraction."""


@dataclass
class Keyframe:
    """A visually distinct frame with its timestamp."""

    timestamp: float
    image: np.ndarray  # BGR frame


def extract_keyframes(
    video_path: Path,
    max_frames: int = 40,
    change_threshold: float = DEFAULT_CHANGE_THRESHOLD,
) -> list[Keyframe]:
    """Extract visually distinct keyframes from a video.

    Args:
        video_path: Path to the video file.
        max_frames: Hard cap on the number of returned keyframes.
        change_threshold: Sensitivity of scene-change detection (lower = more frames).

    Raises:
        FrameExtractionError: If the video cannot be opened.
    """
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise FrameExtractionError(f"Could not open video: {video_path}")

    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        frame_interval = max(1, int(round(fps / SAMPLE_FPS)))

        keyframes: list[Keyframe] = []
        prev_gray: np.ndarray | None = None
        frame_idx = 0

        while len(keyframes) < max_frames:
            ok, frame = cap.read()
            if not ok:
                break
            if frame_idx % frame_interval == 0:
                small = cv2.resize(frame, (160, 90))
                gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
                if prev_gray is None or _mean_abs_diff(gray, prev_gray) >= change_threshold:
                    keyframes.append(
                        Keyframe(timestamp=frame_idx / fps, image=frame)
                    )
                    prev_gray = gray
            frame_idx += 1

        logger.info(
            "Extracted %d keyframes from %s", len(keyframes), video_path.name
        )
        return keyframes
    finally:
        cap.release()


def _mean_abs_diff(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.mean(cv2.absdiff(a, b)))


def save_keyframes(keyframes: list[Keyframe], dest_dir: Path) -> list[Path]:
    """Save keyframes as JPEGs (used by vision stage which needs file inputs)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for kf in keyframes:
        path = dest_dir / f"frame_{kf.timestamp:08.2f}.jpg"
        cv2.imwrite(str(path), kf.image, [cv2.IMWRITE_JPEG_QUALITY, 90])
        paths.append(path)
    return paths
