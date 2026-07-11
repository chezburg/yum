"""Evidence collation: organizes all extracted sources into a prioritized
text bundle for the LLM reconstruction stage.

Priority order (highest first):
    1. creator_reply / creator_comment
    2. caption
    3. ocr
    4. transcript
    5. vision
    6. top comments (community - lowest trust)
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class EvidenceBundle:
    """All textual evidence for one post, ready for prompt construction."""

    caption: str = ""
    title: str = ""
    author: str = ""
    hashtags: list[str] = field(default_factory=list)
    transcript_segments: list[dict] = field(default_factory=list)
    ocr_detections: list[dict] = field(default_factory=list)
    vision_facts: list[str] = field(default_factory=list)
    comments: list[dict] = field(default_factory=list)

    @property
    def creator_comments(self) -> list[dict]:
        return [c for c in self.comments if c.get("is_creator")]

    @property
    def community_comments(self) -> list[dict]:
        return [c for c in self.comments if not c.get("is_creator")]

    def has_any_evidence(self) -> bool:
        return bool(
            self.caption.strip()
            or self.transcript_segments
            or self.ocr_detections
            or self.vision_facts
            or self.comments
        )


def format_transcript(segments: list[dict]) -> str:
    """Render transcript segments as timestamped lines."""
    lines = []
    for seg in segments:
        start = float(seg.get("start", 0.0))
        text = str(seg.get("text", "")).strip()
        if text:
            lines.append(f"[{_fmt_ts(start)}] {text}")
    return "\n".join(lines)


def format_ocr(detections: list[dict]) -> str:
    """Render OCR detections as timestamped lines."""
    lines = []
    for det in detections:
        ts = float(det.get("timestamp", 0.0))
        text = str(det.get("text", "")).strip()
        if text:
            lines.append(f"[{_fmt_ts(ts)}] {text}")
    return "\n".join(lines)


def format_comments(comments: list[dict], limit: int = 20) -> tuple[str, str]:
    """Render (creator_comments_block, community_comments_block)."""
    creator_lines: list[str] = []
    community_lines: list[str] = []
    for c in comments[:limit * 2]:
        text = str(c.get("text", "")).strip()
        if not text:
            continue
        prefix = "reply" if c.get("is_reply") else "comment"
        line = f"- ({prefix}, {c.get('likes', 0)} likes) {text}"
        if c.get("is_creator"):
            creator_lines.append(line)
        elif len(community_lines) < limit:
            community_lines.append(line)
    return "\n".join(creator_lines), "\n".join(community_lines)


def _fmt_ts(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"
