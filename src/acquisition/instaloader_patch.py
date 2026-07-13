"""Compatibility patches for three independent instaloader bugs.

Background - Post metadata (doc_id deprecation)
-------------------------------------------------
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

Background - missing user_id after load_session() (iPhone comment endpoint)
-----------------------------------------------------------------------------
Once the doc_id fix above is applied, ``Post.get_comments()`` succeeds for
small comment counts, but any post with more comments than a single GraphQL
page (~50) is unconditionally routed to
``Post._get_comments_via_iphone_endpoint()`` (a permanent workaround
instaloader itself added for a separate, older pagination bug,
:issue:`2125`). That method calls
``InstaloaderContext.get_iphone_json()``, which sends a
``ig-intended-user-id`` header built from ``self.user_id``.

``InstaloaderContext.load_session()`` - the method this codebase's
``build_loader()`` always uses to restore a stored session - only restores
cookies; it never sets ``self.user_id`` (that attribute is only populated by
``login()``/``two_factor_login()``, and is never itself persisted in the
saved session data). So every session restored from storage sends the
literal string ``"None"`` as ``ig-intended-user-id``, and Instagram's
private API responds with a generic::

    {"status": "fail", "message": "We're sorry, but something went wrong. Please try again."}

This is a genuine gap in instaloader's own ``load_session()`` (not an
Instagram-side deprecation), reproducible with any session restored via
``load_session()`` rather than a fresh interactive ``login()``. It has no
known upstream issue/PR to port from, so the fix here is derived directly
from tracing instaloader's own code: Instagram's login flow always sets a
``ds_user_id`` cookie, which ``load_session()`` *does* restore - so we
recover ``user_id`` from that cookie right after the original
``load_session()`` runs.

Background - iPhone comments endpoint still rejecting cookie-restored sessions
--------------------------------------------------------------------------------
Even with ``user_id`` recovered, comment fetching for any post with more
than ~50 comments kept failing with the same generic
``{"status": "fail", "message": "...something went wrong..."}`` against
``i.instagram.com``. ``get_iphone_json()`` builds several more
session-derived headers the same way it builds ``ig-intended-user-id``
(``x-mid`` from the ``mid`` cookie, ``x-ig-device-id``/
``x-ig-family-device-id`` from the ``ig_did`` cookie) - any of which may be
missing/stale in a web-login-derived session, and Instagram's mobile API
may also be gating this endpoint on device-identity signals a browser
login can never legitimately produce (a real iOS app registers a device;
instaloader's ``login()`` never does).

Rather than chase every possible missing mobile-device header, this module
sidesteps the entire iPhone endpoint: ``comments.py`` only ever needs the
first ~15 comments (``MAX_TOP_COMMENTS``), but ``Post.get_comments()``
routes to the iPhone endpoint purely based on the post's *total* comment
count, not on how many the caller actually wants. The patched
``get_comments()`` below removes that branch so it always uses the
GraphQL ``doc_id`` path (doc_id ``6974885689225067``), which never touches
``i.instagram.com`` and has no device-identity requirements.

Background - Instagram deprecated the comment query_hash (July 2026)
--------------------------------------------------------------------
Around July 2026, Instagram deprecated the legacy GraphQL ``query_hash``
endpoint for comments (``97b41c52301f77ce508f55e66d17620e``), migrating
to the modern ``doc_id``-based POST request pattern (same as the post
metadata migration in June 2026). The deprecated endpoint returns 401
"Please wait a few minutes before you try again" regardless of rate
limiting or session state. The patched ``get_comments()`` now uses
``doc_id`` ``6974885689225067`` with the post's numeric ``mediapk``
identifier. If Instagram deprecates this doc_id in the future,
``get_comments()`` will raise normally and ``comments.py``'s existing
``except Exception`` handling degrades gracefully (comments unavailable,
pipeline continues) - this patch cannot make comment fetching worse than
the unpatched baseline.

This module monkeypatches the affected methods in-process so comment/post
fetching keeps working without depending on an unmerged fork. Remove the
doc_id-related patches once a released ``instaloader`` version ships those
fixes; remove the ``load_session`` patch once ``load_session()`` upstream
sets ``user_id`` itself. Check by bumping the dependency and running
``tests/test_instagram_auth.py``, ``tests/test_instaloader_patch.py``,
and a manual fetch against a real shortcode with many comments.
"""

