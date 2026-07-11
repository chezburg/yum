"""Audio extraction (FFmpeg) and speech-to-text via an OpenAI-compatible API.

Transcription always goes through the standard `POST {base}/audio/transcriptions`
REST endpoint, so any compatible server works: OpenAI, Groq, or a self-hosted
Whisper server (speaches, faster-whisper-server, whisper.cpp server, ...).
Configure via STT engine settings: mode (local/cloud), API base URL, API key,
and model name.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

from src.config import Settings

logger = logging.getLogger(__name__)

STT_TIMEOUT_SECONDS = 600.0


class TranscriptionError(RuntimeError):
    """Raised when audio extraction or transcription fails."""


@dataclass
class TranscriptSegment:
    """A timestamped chunk of transcribed speech."""

    start: float
    end: float
    text: str


def extract_audio(video_path: Path, dest_dir: Path) -> Path:
    """Extract mono 16 kHz WAV audio from a video using FFmpeg.

    16 kHz mono is the optimal input format for Whisper models.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    audio_path = dest_dir / f"{video_path.stem}.wav"

    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(video_path),
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        "-f", "wav",
        str(audio_path),
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300, check=False
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        raise TranscriptionError(f"FFmpeg audio extraction failed: {exc}") from exc

    if result.returncode != 0 or not audio_path.exists():
        raise TranscriptionError(
            f"FFmpeg exited with code {result.returncode}: {result.stderr[-500:]}"
        )
    return audio_path


def transcribe(audio_path: Path, settings: Settings) -> list[TranscriptSegment]:
    """Transcribe audio via the configured OpenAI-compatible endpoint."""
    if not settings.stt_api_base:
        raise TranscriptionError(
            "STT API base URL is not configured. Set it in Settings "
            "(e.g. http://localhost:8000/v1 for a local Whisper server)."
        )
    logger.info(
        "Transcribing %s via %s (mode=%s, model=%s)",
        audio_path.name,
        settings.stt_api_base,
        settings.stt_engine_mode.value,
        settings.stt_model or "(server default)",
    )

    url = f"{settings.stt_api_base.rstrip('/')}/audio/transcriptions"
    headers: dict[str, str] = {}
    if settings.stt_api_key:
        headers["Authorization"] = f"Bearer {settings.stt_api_key}"

    data: dict[str, str] = {"response_format": "verbose_json"}
    if settings.stt_model:
        data["model"] = settings.stt_model

    try:
        with audio_path.open("rb") as fh:
            response = httpx.post(
                url,
                headers=headers,
                data=data,
                files={"file": (audio_path.name, fh, "audio/wav")},
                timeout=STT_TIMEOUT_SECONDS,
            )
    except httpx.HTTPError as exc:
        raise TranscriptionError(
            f"STT request to {url} failed: {exc}"
        ) from exc

    if response.status_code >= 400:
        raise TranscriptionError(
            f"STT endpoint returned HTTP {response.status_code}: "
            f"{response.text[:500]}"
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise TranscriptionError(
            f"STT endpoint returned non-JSON response: {response.text[:200]}"
        ) from exc

    return _segments_from_verbose_json(payload)


def _segments_from_verbose_json(response: object) -> list[TranscriptSegment]:
    """Normalize a verbose_json transcription response into segments.

    Accepts either a dict (parsed JSON) or an object with attributes,
    covering the response shapes of all OpenAI-compatible servers.
    """
    if isinstance(response, dict):
        segments = response.get("segments") or []
        flat_text = response.get("text", "") or ""
    else:
        segments = getattr(response, "segments", None) or []
        flat_text = getattr(response, "text", "") or ""

    result: list[TranscriptSegment] = []
    for seg in segments:
        get = seg.get if isinstance(seg, dict) else lambda k, s=seg: getattr(s, k, None)
        text = (get("text") or "").strip()
        if text:
            result.append(
                TranscriptSegment(
                    start=float(get("start") or 0.0),
                    end=float(get("end") or 0.0),
                    text=text,
                )
            )
    if not result:
        # Fall back to the flat text if segment data is missing.
        text = flat_text.strip()
        if text:
            result.append(TranscriptSegment(start=0.0, end=0.0, text=text))
    return result


def segments_to_dicts(segments: list[TranscriptSegment]) -> list[dict]:
    """Serialize segments for JSON storage."""
    return [asdict(s) for s in segments]
