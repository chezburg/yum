"""Tandoor Recipes export integration.

Creates a recipe via the Tandoor API. Requires TANDOOR_URL and TANDOOR_API_TOKEN.
"""

from __future__ import annotations

import logging

import httpx

from src.config import Settings
from src.reconstruction.schemas import StructuredRecipe

logger = logging.getLogger(__name__)

TIMEOUT = 30.0


class TandoorExportError(RuntimeError):
    """Raised when the Tandoor export fails."""


def export_to_tandoor(
    recipe: StructuredRecipe, source_url: str, settings: Settings
) -> str:
    """Export a recipe to Tandoor. Returns the created recipe ID."""
    if not settings.tandoor_url or not settings.tandoor_api_token:
        raise TandoorExportError("TANDOOR_URL and TANDOOR_API_TOKEN must be configured.")

    base = settings.tandoor_url.rstrip("/")
    headers = {
        "Authorization": f"Bearer {settings.tandoor_api_token}",
        "Content-Type": "application/json",
    }

    # Tandoor models a recipe as steps, each with its own ingredients.
    # We attach all ingredients to the first step for simplicity.
    ingredients = []
    for ing in recipe.ingredients:
        ingredients.append(
            {
                "food": {"name": ing.name},
                "unit": None,
                "amount": 0,
                "note": " ".join(
                    p for p in (ing.amount, ing.preparation) if p
                ),
            }
        )

    steps = []
    for i, step in enumerate(sorted(recipe.instructions, key=lambda s: s.step_number)):
        steps.append(
            {
                "name": "",
                "instruction": step.text,
                "ingredients": ingredients if i == 0 else [],
                "show_ingredients_table": i == 0,
            }
        )

    payload = {
        "name": recipe.title[:128],
        "description": (recipe.description or f"Imported from {source_url}")[:512],
        "keywords": [{"name": tag} for tag in recipe.tags],
        "steps": steps or [{"name": "", "instruction": "", "ingredients": ingredients}],
        "source_url": source_url,
        "internal": True,
        "servings": _parse_servings(recipe.servings),
        "working_time": _parse_minutes(recipe.prep_time),
        "waiting_time": _parse_minutes(recipe.cook_time),
    }

    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            resp = client.post(f"{base}/api/recipe/", headers=headers, json=payload)
            resp.raise_for_status()
            recipe_id = resp.json().get("id")
    except httpx.HTTPError as exc:
        raise TandoorExportError(f"Tandoor API error: {exc}") from exc

    logger.info("Exported recipe to Tandoor: id=%s", recipe_id)
    return str(recipe_id)


def _parse_servings(servings: str | None) -> int:
    """Extract a leading integer from a servings string, defaulting to 1."""
    if not servings:
        return 1
    digits = "".join(ch for ch in servings if ch.isdigit())
    return int(digits) if digits else 1


def _parse_minutes(time_str: str | None) -> int:
    """Best-effort conversion of '1 hr 20 min' style strings to minutes."""
    if not time_str:
        return 0
    import re

    total = 0
    for value, unit in re.findall(r"(\d+)\s*(h|hr|hour|m|min|minute)?", time_str.lower()):
        n = int(value)
        total += n * 60 if unit and unit.startswith("h") else n
    return total
