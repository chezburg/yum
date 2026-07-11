"""Stage 8: semantic validation of reconstructed recipes.

Checks:
    - Missing quantities on ingredients
    - Duplicate ingredients
    - Instructions referencing ingredients not in the ingredient list
    - Empty/suspicious recipe structure
    - Low-confidence extractions
"""

from __future__ import annotations

import re

from src.reconstruction.schemas import (
    StructuredRecipe,
    ValidationIssue,
    ValidationReport,
)

LOW_CONFIDENCE_THRESHOLD = 0.5

# Words too generic to flag as "unreferenced ingredient" mentions.
_STOPWORDS = frozenset(
    {
        "a", "an", "the", "and", "or", "of", "in", "on", "to", "with", "for",
        "into", "until", "then", "add", "mix", "stir", "heat", "bake", "cook",
        "pan", "bowl", "oven", "medium", "high", "low", "minutes", "min",
        "water", "it", "them", "all", "half", "rest", "top", "over", "each",
    }
)


def validate_recipe(recipe: StructuredRecipe) -> ValidationReport:
    """Run all validation checks and return a report."""
    issues: list[ValidationIssue] = []

    issues.extend(_check_structure(recipe))
    issues.extend(_check_missing_quantities(recipe))
    issues.extend(_check_duplicate_ingredients(recipe))
    issues.extend(_check_unreferenced_ingredients(recipe))
    issues.extend(_check_low_confidence(recipe))

    return ValidationReport(issues=issues)


def _check_structure(recipe: StructuredRecipe) -> list[ValidationIssue]:
    issues = []
    if not recipe.title.strip():
        issues.append(
            ValidationIssue(
                severity="error", code="missing_title", message="Recipe has no title."
            )
        )
    if not recipe.ingredients:
        issues.append(
            ValidationIssue(
                severity="error",
                code="no_ingredients",
                message="No ingredients were extracted.",
            )
        )
    if not recipe.instructions:
        issues.append(
            ValidationIssue(
                severity="error",
                code="no_instructions",
                message="No instructions were extracted.",
            )
        )
    return issues


def _check_missing_quantities(recipe: StructuredRecipe) -> list[ValidationIssue]:
    issues = []
    for ing in recipe.ingredients:
        if not ing.amount or not ing.amount.strip():
            issues.append(
                ValidationIssue(
                    severity="warning",
                    code="missing_quantity",
                    message=f"Ingredient '{ing.name}' has no quantity.",
                )
            )
    return issues


def _check_duplicate_ingredients(recipe: StructuredRecipe) -> list[ValidationIssue]:
    seen: dict[str, int] = {}
    issues = []
    for ing in recipe.ingredients:
        key = ing.name.strip().lower()
        seen[key] = seen.get(key, 0) + 1
    for name, count in seen.items():
        if count > 1:
            issues.append(
                ValidationIssue(
                    severity="warning",
                    code="duplicate_ingredient",
                    message=f"Ingredient '{name}' appears {count} times.",
                )
            )
    return issues


def _check_unreferenced_ingredients(recipe: StructuredRecipe) -> list[ValidationIssue]:
    """Flag instruction nouns that look like ingredients but are not listed.

    Heuristic: find listed ingredient names inside instruction text; then flag
    common cooking ingredients mentioned in instructions but missing from the
    ingredient list (e.g. 'Add the butter' with no butter listed).
    """
    issues = []
    listed = {w for ing in recipe.ingredients for w in _tokens(ing.name)}

    common_ingredients = {
        "butter", "salt", "pepper", "oil", "garlic", "onion", "sugar", "flour",
        "egg", "eggs", "milk", "cream", "cheese", "lemon", "vinegar", "honey",
        "soy", "ginger", "basil", "parsley", "cilantro", "thyme", "oregano",
        "paprika", "cumin", "chili", "tomato", "rice", "pasta", "chicken",
        "beef", "pork", "shrimp", "vanilla", "cinnamon", "yeast", "baking",
    }

    mentioned: set[str] = set()
    for step in recipe.instructions:
        mentioned.update(_tokens(step.text))

    for word in sorted((mentioned & common_ingredients) - listed - _STOPWORDS):
        issues.append(
            ValidationIssue(
                severity="warning",
                code="unlisted_ingredient",
                message=(
                    f"Instructions mention '{word}' but it is not in the "
                    "ingredients list."
                ),
            )
        )
    return issues


def _check_low_confidence(recipe: StructuredRecipe) -> list[ValidationIssue]:
    issues = []
    for ing in recipe.ingredients:
        if ing.confidence < LOW_CONFIDENCE_THRESHOLD:
            issues.append(
                ValidationIssue(
                    severity="warning",
                    code="low_confidence",
                    message=(
                        f"Low confidence ({ing.confidence:.2f}) for ingredient "
                        f"'{ing.name}' (source: {ing.source.value})."
                    ),
                )
            )
    if recipe.overall_confidence < LOW_CONFIDENCE_THRESHOLD:
        issues.append(
            ValidationIssue(
                severity="warning",
                code="low_overall_confidence",
                message=f"Overall confidence is low ({recipe.overall_confidence:.2f}).",
            )
        )
    return issues


def _tokens(text: str) -> set[str]:
    return {w.lower() for w in re.findall(r"[a-zA-Z]+", text) if len(w) > 2}
