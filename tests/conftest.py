"""Shared test fixtures."""

from __future__ import annotations

import pytest

from src.reconstruction.schemas import (
    EvidenceSource,
    Ingredient,
    InstructionStep,
    StructuredRecipe,
)


@pytest.fixture
def sample_recipe() -> StructuredRecipe:
    """A complete, valid recipe for testing validation and export."""
    return StructuredRecipe(
        title="Garlic Butter Pasta",
        description="Quick weeknight pasta.",
        ingredients=[
            Ingredient(
                name="spaghetti",
                amount="200 g",
                source=EvidenceSource.CAPTION,
                confidence=0.95,
            ),
            Ingredient(
                name="garlic",
                amount="4 cloves",
                preparation="minced",
                source=EvidenceSource.CREATOR_REPLY,
                confidence=0.97,
            ),
            Ingredient(
                name="butter",
                amount="3 tbsp",
                source=EvidenceSource.OCR,
                confidence=0.85,
            ),
        ],
        instructions=[
            InstructionStep(
                step_number=1,
                text="Boil the spaghetti until al dente.",
                duration="8 min",
                source=EvidenceSource.TRANSCRIPT,
                confidence=0.9,
            ),
            InstructionStep(
                step_number=2,
                text="Melt butter, add garlic, toss with pasta.",
                source=EvidenceSource.TRANSCRIPT,
                confidence=0.88,
            ),
        ],
        equipment=["large pot", "skillet"],
        prep_time="5 min",
        cook_time="10 min",
        servings="2 servings",
        tags=["italian", "pasta"],
        overall_confidence=0.9,
        notes=[],
    )
