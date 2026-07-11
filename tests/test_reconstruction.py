"""Tests for LLM output parsing in the reconstruction layer."""

import json

import pytest
from pydantic import ValidationError

from src.reconstruction.llm_client import _parse_recipe


def _valid_payload() -> dict:
    return {
        "title": "Test Pasta",
        "description": None,
        "ingredients": [
            {
                "name": "spaghetti",
                "amount": "200 g",
                "preparation": None,
                "source": "caption",
                "confidence": 0.95,
            }
        ],
        "instructions": [
            {
                "step_number": 1,
                "text": "Boil pasta.",
                "duration": None,
                "source": "transcript",
                "confidence": 0.9,
            }
        ],
        "equipment": ["pot"],
        "prep_time": "5 min",
        "cook_time": "10 min",
        "servings": "2",
        "tags": ["italian"],
        "overall_confidence": 0.9,
        "notes": [],
    }


class TestParseRecipe:
    def test_plain_json(self):
        recipe = _parse_recipe(json.dumps(_valid_payload()))
        assert recipe.title == "Test Pasta"
        assert recipe.ingredients[0].source.value == "caption"

    def test_json_in_markdown_fence(self):
        raw = f"```json\n{json.dumps(_valid_payload())}\n```"
        recipe = _parse_recipe(raw)
        assert recipe.title == "Test Pasta"

    def test_fence_without_language_tag(self):
        raw = f"```\n{json.dumps(_valid_payload())}\n```"
        assert _parse_recipe(raw).title == "Test Pasta"

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _parse_recipe("this is not json")

    def test_confidence_out_of_range_rejected(self):
        payload = _valid_payload()
        payload["overall_confidence"] = 1.5
        with pytest.raises(ValidationError):
            _parse_recipe(json.dumps(payload))

    def test_invalid_source_rejected(self):
        payload = _valid_payload()
        payload["ingredients"][0]["source"] = "hallucination"
        with pytest.raises(ValidationError):
            _parse_recipe(json.dumps(payload))
