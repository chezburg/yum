"""Markdown renderer for recipes (Obsidian-friendly with YAML frontmatter).

The rendered Markdown is stored in the database record (single source of
truth) and optionally written to MARKDOWN_EXPORT_DIR for Obsidian vaults.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from src.reconstruction.schemas import StructuredRecipe, ValidationReport

_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def render_markdown(
    recipe: StructuredRecipe,
    source_url: str,
    author: str = "",
    validation: ValidationReport | None = None,
) -> str:
    """Render a recipe as Markdown with Obsidian YAML frontmatter."""
    frontmatter = {
        "title": recipe.title,
        "source": source_url,
        "author": author or None,
        "tags": ["recipe", *recipe.tags],
        "prep_time": recipe.prep_time,
        "cook_time": recipe.cook_time,
        "servings": recipe.servings,
        "confidence": round(recipe.overall_confidence, 2),
    }
    frontmatter = {k: v for k, v in frontmatter.items() if v is not None}

    lines: list[str] = [
        "---",
        yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).strip(),
        "---",
        "",
        f"# {recipe.title}",
        "",
    ]

    if recipe.description:
        lines += [recipe.description, ""]

    meta_bits = []
    if recipe.prep_time:
        meta_bits.append(f"**Prep:** {recipe.prep_time}")
    if recipe.cook_time:
        meta_bits.append(f"**Cook:** {recipe.cook_time}")
    if recipe.servings:
        meta_bits.append(f"**Servings:** {recipe.servings}")
    if meta_bits:
        lines += [" · ".join(meta_bits), ""]

    lines += ["## Ingredients", ""]
    for ing in recipe.ingredients:
        parts = []
        if ing.amount:
            parts.append(ing.amount)
        parts.append(ing.name)
        if ing.preparation:
            parts.append(f"({ing.preparation})")
        lines.append(f"- {' '.join(parts)}")
    lines.append("")

    lines += ["## Instructions", ""]
    for step in sorted(recipe.instructions, key=lambda s: s.step_number):
        suffix = f" *({step.duration})*" if step.duration else ""
        lines.append(f"{step.step_number}. {step.text}{suffix}")
    lines.append("")

    if recipe.equipment:
        lines += ["## Equipment", ""]
        lines += [f"- {item}" for item in recipe.equipment]
        lines.append("")

    if recipe.notes:
        lines += ["## Notes", ""]
        lines += [f"- {note}" for note in recipe.notes]
        lines.append("")

    if validation and validation.issues:
        lines += ["## Extraction warnings", ""]
        lines += [
            f"- **{issue.severity}**: {issue.message}" for issue in validation.issues
        ]
        lines.append("")

    lines += [f"> Source: {source_url}", ""]
    return "\n".join(lines)


def safe_filename(title: str, max_length: int = 80) -> str:
    """Sanitize a recipe title into a safe filename (prevents path traversal)."""
    name = _INVALID_FILENAME_CHARS.sub("", title).strip().strip(".")
    name = re.sub(r"\s+", " ", name)
    if not name:
        name = "untitled-recipe"
    return name[:max_length]


def write_markdown_file(markdown: str, title: str, export_dir: Path) -> Path:
    """Write rendered markdown to the export directory (e.g. Obsidian vault)."""
    export_dir.mkdir(parents=True, exist_ok=True)
    base = safe_filename(title)
    path = export_dir / f"{base}.md"
    # Avoid silently overwriting a different recipe with the same title.
    counter = 1
    while path.exists() and path.read_text(encoding="utf-8") != markdown:
        counter += 1
        path = export_dir / f"{base} ({counter}).md"
    path.write_text(markdown, encoding="utf-8")
    return path
