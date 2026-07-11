"""Strict Pydantic schemas for the structured recipe output (Stages 6 & 7).

Every extracted fact carries its source and a confidence score so
conflicts can be resolved and weak extractions flagged for review.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class EvidenceSource(str, Enum):
    CREATOR_REPLY = "creator_reply"
    CREATOR_COMMENT = "creator_comment"
    PINNED_COMMENT = "pinned_comment"
    CAPTION = "caption"
    OCR = "ocr"
    TRANSCRIPT = "transcript"
    VISION = "vision"
    COMMUNITY_COMMENT = "community_comment"
    INFERRED = "inferred"


class Ingredient(BaseModel):
    """A single ingredient with quantity and provenance."""

    name: str = Field(description="Ingredient name, e.g. 'garlic'")
    amount: str | None = Field(
        default=None, description="Quantity with unit, e.g. '4 cloves', '2 cups'"
    )
    preparation: str | None = Field(
        default=None, description="Prep note, e.g. 'minced', 'room temperature'"
    )
    source: EvidenceSource = Field(description="Where this fact came from")
    confidence: float = Field(ge=0.0, le=1.0, description="Extraction confidence 0-1")


class InstructionStep(BaseModel):
    """A single ordered cooking step."""

    step_number: int = Field(ge=1)
    text: str = Field(description="The instruction text")
    duration: str | None = Field(
        default=None, description="Step duration if stated, e.g. '25 min'"
    )
    source: EvidenceSource
    confidence: float = Field(ge=0.0, le=1.0)


class StructuredRecipe(BaseModel):
    """The complete reconstructed recipe (Stage 6 output schema)."""

    title: str = Field(description="Recipe title")
    description: str | None = Field(default=None, description="Short summary")
    ingredients: list[Ingredient] = Field(default_factory=list)
    instructions: list[InstructionStep] = Field(default_factory=list)
    equipment: list[str] = Field(default_factory=list)
    prep_time: str | None = Field(default=None, description="e.g. '15 min'")
    cook_time: str | None = Field(default=None, description="e.g. '25 min'")
    servings: str | None = Field(default=None, description="e.g. '4 servings'")
    tags: list[str] = Field(
        default_factory=list, description="Cuisine/category tags, e.g. 'italian', 'dessert'"
    )
    overall_confidence: float = Field(
        ge=0.0, le=1.0, description="Overall confidence in the reconstruction"
    )
    notes: list[str] = Field(
        default_factory=list,
        description="Caveats, ambiguities, or extraction warnings for the user",
    )


class ValidationIssue(BaseModel):
    """A single validation warning (Stage 8)."""

    severity: str = Field(description="'warning' or 'error'")
    code: str = Field(description="Machine-readable issue code")
    message: str = Field(description="Human-readable explanation")


class ValidationReport(BaseModel):
    """Result of validating a StructuredRecipe."""

    issues: list[ValidationIssue] = Field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(i.severity == "error" for i in self.issues)
