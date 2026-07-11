"""Tests for share-sheet URL extraction and sanitization."""

import pytest

from src.utils.url_parser import URLParseError, extract_instagram_url


class TestExtractInstagramURL:
    def test_plain_reel_url(self):
        result = extract_instagram_url("https://www.instagram.com/reel/C-xyz123/")
        assert result.shortcode == "C-xyz123"
        assert result.kind == "reel"
        assert result.canonical_url == "https://www.instagram.com/reel/C-xyz123/"

    def test_share_sheet_text_with_tracking_params(self):
        text = "Check out this recipe! https://www.instagram.com/reel/DEf_45-gh/?igsh=abc123&utm_source=ig"
        result = extract_instagram_url(text)
        assert result.shortcode == "DEf_45-gh"
        assert "igsh" not in result.canonical_url
        assert result.canonical_url == "https://www.instagram.com/reel/DEf_45-gh/"

    def test_post_url(self):
        result = extract_instagram_url("https://instagram.com/p/ABC123/")
        assert result.kind == "p"
        assert result.shortcode == "ABC123"

    def test_reels_plural_normalized(self):
        result = extract_instagram_url("https://www.instagram.com/reels/XYZ789/")
        assert result.kind == "reel"

    def test_share_link_with_username_segment(self):
        result = extract_instagram_url(
            "https://www.instagram.com/some.chef/reel/Cabc123/"
        )
        assert result.shortcode == "Cabc123"

    def test_tv_url(self):
        result = extract_instagram_url("https://www.instagram.com/tv/IGTV123/")
        assert result.kind == "tv"

    def test_text_without_url_raises(self):
        with pytest.raises(URLParseError):
            extract_instagram_url("just some text about cooking")

    def test_empty_input_raises(self):
        with pytest.raises(URLParseError):
            extract_instagram_url("")

    def test_non_instagram_url_raises(self):
        with pytest.raises(URLParseError):
            extract_instagram_url("https://www.youtube.com/watch?v=abc")

    def test_multiline_share_text(self):
        text = "Amazing carbonara!\n\nhttps://www.instagram.com/reel/Cmulti1/\n#pasta"
        result = extract_instagram_url(text)
        assert result.shortcode == "Cmulti1"
