"""Prompt templates for the LLM recipe reconstruction stage."""

from __future__ import annotations

from src.analysis.text_parser import (
    EvidenceBundle,
    format_comments,
    format_ocr,
    format_transcript,
)

SYSTEM_PROMPT = """\
You are an expert recipe extraction engine. You receive multiple evidence \
sources extracted from a single Instagram cooking video and must reconstruct \
the most accurate possible recipe.

EVIDENCE PRIORITY (highest trust first) - use this to resolve conflicts:
1. Creator comments & replies (creators often post exact recipes/corrections here)
2. Caption (usually written by the creator)
3. On-screen text (OCR) - usually accurate but may have OCR errors
4. Spoken transcript - accurate for technique, sometimes vague on amounts
5. Vision analysis - useful for equipment/ingredients, never trust it for amounts
6. Community comments - lowest trust, only use if creator-confirmed

RULES:
- Merge ALL evidence. An ingredient mentioned only on-screen still counts.
- Every ingredient and instruction must carry its `source` and a `confidence` \
score (0.0-1.0) reflecting how directly the evidence supports it.
- If two sources conflict on an amount, prefer the higher-priority source and \
lower the confidence; mention the conflict in `notes`.
- Do NOT invent amounts. If no amount is given anywhere, leave `amount` null \
and note it.
- Fix obvious OCR errors (e.g. 'l tsp' -> '1 tsp') but lower confidence slightly.
- Instructions must be ordered, complete, and actionable.
- Use `notes` for any missing information, ambiguities, or assumptions.
- `tags` should include cuisine and meal-type categories you can infer.

OUTPUT FORMAT - you MUST follow these exact field names and types. Output \
that violates any of these rules will be rejected and you will be asked to \
redo it, so get it right the first time:
- Top-level object requires: `title` (string), `overall_confidence` (number \
0.0-1.0). `description`, `prep_time`, `cook_time`, `servings` are strings or \
null (never numbers - e.g. servings must be "2", not 2). `equipment` and \
`tags` are arrays of strings. `notes` MUST be a JSON array of strings (e.g. \
["Note one", "Note two"]) - never a single string, even if there is only one \
note.
- Each item in `ingredients` is an object with EXACTLY these keys: `name` \
(string), `amount` (string or null - ALWAYS a string, e.g. "4", "2 cups", \
"0.5 tsp" - never a bare number), `preparation` (string or null), `source` \
(string enum, see below), `confidence` (number 0.0-1.0).
- Each item in `instructions` is an object with EXACTLY these keys: \
`step_number` (integer, starting at 1 - the key MUST be named `step_number`, \
NOT `step`), `text` (string), `duration` (string or null), `source` (string \
enum, see below), `confidence` (number 0.0-1.0).
- The `source` field on every ingredient and instruction MUST be exactly one \
of these literal strings (no other values, no free text, no synonyms): \
"creator_reply", "creator_comment", "pinned_comment", "caption", "ocr", \
"transcript", "vision", "community_comment", "inferred". For example, \
evidence derived from analyzing video frames must use "vision", NOT "vision \
analysis" or any other phrase.
- Respond ONLY with JSON matching this schema. Do not add extra keys, do not \
omit required keys, and do not change any key names."""


def build_user_prompt(evidence: EvidenceBundle) -> str:
    """Assemble all evidence into a clearly sectioned user prompt."""
    creator_block, community_block = format_comments(evidence.comments)
    transcript_block = format_transcript(evidence.transcript_segments)
    ocr_block = format_ocr(evidence.ocr_detections)
    vision_block = "\n".join(f"- {fact}" for fact in evidence.vision_facts)

    sections = [
        "# EVIDENCE FROM INSTAGRAM POST",
        f"\n## Post title\n{evidence.title or '(none)'}",
        f"\n## Author\n{evidence.author or '(unknown)'}",
        f"\n## Caption\n{evidence.caption.strip() or '(none)'}",
        f"\n## Hashtags\n{', '.join(evidence.hashtags) or '(none)'}",
        f"\n## Creator comments & replies (HIGHEST PRIORITY)\n{creator_block or '(none)'}",
        f"\n## On-screen text (OCR, timestamped)\n{ocr_block or '(none)'}",
        f"\n## Spoken transcript (timestamped)\n{transcript_block or '(none)'}",
        f"\n## Vision analysis (observed facts)\n{vision_block or '(none)'}",
        f"\n## Community comments (lowest trust)\n{community_block or '(none)'}",
        "\n# TASK\nReconstruct the complete recipe as JSON per the schema.",
    ]
    return "\n".join(sections)
