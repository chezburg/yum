"""Compatibility patch for instaloader's broken Post-metadata fetching.

Background
----------
Around June 2026, Instagram deprecated the GraphQL ``doc_id``
(``8845758582119845``, the ``xdt_shortcode_media`` query) that
``instaloader`` (as of the installed 4.15.2, and every release up to it)
uses in ``Post._obtain_metadata()`` to resolve a shortcode into a full post
node. Instagram now returns a *successful* response whose
``data.xdt_shortcode_media`` field is ``null`` instead of an HTTP error, so
instaloader raises::

    instaloader.exceptions.BadResponseException: Fetching Post metadata failed.

This is not caused by anything in this codebase (no rate limiting, no
missing/expired session) - it reproduces even against upstream instaloader's
own CLI and is tracked at
https://github.com/instaloader/instaloader/issues/2704. A fix exists at
https://github.com/instaloader/instaloader/pull/2706 (migrating to the new
``PolarisPostRootQuery`` doc_id ``27128499623469141`` and adding the
``X-CSRFToken`` header the new endpoint requires) but is unmerged and not
yet part of any PyPI release as of this writing.

This module monkeypatches the two affected methods in-process using the
logic from that PR, so comment/post-metadata fetching keeps working without
depending on an unmerged fork. Remove this module (and its call site in
``src/acquisition/auth.py``) once a released ``instaloader`` version ships
the fix - check by bumping the dependency and running
``tests/test_instagram_auth.py`` / a manual fetch against a real shortcode.
"""

from __future__ import annotations

import json
import logging
import urllib.parse
from typing import Any, Dict, Optional

from instaloader.exceptions import BadResponseException, PostChangedException
from instaloader.instaloadercontext import InstaloaderContext, copy_session
from instaloader.structures import Post

logger = logging.getLogger(__name__)

# New working doc_ids that replace the deprecated ones (see module docstring).
_POST_METADATA_DOC_ID = "27128499623469141"  # PolarisPostRootQuery
_CLIPS_CONNECTION_DOC_ID = "27234427476213202"  # play_count fallback for reels

_MEDIA_TYPES = {1: "GraphImage", 2: "GraphVideo", 8: "GraphSidecar"}

_patched = False


def apply_metadata_patch() -> None:
    """Monkeypatch instaloader's Post metadata fetching (idempotent)."""
    global _patched
    if _patched:
        return

    InstaloaderContext.doc_id_graphql_query = _patched_doc_id_graphql_query  # type: ignore[method-assign]
    Post._obtain_metadata = _patched_obtain_metadata  # type: ignore[method-assign]
    Post._fetch_play_count_from_clips = staticmethod(_fetch_play_count_from_clips)  # type: ignore[attr-defined]

    _patched = True
    logger.info(
        "Applied instaloader post-metadata compatibility patch (Instagram "
        "deprecated the old doc_id upstream - see "
        "https://github.com/instaloader/instaloader/issues/2704)."
    )


def _patched_doc_id_graphql_query(
    self: InstaloaderContext,
    doc_id: str,
    variables: Dict[str, Any],
    referer: Optional[str] = None,
) -> Dict[str, Any]:
    """Same as upstream, plus the X-CSRFToken header the new endpoints require.

    Ported from https://github.com/instaloader/instaloader/pull/2706.
    """
    csrf = next(
        (c.value for c in self._session.cookies if c.name == "csrftoken" and c.value),
        None,
    )
    if not csrf:
        # Anonymous session with no csrftoken yet - fetch one.
        self._session.get("https://www.instagram.com/", timeout=self.request_timeout)
        csrf = next(
            (c.value for c in self._session.cookies if c.name == "csrftoken" and c.value),
            "",
        )

    with copy_session(self._session, self.request_timeout) as tmpsession:
        tmpsession.headers.update(self._default_http_header(empty_session_only=True))
        del tmpsession.headers["Connection"]
        del tmpsession.headers["Content-Length"]
        tmpsession.headers["authority"] = "www.instagram.com"
        tmpsession.headers["scheme"] = "https"
        tmpsession.headers["accept"] = "*/*"
        tmpsession.headers["x-csrftoken"] = csrf
        if referer is not None:
            tmpsession.headers["referer"] = urllib.parse.quote(referer)

        variables_json = json.dumps(variables, separators=(",", ":"))

        resp_json = self.get_json(
            "graphql/query",
            params={
                "variables": variables_json,
                "doc_id": doc_id,
                "server_timestamps": "true",
            },
            session=tmpsession,
            use_post=True,
        )
    if "status" not in resp_json:
        self.error('GraphQL response did not contain a "status" field.')
    return resp_json


