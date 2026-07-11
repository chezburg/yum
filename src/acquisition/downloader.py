"""Video + metadata acquisition via yt-dlp (cookie-authenticated).

Downloads the Reel/Post video file and captures raw metadata
(caption/description, author, title, hashtags) in one pass.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import yt_dlp

from src.acquisition.auth import write_netscape_cookies
from src.config import Settings

logger = logging.getLogger(__name__)

_HASHTAG_RE = re.compile(r"#(\w+)")


class DownloadError(RuntimeError):
    """Raised when video/metadata acquisition fails."""


@dataclass
class AcquiredContent:
    """Result of the acquisition stage."""

    video_path: Path
    caption: str
    title: str
    author: str
    hashtags: list[str] = field(default_factory=list)
    duration_seconds: float | None = None
    raw_info: dict = field(default_factory=dict)

    def metadata_dict(self) -> dict:
        """Compact metadata for DB storage (excludes bulky raw_info)."""
        return {
            "title": self.title,
            "caption": self.caption,
            "author": self.author,
            "hashtags": self.hashtags,
            "duration_seconds": self.duration_seconds,
        }


# Whitelist of raw_info keys kept for storage - avoids dumping megabytes of
# format lists / URLs into the database.
_KEPT_INFO_KEYS = (
    "id",
    "title",
    "description",
    "uploader",
    "uploader_id",
    "channel",
    "timestamp",
    "duration",
    "like_count",
    "comment_count",
    "view_count",
    "webpage_url",
)


def download_content(
    url: str,
    dest_dir: Path,
    settings: Settings,
) -> AcquiredContent:
    """Download the video and metadata for an Instagram post/reel.

    Args:
        url: Canonical Instagram URL.
        dest_dir: Directory to store the downloaded video.
        settings: App settings (provides cookie file path).

    Raises:
        DownloadError: On any acquisition failure (private post, rate limit, etc.).
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    output_template = str(dest_dir / "%(id)s.%(ext)s")

    ydl_opts: dict = {
        "outtmpl": output_template,
        "format": "mp4/bestvideo*+bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 3,
    }

    cookie_file = write_netscape_cookies(settings, dest_dir)
    if cookie_file is not None:
        ydl_opts["cookiefile"] = str(cookie_file)
    else:
        logger.warning(
            "No Instagram session stored - downloads may be rate-limited. "
            "Connect your account in Settings."
        )

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info is None:
                raise DownloadError(f"yt-dlp returned no info for {url}")
            video_path = Path(ydl.prepare_filename(info))
    except yt_dlp.utils.DownloadError as exc:
        raise DownloadError(f"Failed to download {url}: {exc}") from exc

    if not video_path.exists():
        # yt-dlp may remux to a different extension; find by stem.
        candidates = list(dest_dir.glob(f"{info.get('id', '*')}.*"))
        if not candidates:
            raise DownloadError(f"Downloaded file not found in {dest_dir}")
        video_path = candidates[0]

    caption = info.get("description") or ""
    return AcquiredContent(
        video_path=video_path,
        caption=caption,
        title=info.get("title") or "",
        author=info.get("uploader") or info.get("channel") or "",
        hashtags=_HASHTAG_RE.findall(caption),
        duration_seconds=info.get("duration"),
        raw_info={k: info.get(k) for k in _KEPT_INFO_KEYS if info.get(k) is not None},
    )