from __future__ import annotations

import json
import logging
import urllib.parse
from datetime import datetime
from typing import Any, Dict, Iterable, Optional

from instaloader.exceptions import (
    BadResponseException,
    LoginRequiredException,
    PostChangedException,
)
from instaloader.instaloadercontext import InstaloaderContext, copy_session
from instaloader.nodeiterator import NodeIterator
from instaloader.structures import Post

logger = logging.getLogger(__name__)

# New working doc_ids that replace the deprecated ones (see module docstring).
_POST_METADATA_DOC_ID = "27128499623469141"  # PolarisPostRootQuery
_CLIPS_CONNECTION_DOC_ID = "27234427476213202"  # play_count fallback for reels
_POST_COMMENTS_DOC_ID = "6974885689225067"  # Comment pagination (replaces query_hash 97b41c52301f77ce508f55e66d17620e)

_MEDIA_TYPES = {1: "GraphImage", 2: "GraphVideo", 8: "GraphSidecar"}

_patched = False

# Captured at patch-apply time so the wrapper can call through to the real
# implementation instead of recursing into itself.
_original_load_session = InstaloaderContext.load_session


def apply_metadata_patch() -> None:
    """Monkeypatch instaloader's Post/comment metadata fetching (idempotent)."""
    global _patched
    if _patched:
        return

    InstaloaderContext.doc_id_graphql_query = _patched_doc_id_graphql_query  # type: ignore[method-assign]
    InstaloaderContext.load_session = _patched_load_session  # type: ignore[method-assign]
    Post._obtain_metadata = _patched_obtain_metadata  # type: ignore[method-assign]
    Post._fetch_play_count_from_clips = staticmethod(_fetch_play_count_from_clips)  # type: ignore[attr-defined]
    Post.get_comments = _patched_get_comments  # type: ignore[method-assign]

    _patched = True
    logger.info(
        "Applied instaloader compatibility patches: post-metadata doc_id "
        "(https://github.com/instaloader/instaloader/issues/2704), "
        "load_session() user_id recovery, and doc_id-based comment "
        "fetching (bypassing deprecated query_hash and device-identity-gated iPhone endpoint)."
    )


class _DocIdCommentIterator:
    """Custom iterator for doc_id-based comment pagination.
    
    Mirrors NodeIterator's interface but uses doc_id_graphql_query instead
    of the deprecated query_hash graphql_query. Required because Instagram
    deprecated the legacy query_hash endpoint for comments in July 2026.
    """

    def __init__(
        self,
        context: InstaloaderContext,
        doc_id: str,
        node_wrapper,
        context_wrapper,
        query_variables: Dict[str, Any],
        query_referer: str,
    ):
        self._context = context
        self._doc_id = doc_id
        self._node_wrapper = node_wrapper
        self._context_wrapper = context_wrapper
        self._query_variables = query_variables.copy()
        self._query_referer = query_referer
        self._page_index = 0
        self._iteration_count = 0

    def __iter__(self):
        return self

    def __next__(self):
        if self._iteration_count == 0:
            # First page - get initial data
            self._data = self._query()
        
        # Extract nodes from current page
        wrapper = self._node_wrapper(self._data)
        
        # Process all nodes on this page
        while self._page_index < len(wrapper):
            node = wrapper[self._page_index]
            self._page_index += 1
            self._iteration_count += 1
            # Extract the actual node data (handle both direct nodes and edge-wrapped)
            if isinstance(node, dict) and "node" in node:
                return self._context_wrapper(node["node"])
            return self._context_wrapper(node)
        
        # Current page exhausted - check for next page
        page_info = self._data.get("data", {}).get(
            "xdt_api__v1__media__info__comments", {}
        ).get("page_info", {})
        
        if not page_info.get("has_next_page"):
            raise StopIteration
        
        # Fetch next page
        self._query_variables["after"] = page_info.get("end_cursor")
        self._page_index = 0
        self._data = self._query()
        
        # Recursively call __next__ to get first item from new page
        return self.__next__()

    def _query(self):
        """Execute doc_id GraphQL query with current variables."""
        return self._context.doc_id_graphql_query(
            self._doc_id,
            self._query_variables,
            self._query_referer,
        )