def _fetch_play_count_from_clips(
    context: InstaloaderContext, user_id: Any, shortcode: str
) -> Optional[int]:
    """Fallback fetch for reel play_count, which the new post endpoint omits
    for non-owner accounts. Best-effort; failures are swallowed since this
    is a non-essential enrichment (not required for comment fetching).
    """
    try:
        resp = context.doc_id_graphql_query(
            _CLIPS_CONNECTION_DOC_ID,
            {
                "data": {
                    "include_feed_video": True,
                    "page_size": 12,
                    "target_user_id": str(user_id),
                }
            },
        )
        connection = (resp.get("data") or {}).get(
            "xdt_api__v1__clips__user__connection_v2"
        ) or {}
        for edge in connection.get("edges") or []:
            media = (edge.get("node") or {}).get("media") or {}
            if media.get("code") == shortcode:
                return media.get("play_count")
    except Exception:  # noqa: BLE001 - best-effort enrichment only
        logger.debug("Play-count fallback fetch failed for %s", shortcode, exc_info=True)
    return None


def _patched_obtain_metadata(self: Post) -> None:
    """Replacement for Post._obtain_metadata() using the new working
    PolarisPostRootQuery endpoint, remapping its v1/iPhone-API response
    shape back to the legacy GraphQL field names the rest of instaloader
    (and this codebase) expects.

    Ported from https://github.com/instaloader/instaloader/pull/2706.
    """
    if self._full_metadata_dict:
        return

    resp = self._context.doc_id_graphql_query(
        _POST_METADATA_DOC_ID,
        {
            "shortcode": self.shortcode,
            "__relay_internal__pv__PolarisAIGMMediaWebLabelEnabledrelayprovider": False,
        },
    )
    web_info = (resp.get("data") or {}).get(
        "xdt_api__v1__media__shortcode__web_info"
    ) or {}
    items = web_info.get("items")
    if not items:
        raise BadResponseException("Fetching Post metadata failed.")
    media = items[0]

    media_type = media.get("media_type")
    typename = _MEDIA_TYPES.get(media_type)
    if not typename:
        raise BadResponseException(f"Unknown media_type in metadata: {media_type}.")

    pic_json: Dict[str, Any] = {
        "shortcode": media["code"],
        "id": media["pk"],
        "__typename": typename,
        "is_video": media_type == 2,
        "taken_at_timestamp": media["taken_at"],
        "owner": {
            "id": media["user"]["pk"],
            "username": media["user"].get("username", ""),
            "full_name": media["user"].get("full_name", ""),
        },
    }

    candidates = (media.get("image_versions2") or {}).get("candidates") or []
    if candidates:
        pic_json["display_url"] = candidates[0]["url"]

    video_versions = media.get("video_versions") or []
    if video_versions:
        pic_json["video_url"] = video_versions[0]["url"]
    if media.get("video_duration") is not None:
        pic_json["video_duration"] = media["video_duration"]
    if media.get("view_count") is not None:
        pic_json["video_view_count"] = media["view_count"]
    if media.get("play_count") is not None:
        pic_json["video_play_count"] = media["play_count"]
    if media_type == 2 and pic_json.get("video_view_count") is None:
        play_count = Post._fetch_play_count_from_clips(
            self._context, media["user"]["pk"], media["code"]
        )
        if play_count is not None:
            pic_json["video_play_count"] = play_count

    caption = media.get("caption")
    caption_text = caption.get("text") if isinstance(caption, dict) else None
    pic_json["edge_media_to_caption"] = (
        {"edges": [{"node": {"text": caption_text}}]}
        if caption_text is not None
        else {"edges": []}
    )
    pic_json["edge_media_preview_like"] = {"count": media.get("like_count") or 0}
    pic_json["edge_media_to_parent_comment"] = {
        "count": media.get("comment_count") or 0,
        "edges": [],
    }
    if media.get("has_liked") is not None:
        pic_json["viewer_has_liked"] = media["has_liked"]
    if media.get("accessibility_caption") is not None:
        pic_json["accessibility_caption"] = media["accessibility_caption"]
    if media.get("location"):
        pic_json["location"] = media["location"]

    carousel = media.get("carousel_media") or []
    if carousel:
        carousel_nodes = []
        for item in carousel:
            item_type = item.get("media_type", 1)
            node: Dict[str, Any] = {
                "shortcode": item.get("code", ""),
                "__typename": _MEDIA_TYPES.get(item_type, "GraphImage"),
                "is_video": item_type == 2,
            }
            item_candidates = (item.get("image_versions2") or {}).get("candidates") or []
            node["display_url"] = item_candidates[0]["url"] if item_candidates else ""
            item_videos = item.get("video_versions") or []
            node["video_url"] = item_videos[0]["url"] if item_videos else None
            if item.get("accessibility_caption") is not None:
                node["accessibility_caption"] = item["accessibility_caption"]
            carousel_nodes.append({"node": node})
        pic_json["edge_sidecar_to_children"] = {"edges": carousel_nodes}

    tagged = (media.get("usertags") or {}).get("in") or []
    if tagged:
        pic_json["edge_media_to_tagged_user"] = {
            "edges": [
                {"node": {"user": {"username": t["user"]["username"].lower()}}}
                for t in tagged
                if (t.get("user") or {}).get("username")
            ]
        }

    self._full_metadata_dict = pic_json
    if self.shortcode != self._full_metadata_dict["shortcode"]:
        self._node.update(self._full_metadata_dict)
        raise PostChangedException
