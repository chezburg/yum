"""Comment acquisition via Instaloader (cookie-authenticated).

Extracts the evidence that most often contains exact quantities:
pinned comments, creator replies, and top comments.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from http.cookiejar import MozillaCookieJar
from pathlib import Path

import instaloader

from src.config import Settings

logger = logging.getLogger(__name__)

MAX_TOP_COMMENTS = 15


class CommentFetchError(RuntimeError):
    """Raised when comment scraping fails (non-fatal for the pipeline)."""


@dataclass
class Comment:
    """A single comment with source classification."""

    text: str
    author: str
    likes: int
    is_creator: bool
    is_reply: bool


def _build_loader(settings: Settings) -> instaloader.Instaloader:
    """Create an Instaloader instance authenticated from a Netscape cookie file."""
    loader = instaloader.Instaloader(
        download_pictures=False,
        download_videos=False,
        download_video_thumbnails=False,
        download_comments=False,
        save_metadata=False,
        quiet=True,
    )

    cookie_file = settings.instagram_cookie_file
    if cookie_file and Path(cookie_file).is_file():
        jar = MozillaCookieJar(str(cookie_file))
        jar.load(ignore_discard=True, ignore_expires=True)
        ig_cookies = {
            c.name: c.value
            for c in jar
            if c.domain.endswith("instagram.com") and c.value is not None
        }
        if "sessionid" in ig_cookies:
            loader.context._session.cookies.update(ig_cookies)
            username = settings.instagram_username
            if username:
                loader.context.username = username
            logger.info("Loaded Instagram session from cookie file.")
        else:
            logger.warning("Cookie file has no 'sessionid' - comments may be unavailable.")
    else:
        logger.warning("No Instagram cookie file configured - fetching comments anonymously.")

    return loader


def fetch_comments(shortcode: str, settings: Settings) -> list[Comment]:
    """Fetch pinned/creator/top comments for a post.

    Comment scraping is best-effort: failures are raised as CommentFetchError
    so the pipeline can continue without comments rather than abort.
    """
    try:
        loader = _build_loader(settings)
        post = instaloader.Post.from_shortcode(loader.context, shortcode)
        creator = post.owner_username

        comments: list[Comment] = []
        for i, c in enumerate(post.get_comments()):
            if i >= MAX_TOP_COMMENTS:
                break
            comments.append(
                Comment(
                    text=c.text or "",
                    author=c.owner.username if c.owner else "",
                    likes=getattr(c, "likes_count", 0) or 0,
                    is_creator=(c.owner.username == creator) if c.owner else False,
                    is_reply=False,
                )
            )
            # Replies (answers) - creator replies are high-priority evidence.
            for answer in getattr(c, "answers", []) or []:
                author = answer.owner.username if answer.owner else ""
                comments.append(
                    Comment(
                        text=answer.text or "",
                        author=author,
                        likes=getattr(answer, "likes_count", 0) or 0,
                        is_creator=author == creator,
                        is_reply=True,
                    )
                )

        # Highest-value evidence first: creator comments, then most-liked.
        comments.sort(key=lambda c: (not c.is_creator, -c.likes))
        return comments
    except Exception as exc:  # noqa: BLE001 - instaloader raises many exception types
        raise CommentFetchError(f"Failed to fetch comments for {shortcode}: {exc}") from exc


def comments_to_dicts(comments: list[Comment]) -> list[dict]:
    """Serialize comments for JSON storage."""
    return [asdict(c) for c in comments]
