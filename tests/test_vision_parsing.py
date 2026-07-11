"""Tests for vision output parsing and the OCR/vision pipeline hand-off."""

from __future__ import annotations

from src.pipeline import _vision_text_to_ocr_dicts
from src.processing.vision import VisionResult, _parse_vision_output


class TestParseVisionOutput:
    def test_splits_tagged_lines(self):
        raw = (
            "FACT: 12-inch cast iron skillet\n"
            "TEXT: 2 cups flour\n"
            "FACT: butter added off-screen\n"
            "TEXT: bake at 180C\n"
        )
        result = _parse_vision_output(raw)
        assert result.facts == [
            "12-inch cast iron skillet",
            "butter added off-screen",
        ]
        assert result.onscreen_text == ["2 cups flour", "bake at 180C"]

    def test_untagged_lines_become_facts(self):
        result = _parse_vision_output("- wooden spoon\n• nonstick pan\n")
        assert result.facts == ["wooden spoon", "nonstick pan"]
        assert result.onscreen_text == []

    def test_case_insensitive_tags_and_blank_lines(self):
        result = _parse_vision_output("text: SALT\n\nfact: whisking shown\n")
        assert result.onscreen_text == ["SALT"]
        assert result.facts == ["whisking shown"]

    def test_empty_output(self):
        result = _parse_vision_output("")
        assert result == VisionResult(facts=[], onscreen_text=[])


class TestVisionTextToOcrDicts:
    def test_shapes_match_ocr_detections(self):
        dicts = _vision_text_to_ocr_dicts(["2 cups flour", "  ", "bake at 180C"])
        assert dicts == [
            {"timestamp": 0.0, "text": "2 cups flour", "confidence": 1.0},
            {"timestamp": 0.0, "text": "bake at 180C", "confidence": 1.0},
        ]

    def test_empty_input(self):
        assert _vision_text_to_ocr_dicts([]) == []