def _patched_load_session(
    self: InstaloaderContext, username: str, sessiondata: Dict[str, Any]
) -> None:
    """Same as upstream ``load_session()``, plus recovering ``user_id`` from
    the restored session's ``ds_user_id`` cookie.

    Instagram sets ``ds_user_id`` on successful login and it is part of the
    cookie set instaloader always includes in a saved session
    (``InstaloaderContext.save_session()`` serializes the full cookie jar).
    Upstream's ``load_session()`` never reads it back into ``self.user_id``,
    which breaks the ``ig-intended-user-id`` header on the iPhone comments
    endpoint (``get_iphone_json()``) for every session restored from
    storage. See module docstring for full details.
    """
    _original_load_session(self, username, sessiondata)
    ds_user_id = sessiondata.get("ds_user_id")
    if ds_user_id:
        try:
            self.user_id = int(ds_user_id)
        except (TypeError, ValueError):
            logger.warning(
                "Stored Instagram session has a non-numeric ds_user_id "
                "cookie (%r) - iPhone-endpoint requests (e.g. large-comment "
                "posts) may still fail.",
                ds_user_id,
            )


def _patched_get_comments(self: Post) -> Iterable[Any]:
    """Same as upstream ``Post.get_comments()``, except uses the modern
    doc_id-based GraphQL endpoint instead of the deprecated query_hash,
    and bypasses the device-identity-gated iPhone endpoint entirely.
    See module docstring for full context.

    Migrated from query_hash ``97b41c52301f77ce508f55e66d17620e`` to
    doc_id ``6974885689225067`` (July 2026).
    """
    # Local import to avoid a module-level circular import (structures.py
    # imports nothing from this module, but Profile/PostComment/
    # PostCommentAnswer live in the same module as Post).
    from instaloader.structures import PostComment, PostCommentAnswer, Profile

    if not self._context.is_logged_in:
        raise LoginRequiredException("Login required to access comments of a post.")

    def _postcommentanswer(node):
        return PostCommentAnswer(
            id=int(node["id"]),
            created_at_utc=datetime.utcfromtimestamp(node["created_at"]),
            text=node["text"],
            owner=Profile(self._context, node["owner"]),
            likes_count=node.get("edge_liked_by", {}).get("count", 0),
        )

    def _postcommentanswers(node):
        if "edge_threaded_comments" not in node:
            return
        answer_count = node["edge_threaded_comments"]["count"]
        if answer_count == 0:
            return
        answer_edges = node["edge_threaded_comments"]["edges"]
        if answer_count == len(answer_edges):
            yield from (_postcommentanswer(comment["node"]) for comment in answer_edges)
            return
        yield from NodeIterator(
            self._context,
            "51fdd02b67508306ad4484ff574a0b62",
            lambda d: d["data"]["comment"]["edge_threaded_comments"],
            _postcommentanswer,
            {"comment_id": node["id"]},
            "https://www.instagram.com/p/{0}/".format(self.shortcode),
        )

    def _postcomment(node):
        return PostComment(
            context=self._context, node=node, answers=_postcommentanswers(node), post=self
        )

    if self.comments == 0:
        return []

    try:
        comment_edges = self._field("edge_media_to_parent_comment", "edges")
    except KeyError:
        comment_edges = self._field("edge_media_to_comment", "edges")

    answers_count = sum(
        edge["node"].get("edge_threaded_comments", {}).get("count", 0)
        for edge in comment_edges
    )

    if self.comments == len(comment_edges) + answers_count:
        return [_postcomment(comment["node"]) for comment in comment_edges]

    # Use doc_id-based pagination instead of deprecated query_hash.
    # The new endpoint requires media_id (numeric pk) instead of shortcode.
    return _DocIdCommentIterator(
        self._context,
        _POST_COMMENTS_DOC_ID,
        lambda d: d["data"]["xdt_api__v1__media__info__comments"]["edges"],
        _postcomment,
        {"media_id": str(self.mediaid)},
        "https://www.instagram.com/p/{0}/".format(self.shortcode),
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
