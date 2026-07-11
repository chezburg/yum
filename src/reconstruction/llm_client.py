"""LLM client for recipe reconstruction via LiteLLM.

Supports any LiteLLM provider (OpenAI, Gemini, Anthropic, Ollama, or any
OpenAI/Anthropic-compatible endpoint) configured through the LLM engine
settings (mode, API base URL, API key, model). Uses JSON mode with strict
Pydantic validation, a light normalization pass to tolerate common near-miss
output (wrong key names, numbers where strings are required, etc.), and up
to MAX_ATTEMPTS retries with the concrete schema errors fed back to the
model on invalid output.
"""

from __future__ import annotations

import json
import logging
import re

import litellm
from pydantic import ValidationError

from src.analysis.text_parser import EvidenceBundle
from src.config import Settings
from src.reconstruction.prompt_templates import SYSTEM_PROMPT, build_user_prompt
from src.reconstruction.schemas import StructuredRecipe

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3


class ReconstructionError(RuntimeError):
    """Raised when the LLM fails to produce a valid structured recipe."""


def reconstruct_recipe(evidence: EvidenceBundle, settings: Settings) -> StructuredRecipe:
    """Merge all evidence into a validated StructuredRecipe via the configured LLM."""
    if not evidence.has_any_evidence():
        raise ReconstructionError("No evidence available to reconstruct a recipe.")

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_prompt(evidence)},
    ]

    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            raw = _call_llm(messages, settings)
            return _parse_recipe(raw)
        except json.JSONDecodeError as exc:
            last_error = exc
            logger.warning(
                "LLM output invalid on attempt %d/%d: %s", attempt, MAX_ATTEMPTS, exc
            )
            # Feed the error back so the model can self-correct.
            messages.append({"role": "assistant", "content": "(invalid output)"})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Your previous response was not valid JSON. "
                        f"Error: {exc}. Respond again with ONLY valid JSON, no "
                        "markdown fences, no commentary."
                    ),
                }
            )
        except ValidationError as exc:
            last_error = exc
            logger.warning(
                "LLM output invalid on attempt %d/%d: %s", attempt, MAX_ATTEMPTS, exc
            )
            # Feed the concrete schema errors back so the model can self-correct
            # exact field names/types rather than just retrying blindly.
            messages.append({"role": "assistant", "content": "(invalid output)"})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Your previous response did NOT match the required JSON "
                        "schema. It was syntactically valid JSON but had wrong "
                        "field names, wrong types, or invalid enum values. Fix "
                        f"EXACTLY these problems and respond again with the full, "
                        f"corrected JSON object (no markdown fences):\n{exc}"
                    ),
                }
            )
        except Exception as exc:  # noqa: BLE001 - provider errors vary
            raise ReconstructionError(f"LLM call failed: {exc}") from exc

    raise ReconstructionError(
        f"LLM produced invalid output after {MAX_ATTEMPTS} attempts: {last_error}"
    )


def _call_llm(messages: list[dict], settings: Settings) -> str:
    kwargs: dict = {
        "model": settings.llm_model,
        "messages": messages,
        "temperature": 0.1,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "structured_recipe",
                "schema": StructuredRecipe.model_json_schema(),
            },
        },
    }
    if settings.llm_api_key:
        kwargs["api_key"] = settings.llm_api_key
    if settings.llm_api_base:
        kwargs["api_base"] = settings.llm_api_base

    try:
        response = litellm.completion(**kwargs)
    except Exception as exc:
        # Some providers reject json_schema response_format; retry with json_object.
        logger.info("json_schema mode failed (%s); retrying with json_object.", exc)
        kwargs["response_format"] = {"type": "json_object"}
        response = litellm.completion(**kwargs)

    content = response.choices[0].message.content
    if not content:
        raise ReconstructionError("LLM returned an empty response.")
    return content


def _parse_recipe(raw: str) -> StructuredRecipe:
    """Parse LLM output into a StructuredRecipe, tolerating markdown fences."""
    text = raw.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    data = json.loads(text)
    _normalize_recipe_dict(data)
    return StructuredRecipe.model_validate(data)


# Aliases for keys the LLM sometimes uses instead of the schema's real names.
_INSTRUCTION_STEP_NUMBER_ALIASES = ("step", "step_num", "number", "order")

# Fields that must always be strings but the LLM sometimes emits as bare
# numbers (e.g. amount: 4 instead of "4", servings: 2 instead of "2").
_STRINGIFY_TOP_LEVEL_FIELDS = ("prep_time", "cook_time", "servings")
_STRINGIFY_INGREDIENT_FIELDS = ("amount", "preparation")


def _normalize_recipe_dict(data: dict) -> None:
    """Coerce common near-miss LLM output into the shape Pydantic expects.

    Mutates `data` in place. This tolerates minor, predictable mistakes
    (wrong-but-obvious key names, numbers where strings are required, a
    single string where a list is required) without requiring another full
    LLM round trip, while still letting genuinely malformed output fail
    validation normally.
    """
    if not isinstance(data, dict):
        return

    # `notes` must be a list[str]; the LLM sometimes sends a single string.
    notes = data.get("notes")
    if isinstance(notes, str):
        data["notes"] = [notes] if notes.strip() else []

    # `tags` / `equipment` have the same failure mode.
    for list_field in ("tags", "equipment"):
        value = data.get(list_field)
        if isinstance(value, str):
            data[list_field] = [value] if value.strip() else []

    # Top-level fields that must be strings or null.
    for field in _STRINGIFY_TOP_LEVEL_FIELDS:
        value = data.get(field)
        if isinstance(value, (int, float)):
            data[field] = str(value)

    for ingredient in data.get("ingredients") or []:
        if not isinstance(ingredient, dict):
            continue
        for field in _STRINGIFY_INGREDIENT_FIELDS:
            value = ingredient.get(field)
            if isinstance(value, (int, float)):
                ingredient[field] = str(value)

    for instruction in data.get("instructions") or []:
        if not isinstance(instruction, dict):
            continue
        if "step_number" not in instruction:
            for alias in _INSTRUCTION_STEP_NUMBER_ALIASES:
                if alias in instruction:
                    instruction["step_number"] = instruction.pop(alias)
                    break
        duration = instruction.get("duration")
        if isinstance(duration, (int, float)):
            instruction["duration"] = str(duration)
