"""Tests for the recipe validation layer (Stage 8)."""

from src.reconstruction.schemas import (
    EvidenceSource,
    Ingredient,
    InstructionStep,
    StructuredRecipe,
)
from src.validation.validator import validate_recipe


def _make_recipe(**overrides) -> StructuredRecipe:
    base = {
        "title": "Test Recipe",
        "ingredients": [
            Ingredient(
                name="flour",
                amount="2 cups",
                source=EvidenceSource.CAPTION,
                confidence=0.9,
            )
        ],
        "instructions": [
            InstructionStep(
                step_number=1,
                text="Mix the flour with water.",
                source=EvidenceSource.TRANSCRIPT,
                confidence=0.9,
            )
        ],
        "overall_confidence": 0.9,
    }
    base.update(overrides)
    return StructuredRecipe(**base)


class TestValidateRecipe:
    def test_valid_recipe_passes(self, sample_recipe):
        report = validate_recipe(sample_recipe)
        assert not report.has_errors

    def test_missing_title_is_error(self):
        report = validate_recipe(_make_recipe(title="  "))
        assert report.has_errors
        assert any(i.code == "missing_title" for i in report.issues)

    def test_no_ingredients_is_error(self):
        report = validate_recipe(_make_recipe(ingredients=[]))
        assert any(i.code == "no_ingredients" for i in report.issues)

    def test_no_instructions_is_error(self):
        report = validate_recipe(_make_recipe(instructions=[]))
        assert any(i.code == "no_instructions" for i in report.issues)

    def test_missing_quantity_warning(self):
        recipe = _make_recipe(
            ingredients=[
                Ingredient(
                    name="salt", source=EvidenceSource.TRANSCRIPT, confidence=0.8
                )
            ]
        )
        report = validate_recipe(recipe)
        assert any(i.code == "missing_quantity" for i in report.issues)
        assert not report.has_errors  # warnings only

    def test_duplicate_ingredient_warning(self):
        ing = Ingredient(
            name="Garlic", amount="2 cloves", source=EvidenceSource.CAPTION, confidence=0.9
        )
        dup = Ingredient(
            name="garlic", amount="1 clove", source=EvidenceSource.OCR, confidence=0.7
        )
        report = validate_recipe(_make_recipe(ingredients=[ing, dup]))
        assert any(i.code == "duplicate_ingredient" for i in report.issues)

    def test_unlisted_ingredient_in_instructions(self):
        recipe = _make_recipe(
            instructions=[
                InstructionStep(
                    step_number=1,
                    text="Add the butter and stir.",
                    source=EvidenceSource.TRANSCRIPT,
                    confidence=0.9,
                )
            ]
        )
        report = validate_recipe(recipe)
        unlisted = [i for i in report.issues if i.code == "unlisted_ingredient"]
        assert len(unlisted) == 1
        assert "butter" in unlisted[0].message

    def test_listed_ingredient_not_flagged(self, sample_recipe):
        # butter IS listed in sample_recipe, and mentioned in instructions
        report = validate_recipe(sample_recipe)
        assert not any(
            i.code == "unlisted_ingredient" and "butter" in i.message
            for i in report.issues
        )

    def test_low_confidence_warning(self):
        recipe = _make_recipe(
            ingredients=[
                Ingredient(
                    name="mystery spice",
                    amount="1 tsp",
                    source=EvidenceSource.VISION,
                    confidence=0.3,
                )
            ]
        )
        report = validate_recipe(recipe)
        assert any(i.code == "low_confidence" for i in report.issues)

    def test_low_overall_confidence_warning(self):
        report = validate_recipe(_make_recipe(overall_confidence=0.2))
        assert any(i.code == "low_overall_confidence" for i in report.issues)
