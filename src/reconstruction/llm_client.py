"""LLM client for recipe reconstruction via LiteLLM.

Supports any LiteLLM provider (OpenAI, Gemini, Anthropic, Ollama, or any
OpenAI/Anthropic-compatible endpoint) configured through the LLM engine
settings (mode, API base URL, API key, model). Uses JSON mode with strict
Pydantic validation and one retry on invalid output.
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

MAX_ATTEMPTS = 2


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
        except (ValidationError, json.JSONDecodeError) as exc:
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
                        "Your previous response was not valid JSON for the schema. "
                        f"Error: {exc}. Respond again with ONLY valid JSON."
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
    return StructuredRecipe.model_validate(data)
