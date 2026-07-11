"""Instagram URL extraction & sanitization.

Handles raw "share sheet" text from mobile apps, e.g.:

    "Check out this recipe! https://www.instagram.com/reel/C-xyz123/?igsh=abc"

We extract the first valid Instagram post/reel URL and strip tracking params.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Shortcodes are alphanumeric plus '-' and '_'
_INSTAGRAM_URL_RE = re.compile(
    r"https?://(?:www\.)?instagram\.com/"
    r"(?:[A-Za-z0-9._]+/)?"  # optional username segment (share links)
    r"(?P<kind>reel|reels|p|tv)/"
    r"(?P<shortcode>[A-Za-z0-9_-]+)",
    re.IGNORECASE,
)


class URLParseError(ValueError):
    """Raised when no valid Instagram URL can be found in the input text."""


@dataclass(frozen=True)
class ParsedInstagramURL:
    """A cleaned, canonical Instagram content URL."""

    shortcode: str
    kind: str  # "reel" | "p" | "tv"
    canonical_url: str


def extract_instagram_url(text: str) -> ParsedInstagramURL:
    """Extract and canonicalize the first Instagram URL found in arbitrary text.

    Args:
        text: Raw text possibly containing an Instagram URL (share-sheet text).

    Returns:
        ParsedInstagramURL with tracking parameters stripped.

    Raises:
        URLParseError: If no Instagram post/reel URL is present.
    """
    if not text or not isinstance(text, str):
        raise URLParseError("Input text is empty.")

    # Defensive cap: share-sheet text should never be huge.
    match = _INSTAGRAM_URL_RE.search(text[:10_000])
    if not match:
        raise URLParseError("No Instagram post/reel URL found in input.")

    kind = match.group("kind").lower()
    if kind == "reels":
        kind = "reel"
    shortcode = match.group("shortcode")

    canonical = f"https://www.instagram.com/{kind}/{shortcode}/"
    return ParsedInstagramURL(shortcode=shortcode, kind=kind, canonical_url=canonical)
