"""Mealie export integration.

Creates a recipe via the Mealie API (v1) and patches in the full details.
Requires MEALIE_URL and MEALIE_API_TOKEN.
"""

from __future__ import annotations

import logging

import httpx

from src.config import Settings
from src.reconstruction.schemas import StructuredRecipe

logger = logging.getLogger(__name__)

TIMEOUT = 30.0


class MealieExportError(RuntimeError):
    """Raised when the Mealie export fails."""


def export_to_mealie(
    recipe: StructuredRecipe, source_url: str, settings: Settings
) -> str:
    """Export a recipe to Mealie. Returns the created recipe slug."""
    if not settings.mealie_url or not settings.mealie_api_token:
        raise MealieExportError("MEALIE_URL and MEALIE_API_TOKEN must be configured.")

    base = settings.mealie_url.rstrip("/")
    headers = {
        "Authorization": f"Bearer {settings.mealie_api_token}",
        "Content-Type": "application/json",
    }

    ingredients = []
    for ing in recipe.ingredients:
        note = " ".join(
            p for p in (ing.amount, ing.name, f"({ing.preparation})" if ing.preparation else "")
            if p
        )
        ingredients.append({"note": note, "display": note})

    instructions = [
        {"text": step.text}
        for step in sorted(recipe.instructions, key=lambda s: s.step_number)
    ]

    payload = {
        "description": recipe.description or f"Imported from {source_url}",
        "recipeIngredient": ingredients,
        "recipeInstructions": instructions,
        "prepTime": recipe.prep_time,
        "performTime": recipe.cook_time,
        "recipeYield": recipe.servings,
        "orgURL": source_url,
    }

    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            # Step 1: create the recipe stub (Mealie returns the slug).
            create_resp = client.post(
                f"{base}/api/recipes", headers=headers, json={"name": recipe.title}
            )
            create_resp.raise_for_status()
            slug = create_resp.json()

            # Step 2: patch in full recipe details.
            patch_resp = client.patch(
                f"{base}/api/recipes/{slug}", headers=headers, json=payload
            )
            patch_resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise MealieExportError(f"Mealie API error: {exc}") from exc

    logger.info("Exported recipe to Mealie: %s", slug)
    return str(slug)
