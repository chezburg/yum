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


class TestNormalization:
    """Regression tests for near-miss LLM output seen in production logs."""

    def test_integer_amount_coerced_to_string(self):
        payload = _valid_payload()
        payload["ingredients"][0]["amount"] = 4
        recipe = _parse_recipe(json.dumps(payload))
        assert recipe.ingredients[0].amount == "4"

    def test_float_amount_coerced_to_string(self):
        payload = _valid_payload()
        payload["ingredients"][0]["amount"] = 0.25
        recipe = _parse_recipe(json.dumps(payload))
        assert recipe.ingredients[0].amount == "0.25"

    def test_integer_servings_coerced_to_string(self):
        payload = _valid_payload()
        payload["servings"] = 2
        recipe = _parse_recipe(json.dumps(payload))
        assert recipe.servings == "2"

    def test_string_notes_wrapped_in_list(self):
        payload = _valid_payload()
        payload["notes"] = "The original caption instructs to garnish."
        recipe = _parse_recipe(json.dumps(payload))
        assert recipe.notes == ["The original caption instructs to garnish."]

    def test_empty_string_notes_wrapped_as_empty_list(self):
        payload = _valid_payload()
        payload["notes"] = ""
        recipe = _parse_recipe(json.dumps(payload))
        assert recipe.notes == []

    def test_step_key_aliased_to_step_number(self):
        payload = _valid_payload()
        del payload["instructions"][0]["step_number"]
        payload["instructions"][0]["step"] = 1
        recipe = _parse_recipe(json.dumps(payload))
        assert recipe.instructions[0].step_number == 1

    def test_string_tags_wrapped_in_list(self):
        payload = _valid_payload()
        payload["tags"] = "italian"
        recipe = _parse_recipe(json.dumps(payload))
        assert recipe.tags == ["italian"]

    def test_invalid_source_still_rejected_after_normalization(self):
        payload = _valid_payload()
        payload["ingredients"][0]["source"] = "vision analysis"
        with pytest.raises(ValidationError):
            _parse_recipe(json.dumps(payload))

    def test_missing_required_title_still_rejected(self):
        payload = _valid_payload()
        del payload["title"]
        with pytest.raises(ValidationError):
            _parse_recipe(json.dumps(payload))
