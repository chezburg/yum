"""Audio extraction (FFmpeg) and speech-to-text (configurable local/API).

Engines:
    - local: faster-whisper (requires requirements-local.txt)
    - openai: OpenAI Whisper API
    - groq: Groq Whisper API (fast + cheap)
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from src.config import Settings, WhisperEngine

logger = logging.getLogger(__name__)


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
    """Transcribe audio using the configured engine."""
    engine = settings.whisper_engine
    logger.info("Transcribing %s with engine=%s", audio_path.name, engine.value)

    if engine == WhisperEngine.LOCAL:
        return _transcribe_local(audio_path, settings)
    if engine == WhisperEngine.OPENAI:
        return _transcribe_openai(audio_path, settings)
    if engine == WhisperEngine.GROQ:
        return _transcribe_groq(audio_path, settings)
    raise TranscriptionError(f"Unknown whisper engine: {engine}")


def _transcribe_local(audio_path: Path, settings: Settings) -> list[TranscriptSegment]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise TranscriptionError(
            "faster-whisper is not installed. Install requirements-local.txt "
            "or set WHISPER_ENGINE=openai/groq."
        ) from exc

    model = WhisperModel(
        settings.whisper_model_size,
        device=settings.whisper_device,
        compute_type=settings.whisper_compute_type,
    )
    segments, _info = model.transcribe(str(audio_path), vad_filter=True)
    return [
        TranscriptSegment(start=s.start, end=s.end, text=s.text.strip())
        for s in segments
        if s.text.strip()
    ]


def _transcribe_openai(audio_path: Path, settings: Settings) -> list[TranscriptSegment]:
    if not settings.openai_api_key:
        raise TranscriptionError("OPENAI_API_KEY is required for WHISPER_ENGINE=openai.")
    from openai import OpenAI

    client = OpenAI(api_key=settings.openai_api_key)
    with audio_path.open("rb") as fh:
        response = client.audio.transcriptions.create(
            model="whisper-1",
            file=fh,
            response_format="verbose_json",
            timestamp_granularities=["segment"],
        )
    return _segments_from_verbose_json(response)


def _transcribe_groq(audio_path: Path, settings: Settings) -> list[TranscriptSegment]:
    if not settings.groq_api_key:
        raise TranscriptionError("GROQ_API_KEY is required for WHISPER_ENGINE=groq.")
    from groq import Groq

    client = Groq(api_key=settings.groq_api_key)
    with audio_path.open("rb") as fh:
        response = client.audio.transcriptions.create(
            model="whisper-large-v3",
            file=(audio_path.name, fh),
            response_format="verbose_json",
        )
    return _segments_from_verbose_json(response)


def _segments_from_verbose_json(response: object) -> list[TranscriptSegment]:
    """Normalize verbose_json responses from OpenAI/Groq into segments."""
    segments = getattr(response, "segments", None) or []
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
        text = (getattr(response, "text", "") or "").strip()
        if text:
            result.append(TranscriptSegment(start=0.0, end=0.0, text=text))
    return result


def segments_to_dicts(segments: list[TranscriptSegment]) -> list[dict]:
    """Serialize segments for JSON storage."""
    return [asdict(s) for s in segments]
