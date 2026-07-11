"""Tests for Markdown rendering and file export."""

from pathlib import Path

from src.export.markdown import render_markdown, safe_filename, write_markdown_file
from src.reconstruction.schemas import ValidationIssue, ValidationReport


class TestRenderMarkdown:
    def test_contains_frontmatter_and_sections(self, sample_recipe):
        md = render_markdown(
            sample_recipe,
            source_url="https://www.instagram.com/reel/ABC123/",
            author="some.chef",
        )
        assert md.startswith("---")
        assert "title: Garlic Butter Pasta" in md
        assert "# Garlic Butter Pasta" in md
        assert "## Ingredients" in md
        assert "- 4 cloves garlic (minced)" in md
        assert "## Instructions" in md
        assert "1. Boil the spaghetti until al dente. *(8 min)*" in md
        assert "## Equipment" in md
        assert "> Source: https://www.instagram.com/reel/ABC123/" in md

    def test_validation_warnings_included(self, sample_recipe):
        report = ValidationReport(
            issues=[
                ValidationIssue(
                    severity="warning",
                    code="missing_quantity",
                    message="Ingredient 'salt' has no quantity.",
                )
            ]
        )
        md = render_markdown(
            sample_recipe, source_url="https://example.com", validation=report
        )
        assert "## Extraction warnings" in md
        assert "missing_quantity" not in md  # code not shown, message is
        assert "Ingredient 'salt' has no quantity." in md

    def test_tags_in_frontmatter(self, sample_recipe):
        md = render_markdown(sample_recipe, source_url="https://example.com")
        assert "- recipe" in md
        assert "- italian" in md


class TestSafeFilename:
    def test_removes_invalid_characters(self):
        assert safe_filename('My "Best" Recipe: Pasta/Rice?') == "My Best Recipe PastaRice"

    def test_empty_title_fallback(self):
        assert safe_filename("???") == "untitled-recipe"

    def test_length_capped(self):
        assert len(safe_filename("x" * 300)) <= 80


class TestWriteMarkdownFile:
    def test_writes_file(self, sample_recipe, tmp_path: Path):
        md = render_markdown(sample_recipe, source_url="https://example.com")
        path = write_markdown_file(md, sample_recipe.title, tmp_path)
        assert path.exists()
        assert path.read_text(encoding="utf-8") == md

    def test_identical_content_reuses_file(self, sample_recipe, tmp_path: Path):
        md = render_markdown(sample_recipe, source_url="https://example.com")
        p1 = write_markdown_file(md, sample_recipe.title, tmp_path)
        p2 = write_markdown_file(md, sample_recipe.title, tmp_path)
        assert p1 == p2

    def test_different_content_gets_new_name(self, sample_recipe, tmp_path: Path):
        md1 = render_markdown(sample_recipe, source_url="https://example.com/1")
        md2 = render_markdown(sample_recipe, source_url="https://example.com/2")
        p1 = write_markdown_file(md1, sample_recipe.title, tmp_path)
        p2 = write_markdown_file(md2, sample_recipe.title, tmp_path)
        assert p1 != p2
        assert p2.name.endswith("(2).md")
