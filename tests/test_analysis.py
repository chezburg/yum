"""Tests for evidence collation and prompt construction."""

from src.analysis.text_parser import (
    EvidenceBundle,
    format_comments,
    format_ocr,
    format_transcript,
)
from src.reconstruction.prompt_templates import build_user_prompt


class TestEvidenceBundle:
    def test_creator_comment_filtering(self):
        bundle = EvidenceBundle(
            comments=[
                {"text": "Full recipe: 2 cups flour...", "is_creator": True, "likes": 10},
                {"text": "Looks great!", "is_creator": False, "likes": 100},
            ]
        )
        assert len(bundle.creator_comments) == 1
        assert len(bundle.community_comments) == 1

    def test_has_any_evidence_empty(self):
        assert not EvidenceBundle().has_any_evidence()

    def test_has_any_evidence_caption_only(self):
        assert EvidenceBundle(caption="2 cups flour").has_any_evidence()


class TestFormatters:
    def test_format_transcript_timestamps(self):
        out = format_transcript(
            [
                {"start": 1.0, "end": 4.0, "text": "Add two cups of flour"},
                {"start": 68.0, "end": 70.0, "text": "Mix until smooth"},
            ]
        )
        assert "[00:01] Add two cups of flour" in out
        assert "[01:08] Mix until smooth" in out

    def test_format_ocr(self):
        out = format_ocr([{"timestamp": 5.2, "text": "2 cups flour", "confidence": 0.9}])
        assert "[00:05] 2 cups flour" in out

    def test_format_comments_split(self):
        creator, community = format_comments(
            [
                {"text": "Use 4 cloves garlic", "is_creator": True, "is_reply": True, "likes": 5},
                {"text": "Yum!", "is_creator": False, "is_reply": False, "likes": 50},
            ]
        )
        assert "Use 4 cloves garlic" in creator
        assert "(reply, 5 likes)" in creator
        assert "Yum!" in community


class TestBuildUserPrompt:
    def test_all_sections_present(self):
        bundle = EvidenceBundle(
            caption="Full recipe below!",
            title="Best Pasta Ever",
            author="some.chef",
            hashtags=["pasta", "easy"],
            transcript_segments=[{"start": 0, "end": 2, "text": "Boil water"}],
            ocr_detections=[{"timestamp": 1.0, "text": "200g spaghetti", "confidence": 0.9}],
            vision_facts=["Uses cast iron skillet"],
            comments=[{"text": "Salt the water!", "is_creator": True, "likes": 3}],
        )
        prompt = build_user_prompt(bundle)
        assert "Best Pasta Ever" in prompt
        assert "Full recipe below!" in prompt
        assert "200g spaghetti" in prompt
        assert "Boil water" in prompt
        assert "Uses cast iron skillet" in prompt
        assert "Salt the water!" in prompt
        assert "HIGHEST PRIORITY" in prompt

    def test_empty_sections_marked_none(self):
        prompt = build_user_prompt(EvidenceBundle(caption="Just a caption"))
        assert "(none)" in prompt
