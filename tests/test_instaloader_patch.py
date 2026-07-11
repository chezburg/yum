"""Tests for the instaloader Post-metadata compatibility patch.

Instagram deprecated the GraphQL doc_id instaloader historically used for
Post._obtain_metadata() (see src/acquisition/instaloader_patch.py for
background). These tests build a fake v1/iPhone-API-shaped response (the
new format Instagram now returns) and verify the patched method correctly
maps it back to the legacy GraphQL fields the rest of instaloader - and
src/acquisition/comments.py - expect.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from instaloader.exceptions import BadResponseException
from instaloader.structures import Post

from src.acquisition import instaloader_patch


@pytest.fixture(autouse=True)
def _apply_patch():
    """Ensure the patch is applied (idempotent) before each test."""
    instaloader_patch.apply_metadata_patch()


def _fake_context(response: dict) -> MagicMock:
    context = MagicMock()
    context.doc_id_graphql_query.return_value = response
    return context


def _web_info_response(items: list[dict]) -> dict:
    return {
        "data": {"xdt_api__v1__media__shortcode__web_info": {"items": items}}
    }


def _sample_media(**overrides) -> dict:
    media = {
        "code": "DZ2omvWRfR5",
        "pk": "123456789",
        "media_type": 1,  # image
        "taken_at": 1750000000,
        "user": {"pk": "42", "username": "chef.user", "full_name": "Chef User"},
        "image_versions2": {"candidates": [{"url": "https://example.com/pic.jpg"}]},
        "caption": {"text": "Delicious pasta recipe!"},
        "like_count": 10,
        "comment_count": 2,
    }
    media.update(overrides)
    return media


class TestPatchedObtainMetadata:
    def test_maps_basic_fields(self):
        context = _fake_context(_web_info_response([_sample_media()]))
        post = Post(context, {"shortcode": "DZ2omvWRfR5"})

        metadata = post._full_metadata

        assert metadata["shortcode"] == "DZ2omvWRfR5"
        assert metadata["id"] == "123456789"
        assert metadata["__typename"] == "GraphImage"
        assert metadata["is_video"] is False
        assert metadata["taken_at_timestamp"] == 1750000000
        assert metadata["owner"]["username"] == "chef.user"
        assert metadata["display_url"] == "https://example.com/pic.jpg"
        assert metadata["edge_media_to_caption"]["edges"][0]["node"]["text"] == (
            "Delicious pasta recipe!"
        )
        assert metadata["edge_media_preview_like"]["count"] == 10
        assert metadata["edge_media_to_parent_comment"]["count"] == 2

    def test_owner_username_property_works(self):
        """Mirrors real usage: comments.py calls Post.from_shortcode(), which
        assigns the full metadata dict (including 'owner') onto post._node
        before owner_username is ever read.
        """
        context = _fake_context(_web_info_response([_sample_media()]))
        post = Post.from_shortcode(context, "DZ2omvWRfR5")

        assert post.owner_username == "chef.user"

    def test_video_maps_video_url_and_duration(self):
        media = _sample_media(
            media_type=2,
            video_versions=[{"url": "https://example.com/vid.mp4"}],
            video_duration=12.5,
        )
        context = _fake_context(_web_info_response([media]))
        post = Post(context, {"shortcode": "DZ2omvWRfR5"})

        metadata = post._full_metadata

        assert metadata["__typename"] == "GraphVideo"
        assert metadata["is_video"] is True
        assert metadata["video_url"] == "https://example.com/vid.mp4"
        assert metadata["video_duration"] == 12.5

    def test_carousel_maps_sidecar_children(self):
        media = _sample_media(
            media_type=8,
            carousel_media=[
                {
                    "code": "child1",
                    "media_type": 1,
                    "image_versions2": {"candidates": [{"url": "https://example.com/1.jpg"}]},
                },
                {
                    "code": "child2",
                    "media_type": 2,
                    "image_versions2": {"candidates": [{"url": "https://example.com/2.jpg"}]},
                    "video_versions": [{"url": "https://example.com/2.mp4"}],
                },
            ],
        )
        context = _fake_context(_web_info_response([media]))
        post = Post(context, {"shortcode": "DZ2omvWRfR5"})

        metadata = post._full_metadata

        edges = metadata["edge_sidecar_to_children"]["edges"]
        assert len(edges) == 2
        assert edges[0]["node"]["display_url"] == "https://example.com/1.jpg"
        assert edges[1]["node"]["video_url"] == "https://example.com/2.mp4"

    def test_null_web_info_items_raises_bad_response(self):
        """Mirrors the exact upstream failure mode (Instagram's deprecated
        doc_id response) to confirm the *new* doc_id path still raises the
        same, well-known exception type if Instagram ever breaks this one
        too - rather than a confusing TypeError.
        """
        context = _fake_context({"data": {"xdt_api__v1__media__shortcode__web_info": None}})
        post = Post(context, {"shortcode": "DZ2omvWRfR5"})

        with pytest.raises(BadResponseException, match="Fetching Post metadata failed"):
            post._full_metadata

    def test_unknown_media_type_raises_bad_response(self):
        context = _fake_context(_web_info_response([_sample_media(media_type=99)]))
        post = Post(context, {"shortcode": "DZ2omvWRfR5"})

        with pytest.raises(BadResponseException, match="Unknown media_type"):
            post._full_metadata

    def test_uses_new_doc_id(self):
        context = _fake_context(_web_info_response([_sample_media()]))
        post = Post(context, {"shortcode": "DZ2omvWRfR5"})

        post._full_metadata

        args, _ = context.doc_id_graphql_query.call_args
        assert args[0] == instaloader_patch._POST_METADATA_DOC_ID
        assert args[1]["shortcode"] == "DZ2omvWRfR5"


class TestPatchIdempotent:
    def test_apply_twice_is_safe(self):
        instaloader_patch.apply_metadata_patch()
        instaloader_patch.apply_metadata_patch()
        assert instaloader_patch._patched is True


class TestPatchedLoadSession:
    """Covers the second, independent bug: load_session() never restoring
    context.user_id, which breaks the ig-intended-user-id header on the
    iPhone comments endpoint for every session restored from storage (as
    opposed to a fresh interactive login()).
    """

    def test_recovers_user_id_from_ds_user_id_cookie(self):
        import instaloader

        loader = instaloader.Instaloader(quiet=True)
        assert loader.context.user_id is None

        loader.context.load_session(
            "chef.user",
            {"sessionid": "s3ss10n", "csrftoken": "tok", "ds_user_id": "987654321"},
        )

        assert loader.context.user_id == 987654321
        assert loader.context.username == "chef.user"

    def test_missing_ds_user_id_leaves_user_id_none(self):
        import instaloader

        loader = instaloader.Instaloader(quiet=True)

        loader.context.load_session(
            "chef.user", {"sessionid": "s3ss10n", "csrftoken": "tok"}
        )

        assert loader.context.user_id is None

    def test_non_numeric_ds_user_id_does_not_raise(self):
        import instaloader

        loader = instaloader.Instaloader(quiet=True)

        loader.context.load_session(
            "chef.user",
            {"sessionid": "s3ss10n", "csrftoken": "tok", "ds_user_id": "not-a-number"},
        )

        assert loader.context.user_id is None

    def test_still_restores_cookies_and_csrf_header(self):
        import instaloader

        loader = instaloader.Instaloader(quiet=True)

        loader.context.load_session(
            "chef.user",
            {"sessionid": "s3ss10n", "csrftoken": "tok", "ds_user_id": "42"},
        )

        cookies = loader.context._session.cookies.get_dict()
        assert cookies["sessionid"] == "s3ss10n"
        assert loader.context._session.headers["X-CSRFToken"] == "tok"
